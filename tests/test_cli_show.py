"""``zabgrab show`` and ``zabgrab fetch --show``, end to end."""

from __future__ import annotations

from pathlib import Path

import pytest

from zabgrab.errors import EXIT_ERROR, EXIT_PARTIAL

from .cli_support import BST, CONFIG, Servers, run
from .fakezabbix import seeded
from .test_cli_export import data
from .test_cli_fetch import TEMPLATES, ok

__all__ = ["data"]  # the fixture, used by name

DEMO = "https://demo.example.org"

# A time as listed: its width (with the system's name for UK summer time) sets the
# columns'.
TIME = f"2025-10-09 09:00:00 {BST}"


def rows(output: str) -> list[list[str]]:
    """Split listing lines into cells (separated by two or more spaces)."""
    return [
        [cell for cell in line.split("  ") if cell.strip()]
        for line in output.splitlines()
    ]


def values(output: str) -> list[str]:
    """The last cell of each line under a heading (the values)."""
    return [
        line.split("  ")[-1].strip()
        for line in output.splitlines()
        if line[:4].isdigit()
    ]


class TestFile:
    def test_time_order(self, data: Path) -> None:
        result = ok(run("show", str(data)))
        lines = result.stdout.splitlines()
        assert lines[0].split() == [
            "Time", "Item", "Log", "time", "Severity", "Source", "Event", "ID",
            "Value",
        ]  # fmt: skip
        assert values(result.stdout) == [
            "1.5",
            "kernel: boot",
            "7.0.5",
            'tab here, comma, "quote" ⏎ newline ✓ 𝄞',
            "10.0",
            "2.5",
            "3.5",
        ]
        assert lines[6].startswith(f"2025-10-09 09:01:00.25 {BST}  demo1: Particles")
        assert result.stderr == ""

    def test_top_value_per_item(self, data: Path) -> None:
        args = ("--sort", "value", "--desc", "-n", "1", "-i", "name=Particles *")
        result = ok(run("show", str(data), *args))
        heading = "Time".ljust(len(TIME)) + "  Value [#/m3]"
        assert result.stdout.splitlines() == [
            "demo1: Particles 0.3um [#/m3]: 1 of 3 values",
            heading,
            f"2025-10-09 09:02:00 {BST}  3.5",
            "",
            "demo1: Particles 0.5um [#/m3]: 1 value",
            heading,
            f"2025-10-09 09:00:30 {BST}  10.0",
        ]

    def test_wrap(self, data: Path) -> None:
        result = ok(run("show", str(data), "-i", "20005", "--wrap"))
        # Time and log time, then severity, source and event ID (widths 8, 6 and 8,
        # the headings'), each followed by a gap of 2.
        width = len(TIME)
        assert result.stdout.splitlines()[-2:] == [
            f"2025-10-09 09:00:01 {BST}{' ' * (width + 11)}0{' ' * 17}0  "
            'tab here, comma, "quote"',
            f"{' ' * (2 * width + 32)}newline ✓ 𝄞",
        ]

    def test_grep(self, data: Path) -> None:
        result = ok(run("show", str(data), "--grep", "(?i)^KERNEL"))
        assert values(result.stdout) == ["kernel: boot"]

    def test_thresholds(self, data: Path) -> None:
        result = ok(run("show", str(data), "--above", "2", "--below", "5"))
        assert values(result.stdout) == ["2.5", "3.5"]

    def test_no_matches(self, data: Path) -> None:
        result = ok(run("show", str(data), "--grep", "nothing like this"))
        assert result.stdout == ""
        assert "No values match --grep, --above or --below." in result.stderr

    def test_slices_in_time_order(self, data: Path) -> None:
        result = ok(run("show", str(data), "--desc", "-n", "2", "--offset", "1"))
        assert values(result.stdout) == ["2.5", "10.0"]
        assert "Showed values 2-3 of 7." in result.stderr
        result = ok(run("show", str(data), "--offset", "10"))
        assert "--offset 10 skipped all 7 values." in result.stderr

    def test_statistic_of_raw_data(self, data: Path) -> None:
        result = run("show", str(data), "--sort", "max")
        assert result.exit_code == EXIT_ERROR
        assert "is raw data, which has no max statistic." in result.stderr

    def test_nothing_selected(self, data: Path) -> None:
        result = run("show", str(data), "-i", "nope")
        assert result.exit_code == EXIT_ERROR
        assert "Nothing to show." in result.stderr
        assert f"`zabgrab info {data}`" in result.stderr

    @pytest.mark.parametrize(
        "args",
        [["-t", "particles"], ["-s", "demo"], ["--resolution", "1h"], ["-y"]],
    )
    def test_fetch_options(self, data: Path, args: list[str]) -> None:
        result = run("show", str(data), *args)
        assert result.exit_code == 2
        assert "only applies when fetching (without FILE)." in result.stderr

    def test_bad_regex(self, data: Path) -> None:
        result = run("show", str(data), "--grep", "(")
        assert result.exit_code == 2
        assert "'(' isn't a valid regular expression" in result.stderr


class TestFetched:
    @pytest.fixture
    def zabbix(self, servers: Servers, user_config: Path) -> Servers:
        user_config.write_text(CONFIG + TEMPLATES)
        servers.by_url[DEMO] = seeded()
        return servers

    def test_template(self, zabbix: Servers) -> None:
        result = ok(run("show", "-t", "particles", "--sort", "value", "-n", "1"))
        assert "Fetched 2 items (4 points) from 'demo'" in result.stderr
        assert values(result.stdout) == ["1.5", "10.0"]
        assert [p for p in Path().iterdir() if p.is_file()] == []  # nothing saved

    def test_no_values(self, zabbix: Servers) -> None:
        args = (
            "-s",
            "demo",
            "-i",
            "20001",
            "--from",
            "2025-10-01",
            "--to",
            "2025-10-02",
        )
        result = ok(run("show", *args))
        assert result.stdout == ""
        assert "There are no values to show." in result.stderr

    def test_partial_failure(self, zabbix: Servers) -> None:
        zabbix.by_url[DEMO].failing_items.add(20002)
        result = run("show", "-t", "particles")
        assert result.exit_code == EXIT_PARTIAL
        assert values(result.stdout) == ["1.5", "2.5", "3.5"]

    def test_next_time(self, zabbix: Servers, interactive: None) -> None:
        result = ok(run("show", "-t", "either", input="2\n"))
        assert "show -t either --server demo\n" in result.stderr
        # show can't save templates.
        assert "--save-template" not in result.stderr

    @pytest.mark.parametrize("args", [["-m", "x*"], ["--group", "fetch_0001"]])
    def test_file_options(self, zabbix: Servers, args: list[str]) -> None:
        result = run("show", "-t", "particles", *args)
        assert result.exit_code == 2
        assert "only applies when reading a FILE." in result.stderr


class TestFetchShow:
    @pytest.fixture
    def zabbix(self, servers: Servers, user_config: Path) -> None:
        user_config.write_text(CONFIG + TEMPLATES)
        servers.by_url[DEMO] = seeded()

    @pytest.mark.usefixtures("zabbix")
    def test_shows_without_saving(self) -> None:
        result = ok(run("fetch", "-t", "particles", "--show"))
        assert values(result.stdout) == ["1.5", "10.0", "2.5", "3.5"]
        assert "wasn't saved" not in result.stderr
        assert [p for p in Path().iterdir() if p.is_file()] == []

    @pytest.mark.usefixtures("zabbix")
    def test_shows_and_saves(self, tmp_path: Path) -> None:
        path = tmp_path / "data.h5"
        result = ok(run("fetch", "-t", "particles", "--show", "-o", str(path)))
        assert len(values(result.stdout)) == 4
        assert path.exists()
