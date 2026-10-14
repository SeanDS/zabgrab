"""Merging fetch groups."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from zabgrab.adapters.formats.hdf5 import FetchGroup
from zabgrab.errors import ZabgrabError
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.dataset import filter_dataset, matches, merge, select_groups

from .fakes import RecordingUI

DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))
LAB = Server("lab", ServerConfig(url="https://lab.example.org"))


def item(itemid: int = 1, units: str = "#", name: str = "Particles") -> Item:
    return Item(itemid, 1, "demo1", "DEMO 1", name, "p[0.3]", ValueType.FLOAT, units)


def series(
    times: list[float],
    values: list[float],
    *,
    server: Server = DEMO,
    it: Item | None = None,
    requests: tuple[str, ...] = ("t",),
) -> Series:
    return Series(
        server, it or item(), Resolution.RAW, {"time": times, "value": values}, requests
    )


def group(name: str, t_from: float, t_to: float, *data: Series) -> FetchGroup:
    meta: dict[str, Any] = {"group": name}
    return FetchGroup(name, meta, t_from, t_to, list(data))


GROUPS = [
    group("fetch_0001", 0, 10, series([1, 5, 9], [1.0, 5.0, 9.0])),
    group(
        "fetch_0002",
        10.5,
        20,
        series([9, 12], [90.0, 12.0], requests=("u",)),
        series([2], [2.0], server=LAB),
    ),
]


class TestMerge:
    def test_joins_sorts_and_prefers_later_values(self) -> None:
        dataset = merge(GROUPS, RecordingUI())
        demo, lab = dataset.series
        assert demo.columns == {"time": [1, 5, 9, 12], "value": [1.0, 5.0, 90.0, 12.0]}
        assert demo.requests == ("t", "u")
        assert lab.server == LAB
        assert lab.columns == {"time": [2], "value": [2.0]}
        assert dataset.groups == ["fetch_0001", "fetch_0002"]
        assert dataset.last_metadata == {"group": "fetch_0002"}

    def test_coverage_joins_adjacent_ranges(self) -> None:
        assert merge(GROUPS, RecordingUI()).coverage == [(0, 20)]
        gappy = [group("a", 0, 10), group("b", 100, 110), group("c", 105, 120)]
        assert merge(gappy, RecordingUI()).coverage == [(0, 10), (100, 120)]

    def test_latest_item_details_and_unit_changes(self) -> None:
        ui = RecordingUI()
        groups = [
            group("a", 0, 1, series([0], [1.0], it=item(units="ug"))),
            group("b", 1, 2, series([1], [2.0], it=item(units="mg", name="Renamed"))),
        ]
        (merged,) = merge(groups, ui).series
        assert merged.item.name == "Renamed"
        assert ui.levels("warning") == [
            "The units of demo1: Renamed changed from 'ug' to 'mg' in group 'b'."
        ]

    def test_no_groups(self) -> None:
        with pytest.raises(ZabgrabError, match="no data"):
            merge([], RecordingUI())


class TestSelectGroups:
    def test_all(self) -> None:
        assert select_groups(GROUPS, []) == GROUPS

    def test_some(self) -> None:
        assert select_groups(GROUPS, ["fetch_0002"]) == [GROUPS[1]]

    def test_missing(self) -> None:
        with pytest.raises(ZabgrabError, match="No group called 'nope'") as exc_info:
            select_groups(GROUPS, ["nope"])
        assert exc_info.value.hint == "The groups are: fetch_0001, fetch_0002."


class TestFilter:
    def test_time_window(self) -> None:
        dataset = filter_dataset(
            merge(GROUPS, RecordingUI()),
            start=datetime.fromtimestamp(4, UTC),
            end=datetime.fromtimestamp(10, UTC),
        )
        assert dataset.series[0].columns == {"time": [5, 9], "value": [5.0, 90.0]}
        assert dataset.coverage == [(4, 10)]

    def test_window_outside_coverage(self) -> None:
        dataset = filter_dataset(
            merge(GROUPS, RecordingUI()), start=datetime.fromtimestamp(50, UTC)
        )
        assert dataset.coverage == []

    def test_items(self) -> None:
        groups = [
            group(
                "a",
                0,
                1,
                series([0], [1.0]),
                series([0], [1.0], it=item(2, name="Other")),
            )
        ]
        dataset = filter_dataset(merge(groups, RecordingUI()), items=["oth*"])
        assert [s.item.itemid for s in dataset.series] == [2]

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("1", True),
            ("particles", True),
            ("PART*", True),
            ("p[[]0.3]", True),
            ("demo1: *", True),
            ("demo1", False),
            ("2", False),
        ],
    )
    def test_matches(self, pattern: str, expected: bool) -> None:
        assert matches(series([], []), pattern) is expected
