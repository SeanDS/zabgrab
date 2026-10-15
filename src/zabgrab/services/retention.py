"""Warning when data is requested from before Zabbix deletes it.

Zabbix keeps each item's raw values for its *history storage period* and its hourly
trends for its *trend storage period*, then the housekeeper deletes them. A fetch
reaching back further can't return the older data, which is easy to mistake for a
gap in monitoring, so zabgrab warns about it (and suggests hourly trends, which are
usually kept much longer, for raw requests).

The periods come from the item settings. A server-wide housekeeping override can
change them without the API showing it, so the warnings say data *may* be gone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ..model.config import Resolution
from ..model.zabbix import TIME_UNITS, Item
from .fetch import Job, skip_reason
from .ports import UI


def format_duration(seconds: float) -> str:
    """Format a duration compactly, in its largest whole unit up to days.

    Parameters
    ----------
    seconds : float
        The duration; negative durations show as ``0s``.

    Returns
    -------
    str
        The duration, rounded down.

    Examples
    --------
    >>> format_duration(150 * 60), format_duration(14 * 86_400)
    ('2h', '14d')
    """
    seconds = max(0, int(seconds))
    for unit in ("d", "h", "m"):
        if seconds >= TIME_UNITS[unit]:
            return f"{seconds // TIME_UNITS[unit]}{unit}"
    return f"{seconds}s"


def warn_beyond_retention(
    jobs: Sequence[Job],
    *,
    start: datetime,
    now: datetime,
    resolution: Resolution,
    ui: UI,
) -> None:
    """Warn about items whose data may be deleted from part of the requested range.

    Items sharing a storage period get one warning between them.

    Parameters
    ----------
    jobs : sequence of Job
        The items to fetch.
    start : datetime
        The start of the requested range.
    now : datetime
        The current time, which storage periods count back from.
    resolution : Resolution
        Raw values (checked against the history period) or hourly trends (checked
        against the trend period).
    ui : UI
        For the warnings.
    """
    raw = resolution is Resolution.RAW
    age = (now - start).total_seconds()
    # Items by the period they keep data for, in order of appearance.
    short: dict[int, list[Item]] = {}
    for job in jobs:
        if skip_reason(job.item, resolution) is not None:
            continue
        kept = job.item.history if raw else job.item.trends
        if kept is not None and kept < age:
            short.setdefault(kept, []).append(job.item)

    for kept, items in short.items():
        who = _who(items)
        data = "raw values" if raw else "hourly trends"
        if kept == 0:
            message = f"{who} {_verb(items)} no {data}, so there are none to fetch."
        else:
            message = (
                f"{who} {_verb(items)} {data} for {format_duration(kept)}, but the "
                f"requested range starts {format_duration(age)} ago: older {data} "
                "may have been deleted."
            )
        if raw and all(_trends_reach(item) for item in items):
            message += " Hourly trends go back further: pass --resolution 1h."
        ui.warning(message)


def _trends_reach(item: Item) -> bool:
    """Tell whether an item's trends may go back further than its raw values."""
    if not item.value_type.numeric or item.trends == 0:
        return False
    return item.trends is None or item.trends > (item.history or 0)


def _who(items: Sequence[Item]) -> str:
    """Name the items a warning is about, listing at most three."""
    names = [f"{item.itemid} ({item.host}: {item.name})" for item in items]
    if len(names) == 1:
        return f"Item {names[0]}"
    shown = ", ".join(names[:3])
    more = len(names) - 3
    return f"Items {shown}" + (f" and {more} more" if more > 0 else "")


def _verb(items: Sequence[Item]) -> str:
    """Conjugate "keep" for the items."""
    return "keeps" if len(items) == 1 else "keep"
