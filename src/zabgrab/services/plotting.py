"""What to plot: numeric series grouped by units, split where nothing was fetched.

This module decides the content of a plot; :mod:`zabgrab.ui.plot` draws it with
matplotlib. Keeping the two apart means the rules (which items can be plotted, where
lines break) are tested without matplotlib.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..errors import ZabgrabError
from ..model.config import Resolution
from ..model.series import Series
from ..model.server import Server
from .ports import UI


@dataclass
class Segment:
    """A run of points drawn as one connected line.

    Attributes
    ----------
    times : list of float
        POSIX seconds.
    values : list of float
        The line's values: raw values, or the hourly average.
    low, high : list of float or None
        The hourly minimum and maximum, drawn as a band around the line (trends
        only, when both were fetched).
    """

    times: list[float]
    values: list[float]
    low: list[float] | None = None
    high: list[float] | None = None


@dataclass
class Line:
    """One series on an axis.

    Attributes
    ----------
    label : str
        The legend entry, ``host: name [units]``.
    segments : list of Segment
        The connected runs of points; lines break between them.
    """

    label: str
    segments: list[Segment] = field(default_factory=list)


@dataclass
class Axis:
    """A vertical axis, shared by the series with the same units.

    Attributes
    ----------
    units : str
        The units of every line on the axis (empty for none).
    lines : list of Line
        The series drawn on this axis.
    """

    units: str
    lines: list[Line] = field(default_factory=list)


@dataclass
class PlotSpec:
    """Everything to draw.

    Attributes
    ----------
    axes : list of Axis
        One axis per distinct unit, in the order the units first appear.
    title : str
        The figure title (may be empty).
    log_y : bool
        Whether the vertical axes use a log scale.
    """

    axes: list[Axis]
    title: str = ""
    log_y: bool = False


def plot_spec(
    series: Sequence[Series],
    coverage: Sequence[tuple[float, float]],
    ui: UI,
    *,
    title: str = "",
    log_y: bool = False,
) -> PlotSpec:
    """Work out what to plot.

    Parameters
    ----------
    series : sequence of Series
        The data, e.g. a merged dataset's series.
    coverage : sequence of (float, float)
        The time ranges that were fetched, sorted and not overlapping (POSIX
        seconds). Lines break between ranges, so gaps nobody fetched aren't drawn
        as if the data were continuous, while sparse data inside a fetched range
        is still joined up.
    ui : UI
        Warns about text items that are left out.
    title : str, optional
        The figure title.
    log_y : bool, optional
        Use log scales for the vertical axes. Lines with values of zero or less,
        which can't be shown on them, are warned about.

    Returns
    -------
    PlotSpec
        One axis per unit, each with a line per numeric series.

    Raises
    ------
    ZabgrabError
        If there's nothing numeric to plot (no series, or only text items).
    """
    numeric = [one for one in series if one.item.value_type.numeric]
    text = [one for one in series if not one.item.value_type.numeric]
    if not numeric:
        if not text:
            raise ZabgrabError(
                "Nothing to plot.", hint="Check --item, --from and --to."
            )
        if len(text) == 1:
            raise ZabgrabError(
                f"{_sentence(_name(text[0]))} is a {_kind(text[0])}; {_TEXT_RULE}."
            )
        raise ZabgrabError(
            f"{_sentence(_TEXT_RULE)}.",
            details=[f"{_name(one)} is a {_kind(one)}" for one in text],
        )
    for one in text:
        ui.warning(f"Not plotting {_name(one)}, a {_kind(one)}; {_TEXT_RULE}.")

    # Name the server in labels only when there's more than one.
    servers = len({one.server.url for one in numeric}) > 1
    axes: dict[str, Axis] = {}
    for one in numeric:
        units = one.item.units
        axis = axes.setdefault(units, Axis(units))
        line = Line(
            _label(one, one.server if servers else None), _segments(one, coverage)
        )
        axis.lines.append(line)
        if log_y and _has_non_positive(line):
            ui.warning(
                f"{line.label} has values of zero or less, which a log scale can't "
                "show; they're left out."
            )
    return PlotSpec(list(axes.values()), title, log_y)


def _has_non_positive(line: Line) -> bool:
    """Whether any of a line's values (or the bottom of its band) is zero or less."""
    return any(
        value <= 0
        for segment in line.segments
        for value in [*segment.values, *(segment.low or [])]
    )


_TEXT_RULE = "text items can't be plotted"


def _sentence(text: str) -> str:
    """``text`` with its first letter capitalised (and the rest as it was)."""
    return text[:1].upper() + text[1:]


def _name(series: Series) -> str:
    item = series.item
    return f"item {item.itemid} ({item.host}: {item.name})"


def _kind(series: Series) -> str:
    return f"{series.item.value_type.label} item"


def _label(series: Series, server: Server | None) -> str:
    item = series.item
    label = f"{item.host}: {item.name}"
    if item.units:
        label += f" [{item.units}]"
    notes = []
    if series.resolution is Resolution.HOURLY:
        notes.append("hourly")
    if server is not None:
        notes.append(f"server: {server.label}")
    if notes:
        label += f" ({', '.join(notes)})"
    return label


def _segments(series: Series, coverage: Sequence[tuple[float, float]]) -> list[Segment]:
    """Split a series into runs of points within the same fetched range."""
    columns = series.columns
    if "value" in columns:
        values, low, high = columns["value"], None, None
    else:
        # Hourly trends: the average as the line (else whichever statistic was
        # fetched), with a min/max band when both are there.
        line = next(name for name in ("avg", "min", "max", "count") if name in columns)
        values = columns[line]
        both = "min" in columns and "max" in columns
        low = columns["min"] if both else None
        high = columns["max"] if both else None

    # Group the points into runs within the same fetched range. A point outside
    # every range (which can't normally happen) gets a run of its own.
    starts = [start for start, _ in coverage]
    # An hourly trend's time is the start of its hour, which can be before the
    # fetch started; it belongs to the range that the hour overlaps.
    span = 3600 - 1 if series.resolution is Resolution.HOURLY else 0
    runs: list[list[int]] = []
    previous: int | None = None
    for index, time in enumerate(columns["time"]):
        where = bisect.bisect_right(starts, time + span) - 1
        if where < 0 or time > coverage[where][1]:
            where = -1 - index
        if where != previous:
            runs.append([])
            previous = where
        runs[-1].append(index)

    def pick(column: list[float] | None, run: list[int]) -> list[float] | None:
        return None if column is None else [float(column[i]) for i in run]

    return [
        Segment(
            [columns["time"][i] for i in run],
            [float(values[i]) for i in run],
            pick(low, run),
            pick(high, run),
        )
        for run in runs
    ]
