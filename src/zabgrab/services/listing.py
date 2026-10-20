"""Listing data points, filtered, sorted and sliced (``zabgrab show``).

A listing is made of sections. Sorted by time, there's one section holding every
item's points, interleaved. Sorted by value, there's one section per item, since
values of different items (often in different units) can't be ranked together.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import ZabgrabError
from ..model.series import Series

#: The hourly trend statistics, in the order they're shown.
STATISTICS = ("min", "avg", "max", "count")

#: What ``--sort`` accepts.
SORT_KEYS = ("time", "value", *STATISTICS)


@dataclass(frozen=True)
class ListingOptions:
    """How to filter, sort and slice a listing.

    Attributes
    ----------
    sort : str
        ``time``, ``value``, or a trend statistic (``min``, ``avg``, ``max`` or
        ``count``).
    descending : bool
        Largest (or latest) first.
    limit : int or None
        At most this many points (per section); ``None`` for all.
    offset : int
        Skip this many points (per section) first.
    grep : re.Pattern or None
        Only points whose value, as text, contains a match.
    above, below : float or None
        Only numeric points whose value is greater than ``above`` and less than
        ``below``.
    """

    sort: str = "time"
    descending: bool = False
    limit: int | None = None
    offset: int = 0
    grep: re.Pattern[str] | None = None
    above: float | None = None
    below: float | None = None


@dataclass(frozen=True)
class Row:
    """One point of a series.

    Attributes
    ----------
    series : Series
        The series.
    index : int
        The point's position in the series' columns.
    """

    series: Series
    index: int

    def get(self, column: str) -> Any:
        """Get the point's value in a column (``None`` if the series lacks it)."""
        values = self.series.columns.get(column)
        return None if values is None else values[self.index]


@dataclass(frozen=True)
class Section:
    """A part of a listing.

    Attributes
    ----------
    series : list of Series
        The items whose points are in the section, in the order given.
    rows : list of Row
        The points shown, in order.
    matched : int
        How many points matched the filters, before ``offset`` and ``limit``.
    """

    series: list[Series]
    rows: list[Row]
    matched: int


def value_column(series: Series, sort: str = "value") -> str:
    """Name the column holding a series' values, for sorting and filtering.

    Raw data has a ``value`` column. Hourly trends have statistics instead: the one
    named by ``sort``, else the average (or the first one fetched).

    Parameters
    ----------
    series : Series
        The series.
    sort : str, optional
        The ``--sort`` key.

    Returns
    -------
    str
        The column's name.

    Raises
    ------
    ZabgrabError
        If ``sort`` names a statistic the series doesn't have.
    """
    label = f"{series.item.host}: {series.item.name}"
    if "value" in series.columns:
        if sort in STATISTICS:
            raise ZabgrabError(
                f"{label} is raw data, which has no {sort} statistic.",
                hint="Sort raw data by value or time.",
            )
        return "value"
    stats = [name for name in STATISTICS if name in series.columns]
    if sort in STATISTICS:
        if sort not in stats:
            raise ZabgrabError(
                f"{label} has no {sort} statistic (it has {', '.join(stats)}).",
                hint=f"Sort by {' or '.join(stats)} instead.",
            )
        return sort
    return "avg" if "avg" in stats else stats[0]


def listing(series: Sequence[Series], options: ListingOptions) -> list[Section]:
    """List the points of some series, filtered, sorted and sliced.

    Parameters
    ----------
    series : sequence of Series
        The items' data.
    options : ListingOptions
        How to filter, sort and slice.

    Returns
    -------
    list of Section
        One section sorted by time, or one per item sorted by value or a
        statistic. Sections with no matching points are left out.

    Raises
    ------
    ZabgrabError
        If the sort key doesn't apply to a series (see :func:`value_column`).
    """
    found = [(one, matching(one, options)) for one in series]
    if options.sort == "time":
        entries = [
            (one.columns["time"][index], order, index)
            for order, (one, indices) in enumerate(found)
            for index in indices
        ]
        # Ties in time keep the items' order, then the points'.
        chosen = _take(entries, lambda entry: entry, options)
        shown = [one for one, indices in found if indices]
        rows = [Row(found[order][0], index) for _, order, index in chosen]
        return [Section(shown, rows, len(entries))] if entries else []

    sections = []
    for one, indices in found:
        if not indices:
            continue
        values = one.columns[value_column(one, options.sort)]
        times = one.columns["time"]

        def key(
            index: int, values: list[Any] = values, times: list[Any] = times
        ) -> tuple[Any, Any]:
            # Equal values are ordered by time.
            return (values[index], times[index])

        chosen = _take(indices, key, options)
        rows = [Row(one, index) for index in chosen]
        sections.append(Section([one], rows, len(indices)))
    return sections


def matching(series: Series, options: ListingOptions) -> list[int]:
    """Find the positions of a series' points that pass the filters.

    Parameters
    ----------
    series : Series
        The series.
    options : ListingOptions
        The filters (``grep``, ``above`` and ``below``), and the sort key, which
        picks the value compared for hourly trends (see :func:`value_column`).

    Returns
    -------
    list of int
        The positions, in time order.
    """
    values = series.columns[value_column(series, options.sort)]
    thresholds = options.above is not None or options.below is not None
    if thresholds and not series.item.value_type.numeric:
        # Text has no size to compare.
        return []

    def keep(value: Any) -> bool:
        if options.grep is not None and not options.grep.search(str(value)):
            return False
        if options.above is not None and not value > options.above:
            return False
        return options.below is None or value < options.below

    return [index for index, value in enumerate(values) if keep(value)]


def _take(
    entries: Sequence[Any], key: Callable[[Any], Any], options: ListingOptions
) -> list[Any]:
    """Sort entries and slice out the requested part.

    With a limit, only the points needed are kept while sorting, so the top few
    of a long series are found without sorting all of it.
    """
    if options.limit is None:
        ordered = sorted(entries, key=key, reverse=options.descending)
    else:
        pick = heapq.nlargest if options.descending else heapq.nsmallest
        ordered = pick(options.offset + options.limit, entries, key=key)
    return ordered[options.offset :]
