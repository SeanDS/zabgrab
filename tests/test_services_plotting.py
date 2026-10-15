"""Deciding what to plot."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from zabgrab.errors import ZabgrabError
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.plotting import Segment, plot_spec

from .fakes import RecordingUI

DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))
LAB = Server("lab", ServerConfig(url="https://lab.example.org"))


def item(
    itemid: int = 1,
    *,
    name: str = "Particles",
    units: str = "#",
    value_type: ValueType = ValueType.FLOAT,
) -> Item:
    return Item(itemid, 1, "demo1", "DEMO 1", name, "k", value_type, units)


def raw(
    times: list[float], values: list[Any], it: Item | None = None, server: Server = DEMO
) -> Series:
    return Series(
        server, it or item(), Resolution.RAW, {"time": times, "value": values}
    )


def trend(times: list[float], **columns: list[float]) -> Series:
    return Series(DEMO, item(), Resolution.HOURLY, {"time": times, **columns})


EVERYTHING = [(0.0, 10**10)]


class TestTextItems:
    def test_only_one(self) -> None:
        text = raw([1], ["boot"], item(5, name="Syslog", value_type=ValueType.LOG))
        with pytest.raises(ZabgrabError) as exc_info:
            plot_spec([text], EVERYTHING, RecordingUI())
        assert exc_info.value.message == (
            "Item 5 (demo1: Syslog) is a log item; text items can't be plotted."
        )

    def test_only_several(self) -> None:
        texts = [
            raw([1], ["a"], item(5, name="A", value_type=ValueType.CHAR)),
            raw([1], ["b"], item(6, name="B", value_type=ValueType.TEXT)),
        ]
        with pytest.raises(ZabgrabError, match=r"Text items can't be plotted\.") as e:
            plot_spec(texts, EVERYTHING, RecordingUI())
        assert e.value.details == (
            "item 5 (demo1: A) is a char item",
            "item 6 (demo1: B) is a text item",
        )

    def test_mixed_warns_and_plots_the_rest(self) -> None:
        ui = RecordingUI()
        spec = plot_spec(
            [raw([1], [1.0]), raw([1], ["x"], item(5, value_type=ValueType.LOG))],
            EVERYTHING,
            ui,
        )
        assert [line.label for line in spec.axes[0].lines] == ["demo1: Particles [#]"]
        assert ui.levels("warning") == [
            "Not plotting item 5 (demo1: Particles), a log item; text items can't be "
            "plotted."
        ]


def test_nothing() -> None:
    with pytest.raises(ZabgrabError, match=r"Nothing to plot\."):
        plot_spec([], EVERYTHING, RecordingUI())


def test_one_axis_per_unit_in_order_of_appearance() -> None:
    spec = plot_spec(
        [
            raw([1], [1], item(1, units="B")),
            raw([1], [2], item(2, units="")),
            raw([1], [3], item(3, units="B", value_type=ValueType.UNSIGNED)),
        ],
        EVERYTHING,
        RecordingUI(),
        title="data.h5",
    )
    assert spec.title == "data.h5"
    assert [(axis.units, len(axis.lines)) for axis in spec.axes] == [("B", 2), ("", 1)]
    assert spec.axes[1].lines[0].label == "demo1: Particles"


def test_labels_name_the_server_when_there_are_several() -> None:
    spec = plot_spec(
        [raw([1], [1]), raw([1], [2], server=LAB), trend([0], avg=[1.0])],
        EVERYTHING,
        RecordingUI(),
    )
    assert [line.label for line in spec.axes[0].lines] == [
        "demo1: Particles [#] (server: demo)",
        "demo1: Particles [#] (server: lab)",
        "demo1: Particles [#] (hourly, server: demo)",
    ]


class TestSegments:
    @staticmethod
    def segments(one: Series, coverage: Sequence[tuple[float, float]]) -> list[Segment]:
        return plot_spec([one], coverage, RecordingUI()).axes[0].lines[0].segments

    def test_lines_break_only_where_nothing_was_fetched(self) -> None:
        one = raw([1, 5, 9, 12, 20], [1, 5, 9, 12, 20])
        segments = self.segments(one, [(0, 10), (11, 30)])
        assert [s.times for s in segments] == [[1, 5, 9], [12, 20]]
        assert segments[0].values == [1.0, 5.0, 9.0]
        assert segments[0].low is None

    def test_points_outside_every_range_stand_alone(self) -> None:
        segments = self.segments(raw([-5, -4, 1, 50], [0, 0, 1, 2]), [(0, 10)])
        assert [s.times for s in segments] == [[-5], [-4], [1], [50]]

    def test_trend_hours_starting_before_the_range_belong_to_it(self) -> None:
        one = trend([0, 3600, 7200], avg=[1, 2, 3])
        segments = self.segments(one, [(1800, 7300)])
        assert [s.times for s in segments] == [[0, 3600, 7200]]

    def test_trend_average_with_band(self) -> None:
        one = trend([0, 3600], min=[0, 1], avg=[1, 2], max=[2, 3], count=[5, 5])
        (segment,) = self.segments(one, EVERYTHING)
        assert (segment.values, segment.low, segment.high) == ([1, 2], [0, 1], [2, 3])

    @pytest.mark.parametrize(
        ("columns", "line"),
        [
            ({"avg": [1.0]}, [1.0]),
            ({"min": [0.5]}, [0.5]),
            ({"max": [2.0], "count": [3]}, [2.0]),
            ({"count": [3]}, [3.0]),
        ],
    )
    def test_trend_without_band(
        self, columns: dict[str, list[float]], line: list[float]
    ) -> None:
        (segment,) = self.segments(trend([0], **columns), EVERYTHING)
        assert (segment.values, segment.low, segment.high) == (line, None, None)


class TestLogScale:
    def test_flagged_in_the_spec(self) -> None:
        assert plot_spec([raw([1], [1])], EVERYTHING, RecordingUI(), log_y=True).log_y
        assert not plot_spec([raw([1], [1])], EVERYTHING, RecordingUI()).log_y

    @pytest.mark.parametrize(
        "one",
        [raw([1, 2], [1, 0]), trend([0], min=[-1], avg=[1], max=[2])],
        ids=["value", "band"],
    )
    def test_values_of_zero_or_less_are_warned_about(self, one: Series) -> None:
        ui = RecordingUI()
        spec = plot_spec([one], EVERYTHING, ui, log_y=True)
        assert ui.levels("warning") == [
            f"{spec.axes[0].lines[0].label} has values of zero or less, which a log "
            "scale can't show; they're left out."
        ]

    def test_positive_values_and_linear_scales_arent(self) -> None:
        ui = RecordingUI()
        plot_spec([raw([1], [0.5])], EVERYTHING, ui, log_y=True)
        plot_spec([raw([1], [0])], EVERYTHING, ui)
        assert ui.levels("warning") == []
