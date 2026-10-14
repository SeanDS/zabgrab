"""``zabgrab fetch``: downloading data."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, tzinfo
from typing import Any
from urllib.parse import urlsplit

import click
from pydantic import ValidationError

from ..adapters.configfiles import format_validation_errors
from ..adapters.dates import EXAMPLES, parse_time
from ..adapters.formats import hdf5
from ..adapters.formats.delimited import write_delimited
from ..errors import EXIT_PARTIAL, ZabgrabError
from ..model.config import Agg, FetchSettings, Format, Resolution
from ..model.item import ItemSpec
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
from ..services.resolve import CliRequest, resolve_query
from ..ui.render import explain_table, format_time, plan_table, requests_table
from .app import AppContext, global_options, pass_app
from .discovery import ItemSpecType


@click.command("fetch")
@click.option(
    "-t",
    "--template",
    "templates",
    multiple=True,
    metavar="NAME",
    help="Template to fetch (repeat to stack several).",
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
    help="Server to fetch from (repeat for several); overrides templates' servers.",
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
@click.option("-y", "--yes", is_flag=True, help="Fetch however many items match.")
@click.option("--dry-run", is_flag=True, help="Show what would be fetched, then stop.")
@click.option(
    "--explain",
    is_flag=True,
    help="Show the settings and where each came from, then stop.",
)
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
    yes: bool,
    dry_run: bool,
    explain: bool,
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
    )
    if not explain:
        save.check(output)

    loaded = app.config()
    now = app.clock.now()

    def parse(text: str, tz: tzinfo | None) -> datetime:
        return parse_time(text, now=now, tz=tz)

    query = resolve_query(
        CliRequest(templates, items, servers, values),
        loaded.config,
        app.ui,
        parse_time=parse,
        defaults_source=lambda key: loaded.source_of("defaults", key),
    )

    if explain:
        app.ui.print_data(explain_table(query))
        app.ui.print_data(requests_table(query))
        return

    result = asyncio.run(_download(app, query, yes=yes, dry_run=dry_run))
    if result is None:
        return

    report_problems(app, result)
    if not result.series:
        raise ZabgrabError("Nothing was fetched.")

    servers_used = list(dict.fromkeys(series.server for series in result.series))
    name = auto_name(
        query,
        hosts=[series.item.host for series in result.series],
        servers=[_short(server) for server in servers_used],
    )
    chosen = targets(output, query, app.ui, name=name, options=save)
    saved = []
    for target in chosen:
        target, data = guard_large_text(
            target,
            result,
            app.ui,
            threshold=query.mat_text_warn_entries,
            allow=allow_large_text,
            existing_entries=hdf5.text_entries,
            accepted_before=hdf5.large_text_accepted,
        )
        metadata = build_metadata(
            query,
            data,
            templates=loaded.config.templates,
            command=sys.argv,
            created=now,
        )
        saved.append(save_target(target, data, metadata, query, save))

    count = len(result.series)
    summary = (
        f"Fetched {count} item{'' if count == 1 else 's'} ({result.points:,} points) "
        f"from {', '.join(server.display for server in servers_used)}, "
        f"{format_time(query.start, query.tz)} to {format_time(query.end, query.tz)}"
    )
    if saved:
        summary += "; saved to " + ", ".join(saved)
    app.ui.success(summary + ".")

    if result.failed:
        click.get_current_context().exit(EXIT_PARTIAL)


async def _download(
    app: AppContext, query: Query, *, yes: bool, dry_run: bool
) -> FetchResult | None:
    plan = await collect(
        query,
        app.ui,
        select_servers=lambda server: app.select_servers([], server),
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
    """Write the data; return a description of where it went."""
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
    return server.name or urlsplit(server.url).hostname or server.url
