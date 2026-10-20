"""What to plot: numeric series grouped by units, split where nothing was fetched.

This module decides the content of a plot; :mod:`zabgrab.ui.plot` draws it with
matplotlib. Keeping the two apart means the rules (which items can be plotted, where
lines break) are tested without matplotlib.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..errors import ZabgrabError
from ..model.config import Resolution
from ..model.series import Series
from ..model.server import Server
from .binning import Bins, histograms
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


@dataclass(frozen=True)
class HLine:
    """A horizontal reference line asked for with ``--hline``.

    Attributes
    ----------
    label : str
        The legend entry: the label given, else the value as given.
    value : float
        Where the line goes, in the data's units.
    units : str or None
        Draw it only on the axes with these units (``None`` for every axis).
    """

    label: str
    value: float
    units: str | None = None

    @classmethod
    def parse(cls, text: str) -> HLine:
        """Read an ``--hline``: ``[LABEL=]VALUE [UNITS]``.

        The label is everything before the last ``=``, so it can contain ``=``
        itself. Without one, the value (and units) as given label the line.

        Parameters
        ----------
        text : str
            The option's value, e.g. ``Max=500``, ``Max=500 #/m3`` or ``500``.

        Returns
        -------
        HLine
            The line.

        Raises
        ------
        ValueError
            If there's no value, or it isn't a finite number.

        Examples
        --------
        >>> HLine.parse("Max value=500")
        HLine(label='Max value', value=500.0, units=None)
        >>> HLine.parse("Max temp=26.5 °C")
        HLine(label='Max temp', value=26.5, units='°C')
        >>> HLine.parse("1e3")
        HLine(label='1e3', value=1000.0, units=None)
        """
        label, separator, rest = text.rpartition("=")
        if not separator:
            label, rest = "", text
        number, _, units = rest.strip().partition(" ")
        if not number:
            raise ValueError(
                f"{text!r} has no value; give e.g. 'Max=500', 'Max=500 #/m3' or '500'"
            )
        try:
            value = float(number)
        except ValueError:
            raise ValueError(f"{number!r} isn't a number") from None
        if not math.isfinite(value):
            raise ValueError(f"{number!r} isn't a finite number")
        return cls(label.strip() or rest.strip(), value, units.strip() or None)


@dataclass(frozen=True)
class Reference:
    """A reference line to draw on an axis.

    Attributes
    ----------
    label : str
        The legend entry.
    at : float
        Where it goes: a value for lines over time, or a position along the bins
        for histograms (see :meth:`zabgrab.services.binning.Bins.position`).
    """

    label: str
    at: float


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
    references: list[Reference] = field(default_factory=list)


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
    hlines: Sequence[HLine] = (),
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
    hlines : sequence of HLine, optional
        Reference lines, drawn on every axis or those with their units.

    Returns
    -------
    PlotSpec
        One axis per unit, each with a line per numeric series.

    Raises
    ------
    ZabgrabError
        If there's nothing numeric to plot (no series, or only text items).
    """
    numeric = _numeric(series, ui)

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
    for hline, targets in _assign(hlines, list(axes), ui):
        if log_y and hline.value <= 0:
            ui.warning(
                f"--hline {hline.label!r} is at {hline.value:g}, which a log scale "
                "can't show; it isn't drawn."
            )
            continue
        for target in targets:
            axes[target].references.append(Reference(hline.label, hline.value))
    return PlotSpec(list(axes.values()), title, log_y)


@dataclass
class Bars:
    """One series' counts in a histogram.

    Attributes
    ----------
    label : str
        The legend entry, ``host: name [units]``.
    counts : list of int
        How many values fall in each bin.
    """

    label: str
    counts: list[int]


@dataclass
class BarAxis:
    """A histogram's subplot, shared by the series with the same units.

    Attributes
    ----------
    units : str
        The units of the values counted (empty for none).
    bars : list of Bars
        Each series' counts, drawn side by side in each bin.
    """

    units: str
    bars: list[Bars] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)


@dataclass
class HistogramSpec:
    """A histogram to draw: counts of values in bins.

    Attributes
    ----------
    bins : list of str
        The bins' labels, e.g. ``< 10``.
    axes : list of BarAxis
        One subplot per distinct unit, in the order the units first appear.
    title : str
        The figure title (may be empty).
    log_y : bool
        Whether the counts use a log scale.
    """

    bins: list[str]
    axes: list[BarAxis]
    title: str = ""
    log_y: bool = False


def histogram_spec(
    series: Sequence[Series],
    bins: Bins,
    ui: UI,
    *,
    title: str = "",
    log_y: bool = False,
    hlines: Sequence[HLine] = (),
) -> HistogramSpec:
    """Work out a histogram of each numeric series' values.

    Parameters
    ----------
    series : sequence of Series
        The data.
    bins : Bins
        The bins (see :class:`zabgrab.services.binning.Bins`).
    ui : UI
        Warns about text items that are left out.
    title : str, optional
        The figure title.
    log_y : bool, optional
        Use log scales for the counts.
    hlines : sequence of HLine, optional
        Reference lines at values, drawn across the bins (see
        :meth:`~zabgrab.services.binning.Bins.position`) on every axis or those
        with their units. Lines inside an open-ended bin are warned about.

    Returns
    -------
    HistogramSpec
        One subplot per unit, each with a set of bars per numeric series.

    Raises
    ------
    ZabgrabError
        If there's nothing numeric to count (no series, or only text items).
    """
    numeric = _numeric(series, ui)
    servers = len({one.server.url for one in numeric}) > 1
    axes: dict[str, BarAxis] = {}
    for histogram in histograms(numeric, bins):
        one = histogram.series
        axis = axes.setdefault(one.item.units, BarAxis(one.item.units))
        label = _label(one, one.server if servers else None)
        axis.bars.append(Bars(label, histogram.counts))
    for line, units in _assign(hlines, list(axes), ui):
        at, exact = bins.position(line.value)
        if not exact:
            ui.warning(
                f"--hline {line.label!r} ({line.value:g}) is inside the open-ended "
                f"bin {bins.labels[bins.index(line.value)]!r}, so it's drawn across "
                f"the bin's middle; add {line.value:g} to --bin to put it on an edge."
            )
        for unit in units:
            axes[unit].references.append(Reference(line.label, at))
    return HistogramSpec(bins.labels, list(axes.values()), title, log_y)


def _assign(
    hlines: Sequence[HLine], units: list[str], ui: UI
) -> list[tuple[HLine, list[str]]]:
    """Work out which axes (by units) each reference line goes on."""
    assigned = []
    for line in hlines:
        targets = units if line.units is None else [u for u in units if u == line.units]
        if not targets:
            ui.warning(
                f"Nothing plotted has units {line.units!r}, so --hline {line.label!r} "
                "isn't drawn."
            )
            continue
        assigned.append((line, targets))
    return assigned


def _numeric(series: Sequence[Series], ui: UI) -> list[Series]:
    """Pick the numeric series, warning about text ones (or failing if all are)."""
    numeric = [one for one in series if one.item.value_type.numeric]
    text = [one for one in series if not one.item.value_type.numeric]
    if not numeric:
        if not text:
            raise ZabgrabError(
                "Nothing to plot.",
                hint="Check --item, --match, --from and --to.",
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
    return numeric


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
