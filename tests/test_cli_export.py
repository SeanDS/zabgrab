"""``zabgrab export`` end to end."""

from __future__ import annotations

from pathlib import Path

import pytest

from zabgrab.adapters.formats.hdf5 import read_fetches
from zabgrab.errors import EXIT_ERROR

from .cli_support import CONFIG, Servers, run
from .test_cli_fetch import TEMPLATES, TIMES, ok, read


@pytest.fixture
def data(servers: Servers, user_config: Path, tmp_path: Path) -> Path:
    """A .mat file with two overlapping fetches of particles and one of logs."""
    user_config.write_text(CONFIG + TEMPLATES)
    path = tmp_path / "data.mat"
    ok(run("fetch", "-t", "particles", "-o", str(path)))
    ok(run("fetch", "-t", "particles", "-o", str(path), "--append"))
    ok(run("fetch", "-t", "logs", "-o", str(path), "--append", "--group", "logs"))
    return path


def test_merged_csv(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    result = ok(run("export", str(data), "-o", str(out)))
    assert (
        f"Exported 4 items (7 points) from 3 groups of {data} to {out}."
        in result.stderr
    )
    metadata, rows = read(out)
    assert metadata["export"]["groups"] == ["fetch_0001", "fetch_0002", "logs"]
    assert metadata["export"]["source"] == str(data)
    # Repeated points appear once.
    assert [row[0] for row in rows[1:]] == [
        "2025-10-09T09:00:00+01:00",
        "2025-10-09T09:01:00.250000+01:00",
        "2025-10-09T09:02:00+01:00",
    ]


@pytest.mark.parametrize("suffix", [".h5", ".mat"])
def test_single_group(data: Path, tmp_path: Path, suffix: str) -> None:
    out = tmp_path / f"out{suffix}"
    ok(run("export", str(data), "-o", str(out)))
    (group,) = read_fetches(out)
    assert group.name == "fetch_0001"
    assert [s.points for s in group.series] == [3, 1, 2, 1]
    assert group.metadata["query"]["templates"] == ["logs"]


def test_filters(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.tsv"
    ok(
        run(
            "export",
            str(data),
            "-o",
            str(out),
            "--item",
            "*0.3um",
            "--from",
            "2025-10-09T08:00:30Z",
            "--to",
            "2025-10-09T08:01:30Z",
        )
    )
    metadata, rows = read(out)
    assert [item["itemid"] for item in metadata["items"]] == [20001]
    assert rows[1:] == [["2025-10-09T09:01:00.250000+01:00", "2.5"]]
    assert metadata["export"]["filters"]["items"] == ["*0.3um"]


def test_groups(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.h5"
    ok(run("export", str(data), "-o", str(out), "--group", "logs"))
    assert [s.item.itemid for s in read_fetches(out)[0].series] == [20005, 20006]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--group", "nope"], "No group called 'nope'"),
        (["--item", "nothing"], "Nothing to export."),
        (["--from", "2030-01-01"], "Nothing to export."),
    ],
)
def test_errors(data: Path, tmp_path: Path, args: list[str], message: str) -> None:
    result = run("export", str(data), "-o", str(tmp_path / "out.csv"), *args)
    assert result.exit_code == EXIT_ERROR
    assert message in result.stderr


def test_invalid_time(data: Path, tmp_path: Path) -> None:
    result = run("export", str(data), "-o", str(tmp_path / "o.csv"), "--to", "garbage")
    assert result.exit_code == 2
    assert "Invalid value for --to" in result.stderr


def test_delimited_input(tmp_path: Path) -> None:
    path = tmp_path / "data.tsv"
    path.write_text("x")
    result = run("export", str(path), "-o", str(tmp_path / "out.csv"))
    assert result.exit_code == EXIT_ERROR
    assert "only .mat and .h5 files can be exported" in result.stderr


def test_mixed_resolutions(data: Path, tmp_path: Path) -> None:
    ok(
        run(
            "fetch",
            "-s",
            "demo",
            "-i",
            "20001",
            *TIMES,
            "--resolution",
            "1h",
            "-o",
            str(data),
            "--append",
        )
    )
    result = run("export", str(data), "-o", str(tmp_path / "out.csv"))
    assert result.exit_code == EXIT_ERROR
    assert "has both raw and hourly data" in result.stderr


def test_existing_output_saved_alongside(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    out.write_text("old")
    result = ok(run("export", str(data), "-o", str(out)))
    assert out.read_text() == "old"
    assert (tmp_path / "out-2.csv").exists()
    assert "--overwrite" in result.stderr


def test_large_text_guard(data: Path, tmp_path: Path, user_config: Path) -> None:
    user_config.write_text(
        CONFIG + TEMPLATES + "[defaults]\nmat_text_warn_entries = 2\n"
    )
    result = ok(run("export", str(data), "-o", str(tmp_path / "out.mat")))
    assert "text values, more than mat_text_warn_entries" in result.stderr


def test_format_from_config(data: Path, tmp_path: Path, user_config: Path) -> None:
    user_config.write_text(CONFIG + TEMPLATES + '[defaults]\nformat = "csv"\n')
    out = tmp_path / "out"
    ok(run("export", str(data), "-o", str(out)))
    assert "," in out.read_text(encoding="utf-8").splitlines()[-1]
