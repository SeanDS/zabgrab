"""Laying out listings of data points."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from zabgrab.model.config import Resolution
from zabgrab.model.series import Series
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.listing import ListingOptions, listing
from zabgrab.ui.listing import Line, label, listing_lines

from .test_services_listing import DEMO

UTC = ZoneInfo("UTC")


def make(
    itemid: int,
    columns: dict[str, list[Any]],
    *,
    value_type: ValueType = ValueType.FLOAT,
    units: str = "",
    resolution: Resolution = Resolution.RAW,
) -> Series:
    item = Item(itemid, 1, "demo1", "DEMO 1", f"Item {itemid}", "k", value_type, units)
    return Series(DEMO, item, resolution, columns)


def text(lines: list[Line]) -> list[str]:
    return [line for line, _ in lines]


LOAD = make(1, {"time": [0.0, 60.25], "value": [1.5, 12.0]}, units="%")
SPEED = make(2, {"time": [30.0], "value": [7]})


def test_labels() -> None:
    assert label(LOAD) == "demo1: Item 1 [%]"
    hourly = make(3, {"time": [0], "avg": [1]}, resolution=Resolution.HOURLY)
    assert label(hourly) == "demo1: Item 3 (hourly)"


def test_one_item() -> None:
    lines = listing_lines(listing([LOAD], ListingOptions()), UTC, headings=False)
    assert lines == [
        ("Time                        Value [%]", "bold"),
        ("1970-01-01 00:00:00 UTC     1.5", ""),
        ("1970-01-01 00:01:00.25 UTC  12.0", ""),
    ]


def test_several_items_get_an_item_column() -> None:
    lines = listing_lines(listing([LOAD, SPEED], ListingOptions()), UTC, headings=False)
    assert text(lines) == [
        "Time                        Item               Value",
        "1970-01-01 00:00:00 UTC     demo1: Item 1 [%]  1.5",
        "1970-01-01 00:00:30 UTC     demo1: Item 2      7",
        "1970-01-01 00:01:00.25 UTC  demo1: Item 1 [%]  12.0",
    ]


def test_local_time() -> None:
    # The tests run in UK time.
    (_, first, _) = listing_lines(
        listing([LOAD], ListingOptions()), None, headings=False
    )
    assert first[0].startswith("1970-01-01 01:00:00 ")


def test_sections_per_item_with_counts() -> None:
    options = ListingOptions(sort="value", descending=True, limit=1, offset=1)
    sections = listing([LOAD, SPEED], ListingOptions(sort="value", descending=True))
    assert text(listing_lines(sections, UTC, headings=True)) == [
        "demo1: Item 1 [%]: 2 values",
        "Time                        Value [%]",
        "1970-01-01 00:01:00.25 UTC  12.0",
        "1970-01-01 00:00:00 UTC     1.5",
        "",
        "demo1: Item 2: 1 value",
        "Time                     Value",
        "1970-01-01 00:00:30 UTC  7",
    ]
    lines = listing_lines(listing([LOAD], options), UTC, headings=True, offset=1)
    assert lines[0] == ("demo1: Item 1 [%]: 2 of 2 values", "bold")
    lines = listing_lines(
        listing([LOAD], ListingOptions(sort="value", limit=2)), UTC, headings=True
    )
    assert lines[0] == ("demo1: Item 1 [%]: 2 values", "bold")
    many = make(8, {"time": [0, 1, 2], "value": [1, 2, 3]})
    lines = listing_lines(
        listing([many], ListingOptions(sort="value", limit=2)), UTC, headings=True
    )
    assert lines[0] == ("demo1: Item 8: 1-2 of 3 values", "bold")
    # Items whose points are all skipped have just their heading.
    skipped = listing([many, LOAD], ListingOptions(sort="value", offset=2))
    assert text(listing_lines(skipped, UTC, headings=True, offset=2)) == [
        "demo1: Item 8: 3 of 3 values",
        "Time                     Value",
        "1970-01-01 00:00:02 UTC  3",
        "",
        "demo1: Item 1 [%]: 2 values, all skipped by --offset",
    ]


class TestText:
    LOG = make(
        5,
        {
            "time": [0, 10],
            "value": ["first\nsecond\tline", "one line"],
            "log_time": [None, 9],
            "source": ["", "app"],
            "severity": [0, 4],
            "event_id": [0, 12],
        },
        value_type=ValueType.LOG,
    )

    def test_log_columns_and_one_line_values(self) -> None:
        lines = listing_lines(
            listing([self.LOG], ListingOptions()), UTC, headings=False
        )
        assert text(lines) == [
            "Time                     Log time                 Severity  Source"
            "  Event ID  Value",
            "1970-01-01 00:00:00 UTC                                  0        "
            "         0  first ⏎ second line",
            "1970-01-01 00:00:10 UTC  1970-01-01 00:00:09 UTC         4  app   "
            "        12  one line",
        ]

    def test_wrapped_values_continue_under_their_column(self) -> None:
        text_item = make(
            6, {"time": [0], "value": ["a\r\nb"]}, value_type=ValueType.TEXT
        )
        sections = listing([text_item, SPEED], ListingOptions())
        assert text(listing_lines(sections, UTC, headings=False, wrap=True)) == [
            "Time                     Item           Value",
            "1970-01-01 00:00:00 UTC  demo1: Item 6  a",
            "                                        b",
            "1970-01-01 00:00:30 UTC  demo1: Item 2  7",
        ]


def test_hourly_statistics() -> None:
    hourly = make(
        7,
        {"time": [0], "min": [1.0], "avg": [2.5], "max": [10.0], "count": [60]},
        resolution=Resolution.HOURLY,
        units="#",
    )
    assert text(
        listing_lines(listing([hourly], ListingOptions()), UTC, headings=False)
    ) == [
        "Time                     Min  Avg   Max  Count",
        "1970-01-01 00:00:00 UTC  1.0  2.5  10.0  60",
    ]
