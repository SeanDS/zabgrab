"""``zabgrab fetch`` end to end, against fake servers."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from zabgrab.adapters.formats.hdf5 import group_names, large_text_accepted, read_fetches
from zabgrab.errors import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_PARTIAL

from .cli_support import CONFIG, Servers, run, table_rows
from .fakezabbix import T0, seeded

TEMPLATES = """
[templates.particles]
server = "demo"
items = [{ host = "demo1", name = "Particles *" }]
from = "2 hours ago"
to = "now"

[templates.uptime]
server = "demo"
items = [{ key = "system.uptime" }, { itemid = 20001 }]
from = "120 minutes ago"

[templates.logs]
server = "demo"
items = [{ itemid = 20005 }, { itemid = 20006 }]
from = "2 hours ago"
to = "now"

[templates.either]
server = { choices = ["lab", "demo"] }
items = [{ itemid = 20004 }]
from = "2 hours ago"
to = "now"
"""

TIMES = ("--from", "2 hours ago", "--to", "now")
NAME = "demo1_particles_20251009T065820Z--20251009T085820Z"


@pytest.fixture
def configured(servers: Servers, user_config: Path) -> Servers:
    user_config.write_text(CONFIG + TEMPLATES)
    return servers


def read(path: Path) -> tuple[dict[str, Any], list[list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    metadata = json.loads(
        "".join(line[2:] for line in lines[1:] if line.startswith("#"))
    )
    data = "".join(line for line in lines if not line.startswith("#"))
    delimiter = "," if path.suffix == ".csv" else "\t"
    return metadata, list(csv.reader(io.StringIO(data), delimiter=delimiter))


def ok(result: Result) -> Result:
    assert result.exit_code == 0, result.output
    return result


class TestExplainAndDryRun:
    def test_explain(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "particles", "--explain", "--jobs", "2"))
        rows = {row[0]: row[1:] for row in table_rows(result.stdout)}
        assert rows["from"][1] == "template 'particles'"
        assert rows["jobs"] == ["2", "command line (--jobs)"]
        assert rows["server"] == ["demo", "template 'particles'"]
        assert rows["particles"] == ["host=demo1,name=Particles *"]
        assert configured.by_url == {}  # nothing was contacted

    def test_dry_run(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "logs", "--dry-run", "--resolution", "1h"))
        assert table_rows(result.stdout) == [
            [
                "20005",
                "demo1",
                "Syslog",
                "log",
                "logs",
                "skip: log items have no hourly trends",
            ],
            [
                "20006",
                "demo1",
                "Agent version",
                "char",
                "logs",
                "skip: char items have no hourly trends",
            ],
        ]
        assert list(Path().glob("*.tsv")) == []


class TestSaving:
    def test_explicit_file(self, configured: Servers, tmp_path: Path) -> None:
        path = tmp_path / "out.csv"
        result = ok(run("fetch", "-t", "particles", "-o", str(path)))
        metadata, rows = read(path)
        assert rows[0][:2] == [
            "demo1: Particles 0.3um time",
            "demo1: Particles 0.3um value",
        ]
        assert rows[1][1] == "1.5"
        assert metadata["query"]["templates"] == ["particles"]
        assert f"saved to {path}." in result.stderr
        assert "Fetched 2 items (4 points) from 'demo'" in result.stderr

    def test_automatic_name(self, configured: Servers) -> None:
        ok(run("fetch", "-t", "particles", "-o", "--format", "tsv"))
        assert Path(f"{NAME}.tsv").exists()

    def test_automatic_name_in_directory(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        directory = tmp_path / "data"
        ok(run("fetch", "-t", "particles", "-o", f"{directory}/", "--format", "csv"))
        assert (directory / f"{NAME}.csv").exists()

    def test_existing_directory(self, configured: Servers, tmp_path: Path) -> None:
        ok(run("fetch", "-t", "particles", "-o", str(tmp_path), "--format", "csv"))
        assert (tmp_path / f"{NAME}.csv").exists()

    def test_output_dir_and_pattern(self, configured: Servers, tmp_path: Path) -> None:
        ok(
            run(
                "fetch",
                "-t",
                "particles",
                "-o",
                "--format",
                "tsv",
                "--output-dir",
                str(tmp_path),
                "--output-pattern",
                "{server}-{resolution}",
            )
        )
        assert (tmp_path / "demo-raw.tsv").exists()

    def test_automatic_name_never_overwrites(self, configured: Servers) -> None:
        Path(f"{NAME}.tsv").write_text("keep me")
        result = ok(run("fetch", "-t", "particles", "-o", "--format", "tsv"))
        assert Path(f"{NAME}.tsv").read_text() == "keep me"
        assert Path(f"{NAME}-2.tsv").exists()
        assert "already exists; saving to" in result.stderr

    def test_several_formats(self, configured: Servers, user_config: Path) -> None:
        user_config.write_text(
            CONFIG
            + TEMPLATES
            + '[defaults]\nformat = { choices = ["tsv", "csv"], select = "all" }\n'
        )
        ok(run("fetch", "-t", "particles", "-o"))
        assert Path(f"{NAME}.tsv").exists()
        assert Path(f"{NAME}.csv").exists()

    def test_custom_items_and_name(self, configured: Servers) -> None:
        ok(run("fetch", "-s", "demo", "-i", "20004", *TIMES, "-o", "--format", "tsv"))
        assert list(Path().glob("demo1___custom_*.tsv"))

    def test_trends(self, configured: Servers, tmp_path: Path) -> None:
        path = tmp_path / "trends.tsv"
        ok(
            run(
                "fetch",
                "-s",
                "demo",
                "-i",
                "20001",
                "--resolution",
                "1h",
                "--agg",
                "avg",
                "--agg",
                "count",
                "--from",
                "2 hours ago",
                "--to",
                "2025-10-09T12:00:00Z",
                "-o",
                str(path),
            )
        )
        assert read(path)[1][:3] == [
            [
                "demo1: Particles 0.3um time",
                "demo1: Particles 0.3um avg",
                "demo1: Particles 0.3um count",
            ],
            ["2025-10-09T09:00:00+01:00", "2.0", "60"],
            ["2025-10-09T10:00:00+01:00", "2.5", "58"],
        ]

    def test_timezone(self, configured: Servers, tmp_path: Path) -> None:
        path = tmp_path / "out.tsv"
        ok(
            run(
                "fetch",
                "-s",
                "demo",
                "-i",
                "20004",
                *TIMES,
                "--timezone",
                "UTC",
                "-o",
                str(path),
            )
        )
        assert read(path)[1][1][0] == "2025-10-09T08:00:00+00:00"


class TestFormat:
    def test_format_overrides_extension(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.csv"
        result = ok(run("fetch", "-t", "particles", "-o", str(path), "--format", "tsv"))
        assert "Saving tsv data to" in result.stderr
        assert "\t" in path.read_text(encoding="utf-8").splitlines()[-1]

    def test_unknown_extension_uses_config(
        self, configured: Servers, tmp_path: Path, user_config: Path
    ) -> None:
        user_config.write_text(CONFIG + TEMPLATES + '[defaults]\nformat = "csv"\n')
        path = tmp_path / "out.dat"
        ok(run("fetch", "-t", "particles", "-o", str(path)))
        assert "," in path.read_text(encoding="utf-8").splitlines()[-1]

    def test_asked_for(self, configured: Servers, interactive: None) -> None:
        ok(run("fetch", "-t", "particles", "-o", input="4\n"))
        assert Path(f"{NAME}.csv").exists()

    def test_missing_non_interactive(self, configured: Servers) -> None:
        result = run("fetch", "-t", "particles", "-o")
        assert result.exit_code == EXIT_ERROR
        assert "Pass --format" in result.stderr


class TestNoOutputOption:
    def test_non_interactive_doesnt_save(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "particles"))
        assert "The data wasn't saved; pass --output" in result.stderr
        assert list(Path().glob("*.tsv")) == []

    def test_interactive_save(self, configured: Servers, interactive: None) -> None:
        result = ok(run("fetch", "-t", "particles", "--format", "tsv", input="y\n"))
        assert f"Save the data to {NAME}.tsv?" in result.stderr
        assert Path(f"{NAME}.tsv").exists()

    def test_interactive_decline(self, configured: Servers, interactive: None) -> None:
        ok(run("fetch", "-t", "particles", "--format", "tsv", input="n\n"))
        assert list(Path().glob("*.tsv")) == []


class TestExistingFile:
    @pytest.fixture
    def existing(self, tmp_path: Path) -> Path:
        path = tmp_path / "out.tsv"
        path.write_text("old")
        return path

    def test_overwrite_flag(self, configured: Servers, existing: Path) -> None:
        ok(run("fetch", "-t", "particles", "-o", str(existing), "--overwrite"))
        assert existing.read_text(encoding="utf-8") != "old"

    def test_non_interactive_saves_alongside(
        self, configured: Servers, existing: Path
    ) -> None:
        result = ok(run("fetch", "-t", "particles", "-o", str(existing)))
        assert existing.read_text() == "old"
        assert existing.with_name("out-2.tsv").exists()
        assert "Pass --overwrite to replace it" in result.stderr

    @pytest.mark.parametrize(
        ("answer", "old_kept", "new_file"), [("1", False, False), ("2", True, True)]
    )
    def test_interactive(
        self,
        configured: Servers,
        existing: Path,
        interactive: None,
        answer: str,
        old_kept: bool,
        new_file: bool,
    ) -> None:
        ok(run("fetch", "-t", "particles", "-o", str(existing), input=f"{answer}\n"))
        assert (existing.read_text() == "old") is old_kept
        assert existing.with_name("out-2.tsv").exists() is new_file

    def test_interactive_cancel(
        self, configured: Servers, existing: Path, interactive: None
    ) -> None:
        result = run("fetch", "-t", "particles", "-o", str(existing), input="3\n")
        assert result.exit_code == EXIT_INTERRUPTED
        assert existing.read_text() == "old"


class TestItems:
    def test_template_and_command_line_share_items(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.tsv"
        ok(
            run(
                "fetch",
                "-t",
                "uptime",
                "--to",
                "now",
                "-i",
                "host=demo1,name=Particles *",
                "-o",
                str(path),
            )
        )
        metadata, _ = read(path)
        requests = {item["itemid"]: item["requests"] for item in metadata["items"]}
        assert requests == {
            20001: ["uptime", "__custom"],
            20002: ["__custom"],
            20004: ["uptime"],
        }

    @pytest.mark.parametrize(
        ("option", "reason"),
        [
            ("-t", "Only one template can be fetched at a time."),
            ("-s", "zabgrab fetches from one server at a time."),
        ],
    )
    def test_only_one_template_and_server(
        self, configured: Servers, option: str, reason: str
    ) -> None:
        values = ["particles", "uptime"] if option == "-t" else ["demo", "lab"]
        result = run("fetch", option, values[0], option, values[1])
        assert result.exit_code == 2
        long = "--template" if option == "-t" else "--server"
        assert f"{long} can only be given once: {reason}" in result.stderr

    def test_nothing_matches(self, configured: Servers) -> None:
        result = run("fetch", "-s", "demo", "-i", "name=nothing", *TIMES)
        assert result.exit_code == EXIT_ERROR
        assert "Nothing matched the items of the command line." in result.stderr
        assert "No items to fetch." in result.stderr

    def test_picker(
        self, configured: Servers, interactive: None, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.tsv"
        result = ok(
            run(
                "fetch",
                "-s",
                "demo",
                *TIMES,
                "-o",
                str(path),
                input="nope\nuptime\n1\n",
            )
        )
        assert "No items match 'nope'" in result.stderr
        assert [item["itemid"] for item in read(path)[0]["items"]] == [20004]

    def test_picker_non_interactive(self, configured: Servers) -> None:
        result = run("fetch", "-s", "demo", *TIMES)
        assert result.exit_code == EXIT_ERROR
        assert "Pass items with --item" in result.stderr

    def test_picker_rejects_empty_search(
        self, configured: Servers, interactive: None
    ) -> None:
        result = run(
            "fetch",
            "-s",
            "demo",
            *TIMES,
            "-o",
            input=" \nuptime\n1\n",
        )
        assert "Please enter a search" in result.stderr


class TestMaxItems:
    ARGS = ("fetch", "-s", "demo", "-i", "host=demo1", *TIMES, "--max-items", "2")

    def test_non_interactive_fetches_all(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        result = ok(run(*self.ARGS, "-o", str(tmp_path / "x.tsv")))
        assert (
            "5 items match the command line, more than max_items (2)." in result.stderr
        )
        assert "Fetched 5 items" in result.stderr

    def test_yes(self, configured: Servers) -> None:
        result = ok(run(*self.ARGS, "--yes"))
        assert "more than max_items" not in result.stderr

    def test_choose(
        self, configured: Servers, interactive: None, tmp_path: Path
    ) -> None:
        path = tmp_path / "x.tsv"
        ok(run(*self.ARGS, "-o", str(path), input="2\n1,3\n"))
        assert len(read(path)[0]["items"]) == 2

    def test_all(self, configured: Servers, interactive: None) -> None:
        result = ok(run(*self.ARGS, "--format", "tsv", input="\nn\n"))
        assert "Fetched 5 items" in result.stderr

    def test_cancel(self, configured: Servers, interactive: None) -> None:
        assert run(*self.ARGS, input="3\n").exit_code == EXIT_INTERRUPTED


class TestProblems:
    def test_partial_failure(self, configured: Servers) -> None:
        configured.by_url["https://demo.example.org"] = zabbix = seeded()
        zabbix.failing_items.add(20002)
        result = run("fetch", "-t", "particles", "-o", "--format", "tsv")
        assert result.exit_code == EXIT_PARTIAL
        assert "Couldn't fetch demo1: Particles 0.5um (20002)." in result.stderr
        assert "Item broken." in result.stderr
        assert "Fetched 1 item (3 points)" in result.stderr

    def test_everything_failed(self, configured: Servers) -> None:
        configured.by_url["https://demo.example.org"] = zabbix = seeded()
        zabbix.failing_items |= {20001, 20002}
        result = run("fetch", "-t", "particles")
        assert result.exit_code == EXIT_ERROR
        assert "Nothing was fetched." in result.stderr

    def test_skipped_items_are_reported(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "logs", "-i", "20001", "--resolution", "1h"))
        assert (
            "Skipped demo1: Syslog (20005): log items have no hourly trends."
            in result.stderr
        )

    def test_server_choice_is_asked(
        self, configured: Servers, interactive: None
    ) -> None:
        result = ok(run("fetch", "-t", "either", "--format", "tsv", input="2\nn\n"))
        assert "Which Zabbix server?" in result.stderr
        assert "from 'demo'" in result.stderr

    def test_server_choice_non_interactive(self, configured: Servers) -> None:
        result = run("fetch", "-t", "either")
        assert result.exit_code == EXIT_ERROR
        assert "Which Zabbix server?" in result.stderr
        assert "Pass --server NAME." in result.stderr

    def test_server_within_the_choice(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "either", "-s", "demo"))
        assert "overrides" not in result.stderr
        assert "from 'demo'" in result.stderr

    def test_invalid_option_value(self, configured: Servers) -> None:
        result = run("fetch", "-t", "particles", "--timezone", "Mars/Olympus")
        assert result.exit_code == 2
        assert "unknown timezone 'Mars/Olympus'" in result.stderr

    def test_server_override_is_logged(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "particles", "-s", "lab"))
        assert (
            "--server lab overrides the server of template 'particles'."
            in result.stderr
        )
        assert "from 'lab'" in result.stderr


class TestDisplayDetails:
    def test_explain_choices_and_unknowns(
        self, configured: Servers, user_config: Path
    ) -> None:
        user_config.write_text(
            CONFIG
            + TEMPLATES
            + """
[templates.choosy]
server = { choices = ["demo", "lab"] }
items = [
    { host = { choices = ["demo1", "demo2"] } },
    { host = { choices = ["demo1"], select = "first" }, key = "k" },
]
"""
        )
        result = ok(run("fetch", "-t", "choosy", "-i", "1", *TIMES, "--explain"))
        rows = table_rows(result.stdout)
        assert ["server", "demo or lab (asked)", "template 'choosy'"] in rows
        assert ["choosy", "host=demo1 or demo2 (prompt)"] in rows
        assert ["", "host=demo1 (first),key=k"] in rows
        assert ["command line", "1"] in rows

    def test_explain_nothing_given(self, configured: Servers) -> None:
        result = ok(run("fetch", *TIMES, "--explain"))
        rows = table_rows(result.stdout)
        assert ["server", "(asked)", ""] in rows
        assert ["command line", "(asked)"] in rows

    def test_picker_shows_limit(
        self, configured: Servers, interactive: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("zabgrab.services.collect.PICKER_LIMIT", 1)
        result = ok(
            run(
                "fetch",
                "-s",
                "demo",
                *TIMES,
                "--format",
                "tsv",
                input="particles\n1\nn\n",
            )
        )
        assert "Showing the first 1 matches." in result.stderr


class TestStructured:
    @pytest.mark.parametrize("suffix", [".mat", ".h5"])
    def test_save_and_append(
        self, configured: Servers, tmp_path: Path, suffix: str
    ) -> None:
        path = tmp_path / f"data{suffix}"
        result = ok(run("fetch", "-t", "particles", "-o", str(path)))
        assert f"saved to {path} (group fetch_0001)." in result.stderr
        result = ok(
            run("fetch", "-t", "logs", "-o", str(path), "--append", "--group", "logs")
        )
        assert "(group logs)" in result.stderr
        ok(run("fetch", "-t", "particles", "-o", str(path), "--append", "--in-place"))
        groups = read_fetches(path)
        assert [g.name for g in groups] == ["fetch_0001", "logs", "fetch_0002"]
        assert [s.item.itemid for s in groups[1].series] == [20005, 20006]
        assert groups[0].metadata["query"]["templates"] == ["particles"]
        assert groups[0].t_to == groups[0].t_from + 7200

    def test_trend_end_is_the_last_complete_hour(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "trends.h5"
        ok(
            run(
                "fetch",
                "-s",
                "demo",
                "-i",
                "20001",
                "--resolution",
                "1h",
                *TIMES,
                "-o",
                str(path),
            )
        )
        (group,) = read_fetches(path)
        # "now" is 08:58:20 UTC, so the last complete hour ended at 08:00:00.
        assert group.t_to == T0 - 1

    def test_existing_file_offers_append(
        self, configured: Servers, interactive: None, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.h5"
        ok(run("fetch", "-t", "particles", "-o", str(path)))
        result = ok(run("fetch", "-t", "particles", "-o", str(path), input="\n"))
        assert "1. Add the data to it as a new group" in result.stderr
        assert group_names(path) == ["fetch_0001", "fetch_0002"]

    def test_existing_file_non_interactive(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.mat"
        ok(run("fetch", "-t", "particles", "-o", str(path)))
        result = ok(run("fetch", "-t", "particles", "-o", str(path)))
        assert "or --append to add to it." in result.stderr
        assert (tmp_path / "data-2.mat").exists()

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (
                ["-o", "x.tsv", "--append"],
                "Only .mat and .h5 files can be appended to.",
            ),
            (["--append"], "--append, --group and --in-place need --output"),
            (["-o", "--append"], "--append, --group and --in-place need --output"),
            (["-o", "x.h5", "--append", "--overwrite"], "can't be used together"),
            (["-o", "x.h5", "--group", "g"], "only apply with --append"),
        ],
    )
    def test_invalid_combinations(
        self, configured: Servers, args: list[str], message: str
    ) -> None:
        result = run("fetch", "-t", "particles", *args)
        assert result.exit_code == EXIT_ERROR
        assert message in result.stderr
        assert configured.by_url == {}  # checked before downloading


class TestLargeText:
    ARGS = ("fetch", "-t", "logs")

    @pytest.fixture
    def low_threshold(self, configured: Servers, user_config: Path) -> Servers:
        user_config.write_text(
            CONFIG + TEMPLATES + "[defaults]\nmat_text_warn_entries = 2\n"
        )
        return configured

    def test_non_interactive_warns_and_continues(
        self, low_threshold: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "logs.mat"
        result = ok(run(*self.ARGS, "-o", str(path)))
        assert (
            "would hold 5 text values, more than mat_text_warn_entries (2)"
            in result.stderr
        )
        assert "--allow-large-text" in result.stderr
        assert not large_text_accepted(path)

    def test_allowed(self, low_threshold: Servers, tmp_path: Path) -> None:
        path = tmp_path / "logs.mat"
        result = ok(run(*self.ARGS, "-o", str(path), "--allow-large-text"))
        assert "text values" not in result.stderr
        assert large_text_accepted(path)
        # Remembered for later appends.
        result = ok(run(*self.ARGS, "-o", str(path), "--append"))
        assert "text values" not in result.stderr

    def test_h5_is_never_guarded(self, low_threshold: Servers, tmp_path: Path) -> None:
        result = ok(run(*self.ARGS, "-o", str(tmp_path / "logs.h5")))
        assert "text values" not in result.stderr

    @pytest.mark.parametrize(
        ("answer", "files", "items"),
        [
            ("1", ["logs.mat"], [20005, 20006]),
            ("2", ["logs.h5"], [20005, 20006]),
            ("3", ["logs.mat"], []),
        ],
    )
    def test_interactive_choices(
        self,
        low_threshold: Servers,
        interactive: None,
        tmp_path: Path,
        answer: str,
        files: list[str],
        items: list[int],
    ) -> None:
        result = run(
            *self.ARGS,
            "-i",
            "host=demo1,name=Particles *",
            "-o",
            str(tmp_path / "logs.mat"),
            input=f"{answer}\n",
        )
        assert result.exit_code == 0, result.output
        assert sorted(p.name for p in tmp_path.glob("logs.*")) == files
        (group,) = read_fetches(tmp_path / files[0])
        assert [s.item.itemid for s in group.series if s.item.itemid > 20004] == items
        if not items:
            assert "Leaving out 2 text item(s)." in result.stderr

    def test_interactive_cancel(
        self, low_threshold: Servers, interactive: None, tmp_path: Path
    ) -> None:
        result = run(*self.ARGS, "-o", str(tmp_path / "logs.mat"), input="4\n")
        assert result.exit_code == EXIT_INTERRUPTED
        assert list(tmp_path.glob("logs.*")) == []

    def test_existing_entries_count(
        self, low_threshold: Servers, user_config: Path, tmp_path: Path
    ) -> None:
        user_config.write_text(
            CONFIG + TEMPLATES + "[defaults]\nmat_text_warn_entries = 5\n"
        )
        path = tmp_path / "logs.mat"
        assert "text values" not in ok(run(*self.ARGS, "-o", str(path))).stderr
        result = ok(run(*self.ARGS, "-o", str(path), "--append"))
        assert "would hold 10 text values" in result.stderr

    def test_disabled(
        self, configured: Servers, user_config: Path, tmp_path: Path
    ) -> None:
        user_config.write_text(
            CONFIG + TEMPLATES + "[defaults]\nmat_text_warn_entries = 0\n"
        )
        result = ok(run(*self.ARGS, "-o", str(tmp_path / "logs.mat")))
        assert "text values" not in result.stderr


def test_append_with_configured_delimited_format(
    configured: Servers, user_config: Path, tmp_path: Path
) -> None:
    user_config.write_text(CONFIG + TEMPLATES + '[defaults]\nformat = "tsv"\n')
    result = run("fetch", "-t", "particles", "-o", str(tmp_path / "data"), "--append")
    assert result.exit_code == EXIT_ERROR
    assert "Only .mat and .h5 files can be appended to." in result.stderr
