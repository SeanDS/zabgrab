"""Laying out listings of data points (``zabgrab show``) as lines of text.

Listings can be long, so they're plain aligned columns rather than rich tables:
quick to produce, and readable in a pager, a pipe or a file.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

from ..model.config import Resolution
from ..model.series import Series
from ..services.binning import Bins, Histogram
from ..services.listing import STATISTICS, Row, Section

#: A line of output and its style: ``""``, ``"bold"`` or ``"dim"``.
Line = tuple[str, str]

#: What newlines in a text value become when it's kept to one line.
NEWLINE = " ⏎ "

#: Space between columns.
GAP = "  "


@dataclass(frozen=True)
class _Column:
    """A column: its heading, how to get a row's cell, and its alignment."""

    heading: str
    cell: Callable[[Row], Any]
    right: bool = False
    # Left out when empty (or zero) in every row shown.
    optional: bool = False


def label(series: Series) -> str:
    """Name a series' item, with its units and resolution if it has them."""
    text = f"{series.item.host}: {series.item.name}"
    if series.item.units:
        text += f" [{series.item.units}]"
    if series.resolution is Resolution.HOURLY:
        text += " (hourly)"
    return text


def listing_lines(
    sections: Sequence[Section],
    tz: tzinfo | None,
    *,
    headings: bool,
    offset: int = 0,
    wrap: bool = False,
) -> list[Line]:
    """Lay out a listing.

    Parameters
    ----------
    sections : sequence of Section
        The listing (see :func:`zabgrab.services.listing.listing`).
    tz : tzinfo or None
        The time zone to show times in (``None`` for local time).
    headings : bool
        Head each section with its item and how many of its points are shown (for
        listings with a section per item).
    offset : int, optional
        How many points were skipped at the start of each section, for the
        headings' counts.
    wrap : bool, optional
        Show text values in full, over several lines, rather than on one line with
        ``⏎`` for each line break.

    Returns
    -------
    list of Line
        The lines, without line breaks, and how to style each.
    """
    lines: list[Line] = []
    for number, section in enumerate(sections):
        if headings:
            if number:
                lines.append(("", ""))
            (series,) = section.series
            lines.append((f"{label(series)}: {_count(section, offset)}", "bold"))
            if not section.rows:
                continue  # all skipped by the offset
        lines += _table(section, tz, wrap=wrap, items=not headings)
    return lines


def _count(section: Section, offset: int) -> str:
    """Describe which of a section's points are shown, e.g. ``1-10 of 34 values``."""
    total = f"{section.matched:,} value{'' if section.matched == 1 else 's'}"
    if len(section.rows) == section.matched:
        return total
    if not section.rows:
        return f"{total}, all skipped by --offset"
    first, last = offset + 1, offset + len(section.rows)
    shown = f"{first:,}" if first == last else f"{first:,}-{last:,}"
    return f"{shown} of {total}"


def _table(
    section: Section, tz: tzinfo | None, *, wrap: bool, items: bool
) -> list[Line]:
    """Lay out a section's points as aligned columns, under a heading line."""
    columns = _columns(section.series, tz, items=items)
    columns = [
        column
        for column in columns
        if not column.optional
        or any(
            _text(column.cell(row), wrap=False) not in ("", "0") for row in section.rows
        )
    ]
    last = len(columns) - 1
    cells = [
        [
            _text(column.cell(row), wrap=wrap and index == last)
            for index, column in enumerate(columns)
        ]
        for row in section.rows
    ]
    # The last column (the value) isn't padded, and may run over several lines.
    widths = [
        max([len(column.heading)] + [len(row[index]) for row in cells])
        for index, column in enumerate(columns[:-1])
    ]

    def join(row: list[str]) -> str:
        padded = [
            cell.rjust(width) if column.right else cell.ljust(width)
            for cell, width, column in zip(row, widths, columns, strict=False)
        ]
        return GAP.join([*padded, row[-1]]).rstrip()

    lines: list[Line] = [(join([column.heading for column in columns]), "bold")]
    for row in cells:
        first, *rest = row[-1].split("\n")
        lines.append((join([*row[:-1], first]), ""))
        # Further lines of a wrapped value go under its column.
        indent = sum(width + len(GAP) for width in widths)
        lines += [(" " * indent + more, "") for more in rest]
    return lines


def _columns(
    series: Sequence[Series], tz: tzinfo | None, *, items: bool
) -> list[_Column]:
    """Choose a section's columns: those that any of its items has."""
    has = {name for one in series for name in one.columns}
    columns = [_Column("Time", lambda row: _time(row.get("time"), tz))]
    if items and len(series) > 1:
        columns.append(_Column("Item", lambda row: label(row.series)))
    if "log_time" in has:
        # Zabbix only fills these in for some logs (e.g. Windows event logs).
        columns += [
            _Column(
                "Log time", lambda row: _time(row.get("log_time"), tz), optional=True
            ),
            _Column(
                "Severity", lambda row: row.get("severity"), right=True, optional=True
            ),
            _Column("Source", lambda row: row.get("source"), optional=True),
            _Column(
                "Event ID", lambda row: row.get("event_id"), right=True, optional=True
            ),
        ]
    for name in STATISTICS:
        if name in has:
            columns.append(_Column(name.capitalize(), _getter(name), right=True))
    if "value" in has:
        units = {one.item.units for one in series}
        heading = "Value"
        if len(series) == 1 and units != {""}:
            heading += f" [{units.pop()}]"
        # Numbers line up on the right, unless text is mixed in.
        numeric = all(one.item.value_type.numeric for one in series)
        columns.append(_Column(heading, _getter("value"), right=numeric))
    return columns


def _getter(name: str) -> Callable[[Row], Any]:
    """Make a cell function reading a column."""
    return lambda row: row.get(name)


def _time(time: float | None, tz: tzinfo | None) -> str | None:
    """Format POSIX seconds, with fractions of a second when there are any."""
    if time is None:
        return None
    moment = datetime.fromtimestamp(time, UTC).astimezone(tz)
    text = moment.strftime("%Y-%m-%d %H:%M:%S")
    if moment.microsecond:
        text += f".{moment.microsecond:06d}".rstrip("0")
    return f"{text} {moment.strftime('%Z')}".rstrip()


def _text(value: Any, wrap: bool) -> str:
    """Format a cell's value: nothing for missing values, text on one line."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\t", " ")
    return text if wrap else text.replace("\n", NEWLINE)


def histogram_lines(histograms: Sequence[Histogram], bins: Bins) -> list[Line]:
    """Lay out bin counts: a section per item, with a line per bin.

    Parameters
    ----------
    histograms : sequence of Histogram
        Each item's counts (see :func:`zabgrab.services.binning.histograms`).
    bins : Bins
        The bins, for their labels.

    Returns
    -------
    list of Line
        The lines, without line breaks, and how to style each.
    """
    lines: list[Line] = []
    for number, histogram in enumerate(histograms):
        if number:
            lines.append(("", ""))
        total = histogram.total
        heading = (
            f"{label(histogram.series)}: {total:,} value{'' if total == 1 else 's'}"
        )
        lines.append((heading, "bold"))
        rows = [
            [name, f"{count:,}", f"{count / total:.1%}" if total else ""]
            for name, count in zip(bins.labels, histogram.counts, strict=True)
        ]
        lines += _aligned(["Bin", "Count", "Share"], rows, right=[False, True, True])
    return lines


def _aligned(
    headings: list[str], rows: list[list[str]], *, right: list[bool]
) -> list[Line]:
    """Lay out cells in aligned columns, under a bold heading line."""
    widths = [
        max(len(cell) for cell in column)
        for column in zip(headings, *rows, strict=True)
    ]

    def join(cells: list[str]) -> str:
        padded = [
            cell.rjust(width) if align else cell.ljust(width)
            for cell, width, align in zip(cells, widths, right, strict=True)
        ]
        return GAP.join(padded).rstrip()

    return [(join(headings), "bold"), *((join(row), "") for row in rows)]
