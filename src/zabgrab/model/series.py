"""Fetched data.

Attributes
----------
FORMAT_VERSION : int
    The version of the layout of ``.h5`` and ``.mat`` files (and of the metadata in
    every format). Increase it whenever the layout changes in a way that older
    versions of zabgrab, or ``zabgrab_load.m``, would misread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Resolution
from .server import Server
from .zabbix import Item

FORMAT_VERSION = 1


@dataclass
class Series:
    """The data fetched for one item, as named columns of equal length.

    Attributes
    ----------
    server : Server
        The server the data came from.
    item : Item
        The item, as it was when fetched.
    resolution : Resolution
        Raw history or hourly trends.
    columns : dict of str to list
        The data. The first column is always ``time`` (POSIX seconds). Raw data then
        has ``value`` (plus ``log_time``, ``source``, ``severity`` and ``event_id``
        for log items); hourly trends have the selected statistics (``min``,
        ``avg``, ``max``, ``count``).
    requests : tuple of str
        Labels of the requests (the template, or ``__custom`` for items given on
        the command line) that matched the item.
    """

    server: Server
    item: Item
    resolution: Resolution
    columns: dict[str, list[Any]]
    requests: tuple[str, ...] = ()

    @property
    def points(self) -> int:
        """The number of data points."""
        return len(self.columns["time"])


@dataclass(frozen=True)
class Skipped:
    """An item that wasn't fetched, and why.

    Attributes
    ----------
    server : Server
        The item's server.
    item : Item
        The item.
    reason : str
        Why it wasn't fetched, as a sentence fragment for messages.
    """

    server: Server
    item: Item
    reason: str


@dataclass
class FetchResult:
    """Everything a fetch produced.

    Attributes
    ----------
    series : list of Series
        The data, one series per item.
    skipped : list of Skipped
        Items that weren't fetched because they can't be (e.g. log items as
        hourly trends).
    failed : list of Skipped
        Items whose download failed, with the error.
    """

    series: list[Series] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    failed: list[Skipped] = field(default_factory=list)

    @property
    def points(self) -> int:
        """The total number of data points."""
        return sum(series.points for series in self.series)
