"""``zabgrab info`` end to end."""

from __future__ import annotations

from pathlib import Path

from zabgrab.errors import EXIT_ERROR

from .cli_support import run, table_rows
from .test_cli_export import data
from .test_cli_fetch import ok

__all__ = ["data"]  # the fixture, used by name


def test_items(data: Path) -> None:
    result = ok(run("info", str(data)))
    assert table_rows(result.stdout) == [
        [
            "20001",
            "DEMO 1",
            "Particles 0.3um",
            "particles[0.3]",
            "#/m3",
            "raw",
            "3",
            "2025-10-09 09:00:00 BST",
            "2025-10-09 09:02:00 BST",
        ],
        [
            "20002",
            "DEMO 1",
            "Particles 0.5um",
            "particles[0.5]",
            "#/m3",
            "raw",
            "1",
            "2025-10-09 09:00:30 BST",
            "2025-10-09 09:00:30 BST",
        ],
        [
            "20005",
            "DEMO 1",
            "Syslog",
            "log[/var/log/syslog]",
            "",
            "raw",
            "2",
            "2025-10-09 09:00:00 BST",
            "2025-10-09 09:00:01 BST",
        ],
        ["20006", "DEMO 1", "Agent version", "agent.version", "", "raw", "1"]
        + ["2025-10-09 09:00:00 BST"] * 2,
    ]


def test_selection(data: Path) -> None:
    for args in (
        ["--item", "name=0.5"],
        ["--item", "tag=area:lab", "--item", "20002"],
        ["--match", "*0.5UM"],
    ):
        result = ok(run("info", str(data), *args))
        ids = [row[0] for row in table_rows(result.stdout)]
        assert ids == (["20001", "20002"] if "tag=area:lab" in args else ["20002"])


def test_nothing_selected(data: Path) -> None:
    result = ok(run("info", str(data), "--item", "nope"))
    assert result.stdout == ""
    assert f"No items in {data} match the selection." in result.stderr


def test_empty_series(data: Path) -> None:
    # Before the first point, items are listed without a time range.
    result = ok(run("info", str(data), "--to", "2025-10-09T07:00:00Z", "-i", "20002"))
    assert table_rows(result.stdout) == [
        [
            "20002",
            "DEMO 1",
            "Particles 0.5um",
            "particles[0.5]",
            "#/m3",
            "raw",
            "0",
            "",
            "",
        ]
    ]


def test_groups(data: Path) -> None:
    result = ok(run("info", str(data), "--groups", "--item", "name=Particles"))
    rows = table_rows(result.stdout)
    assert [(row[0], row[4], row[5]) for row in rows] == [
        ("fetch_0001", "2", "4"),
        ("fetch_0002", "2", "4"),
        ("logs", "0", "0"),
    ]
    assert rows[0][1].endswith("BST")


def test_delimited_files(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("x")
    result = run("info", str(path))
    assert result.exit_code == EXIT_ERROR
    assert "only .mat and .h5 files can be read" in result.stderr
