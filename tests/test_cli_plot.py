"""``zabgrab plot`` and ``zabgrab fetch --plot``, end to end."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import pytest

from zabgrab.errors import EXIT_ERROR
from zabgrab.services.plotting import HistogramSpec, PlotSpec, Reference

from .cli_support import CONFIG, Servers, run
from .fakezabbix import FakeZabbix, seeded
from .test_cli_fetch import TEMPLATES, ok

DEMO = "https://demo.example.org"
# The fake data starts at 2025-10-09 08:00:00 UTC: item 20001 has points at 08:00,
# 08:01:00.25 and 08:02, and item 20002 at 08:00:30.
EARLY = ("--from", "2025-10-09T07:00:00Z", "--to", "2025-10-09T08:00:30Z")
LATE = ("--from", "2025-10-09T08:01:30Z", "--to", "now")


@dataclass
class Shown:
    """Plots shown or saved, instead of drawing them."""

    calls: list[tuple[PlotSpec | HistogramSpec, tzinfo | None, Sequence[Path]]] = field(
        default_factory=list
    )

    def __call__(
        self,
        spec: PlotSpec | HistogramSpec,
        tz: tzinfo | None,
        outputs: Sequence[Path] = (),
    ) -> None:
        self.calls.append((spec, tz, outputs))

    @property
    def spec(self) -> PlotSpec:
        (call,) = self.calls
        assert isinstance(call[0], PlotSpec)
        return call[0]

    @property
    def histogram(self) -> HistogramSpec:
        (call,) = self.calls
        assert isinstance(call[0], HistogramSpec)
        return call[0]


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> Shown:
    recorder = Shown()
    monkeypatch.setattr("zabgrab.ui.plot.show_or_save", recorder)
    return recorder


@pytest.fixture
def zabbix(servers: Servers, user_config: Path) -> FakeZabbix:
    user_config.write_text(CONFIG + TEMPLATES)
    servers.by_url[DEMO] = server = seeded()
    return server


def segment_times(spec: PlotSpec) -> dict[str, list[int]]:
    """Each line's segments, as their numbers of points."""
    return {
        line.label: [len(segment.times) for segment in line.segments]
        for axis in spec.axes
        for line in axis.lines
    }


class TestPlot:
    @pytest.mark.parametrize("suffix", [".mat", ".h5"])
    def test_saves_an_image(
        self, zabbix: FakeZabbix, tmp_path: Path, suffix: str
    ) -> None:
        data, image = tmp_path / f"data{suffix}", tmp_path / "plot.png"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = ok(run("plot", str(data), "-o", str(image)))
        assert f"Saved the plot to {image}." in result.stderr
        assert image.read_bytes().startswith(b"\x89PNG")

    def test_saves_several_images(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        data, png, svg = (tmp_path / name for name in ("data.h5", "a.png", "b.svg"))
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = ok(run("plot", str(data), "-o", str(png), "-o", str(svg)))
        assert f"Saved the plot to {png}, {svg}." in result.stderr
        assert png.read_bytes().startswith(b"\x89PNG")
        assert b"<svg" in svg.read_bytes()

    def test_shows_by_default(
        self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = ok(run("plot", str(data)))
        assert "Saved" not in result.stderr
        ((spec, tz, outputs),) = shown.calls
        assert spec.title == "data.h5"
        assert (tz, outputs) == (None, ())

    def test_histogram(self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        ok(run("plot", str(data), "--bin", "2,3", "--log"))
        spec = shown.histogram
        assert (spec.bins, spec.title, spec.log_y) == (
            ["< 2", "2 to 3", "≥ 3"],
            "data.h5",
            True,
        )
        assert [bars.counts for bars in spec.axes[0].bars] == [[1, 1, 1], [0, 0, 1]]

    def test_reference_lines(
        self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        args = ("--hline", "Max value=5", "--hline", "2.5 #/m3", "--hline", "Hot=26 °C")
        result = ok(run("plot", str(data), *args))
        assert shown.spec.axes[0].hlines == [
            Reference("Max value", 5.0),
            Reference("2.5 #/m3", 2.5),
        ]
        assert "Nothing plotted has units '°C', so --hline 'Hot'" in result.stderr

    def test_vertical_lines_at_times(
        self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        # Times as for --from: here local (UK summer) time, or with a zone.
        args = (
            "--vline",
            "Maintenance=2025-10-09 09:00",
            "--vline",
            "2025-10-09T08:01Z",
        )
        ok(run("plot", str(data), *args))

        def at(hour: int, minute: int) -> float:
            return datetime(2025, 10, 9, hour, minute, tzinfo=UTC).timestamp()

        assert shown.spec.axes[0].vlines == [
            Reference("Maintenance", at(8, 0)),
            Reference("2025-10-09T08:01Z", at(8, 1)),
        ]

    def test_invalid_vertical_lines(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = run("plot", str(data), "--vline", "Maintenance=whenever")
        assert result.exit_code == 2
        assert "Invalid value for '--vline'" in result.stderr
        result = run("plot", str(data), "--bin", "2", "--vline", "Limit=lots")
        assert result.exit_code == 2
        assert "'lots' isn't a number." in result.stderr

    def test_reference_lines_on_histograms(
        self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        args = ("--bin", "2,4", "--hline", "Most=2", "--vline", "Limit=3 #/m3")
        ok(run("plot", str(data), *args))
        # A count through the bars, and a value among the bins.
        (axis,) = shown.histogram.axes
        assert (axis.hlines, axis.vlines) == (
            [Reference("Most", 2.0)],
            [Reference("Limit", 1.0)],
        )

    def test_invalid_reference_line(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        result = run("plot", str(tmp_path), "--hline", "Max=lots")
        assert result.exit_code == 2
        assert "'lots' isn't a number." in result.stderr

    def test_saves_a_histogram(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        data, image = tmp_path / "data.h5", tmp_path / "bins.png"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        ok(run("plot", str(data), "--bin", "2", "-o", str(image)))
        assert image.read_bytes().startswith(b"\x89PNG")

    def test_log_scale(self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = ok(run("plot", str(data), "--log"))
        assert shown.spec.log_y
        assert "--log-y" not in result.stderr

    def test_old_log_scale_option(
        self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = ok(run("plot", str(data), "--log-y"))
        assert shown.spec.log_y
        assert "--log-y is now --log; --log-y will stop working in 2.0." in (
            result.stderr
        )
        assert "--log-y" not in ok(run("plot", "--help")).stdout

    def test_appended_groups_join_and_gaps_break(
        self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", *EARLY, "-o", str(data)))
        ok(run("fetch", "-t", "particles", *LATE, "-o", str(data), "--append"))
        ok(run("plot", str(data)))
        # Point 2 of 20001 (08:01:00.25) is in the gap between the two fetches.
        assert segment_times(shown.spec) == {
            "demo1: Particles 0.3um [#/m3]": [1, 1],
            "demo1: Particles 0.5um [#/m3]": [1],
        }

    def test_selection(self, zabbix: FakeZabbix, tmp_path: Path, shown: Shown) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", *EARLY, "-o", str(data)))
        ok(
            run(
                "fetch",
                "-t",
                "particles",
                *LATE,
                "-o",
                str(data),
                "--append",
                "--group",
                "late",
            )
        )
        ok(run("plot", str(data), "-i", "20001", "--group", "late"))
        assert segment_times(shown.spec) == {"demo1: Particles 0.3um [#/m3]": [1]}

    def test_nothing_selected(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = run("plot", str(data), "-i", "nope")
        assert result.exit_code == EXIT_ERROR
        assert "Nothing to plot." in result.stderr
        assert f"`zabgrab info {data}` shows what the file holds" in result.stderr

    def test_text_items(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "logs", "-o", str(data)))
        result = run("plot", str(data))
        assert result.exit_code == EXIT_ERROR
        assert "Text items can't be plotted." in result.stderr
        assert "item 20005 (demo1: Syslog) is a log item" in result.stderr

    def test_delimited_files(self, tmp_path: Path) -> None:
        data = tmp_path / "data.tsv"
        data.write_text("x")
        result = run("plot", str(data))
        assert result.exit_code == EXIT_ERROR
        assert "only .mat and .h5 files can be plotted" in result.stderr

    def test_invalid_time(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(data)))
        result = run("plot", str(data), "--from", "garbage")
        assert result.exit_code == 2
        assert "Invalid value for --from" in result.stderr


class TestFetchPlot:
    def test_plots_without_saving_or_asking(
        self, zabbix: FakeZabbix, shown: Shown, interactive: None
    ) -> None:
        result = ok(run("fetch", "-t", "particles", "--plot"))
        assert "Save the data" not in result.stderr
        assert list(Path().glob("*.h5")) == []
        assert segment_times(shown.spec) == {
            "demo1: Particles 0.3um [#/m3]": [3],
            "demo1: Particles 0.5um [#/m3]": [1],
        }

    def test_non_interactive_doesnt_warn(
        self, zabbix: FakeZabbix, shown: Shown
    ) -> None:
        result = ok(run("fetch", "-t", "particles", "--plot"))
        assert "wasn't saved" not in result.stderr
        assert len(shown.calls) == 1

    def test_with_output_saves_too(
        self, zabbix: FakeZabbix, shown: Shown, tmp_path: Path
    ) -> None:
        data = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "--plot", "-o", str(data)))
        assert data.exists()
        assert len(shown.calls) == 1

    def test_log_scale(self, zabbix: FakeZabbix, shown: Shown) -> None:
        ok(run("fetch", "-t", "particles", "--plot", "--log"))
        assert shown.spec.log_y

    def test_log_scale_needs_plot(self, zabbix: FakeZabbix) -> None:
        result = run("fetch", "-t", "particles", "--log-y")
        assert result.exit_code == 2
        assert "--log only applies with --plot." in result.stderr

    def test_not_with_dry_run(self, zabbix: FakeZabbix, shown: Shown) -> None:
        ok(run("fetch", "-t", "particles", "--plot", "--dry-run"))
        assert shown.calls == []
