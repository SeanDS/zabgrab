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
from zabgrab.services.plotting import (
    Axis,
    BarAxis,
    Bars,
    HistogramSpec,
    Line,
    PlotSpec,
    Segment,
)
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


def test_log_scale() -> None:
    linear = plot.draw(SPEC, None)
    assert [axes.get_yscale() for axes in linear.axes] == ["linear", "linear"]
    spec = PlotSpec(SPEC.axes, log_y=True)
    assert [axes.get_yscale() for axes in plot.draw(spec, None).axes] == ["log", "log"]


def test_local_time_and_no_title() -> None:
    figure = plot.draw(PlotSpec(SPEC.axes[1:]), None)
    assert figure.get_suptitle() == ""
    assert figure.axes[0].get_xlabel() == "Time (local time)"


def test_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plt, "show", lambda: pytest.fail("shown"))
    png, pdf = tmp_path / "plot.png", tmp_path / "plot.pdf"
    plot.show_or_save(SPEC, None, [png, pdf])
    assert png.read_bytes().startswith(b"\x89PNG")
    assert pdf.read_bytes().startswith(b"%PDF")
    assert plt.get_fignums() == []


@pytest.mark.parametrize("name", ["missing/plot.png", "plot.unknown"])
def test_save_fails(tmp_path: Path, name: str) -> None:
    with pytest.raises(ZabgrabError, match="Can't save the plot"):
        plot.show_or_save(SPEC, None, [tmp_path / name])
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


HISTOGRAM = HistogramSpec(
    ["< 10", "10 to 100", "≥ 100"],
    [
        BarAxis("#", [Bars("a [#]", [1, 0, 5]), Bars("b [#]", [2, 3, 4])]),
        BarAxis("", [Bars("c", [7, 0, 0])]),
    ],
    title="data.h5",
)


def test_draw_histogram() -> None:
    figure = plot.draw_histogram(HISTOGRAM)
    top, bottom = figure.axes
    assert figure.get_suptitle() == "data.h5"
    assert len(top.patches) == 6  # 2 items x 3 bins
    # Items' bars sit side by side, centred on each bin.
    bars: list[Any] = list(top.patches)  # Rectangles
    centres = sorted(round(bar.get_x() + bar.get_width() / 2, 2) for bar in bars)
    assert centres == [-0.2, 0.2, 0.8, 1.2, 1.8, 2.2]
    assert [t.get_text() for t in top.get_xticklabels()] == HISTOGRAM.bins
    assert (top.get_ylabel(), top.get_xlabel()) == ("Count", "Value [#]")
    assert bottom.get_xlabel() == "Value"
    assert top.get_yscale() == "linear"


def test_histogram_log_scale_and_no_title() -> None:
    spec = HistogramSpec(HISTOGRAM.bins, HISTOGRAM.axes[1:], log_y=True)
    figure = plot.draw_histogram(spec)
    assert figure.get_suptitle() == ""
    assert figure.axes[0].get_yscale() == "log"


def test_save_histogram(tmp_path: Path) -> None:
    path = tmp_path / "histogram.png"
    plot.show_or_save(HISTOGRAM, None, [path])
    assert path.read_bytes().startswith(b"\x89PNG")
    assert plt.get_fignums() == []
