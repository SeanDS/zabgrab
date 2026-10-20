"""``zabgrab show``: listing data points, from a file or straight from Zabbix."""

from __future__ import annotations

import re
from pathlib import Path

import click

from ..adapters.dates import EXAMPLES
from ..errors import EXIT_PARTIAL, ZabgrabError
from ..model.config import Agg, Resolution
from ..model.item import ItemSpec
from ..model.itemspec import EXAMPLE
from ..model.series import Series
from ..services.binning import Bins, histograms
from ..services.listing import SORT_KEYS, ListingOptions, Section, listing
from ..ui.listing import histogram_lines, listing_lines
from .app import AppContext, global_options, pass_app
from .discovery import ItemSpecType
from .fetch import (
    download,
    fetch_summary,
    resolve_request,
    settings_values,
    show_equivalent,
)
from .reading import bin_option, nothing_selected, read_dataset


def _pattern(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> re.Pattern[str] | None:
    """Compile ``--grep``'s regular expression."""
    if value is None:
        return None
    try:
        return re.compile(value)
    except re.error as exc:
        raise click.BadParameter(
            f"{value!r} isn't a valid regular expression: {exc}."
        ) from None


@click.command("show")
@click.argument(
    "file",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-t",
    "--template",
    "templates",
    multiple=True,
    metavar="NAME",
    help="Without FILE: template to fetch.",
)
@click.option(
    "-i",
    "--item",
    "items",
    type=ItemSpecType(),
    multiple=True,
    help=f"Items to show, selected as for fetch (repeat for several); {EXAMPLE}. "
    "From FILE, they select its items; otherwise they're fetched.",
)
@click.option(
    "-m",
    "--match",
    multiple=True,
    metavar="PATTERN",
    help="With FILE: only items whose whole ID, name, key or 'host: name' matches "
    "this fnmatch pattern, in any case (repeat for several).",
)
@click.option(
    "-s",
    "--server",
    "servers",
    multiple=True,
    metavar="NAME|URL",
    help="Without FILE: server to fetch from.",
)
@click.option(
    "--from", "from_", metavar="TIME", help=f"Only data from this time, {EXAMPLES}."
)
@click.option("--to", metavar="TIME", help="Only data up to this time.")
@click.option(
    "--resolution",
    type=click.Choice([r.value for r in Resolution]),
    help="Without FILE: raw history, or Zabbix's hourly trends (1h).",
)
@click.option(
    "--agg",
    multiple=True,
    type=click.Choice([a.value for a in Agg]),
    help="Without FILE: hourly trend statistic to fetch (repeat for several).",
)
@click.option(
    "--group",
    "groups",
    multiple=True,
    metavar="NAME",
    help="With FILE: only these fetch groups (repeat for several).",
)
@click.option(
    "-y", "--yes", is_flag=True, help="Without FILE: fetch however many items match."
)
@click.option(
    "--sort",
    type=click.Choice(SORT_KEYS),
    default="time",
    show_default=True,
    help="Order by time, or by value (each item separately). For hourly trends, "
    "value means the average; min, max or count sort by those instead.",
)
@click.option("--desc", is_flag=True, help="Largest or latest first.")
@click.option(
    "-n",
    "--limit",
    type=click.IntRange(min=1),
    metavar="N",
    help="Show at most this many values (for each item, sorting by value).",
)
@click.option(
    "--offset",
    type=click.IntRange(min=0),
    default=0,
    metavar="N",
    help="Skip this many values first (for each item, sorting by value).",
)
@click.option(
    "--grep",
    metavar="REGEX",
    callback=_pattern,
    help="Only values containing a match for this regular expression (Python's "
    "syntax, like grep -E; start it with (?i) to ignore case).",
)
@click.option(
    "--above",
    type=float,
    metavar="NUMBER",
    help="Only numeric values greater than this.",
)
@click.option(
    "--below",
    type=float,
    metavar="NUMBER",
    help="Only numeric values less than this.",
)
@click.option(
    "--wrap",
    is_flag=True,
    help="Show text values in full, over several lines, rather than on one line.",
)
@bin_option
@click.option(
    "--no-pager", is_flag=True, help="Print everything, without a pager (less)."
)
@global_options
@pass_app
def show_command(
    app: AppContext,
    file: Path | None,
    templates: tuple[str, ...],
    items: tuple[ItemSpec, ...],
    match: tuple[str, ...],
    servers: tuple[str, ...],
    from_: str | None,
    to: str | None,
    resolution: str | None,
    agg: tuple[str, ...],
    groups: tuple[str, ...],
    yes: bool,
    sort: str,
    desc: bool,
    limit: int | None,
    offset: int,
    grep: re.Pattern[str] | None,
    above: float | None,
    below: float | None,
    wrap: bool,
    bins: Bins | None,
    no_pager: bool,
) -> None:
    """List data points: from FILE (.mat or .h5), or fetched from Zabbix.

    From FILE, the data of every fetch group is merged, and --item, --match,
    --from, --to and --group select as for plot and export. Without FILE, the data
    is fetched as by fetch (with --template, --item, --server, --from and --to),
    but not saved.

    Points are listed in time order, or with --sort value, each item's points by
    value. With --bin, each item's values are counted in bins instead. In a
    terminal, the list opens in a pager ($PAGER, or less).
    """
    if bins is not None:
        _not_with_bins(
            {
                "--sort": sort != "time",
                "--desc": desc,
                "--limit": limit is not None,
                "--offset": offset != 0,
                "--wrap": wrap,
            }
        )
    options = ListingOptions(
        sort=sort,
        descending=desc,
        limit=limit,
        offset=offset,
        grep=grep,
        above=above,
        below=below,
    )
    query = None
    failed = False
    if file is not None:
        _only_when(
            "fetching (without FILE)",
            {
                "--template": templates,
                "--server": servers,
                "--resolution": resolution,
                "--agg": agg,
                "--yes": yes,
            },
        )
        dataset, tz = read_dataset(
            app,
            file,
            verb=("show", "shown"),
            items=items,
            match=match,
            from_=from_,
            to=to,
            groups=groups,
        )
        if not dataset.series:
            raise ZabgrabError("Nothing to show.", hint=nothing_selected(file))
        series = dataset.series
    else:
        _only_when("reading a FILE", {"--match": match, "--group": groups})
        given = {"from": from_, "to": to, "resolution": resolution}
        values = settings_values(given | {"agg": list(agg) or None})
        _, query = resolve_request(
            app,
            templates=templates,
            items=items,
            servers=servers,
            values=values,
            now=app.clock.now(),
        )
        result = download(app, query, yes=yes)
        app.ui.success(fetch_summary(result, query) + ".")
        series, tz, failed = result.series, query.tz, bool(result.failed)

    if bins is not None:
        _show_bins(app, series, bins, options, pager=not no_pager)
    elif sections := listing(series, options):
        lines = listing_lines(
            sections, tz, headings=sort != "time", offset=offset, wrap=wrap
        )
        app.ui.page(lines, pager=not no_pager)
        if sort == "time":
            _report_slice(app, sections[0], offset)
    elif grep is not None or above is not None or below is not None:
        app.ui.info("No values match --grep, --above or --below.")
    else:
        app.ui.info("There are no values to show.")

    if query is not None and query.answers:
        show_equivalent(app, query, templates=False)
    if failed:
        click.get_current_context().exit(EXIT_PARTIAL)


def _not_with_bins(given: dict[str, bool]) -> None:
    """Refuse the ordering and slicing options, which don't apply to bins."""
    for option, value in given.items():
        if value:
            raise click.UsageError(f"{option} doesn't apply with --bin.")


def _show_bins(
    app: AppContext,
    series: list[Series],
    bins: Bins,
    options: ListingOptions,
    *,
    pager: bool,
) -> None:
    """List how many of each item's values fall in each bin."""
    counted = histograms(series, bins, options)
    for one in series:
        if not one.item.value_type.numeric:
            item = one.item
            app.ui.info(
                f"Not counting item {item.itemid} ({item.host}: {item.name}), a "
                f"{item.value_type.label} item; only numbers can be binned."
            )
    if not counted:
        raise ZabgrabError(
            "There are no numeric items to count.",
            hint="Only numbers can be binned; list text items without --bin.",
        )
    app.ui.page(histogram_lines(counted, bins), pager=pager)


def _only_when(when: str, given: dict[str, object]) -> None:
    """Refuse options that only apply in the other mode."""
    for option, value in given.items():
        if value:
            raise click.UsageError(f"{option} only applies when {when}.")


def _report_slice(app: AppContext, section: Section, offset: int) -> None:
    """Say which values a listing in time order showed, if not all of them."""
    shown, matched = len(section.rows), section.matched
    if shown == matched:
        return
    if shown:
        app.ui.info(f"Showed values {offset + 1:,}-{offset + shown:,} of {matched:,}.")
    else:
        app.ui.info(f"--offset {offset:,} skipped all {matched:,} values.")
