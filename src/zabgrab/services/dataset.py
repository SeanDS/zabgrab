"""The merged view of a file's fetch groups.

Groups only exist because of how appending works, so reading operations see one
dataset: each item's data joined across groups, sorted by time, with duplicate
timestamps resolved in favour of the later group.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..errors import ZabgrabError
from ..model.series import Series
from .ports import UI

#: Gaps between fetched ranges up to this long (seconds) still count as covered,
#: since each update starts just after the previous fetch's end.
COVERAGE_TOLERANCE = 1.0


class Group(Protocol):
    """A fetch group read from a file."""

    name: str
    metadata: dict[str, Any]
    t_from: float
    t_to: float
    series: list[Series]


@dataclass
class Dataset:
    """Every series in a file (or selection of its groups), merged."""

    series: list[Series]
    coverage: list[tuple[float, float]]
    """The fetched time ranges, merged, in POSIX seconds."""
    groups: list[str]
    last_metadata: dict[str, Any] = field(default_factory=dict)
    """The metadata of the last group (the most recent query)."""


def merge(groups: Sequence[Group], ui: UI) -> Dataset:
    """Merge groups' series by (server, item, resolution, columns)."""
    if not groups:
        raise ZabgrabError("The file has no data.")

    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for group in groups:
        for series in group.series:
            key = (
                series.server.url,
                series.item.itemid,
                series.resolution,
                tuple(series.columns),
            )
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


def select_groups(groups: Sequence[Group], names: Sequence[str]) -> list[Group]:
    """The groups with the given names (all of them if none are given)."""
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
    items: Sequence[str] = (),
    start: datetime | None = None,
    end: datetime | None = None,
) -> Dataset:
    """Only the series matching any of ``items`` (patterns, see :func:`matches`),
    and only their data between ``start`` and ``end``."""
    first = start.timestamp() if start else float("-inf")
    last = end.timestamp() if end else float("inf")
    series = []
    for one in dataset.series:
        if items and not any(matches(one, pattern) for pattern in items):
            continue
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


def matches(series: Series, pattern: str) -> bool:
    """Whether a series' item ID, name, key or "host: name" matches a glob pattern
    (case-insensitive)."""
    item = series.item
    candidates = (str(item.itemid), item.name, item.key, f"{item.host}: {item.name}")
    return any(fnmatch.fnmatchcase(c.lower(), pattern.lower()) for c in candidates)


def _union(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + COVERAGE_TOLERANCE:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
