"""Zabbix objects as seen by zabgrab."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class ValueType(IntEnum):
    """Zabbix item value types (the ``value_type`` / ``history`` API values)."""

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
    """A monitored host."""

    hostid: int
    host: str
    """Technical name."""
    name: str
    """Visible name."""
    enabled: bool = True
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """An item on a host."""

    itemid: int
    hostid: int
    host: str
    """Technical name of the host."""
    host_name: str
    """Visible name of the host."""
    name: str
    key: str
    value_type: ValueType
    units: str = ""
    enabled: bool = True
    last_value: str | None = None
    last_clock: datetime | None = None


@dataclass(frozen=True)
class HistoryRow:
    """One raw value of an item."""

    time: float
    """POSIX seconds, including the nanosecond part."""
    value: str
    # Log items only.
    log_time: int | None = None
    source: str = ""
    severity: int = 0
    event_id: int = 0


@dataclass(frozen=True)
class TrendRow:
    """Hourly statistics of a numeric item."""

    time: int
    """Start of the hour, POSIX seconds."""
    count: int
    min: str
    avg: str
    max: str
