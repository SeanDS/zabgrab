"""Drawing plots with matplotlib."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pytest

from zabgrab.errors import ZabgrabError
from zabgrab.services.plotting import Axis, Line, PlotSpec, Segment
from zabgrab.ui import plot

T0 = datetime(
    2026, 3, 29, 0, 30, tzinfo=UTC
).timestamp()  # UK clocks go forward at 1:00

SPEC = PlotSpec(
    [
        Axis(
            "#",
            [
                Line(
                    "a [#]", [Segment([T0, T0 + 60], [1, 2]), Segment([T0 + 3600], [3])]
                ),
                Line(
                    "b [#] (hourly)",
                    [Segment([T0, T0 + 3600], [1, 2], low=[0, 1], high=[2, 3])],
                ),
            ],
        ),
        Axis("", [Line("c", [Segment([T0], [5], low=[4], high=[7])])]),
    ],
    title="data.h5",
)


@pytest.fixture(autouse=True)
def _close_figures() -> None:
    plt.close("all")


def test_draw() -> None:
    figure = plot.draw(SPEC, ZoneInfo("Europe/London"))
    top, bottom = figure.axes
    assert figure.get_suptitle() == "data.h5"
    assert top.get_ylabel() == "#"
    assert bottom.get_xlabel() == "Time (Europe/London)"
    # One legend entry per line, however many segments it has.
    legend = top.get_legend()
    assert legend is not None
    assert [t.get_text() for t in legend.get_texts()] == [
        "a [#]",
        "b [#] (hourly)",
    ]
    assert len(top.get_lines()) == 3
    # Times are wall-clock times, which jump an hour as the clocks go forward.
    first: Any = top.get_lines()[0].get_xdata()
    second: Any = top.get_lines()[1].get_xdata()
    assert [t.strftime("%H:%M") for t in first] == ["00:30", "00:31"]
    assert second[0].strftime("%H:%M") == "02:30"
    assert len(top.collections) == 1  # the min/max band
    assert len(bottom.containers) == 1  # a lone point's range, as an error bar


def test_local_time_and_no_title() -> None:
    figure = plot.draw(PlotSpec(SPEC.axes[1:]), None)
    assert figure.get_suptitle() == ""
    assert figure.axes[0].get_xlabel() == "Time (local time)"


def test_save(tmp_path: Path) -> None:
    path = tmp_path / "plot.png"
    plot.show_or_save(SPEC, None, path)
    assert path.read_bytes().startswith(b"\x89PNG")
    assert plt.get_fignums() == []


@pytest.mark.parametrize("name", ["missing/plot.png", "plot.unknown"])
def test_save_fails(tmp_path: Path, name: str) -> None:
    with pytest.raises(ZabgrabError, match="Can't save the plot"):
        plot.show_or_save(SPEC, None, tmp_path / name)
    assert plt.get_fignums() == []


def test_show(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = []
    monkeypatch.setattr(plt, "show", lambda: shown.append(plt.get_fignums()))
    plot.show_or_save(SPEC, None)
    assert [len(figures) for figures in shown] == [1]
    assert plt.get_fignums() == []


def test_matplotlib_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    with pytest.raises(ZabgrabError, match="Plotting needs matplotlib") as exc_info:
        plot.draw(SPEC, None)
    assert exc_info.value.hint == "Install it with: pip install 'zabgrab[plot]'"
