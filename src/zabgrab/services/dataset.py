"""The merged view of a file's fetch groups.

Groups only exist because of how appending works, so reading operations see one
dataset: each item's data joined across groups, sorted by time, with duplicate
timestamps resolved in favour of the later group.
"""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..errors import ZabgrabError
from ..model.choice import Choice
from ..model.item import ItemSpec
from ..model.series import Series
from .ports import UI

#: Gaps between fetched ranges up to this long (seconds) still count as covered,
#: since each update starts just after the previous fetch's end.
COVERAGE_TOLERANCE = 1.0


class Group(Protocol):
    """A fetch group read from a file.

    See :class:`zabgrab.adapters.formats.hdf5.FetchGroup`, which this describes
    without the services depending on the adapter.
    """

    name: str
    metadata: dict[str, Any]
    t_from: float
    t_to: float
    series: list[Series]


@dataclass
class Dataset:
    """Every series in a file (or selection of its groups), merged.

    Attributes
    ----------
    series : list of Series
        One series per item (and resolution), joined across groups.
    coverage : list of (float, float)
        The fetched time ranges, merged, in POSIX seconds.
    groups : list of str
        The names of the groups merged.
    last_metadata : dict
        The metadata of the last group (the most recent query).
    """

    series: list[Series]
    coverage: list[tuple[float, float]]
    groups: list[str]
    last_metadata: dict[str, Any] = field(default_factory=dict)


def merge(groups: Sequence[Group], ui: UI) -> Dataset:
    """Merge fetch groups into one dataset.

    Series are matched by server, item, resolution and columns. Each is joined
    across groups and sorted by time; points with the same time keep the later
    group's value. The item's details come from the latest group.

    Parameters
    ----------
    groups : sequence of Group
        The groups, in the order they were written.
    ui : UI
        Warns if an item's units changed between groups.

    Returns
    -------
    Dataset
        The merged data.

    Raises
    ------
    ZabgrabError
        If there are no groups.
    """
    if not groups:
        raise ZabgrabError("The file has no data.")

    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for group in groups:
        for series in group.series:
            key = _key(series)
            entry = merged.get(key)
            if entry is None:
                entry = merged[key] = {"series": series, "rows": {}, "requests": []}
            elif entry["series"].item.units != series.item.units:
                ui.warning(
                    f"The units of {series.item.host}: {series.item.name} changed from "
                    f"{entry['series'].item.units!r} to {series.item.units!r} in "
                    f"group {group.name!r}."
                )
            entry["series"] = series  # the latest details win
            entry["requests"] += [
                r for r in series.requests if r not in entry["requests"]
            ]
            names = list(series.columns)
            # Keyed by time, so a later group's point replaces an earlier one's.
            for row in zip(*series.columns.values(), strict=True):
                entry["rows"][row[0]] = row
            entry["names"] = names

    result = []
    for entry in merged.values():
        latest: Series = entry["series"]
        rows = [entry["rows"][time] for time in sorted(entry["rows"])]
        columns = {
            name: [row[index] for row in rows]
            for index, name in enumerate(entry["names"])
        }
        result.append(
            Series(
                latest.server,
                latest.item,
                latest.resolution,
                columns,
                tuple(entry["requests"]),
            )
        )

    return Dataset(
        series=result,
        coverage=_union([(g.t_from, g.t_to) for g in groups]),
        groups=[g.name for g in groups],
        last_metadata=groups[-1].metadata,
    )


def _key(series: Series) -> tuple[Any, ...]:
    """Identify a series across groups: the same item, resolution and columns."""
    return (
        series.server.url,
        series.item.itemid,
        series.resolution,
        tuple(series.columns),
    )


def repeated_points(datasets: Sequence[Dataset]) -> list[int]:
    """Count the points each dataset shares with any of the others.

    A point is shared if another dataset has the same series (item, resolution and
    columns) with a point at the same time. Use it with each group read on its own,
    to see how much of a file is held twice.

    Parameters
    ----------
    datasets : sequence of Dataset
        Typically one per fetch group.

    Returns
    -------
    list of int
        For each dataset, how many of its points another also holds.
    """
    points = [
        {
            (_key(series), time)
            for series in data.series
            for time in series.columns["time"]
        }
        for data in datasets
    ]
    holders = Counter(point for held in points for point in held)
    return [sum(holders[point] > 1 for point in held) for held in points]


@dataclass(frozen=True)
class Overlap:
    """Part of a new fetch's range that an existing group already covers.

    Attributes
    ----------
    group : str
        The existing group's name.
    start, end : float
        The time range covered by both, in POSIX seconds.
    items : int
        How many of the new fetch's items the group also holds.
    """

    group: str
    start: float
    end: float
    items: int


class GroupRange(Protocol):
    """A fetch group's metadata and range, without its data.

    See :class:`zabgrab.adapters.formats.hdf5.GroupRange`.
    """

    name: str
    metadata: dict[str, Any]
    t_from: float
    t_to: float


def overlaps(
    groups: Sequence[GroupRange], series: Sequence[Series], t_from: float, t_to: float
) -> list[Overlap]:
    """Find the existing groups that already hold some of a new fetch's data.

    A group overlaps if it holds any of the same items at the same resolution, and
    its range and the new fetch's overlap for longer than an instant (an update
    starting where the last fetch ended doesn't count).

    Parameters
    ----------
    groups : sequence of GroupRange
        The file's existing groups.
    series : sequence of Series
        The newly fetched series.
    t_from, t_to : float
        The new fetch's range, in POSIX seconds.

    Returns
    -------
    list of Overlap
        The overlapping groups, in the order they were written.
    """
    fetched = {(s.server.url, s.item.itemid, str(s.resolution)) for s in series}
    found = []
    for group in groups:
        resolution = group.metadata.get("query", {}).get("resolution", "raw")
        held = {
            (entry["server"], entry["itemid"], resolution)
            for entry in group.metadata.get("items", [])
        }
        shared = len(fetched & held)
        start, end = max(t_from, group.t_from), min(t_to, group.t_to)
        if shared and start < end:
            found.append(Overlap(group.name, start, end, shared))
    return found


def select_groups(groups: Sequence[Group], names: Sequence[str]) -> list[Group]:
    """Pick groups by name.

    Parameters
    ----------
    groups : sequence of Group
        Every group in the file.
    names : sequence of str
        The names wanted (all groups if empty).

    Returns
    -------
    list of Group
        The groups, in the order named.

    Raises
    ------
    ZabgrabError
        If a name doesn't match a group.
    """
    if not names:
        return list(groups)
    known = {group.name: group for group in groups}
    missing = [name for name in names if name not in known]
    if missing:
        raise ZabgrabError(
            f"No group called {', '.join(map(repr, missing))}.",
            hint=f"The groups are: {', '.join(known)}.",
        )
    return [known[name] for name in names]


def filter_dataset(
    dataset: Dataset,
    *,
    items: Sequence[ItemSpec] = (),
    patterns: Sequence[str] = (),
    start: datetime | None = None,
    end: datetime | None = None,
) -> Dataset:
    """Limit a dataset to some items and a time range.

    Parameters
    ----------
    dataset : Dataset
        The data.
    items : sequence of ItemSpec, optional
        Item specs (``--item``; see :func:`spec_matches`).
    patterns : sequence of str, optional
        Glob patterns (``--match``; see :func:`matches`). A series is kept if any
        item spec or pattern matches it, or if there are none of either.
    start, end : datetime, optional
        The time range to keep (unbounded if not given).

    Returns
    -------
    Dataset
        The selected data, with its coverage cut to the time range.
    """
    first = start.timestamp() if start else float("-inf")
    last = end.timestamp() if end else float("inf")
    series = []
    for one in select(dataset.series, items, patterns):
        keep = [i for i, t in enumerate(one.columns["time"]) if first <= t <= last]
        columns = {
            name: [values[i] for i in keep] for name, values in one.columns.items()
        }
        series.append(
            Series(one.server, one.item, one.resolution, columns, one.requests)
        )
    coverage = [
        (max(a, first), min(b, last))
        for a, b in dataset.coverage
        if a <= last and b >= first
    ]
    return Dataset(series, coverage, dataset.groups, dataset.last_metadata)


def select(
    series: Sequence[Series], items: Sequence[ItemSpec], patterns: Sequence[str]
) -> list[Series]:
    """Select the series that any item spec or pattern matches.

    Parameters
    ----------
    series : sequence of Series
        The series.
    items : sequence of ItemSpec
        Item specs (see :func:`spec_matches`).
    patterns : sequence of str
        Glob patterns (see :func:`matches`).

    Returns
    -------
    list of Series
        The matching series, in order; all of them if there are no specs or
        patterns.
    """
    if not items and not patterns:
        return list(series)
    return [
        one
        for one in series
        if any(spec_matches(one, spec) for spec in items)
        or any(matches(one, pattern) for pattern in patterns)
    ]


def spec_matches(series: Series, spec: ItemSpec) -> bool:
    """Check whether an item spec selects a series' item, as the Zabbix API would.

    Patterns match like the API's searches (``item.get`` and ``host.get`` with
    ``searchWildcardsEnabled``): the whole value, ignoring case, with ``*`` matching
    any text and every other character literal. The host matches its technical or
    visible name. A tag (``name`` or ``name:value``) must be among the item's stored
    tags exactly, as the API's tag filters compare them. Every field given must
    match.

    Parameters
    ----------
    series : Series
        The series.
    spec : ItemSpec
        The item spec.

    Returns
    -------
    bool
        Whether it matches.

    Examples
    --------
    >>> from zabgrab.model.config import Resolution, ServerConfig
    >>> from zabgrab.model.server import Server
    >>> from zabgrab.model.zabbix import Item, ValueType
    >>> item = Item(1, 1, "pc2", "Particle counter 2", "0.3 µm particles", "p[0.3]",
    ...             ValueType.FLOAT)
    >>> one = Series(Server("lab", ServerConfig(url="https://z.example.org")), item,
    ...              Resolution.RAW, {"time": []})
    >>> spec_matches(one, ItemSpec(name="0.3*PARTICLES", host="*counter*"))
    True
    >>> spec_matches(one, ItemSpec(name="0.3"))
    False
    """
    item = series.item
    if spec.itemid is not None:
        return item.itemid == spec.itemid
    host = spec.host
    hosts = host.choices if isinstance(host, Choice) else [] if host is None else [host]
    if hosts and not any(
        _like(item.host, host) or _like(item.host_name, host) for host in hosts
    ):
        return False
    if spec.name is not None and not _like(item.name, spec.name):
        return False
    if spec.key is not None and not _like(item.key, spec.key):
        return False
    if spec.tag is not None:
        name, colon, value = spec.tag.partition(":")
        return any(
            tag == name and (not colon or tag_value == value)
            for tag, tag_value in item.tags
        )
    return True


def _like(text: str, pattern: str) -> bool:
    """Match like a Zabbix API search with ``searchWildcardsEnabled``.

    Zabbix (``zbx_db_search``) uppercases the pattern, escapes SQL's ``%`` and
    ``_``, turns ``*`` into ``%`` and compares it with ``UPPER(field) LIKE``, so
    the pattern must match the whole value, ignoring case.
    """
    regex = ".*".join(re.escape(part) for part in pattern.upper().split("*"))
    return re.fullmatch(regex, text.upper(), re.DOTALL) is not None


def matches(series: Series, pattern: str) -> bool:
    """Check whether a series' item ID, name, key or ``host: name`` matches a pattern.

    Parameters
    ----------
    series : Series
        The series.
    pattern : str
        A pattern in Python's :mod:`fnmatch` syntax (``*``, ``?``, ``[seq]`` and
        ``[!seq]``), which must match the whole of one of them, ignoring case.

    Returns
    -------
    bool
        Whether it matches.
    """
    item = series.item
    candidates = (str(item.itemid), item.name, item.key, f"{item.host}: {item.name}")
    return any(fnmatch.fnmatchcase(c.lower(), pattern.lower()) for c in candidates)


def _union(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping (or nearly touching) time ranges, sorted."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + COVERAGE_TOLERANCE:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
