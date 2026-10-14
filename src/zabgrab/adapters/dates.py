"""Parsing of user-supplied times, such as ``2026-10-14 09:00`` or ``24 hours ago``."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

import dateparser

EXAMPLES = "e.g. 'now', '24 hours ago', 'yesterday', '2026-10-14 09:00' or '14/10/2026'"


def timezone_for(name: str) -> tzinfo | None:
    """The timezone for a config ``timezone`` value (``None`` for local time)."""
    return None if name == "local" else ZoneInfo(name)


def parse_time(text: str, *, now: datetime, tz: tzinfo | None) -> datetime:
    """Parse a time typed by the user, returning an aware UTC datetime.

    ISO 8601 (``2026-10-14``, ``2026-10-14 09:00``, ``2026-10-14T09:00:00Z``) is
    parsed exactly. Anything else goes to dateparser, which understands relative
    times and natural language, with day-first numeric dates (``14/10/2026``).
    Times without an explicit offset are in ``tz`` (``None`` for local time), and
    relative times are relative to ``now``.

    Raises
    ------
    ValueError
        With a message suitable for showing to the user.
    """
    text = text.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = _natural(text, now=now, tz=tz)
    else:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz) if tz else parsed.astimezone()
    return parsed.astimezone(UTC)


def _natural(text: str, *, now: datetime, tz: tzinfo | None) -> datetime:
    local_now = now.astimezone(tz)
    settings = {
        "RELATIVE_BASE": local_now.replace(tzinfo=None),
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "past",
        "DATE_ORDER": "DMY",
    }
    if tz is not None:
        settings["TIMEZONE"] = str(tz)
    parsed = dateparser.parse(text, settings=settings) if text else None
    if parsed is None:
        raise ValueError(f"Can't understand the time {text!r}; use {EXAMPLES}.")
    assert isinstance(parsed, datetime)
    return parsed
