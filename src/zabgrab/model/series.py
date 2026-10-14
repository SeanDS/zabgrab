"""Fetched data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Resolution
from .server import Server
from .zabbix import Item


@dataclass
class Series:
    """The data fetched for one item, as named columns of equal length.

    The first column is always ``time`` (POSIX seconds). Raw data then has
    ``value`` (plus ``log_time``, ``source``, ``severity`` and ``event_id`` for log
    items); hourly trends have the selected statistics (``min``, ``avg``, ``max``,
    ``count``).
    """

    server: Server
    item: Item
    resolution: Resolution
    columns: dict[str, list[Any]]
    requests: tuple[str, ...] = ()
    """Labels of the requests (templates or ``__custom``) that matched the item."""

    @property
    def points(self) -> int:
        return len(self.columns["time"])


@dataclass(frozen=True)
class Skipped:
    """An item that wasn't fetched, and why."""

    server: Server
    item: Item
    reason: str


@dataclass
class FetchResult:
    series: list[Series] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    failed: list[Skipped] = field(default_factory=list)
    """Items whose download failed, with the error."""

    @property
    def points(self) -> int:
        return sum(series.points for series in self.series)
