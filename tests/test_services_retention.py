"""Warning when data is requested from before Zabbix deletes it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType, parse_period
from zabgrab.services.fetch import Job
from zabgrab.services.retention import format_duration, warn_beyond_retention

from .fakes import RecordingUI

NOW = datetime(2026, 1, 31, tzinfo=UTC)
DAY = 86_400
DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))


def job(
    itemid: int = 1,
    *,
    history: int | None = 7 * DAY,
    trends: int | None = 365 * DAY,
    value_type: ValueType = ValueType.FLOAT,
) -> Job:
    item = Item(
        itemid,
        1,
        "demo1",
        "DEMO 1",
        f"Item {itemid}",
        "k",
        value_type,
        history=history,
        trends=trends,
    )
    return Job(DEMO, item, ("t",))


def warnings(
    *jobs: Job, days: float, resolution: Resolution = Resolution.RAW
) -> list[str]:
    ui = RecordingUI()
    warn_beyond_retention(
        jobs,
        start=NOW - timedelta(days=days),
        now=NOW,
        resolution=resolution,
        ui=ui,
    )
    return ui.levels("warning")


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("0", 0),
        ("3600", 3600),
        (" 90d ", 90 * DAY),
        ("1w", 7 * DAY),
        ("15m", 900),
        ("2h", 7200),
        ("30s", 30),
        ("{$HISTORY}", None),
        ("", None),
        ("1y", None),
        ("-1d", None),
    ],
)
def test_parse_period(text: str, seconds: int | None) -> None:
    assert parse_period(text) == seconds


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(-5, "0s"), (59, "59s"), (60, "1m"), (3599, "59m"), (DAY * 3 + 5, "3d")],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text


def test_within_retention() -> None:
    assert warnings(job(), days=6.9) == []


def test_raw_beyond_history_suggests_trends() -> None:
    assert warnings(job(), days=30) == [
        "Item 1 (demo1: Item 1) keeps raw values for 7d, but the requested range "
        "starts 30d ago: older raw values may have been deleted. Hourly trends go "
        "back further: pass --resolution 1h."
    ]


@pytest.mark.parametrize(
    "one",
    [
        job(value_type=ValueType.TEXT, trends=0),
        job(trends=0),
        job(trends=DAY),
    ],
    ids=["text", "no trends", "shorter trends"],
)
def test_no_trends_suggestion(one: Job) -> None:
    (warning,) = warnings(one, days=30)
    assert warning.endswith("older raw values may have been deleted.")


def test_unknown_trends_are_suggested() -> None:
    (warning,) = warnings(job(trends=None), days=30)
    assert warning.endswith("pass --resolution 1h.")


def test_unknown_periods_and_skipped_items_arent_warned_about() -> None:
    assert warnings(job(history=None), days=1000) == []
    assert warnings(job(value_type=ValueType.BINARY), days=1000) == []
    text = job(value_type=ValueType.LOG)
    assert warnings(text, days=1000, resolution=Resolution.HOURLY) == []


def test_nothing_kept() -> None:
    assert warnings(job(history=0), days=0.5) == [
        "Item 1 (demo1: Item 1) keeps no raw values, so there are none to fetch. "
        "Hourly trends go back further: pass --resolution 1h."
    ]


def test_hourly_trends() -> None:
    assert warnings(job(), days=400, resolution=Resolution.HOURLY) == [
        "Item 1 (demo1: Item 1) keeps hourly trends for 365d, but the requested "
        "range starts 400d ago: older hourly trends may have been deleted."
    ]
    assert warnings(job(trends=0), days=1, resolution=Resolution.HOURLY) == [
        "Item 1 (demo1: Item 1) keeps no hourly trends, so there are none to fetch."
    ]


def test_items_sharing_a_period_share_a_warning() -> None:
    jobs = [job(n) for n in range(1, 6)] + [job(9, history=DAY)]
    first, second = warnings(*jobs, days=30)
    assert first.startswith(
        "Items 1 (demo1: Item 1), 2 (demo1: Item 2), 3 (demo1: Item 3) and 2 more "
        "keep raw values for 7d"
    )
    assert second.startswith("Item 9 (demo1: Item 9) keeps raw values for 1d")
    two = warnings(job(1), job(2), days=30)
    assert two[0].startswith("Items 1 (demo1: Item 1), 2 (demo1: Item 2) keep raw")
