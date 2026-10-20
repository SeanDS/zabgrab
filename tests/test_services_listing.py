"""Listing points: filtering, sorting and slicing."""

from __future__ import annotations

import re
from typing import Any

import pytest

from zabgrab.errors import ZabgrabError
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.listing import (
    ListingOptions,
    Row,
    Section,
    listing,
    value_column,
)

DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))


def raw(itemid: int, times: list[float], values: list[Any], **kind: Any) -> Series:
    value_type = kind.get("value_type", ValueType.FLOAT)
    item = Item(itemid, 1, "demo1", "DEMO 1", f"Item {itemid}", "k", value_type, "")
    return Series(DEMO, item, Resolution.RAW, {"time": times, "value": values})


def hourly(columns: dict[str, list[float]]) -> Series:
    item = Item(9, 1, "demo1", "DEMO 1", "Load", "load", ValueType.FLOAT, "")
    return Series(DEMO, item, Resolution.HOURLY, {"time": [0, 3600], **columns})


# Two numeric items with interleaved times, and a text item.
A = raw(1, [10, 30, 50], [5.0, 1.0, 3.0])
B = raw(2, [20, 30, 40], [2.0, 9.0, 4.0])
TEXT = raw(3, [15, 25], ["usb error", "disk ok"], value_type=ValueType.TEXT)


def points(sections: list[Section]) -> list[list[tuple[int, Any]]]:
    """Each section's points, as (item ID, value or time)."""
    return [
        [(row.series.item.itemid, row.get("value")) for row in section.rows]
        for section in sections
    ]


def times(section: Section) -> list[float]:
    return [row.get("time") for row in section.rows]


class TestTimeOrder:
    def test_interleaves_items(self) -> None:
        (section,) = listing([A, B], ListingOptions())
        assert times(section) == [10, 20, 30, 30, 40, 50]
        # Equal times keep the items' order.
        assert [row.series.item.itemid for row in section.rows[2:4]] == [1, 2]
        assert section.series == [A, B]
        assert section.matched == 6

    def test_descending_limit_offset(self) -> None:
        options = ListingOptions(descending=True, limit=2, offset=1)
        (section,) = listing([A, B], options)
        assert times(section) == [40, 30]
        assert section.matched == 6

    def test_limit_without_offset(self) -> None:
        (section,) = listing([A, B], ListingOptions(limit=3))
        assert times(section) == [10, 20, 30]

    def test_nothing_matches(self) -> None:
        assert listing([A], ListingOptions(above=100)) == []

    def test_items_without_matches_are_left_out(self) -> None:
        (section,) = listing([A, TEXT], ListingOptions(grep=re.compile("usb")))
        assert section.series == [TEXT]


class TestValueOrder:
    def test_one_section_per_item(self) -> None:
        sections = listing([A, B], ListingOptions(sort="value", descending=True))
        assert points(sections) == [
            [(1, 5.0), (1, 3.0), (1, 1.0)],
            [(2, 9.0), (2, 4.0), (2, 2.0)],
        ]
        assert [s.series for s in sections] == [[A], [B]]

    def test_top_n_per_item(self) -> None:
        options = ListingOptions(sort="value", descending=True, limit=1)
        sections = listing([A, B], options)
        assert points(sections) == [[(1, 5.0)], [(2, 9.0)]]
        assert [s.matched for s in sections] == [3, 3]

    def test_ascending_with_offset(self) -> None:
        options = ListingOptions(sort="value", limit=1, offset=1)
        assert points(listing([A], options)) == [[(1, 3.0)]]

    def test_equal_values_by_time(self) -> None:
        same = raw(4, [3, 1, 2], [7.0, 7.0, 7.0])
        (section,) = listing([same], ListingOptions(sort="value"))
        assert times(section) == [1, 2, 3]

    def test_text_alphabetically(self) -> None:
        assert points(listing([TEXT], ListingOptions(sort="value"))) == [
            [(3, "disk ok"), (3, "usb error")]
        ]

    def test_empty_items_have_no_section(self) -> None:
        sections = listing([A, B], ListingOptions(sort="value", above=4.5))
        assert points(sections) == [[(1, 5.0)], [(2, 9.0)]]
        assert listing([A], ListingOptions(sort="value", above=10)) == []


class TestFilters:
    def test_grep_is_a_regular_expression_searched_anywhere(self) -> None:
        options = ListingOptions(grep=re.compile(r"e?rr"))
        assert points(listing([TEXT], options)) == [[(3, "usb error")]]
        options = ListingOptions(grep=re.compile("(?i)^USB"))
        assert points(listing([TEXT], options)) == [[(3, "usb error")]]

    def test_grep_matches_numbers_as_text(self) -> None:
        options = ListingOptions(grep=re.compile(r"^9\."))
        assert points(listing([A, B], options)) == [[(2, 9.0)]]

    def test_above_and_below_are_exclusive(self) -> None:
        options = ListingOptions(above=2.0, below=5.0)
        # 2.0 and 5.0 themselves are left out; in time order, 4.0 (at 40) is first.
        assert points(listing([A, B], options)) == [[(2, 4.0), (1, 3.0)]]

    def test_thresholds_leave_out_text(self) -> None:
        assert listing([TEXT], ListingOptions(below=1e9)) == []


class TestHourlyTrends:
    STATS = hourly({"min": [1, 2], "avg": [3, 2.5], "max": [8, 4]})

    def test_value_means_the_average(self) -> None:
        assert value_column(self.STATS) == "avg"
        (section,) = listing([self.STATS], ListingOptions(sort="value"))
        assert [row.get("avg") for row in section.rows] == [2.5, 3]

    def test_sort_by_a_statistic(self) -> None:
        options = ListingOptions(sort="max", descending=True, above=5)
        (section,) = listing([self.STATS], options)
        assert [row.get("max") for row in section.rows] == [8]

    def test_first_statistic_without_an_average(self) -> None:
        assert value_column(hourly({"max": [1, 2], "count": [3, 4]})) == "max"

    def test_missing_statistic(self) -> None:
        with pytest.raises(ZabgrabError, match="has no count statistic") as info:
            listing([self.STATS], ListingOptions(sort="count"))
        assert info.value.hint == "Sort by min or avg or max instead."

    def test_statistic_of_raw_data(self) -> None:
        with pytest.raises(ZabgrabError, match="is raw data, which has no avg"):
            listing([A], ListingOptions(sort="avg"))


def test_row_get() -> None:
    row = Row(A, 1)
    assert (row.get("time"), row.get("value"), row.get("severity")) == (30, 1.0, None)
