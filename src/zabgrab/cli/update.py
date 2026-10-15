"""``zabgrab update``: adding new data to a file fetched before."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from ..adapters.dates import EXAMPLES, parse_time, timezone_for
from ..adapters.formats import hdf5
from ..errors import EXIT_PARTIAL, ZabgrabError
from ..model.choice import Choice
from ..model.config import Format, Template
from ..model.series import FetchResult
from ..model.server import Server
from ..services.collect import Plan, collect
from ..services.fetch import fetch
from ..services.metadata import build_metadata
from ..services.output import SaveOptions, Target, guard_large_text
from ..services.retention import warn_beyond_retention
from ..services.servers import resolve_server
from ..services.update import (
    UpdatePlan,
    collect_previous_items,
    plan_update,
)
from ..ui.render import format_time, plan_table
from .app import AppContext, global_options, pass_app
from .fetch import report_problems, save_target


@click.command("update")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--to", metavar="TIME", help=f"Fetch up to this time (default now), {EXAMPLES}."
)
@click.option(
    "-s",
    "--server",
    metavar="NAME|URL",
    help="Fetch from this server instead of the one(s) used before.",
)
@click.option(
    "--rematch",
    is_flag=True,
    help="Look the items up again from the original templates and patterns, "
    "picking up new items, instead of reusing the item IDs fetched before.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be fetched, then stop.")
@click.option(
    "--in-place",
    is_flag=True,
    help="Append without the safety copy (faster for very large files).",
)
@click.option(
    "--allow-large-text",
    is_flag=True,
    help="Don't warn about writing lots of text to a .mat file.",
)
@global_options
@pass_app
def update_command(
    app: AppContext,
    file: Path,
    to: str | None,
    server: str | None,
    rematch: bool,
    dry_run: bool,
    in_place: bool,
    allow_large_text: bool,
) -> None:
    """Add the data since the last fetch to FILE (.mat or .h5).

    The fetch is repeated from where the file's last group ended, with the same
    items, servers and settings, and appended as a new group.
    """
    if file.suffix.lower() in (".tsv", ".csv", ".txt"):
        raise ZabgrabError(
            f"Can't update {file}: only .mat and .h5 files can be updated.",
            hint="Fetch into a .mat or .h5 file to be able to update it later.",
        )
    groups = hdf5.read_fetches(file)
    file_format = Format.MAT if hdf5.is_mat(file) else Format.H5
    config = app.config().config
    now = app.clock.now()
    stored_timezone = (
        groups[-1].metadata.get("query", {}).get("timezone", "local")
        if groups
        else "local"
    )
    tz = timezone_for(stored_timezone)

    end = None
    if to is not None:
        try:
            end = parse_time(to, now=now, tz=tz)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--to") from None

    override = (
        resolve_server(server, config, app.ui, save=app.save_server) if server else None
    )
    plan = plan_update(
        groups,
        config=config,
        ui=app.ui,
        now=now,
        end=end,
        end_text=to,
        server=override,
        file_format=file_format,
    )
    if plan is None:
        app.ui.success(f"{file} is already up to date.")
        return
    query = plan.query
    app.ui.info(
        f"Updating {file} from {format_time(query.start, query.tz)} to "
        f"{format_time(query.end, query.tz)}."
    )

    result = asyncio.run(_download(app, plan, rematch=rematch, dry_run=dry_run))
    if result is None:
        return
    report_problems(app, result)
    if not result.series:
        raise ZabgrabError("Nothing was fetched.")
    if not result.points:
        app.ui.success(f"No new data since the last fetch; {file} wasn't changed.")
        return

    target, data = guard_large_text(
        Target(file, file_format, append=True),
        result,
        app.ui,
        threshold=query.mat_text_warn_entries,
        allow=allow_large_text,
        existing_entries=hdf5.text_entries,
        accepted_before=hdf5.large_text_accepted,
    )
    templates = {
        name: Template.model_validate(template)
        for name, template in groups[-1].metadata.get("templates", {}).items()
    }
    metadata = build_metadata(
        query, data, templates=templates, command=sys.argv, created=now
    )
    metadata["update_of"] = plan.last_group
    where = save_target(target, data, metadata, query, SaveOptions(in_place=in_place))

    count = len(data.series)
    app.ui.success(
        f"Added {data.points:,} points for {count} item{'' if count == 1 else 's'} "
        f"to {where}."
    )
    if result.failed:
        click.get_current_context().exit(EXIT_PARTIAL)


async def _download(
    app: AppContext, plan: UpdatePlan, *, rematch: bool, dry_run: bool
) -> FetchResult | None:
    """Find the items to update and fetch their data; ``None`` for a dry run."""
    query = plan.query
    if rematch:
        if len(plan.servers) > 1:
            raise ZabgrabError(
                f"Group {plan.last_group!r} was fetched from several servers, so "
                "its items can't be looked up again.",
                hint="Update it without --rematch, or fetch from each server "
                "separately.",
            )

        def select(ref: str | Choice[str] | None) -> Server:
            """Choose the server to look the items up on again."""
            # The server the group was fetched from (unless it found no items).
            if plan.servers:
                return next(iter(plan.servers.values()))
            return app.select_server(ref)

        collected: Plan = await collect(
            query, app.ui, select_server=select, connect=app.connect, yes=True
        )
    else:
        collected = await collect_previous_items(plan, app.ui, connect=app.connect)

    try:
        if not collected.jobs:
            raise ZabgrabError("No items to update.")
        warn_beyond_retention(
            collected.jobs,
            start=query.start,
            now=app.clock.now(),
            resolution=query.resolution,
            ui=app.ui,
        )
        if dry_run:
            app.ui.print_data(plan_table(collected.jobs, query.resolution))
            return None
        return await fetch(
            collected.jobs,
            collected.clients,
            start=query.start,
            end=query.end,
            resolution=query.resolution,
            agg=query.agg,
            concurrency=query.jobs,
            ui=app.ui,
        )
    finally:
        await collected.close()
