"""Parsing user-supplied times."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from zabgrab.adapters.dates import parse_time, timezone_for

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
LONDON = ZoneInfo("Europe/London")  # UTC+1 in October


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "tz", "expected"),
    [
        ("now", None, NOW),
        ("1 hour ago", LONDON, utc(2026, 10, 14, 11)),
        ("24 hours ago", ZoneInfo("UTC"), utc(2026, 10, 13, 12)),
        ("yesterday", LONDON, utc(2026, 10, 13, 12)),
        # ISO dates are never read day-first.
        ("2026-10-01", ZoneInfo("UTC"), utc(2026, 10, 1)),
        ("2026-10-01 09:00", LONDON, utc(2026, 10, 1, 8)),
        ("2026-10-01T09:00:00Z", LONDON, utc(2026, 10, 1, 9)),
        ("2026-10-01T09:00:00+02:00", None, utc(2026, 10, 1, 7)),
        # Other numeric dates are day-first.
        ("01/10/2026 09:00", LONDON, utc(2026, 10, 1, 8)),
        (" 14 October 2026 ", ZoneInfo("UTC"), utc(2026, 10, 14)),
    ],
)
def test_parse(text: str, tz: ZoneInfo | None, expected: datetime) -> None:
    parsed = parse_time(text, now=NOW, tz=tz)
    assert parsed == expected
    assert parsed.tzinfo is UTC


def test_naive_iso_in_local_time(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = parse_time("2026-10-01 09:00", now=NOW, tz=None)
    assert parsed == datetime(2026, 10, 1, 9).astimezone().astimezone(UTC)


@pytest.mark.parametrize("text", ["", "garbage", "last mondayish"])
def test_invalid(text: str) -> None:
    with pytest.raises(ValueError, match="Can't understand the time"):
        parse_time(text, now=NOW, tz=None)


def test_timezone_for() -> None:
    assert timezone_for("local") is None
    assert timezone_for("Europe/London") == LONDON
