"""``zabgrab migrate``: moving a file's items to new IDs or servers, or retiring."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterable
from pathlib import Path

import click

from .. import __version__
from ..adapters.formats import hdf5
from ..errors import ZabgrabError
from ..model.server import Server
from ..model.zabbix import Item
from ..services.migrate import (
    MigrationPlan,
    apply_migration,
    find_items,
    look_up,
    parse_mapping,
    plan_migration,
    read_mapping_file,
    server_names,
    specs_by_id,
)
from ..services.records import Identity, active_entries, latest_entries
from ..services.servers import resolve_server
from ..services.update import stored_server
from ..ui.render import migration_table
from .app import AppContext, global_options, pass_app


def _mappings(
    ctx: click.Context, param: click.Parameter, values: tuple[str, ...]
) -> list[tuple[int, int]]:
    """Parse ``--map OLD=NEW`` values."""
    try:
        return [parse_mapping(value) for value in values]
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from None


@click.command("migrate")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-s",
    "--server",
    metavar="NAME|URL",
    help="Move the items update fetches to this server, finding each by its host "
    "and key there. Items not found are retired.",
)
@click.option(
    "--map",
    "maps",
    multiple=True,
    metavar="OLD=NEW",
    callback=_mappings,
    help="Give the item with ID OLD the new ID NEW (on --server, if given), "
    "instead of any match by host and key (repeat for several).",
)
@click.option(
    "--map-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read OLD and NEW item IDs from a file, one pair per line, separated by "
    "a comma, a tab or spaces.",
)
@click.option(
    "--no-match",
    is_flag=True,
    help="With --server, don't find items by host and key: only --map and "
    "--map-file move items, and the rest are retired.",
)
@click.option(
    "--retire",
    multiple=True,
    type=int,
    metavar="ID",
    help="Stop updating this item, keeping its data (repeat for several).",
)
@click.option(
    "--unretire",
    multiple=True,
    type=int,
    metavar="ID",
    help="Start updating a retired item again (repeat for several).",
)
@click.option("--dry-run", is_flag=True, help="Show what would change, then stop.")
@click.option("-y", "--yes", is_flag=True, help="Change the file without asking.")
@global_options
@pass_app
def migrate_command(
    app: AppContext,
    file: Path,
    server: str | None,
    maps: list[tuple[int, int]],
    map_file: Path | None,
    no_match: bool,
    retire: tuple[int, ...],
    unretire: tuple[int, ...],
    dry_run: bool,
    yes: bool,
) -> None:
    """Move FILE's items (.mat or .h5) to new item IDs or a new server, or retire them.

    Use it when a Zabbix server is replaced without its history, so that the same
    items have new IDs: with --server NEW, each item update fetches is found on
    NEW by its host and key (--map and --map-file give or correct IDs), and every
    group of FILE is rewritten with the new server and IDs, so the data from
    before and after reads as one series per item. Items not found on NEW are
    retired: their data stays, but update no longer fetches them.

    --map without --server changes item IDs on the same server, and --retire and
    --unretire stop or restart updating single items, e.g. sensors that were
    unplugged. Each item's earlier servers and IDs, and a log of every migration,
    are kept in the file's metadata (zabgrab info FILE --migrations lists them).
    The changes are shown, and asked about, before FILE is rewritten.
    """
    if file.suffix.lower() in (".tsv", ".csv", ".txt"):
        raise ZabgrabError(
            f"Can't migrate {file}: only .mat and .h5 files can be migrated.",
            hint="Fetch into a .mat or .h5 file to be able to update it.",
        )
    if not (server or maps or map_file or retire or unretire):
        raise click.UsageError(
            "Nothing to do: give --server, --map, --map-file, --retire or --unretire."
        )
    if no_match and not server:
        raise click.UsageError("--no-match only applies with --server.")
    pairs = list(maps)
    if map_file is not None:
        try:
            pairs += read_mapping_file(map_file.read_text(), str(map_file))
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--map-file") from None
    new_ids = dict(pairs)
    if len(new_ids) < len(pairs):
        clashing = [old for old, new in pairs if new_ids[old] != new]
        if clashing:
            raise click.UsageError(f"Item {clashing[0]} is given two new IDs.")

    groups = hdf5.read_fetches(file)
    if not groups:
        raise ZabgrabError(f"{file} has no data to migrate.")
    olds = find_items(groups, new_ids, "--map")
    retiring = find_items(groups, retire, "--retire")
    returning = find_items(groups, unretire, "--unretire")

    config = app.config().config
    target = (
        resolve_server(server, config, app.ui, save=app.save_server) if server else None
    )
    matched: dict[Identity, Item] = {}
    given: dict[Identity, tuple[Server, Item]] = {}
    if target is not None or new_ids:
        where = target or _mapped_server(app, groups, olds.values())
        # Items brought back are matched too, so they move with the rest.
        known = latest_entries(groups)
        entries = (
            []
            if target is None or no_match
            else [
                *active_entries(groups[-1].metadata),
                *(known[key] for key in returning.values()),
            ]
        )
        matched, found = asyncio.run(
            look_up(where, app.connect, entries=entries, itemids=new_ids.values())
        )
        given = {olds[old]: (where, found[new]) for old, new in new_ids.items()}
    plan = plan_migration(
        groups,
        target=target,
        matched=matched,
        given=given,
        retire=retiring.values(),
        unretire=returning.values(),
    )

    if plan.unchanged:
        count = len(plan.unchanged)
        app.ui.info(
            f"{count} item{'' if count == 1 else 's'} already "
            f"{'has' if count == 1 else 'have'} the same ID on "
            f"{target.display if target else 'their server'}."
        )
    if not plan.changes:
        app.ui.success(f"Nothing to change in {file}.")
        return
    app.ui.print_data(migration_table(plan))
    if dry_run:
        app.ui.info(f"Dry run: {file} wasn't changed.")
        return
    _warn_retirements(app, plan)
    if not yes and not app.ui.confirm(
        f"Rewrite {file} ({len(groups)} group{'' if len(groups) == 1 else 's'})?",
        fix="pass --yes to change the file without being asked",
        default=False,
    ):
        app.ui.info(f"{file} wasn't changed.")
        return

    changed = apply_migration(
        groups, plan, at=app.clock.now(), command=sys.argv, version=__version__
    )
    hdf5.rewrite_fetches(file, changed)
    _note_template(app, groups, plan)
    app.ui.success(f"Migrated {file}: {_summary(plan)}.")


def _mapped_server(
    app: AppContext, groups: list[hdf5.FetchGroup], items: Iterable[Identity]
) -> Server:
    """Get the server items given new IDs without --server stay on."""
    urls = list(dict.fromkeys(url for url, _ in items))
    if len(urls) > 1:
        raise ZabgrabError(
            f"The items given new IDs are on several servers ({', '.join(urls)}).",
            hint="Give the server they're on now with --server.",
        )
    names = server_names(groups)
    return stored_server(urls[0], names.get(urls[0]), app.config().config, app.ui)


def _warn_retirements(app: AppContext, plan: MigrationPlan) -> None:
    """Make sure retirements are noticed before the file changes."""
    count = len(plan.retirements)
    if count:
        app.ui.warning(
            f"{count} item{'' if count == 1 else 's'} will be retired: "
            f"{'its' if count == 1 else 'their'} data stays in the file, but update "
            "won't fetch any more (--unretire brings an item back)."
        )


def _note_template(
    app: AppContext, groups: list[hdf5.FetchGroup], plan: MigrationPlan
) -> None:
    """Note a config template that still selects moved items by their old IDs."""
    name = next(iter(groups[-1].metadata.get("query", {}).get("templates", [])), None)
    template = app.config().config.templates.get(name) if name else None
    if template is None:
        return
    old = specs_by_id(template.items, (move.old[1] for move in plan.moves))
    if old:
        app.ui.info(
            f"Template {name!r} in your config selects item"
            f"{'' if len(old) == 1 else 's'} {', '.join(map(str, old))} by ID; "
            "change them to the new IDs (zabgrab config edit) for future fetches."
        )


def _summary(plan: MigrationPlan) -> str:
    """Summarise what a migration changed, e.g. ``moved 5 items to 'b'``."""
    parts = []
    if plan.moves:
        count = len(plan.moves)
        parts.append(
            f"moved {count} item{'' if count == 1 else 's'}"
            + (f" to {plan.target.display}" if plan.target else "")
        )
    if plan.retirements:
        parts.append(f"retired {len(plan.retirements)}")
    if plan.unretired:
        parts.append(f"brought back {len(plan.unretired)}")
    return ", ".join(parts)
