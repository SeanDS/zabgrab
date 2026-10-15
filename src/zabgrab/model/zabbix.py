"""Zabbix objects as seen by zabgrab.

Attributes
----------
TIME_UNITS : dict of str to int
    Seconds per Zabbix time suffix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800}


class ValueType(IntEnum):
    """Zabbix item value types (the ``value_type``/``history`` API values).

    Attributes
    ----------
    FLOAT, UNSIGNED
        Numbers, which have hourly trends and can be plotted.
    CHAR, LOG, TEXT
        Text. Log items also have a log time, source, severity and event ID.
    BINARY
        Binary data, which zabgrab doesn't fetch.
    """

    FLOAT = 0
    CHAR = 1
    LOG = 2
    UNSIGNED = 3
    TEXT = 4
    BINARY = 5

    @property
    def label(self) -> str:
        """Short human-readable name."""
        return _LABELS[self]

    @property
    def numeric(self) -> bool:
        """Whether values are numbers (and so have trends and can be plotted)."""
        return self in (ValueType.FLOAT, ValueType.UNSIGNED)


_LABELS = {
    ValueType.FLOAT: "float",
    ValueType.CHAR: "char",
    ValueType.LOG: "log",
    ValueType.UNSIGNED: "unsigned",
    ValueType.TEXT: "text",
    ValueType.BINARY: "binary",
}


@dataclass(frozen=True)
class Host:
    """A monitored host.

    Attributes
    ----------
    hostid : int
        The host's ID.
    host : str
        The technical name.
    name : str
        The visible name.
    enabled : bool
        Whether it's monitored.
    groups : tuple of str
        The names of its host groups.
    """

    hostid: int
    host: str
    name: str
    enabled: bool = True
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """An item on a host.

    Attributes
    ----------
    itemid, hostid : int
        The item's and its host's IDs.
    host, host_name : str
        The host's technical and visible names.
    name, key : str
        The item's name and key.
    value_type : ValueType
        The type of its values.
    units : str
        The units of its values (may be empty).
    enabled : bool
        Whether it's monitored.
    last_value : str or None
        The latest value, if any.
    last_clock : datetime or None
        When the latest value was collected.
    history, trends : int or None
        How long raw values and hourly trends are kept, in seconds (0 means they
        aren't), or ``None`` if not known.
    tags : tuple of (str, str)
        The item's tags, as (name, value) pairs (the value may be empty). A name
        can appear more than once.
    """

    itemid: int
    hostid: int
    host: str
    host_name: str
    name: str
    key: str
    value_type: ValueType
    units: str = ""
    enabled: bool = True
    last_value: str | None = None
    last_clock: datetime | None = None
    history: int | None = None
    trends: int | None = None
    tags: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class HistoryRow:
    """One raw value of an item.

    Attributes
    ----------
    time : float
        When it was collected, in POSIX seconds including the nanosecond part.
    value : str
        The value, as Zabbix returns it.
    log_time, source, severity, event_id
        The log entry's own time, source, severity and event ID (log items only).
    """

    time: float
    value: str
    log_time: int | None = None
    source: str = ""
    severity: int = 0
    event_id: int = 0


@dataclass(frozen=True)
class TrendRow:
    """Hourly statistics of a numeric item.

    Attributes
    ----------
    time : int
        The start of the hour, in POSIX seconds.
    count : int
        How many values the hour had.
    min, avg, max : str
        The hour's minimum, average and maximum, as Zabbix returns them.
    """

    time: int
    count: int
    min: str
    avg: str
    max: str


def parse_period(text: str) -> int | None:
    """Parse a Zabbix storage period, such as ``31d`` or ``3600``.

    Parameters
    ----------
    text : str
        The period, as ``item.get`` returns it: whole seconds, optionally with a
        time suffix (``s``, ``m``, ``h``, ``d`` or ``w``), or a user macro.

    Returns
    -------
    int or None
        The period in seconds (0 means nothing is kept), or ``None`` if it isn't
        known (e.g. a macro, which the API doesn't resolve).

    Examples
    --------
    >>> parse_period("2w"), parse_period("3600"), parse_period("{$HISTORY}")
    (1209600, 3600, None)
    """
    text = text.strip()
    multiplier = TIME_UNITS.get(text[-1:], 1)
    number = text[:-1] if text[-1:] in TIME_UNITS else text
    return int(number) * multiplier if number.isdigit() else None
