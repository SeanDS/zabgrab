"""``zabgrab fetch``: downloading data."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click
from pydantic import ValidationError

from ..adapters.configfiles import (
    add_template,
    format_validation_errors,
    read_config_file,
)
from ..adapters.dates import EXAMPLES, parse_time
from ..adapters.formats import hdf5
from ..adapters.formats.delimited import write_delimited
from ..errors import EXIT_PARTIAL, ZabgrabError
from ..model.config import Agg, Config, FetchSettings, Format, Resolution
from ..model.item import ItemSpec
from ..model.names import NAME_RULE, is_valid_name
from ..model.query import Query
from ..model.series import FetchResult
from ..model.server import Server
from ..services.collect import collect
from ..services.fetch import fetch, stored_end
from ..services.metadata import build_metadata
from ..services.output import (
    STRUCTURED,
    SaveOptions,
    Target,
    auto_name,
    guard_large_text,
    targets,
)
from ..services.plotting import plot_spec
from ..services.resolve import CliRequest, resolve_query
from ..services.templates import equivalent_command, template_table
from ..ui import plot
from ..ui.render import explain_table, format_time, plan_table, requests_table
from .app import AppContext, global_options, pass_app
from .config import EDIT_TARGETS, edit_target_path
from .discovery import ItemSpecType


@click.command("fetch")
@click.option(
    "-t",
    "--template",
    "templates",
    multiple=True,
    metavar="NAME",
    help="Template to fetch.",
)
@click.option(
    "-i",
    "--item",
    "items",
    type=ItemSpecType(),
    multiple=True,
    help="Item to fetch as well (repeat for several), e.g. '12345' or "
    "'host=myhost,name=CPU *'.",
)
@click.option(
    "-s",
    "--server",
    "servers",
    multiple=True,
    metavar="NAME|URL",
    help="Server to fetch from; overrides the template's server.",
)
@click.option("--from", "from_", metavar="TIME", help=f"Start time, {EXAMPLES}.")
@click.option("--to", metavar="TIME", help="End time (as for --from).")
@click.option(
    "--resolution",
    type=click.Choice([r.value for r in Resolution]),
    help="raw history, or Zabbix's hourly trends (1h).",
)
@click.option(
    "--agg",
    multiple=True,
    type=click.Choice([a.value for a in Agg]),
    help="Hourly trend statistic to save (repeat for several).",
)
@click.option(
    "-o",
    "--output",
    is_flag=False,
    flag_value="",
    default=None,
    metavar="[PATH]",
    help="Save the data: to PATH, to a directory ending in '/', or with no value "
    "to an automatic name.",
)
@click.option(
    "--format",
    "format_",
    type=click.Choice([f.value for f in Format]),
    help="Output format (default: from the output file name).",
)
@click.option("--output-dir", metavar="DIR", help="Directory for automatic names.")
@click.option(
    "--output-pattern", metavar="PATTERN", help="Pattern for automatic names."
)
@click.option("--timezone", metavar="TZ", help="'local' or e.g. 'Europe/London'.")
@click.option("--jobs", type=click.IntRange(min=1), help="Items to download at once.")
@click.option(
    "--max-items",
    type=click.IntRange(min=1),
    help="Ask before fetching more items than this.",
)
@click.option("--overwrite", is_flag=True, help="Replace an existing output file.")
@click.option(
    "--append",
    is_flag=True,
    help="Add the data to an existing .mat/.h5 file as a new group.",
)
@click.option(
    "--group", metavar="NAME", help="Name of the new group (default fetch_NNNN)."
)
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
@click.option(
    "--plot",
    "plot_",
    is_flag=True,
    help="Plot the data (without saving it, unless --output is given). Needs "
    "matplotlib: pip install 'zabgrab[plot]'.",
)
@click.option(
    "--log-y", is_flag=True, help="With --plot: use log scales for the vertical axes."
)
@click.option("-y", "--yes", is_flag=True, help="Fetch however many items match.")
@click.option("--dry-run", is_flag=True, help="Show what would be fetched, then stop.")
@click.option(
    "--explain",
    is_flag=True,
    help="Show the settings and where each came from, then stop.",
)
@click.option(
    "--save-template",
    metavar="NAME",
    help="Save this fetch (with any answers to questions) as a template.",
)
@click.option(
    "--save-to",
    metavar="user|project|system|PATH",
    help="Config file to save the template to.  [default: user]",
)
@click.option("--force", is_flag=True, help="Replace an existing template.")
@global_options
@pass_app
def fetch_command(
    app: AppContext,
    templates: tuple[str, ...],
    items: tuple[ItemSpec, ...],
    servers: tuple[str, ...],
    from_: str | None,
    to: str | None,
    resolution: str | None,
    agg: tuple[str, ...],
    output: str | None,
    format_: str | None,
    output_dir: str | None,
    output_pattern: str | None,
    timezone: str | None,
    jobs: int | None,
    max_items: int | None,
    overwrite: bool,
    append: bool,
    group: str | None,
    in_place: bool,
    allow_large_text: bool,
    plot_: bool,
    log_y: bool,
    yes: bool,
    dry_run: bool,
    explain: bool,
    save_template: str | None,
    save_to: str | None,
    force: bool,
) -> None:
    """Fetch data from Zabbix, from templates and/or items.

    Settings come from the command line, then the templates, then [defaults] in
    your config. Anything missing is asked for.
    """
    given = {
        "from": from_,
        "to": to,
        "resolution": resolution,
        "agg": list(agg) or None,
        "format": format_,
        "output_dir": output_dir,
        "output_pattern": output_pattern,
        "timezone": timezone,
        "jobs": jobs,
        "max_items": max_items,
    }
    values = {key: value for key, value in given.items() if value is not None}
    try:
        FetchSettings.model_validate(values)
    except ValidationError as exc:
        raise click.UsageError("; ".join(format_validation_errors(exc))) from None

    save = SaveOptions(
        format=Format(format_) if format_ else None,
        overwrite=overwrite,
        append=append,
        group=group,
        in_place=in_place,
        allow_large_text=allow_large_text,
        plot=plot_,
    )
    if log_y and not plot_:
        raise click.UsageError("--log-y only applies with --plot.")
    if not explain:
        save.check(output)

    loaded = app.config()
    template_target = _template_target(
        app, loaded.config, save_template, save_to, force=force
    )
    now = app.clock.now()

    def parse(text: str, tz: tzinfo | None) -> datetime:
        """Parse a time relative to the start of the command."""
        return parse_time(text, now=now, tz=tz)

    cli = CliRequest(
        template=_one(
            templates, "--template", "Only one template can be fetched at a time."
        ),
        items=items,
        server=_one(servers, "--server", "zabgrab fetches from one server at a time."),
        values=values,
    )
    query = resolve_query(
        cli,
        loaded.config,
        app.ui,
        parse_time=parse,
        defaults_source=lambda key: loaded.source_of("defaults", key),
    )

    partial = False
    if explain:
        app.ui.print_data(explain_table(query))
        app.ui.print_data(requests_table(query))
    else:
        partial = _fetch_and_save(
            app,
            query,
            loaded.config,
            output=output,
            options=save,
            yes=yes,
            dry_run=dry_run,
            now=now,
            log_y=log_y,
        )

    if template_target is not None:
        _save_template(app, template_target, cli, query, loaded.config)
    elif query.answers:
        _show_equivalent(app, query)
    if partial:
        click.get_current_context().exit(EXIT_PARTIAL)


def _one(values: tuple[str, ...], option: str, reason: str) -> str | None:
    """Get an option that can only be given once, failing if it's repeated."""
    if len(values) > 1:
        raise click.UsageError(f"{option} can only be given once: {reason}")
    return values[0] if values else None


@dataclass(frozen=True)
class _TemplateTarget:
    """Where ``--save-template`` saves, and whether it replaces a template."""

    name: str
    path: Path
    replace: bool


def _template_target(
    app: AppContext,
    config: Config,
    name: str | None,
    save_to: str | None,
    *,
    force: bool,
) -> _TemplateTarget | None:
    """Where --save-template saves to, checked before anything is fetched."""
    if name is None:
        for option, given in (("--save-to", save_to is not None), ("--force", force)):
            if given:
                raise click.UsageError(f"{option} only applies with --save-template.")
        return None
    if not is_valid_name(name):
        raise click.BadParameter(
            f"{name!r}: {NAME_RULE}", param_hint="'--save-template'"
        )
    target = save_to or "user"
    path = (
        edit_target_path(app.env, EDIT_TARGETS[target])
        if target in EDIT_TARGETS
        else Path(target)
    )
    if not force:
        if path.exists() and name in read_config_file(path).get("templates", {}):
            where = str(path)
        elif name in config.templates:
            where = str(app.config().source_of("templates", name))
        else:
            where = ""
        if where:
            raise ZabgrabError(
                f"Template {name!r} already exists in {where}.",
                hint="Pass --force to replace it, or choose another name.",
            )
    return _TemplateTarget(name, path, replace=force)


def _save_template(
    app: AppContext,
    target: _TemplateTarget,
    cli: CliRequest,
    query: Query,
    config: Config,
) -> None:
    """Save the fetch as a template (``--save-template``)."""
    table = template_table(cli, query, config)
    written = add_template(target.path, target.name, table, replace=target.replace)
    app.ui.success(f"Saved template {target.name!r} to {target.path}:")
    app.ui.info(written)
    app.ui.info(f"Run it again with `zabgrab fetch -t {target.name}`.")


def _show_equivalent(app: AppContext, query: Query) -> None:
    """Show the command that repeats this fetch without its questions."""
    equivalent = equivalent_command(sys.argv[1:], query.answers)
    app.ui.info(f"Next time: {equivalent.command}")
    for note in equivalent.notes:
        app.ui.info(note)
    app.ui.info("Or add --save-template NAME to save it as a template.")


def _fetch_and_save(
    app: AppContext,
    query: Query,
    config: Config,
    *,
    output: str | None,
    options: SaveOptions,
    yes: bool,
    dry_run: bool,
    now: datetime,
    log_y: bool = False,
) -> bool:
    """Download and save the data; return whether some items failed."""
    result = asyncio.run(_download(app, query, yes=yes, dry_run=dry_run))
    if result is None:
        return False

    report_problems(app, result)
    if not result.series:
        raise ZabgrabError("Nothing was fetched.")

    servers_used = list(dict.fromkeys(series.server for series in result.series))
    name = auto_name(
        query,
        hosts=[series.item.host for series in result.series],
        servers=[_short(server) for server in servers_used],
    )
    chosen = targets(output, query, app.ui, name=name, options=options)
    saved = []
    for target in chosen:
        target, data = guard_large_text(
            target,
            result,
            app.ui,
            threshold=query.mat_text_warn_entries,
            allow=options.allow_large_text,
            existing_entries=hdf5.text_entries,
            accepted_before=hdf5.large_text_accepted,
        )
        metadata = build_metadata(
            query,
            data,
            templates=config.templates,
            command=sys.argv,
            created=now,
        )
        saved.append(save_target(target, data, metadata, query, options))

    count = len(result.series)
    summary = (
        f"Fetched {count} item{'' if count == 1 else 's'} ({result.points:,} points) "
        f"from {', '.join(server.display for server in servers_used)}, "
        f"{format_time(query.start, query.tz)} to {format_time(query.end, query.tz)}"
    )
    if saved:
        summary += "; saved to " + ", ".join(saved)
    app.ui.success(summary + ".")
    if options.plot:
        # The whole requested range was fetched, so lines join up across it.
        coverage = [(query.start.timestamp(), stored_end(query))]
        spec = plot_spec(result.series, coverage, app.ui, log_y=log_y)
        plot.show_or_save(spec, query.tz)
    return bool(result.failed)


async def _download(
    app: AppContext, query: Query, *, yes: bool, dry_run: bool
) -> FetchResult | None:
    """Find the items and fetch their data; return ``None`` for a dry run."""
    plan = await collect(
        query,
        app.ui,
        select_server=app.select_server,
        connect=app.connect,
        yes=yes,
    )
    try:
        if not plan.jobs:
            raise ZabgrabError(
                "No items to fetch.",
                hint="Check the item patterns with `zabgrab items`.",
            )
        if dry_run:
            app.ui.print_data(plan_table(plan.jobs, query.resolution))
            return None
        return await fetch(
            plan.jobs,
            plan.clients,
            start=query.start,
            end=query.end,
            resolution=query.resolution,
            agg=query.agg,
            concurrency=query.jobs,
            ui=app.ui,
        )
    finally:
        await plan.close()


def save_target(
    target: Target,
    result: FetchResult,
    metadata: dict[str, Any],
    query: Query,
    options: SaveOptions,
) -> str:
    """Write fetched data to a file.

    Parameters
    ----------
    target : Target
        Where and how to save.
    result : FetchResult
        The data.
    metadata : dict
        The metadata to save with it.
    query : Query
        The query, for its time range and timezone.
    options : SaveOptions
        ``--group`` and ``--in-place``, for appending.

    Returns
    -------
    str
        A description of where the data went, for the summary.
    """
    if target.format not in STRUCTURED:
        delimiter = "\t" if target.format is Format.TSV else ","
        write_delimited(target.path, result, metadata, delimiter=delimiter, tz=query.tz)
        return str(target.path)
    group = hdf5.write_fetch(
        target.path,
        result,
        metadata,
        mat=target.format is Format.MAT,
        t_from=query.start.timestamp(),
        t_to=stored_end(query),
        append=target.append,
        group=options.group,
        in_place=options.in_place,
        large_text_accepted=target.large_text_accepted,
    )
    return f"{target.path} (group {group})"


def report_problems(app: AppContext, result: FetchResult) -> None:
    """Warn about skipped items and report failed ones.

    Parameters
    ----------
    app : AppContext
        For the UI.
    result : FetchResult
        The fetched data.
    """
    for skipped in result.skipped:
        app.ui.warning(
            f"Skipped {skipped.item.host}: {skipped.item.name} "
            f"({skipped.item.itemid}): {skipped.reason}."
        )
    for failed in result.failed:
        app.ui.error(
            f"Couldn't fetch {failed.item.host}: {failed.item.name} "
            f"({failed.item.itemid}).",
            details=[failed.reason],
        )


def _short(server: Server) -> str:
    """Name a server briefly, for automatic file names."""
    return server.name or urlsplit(server.url).hostname or server.url
