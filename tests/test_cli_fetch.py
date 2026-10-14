"""``zabgrab fetch`` end to end, against fake servers."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from zabgrab.errors import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_PARTIAL

from .cli_support import CONFIG, Servers, run, table_rows
from .fakezabbix import TOKEN, seeded

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
server = { choices = ["lab", "demo"], select = "first" }
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
        assert rows["particles"] == ["demo", "host=demo1,name=Particles *"]
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
        assert "\t" in path.read_text().splitlines()[-1]

    def test_unknown_extension_uses_config(
        self, configured: Servers, tmp_path: Path, user_config: Path
    ) -> None:
        user_config.write_text(CONFIG + TEMPLATES + '[defaults]\nformat = "csv"\n')
        path = tmp_path / "out.dat"
        ok(run("fetch", "-t", "particles", "-o", str(path)))
        assert "," in path.read_text().splitlines()[-1]

    def test_not_available_yet(self, configured: Servers, tmp_path: Path) -> None:
        result = run("fetch", "-t", "particles", "-o", str(tmp_path / "out.mat"))
        assert result.exit_code == EXIT_ERROR
        assert "Saving as mat isn't available yet." in result.stderr

    def test_asked_for(self, configured: Servers, interactive: None) -> None:
        ok(run("fetch", "-t", "particles", "-o", input="2\n"))
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
        assert existing.read_text() != "old"

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
    def test_stacked_templates_share_items(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.tsv"
        ok(run("fetch", "-t", "particles", "-t", "uptime", "-o", str(path)))
        metadata, _ = read(path)
        requests = {item["itemid"]: item["requests"] for item in metadata["items"]}
        assert requests == {
            20001: ["particles", "uptime"],
            20002: ["particles"],
            20004: ["uptime"],
        }

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
        result = ok(run("fetch", "-t", "logs", "-t", "particles", "--resolution", "1h"))
        assert (
            "Skipped demo1: Syslog (20005): log items have no hourly trends."
            in result.stderr
        )

    def test_failover(self, configured: Servers) -> None:
        configured.down.add("https://lab.example.org")
        result = ok(run("fetch", "-t", "either"))
        assert "Trying the next server." in result.stderr
        assert "from 'demo'" in result.stderr

    def test_failover_all_down(self, configured: Servers) -> None:
        configured.down |= {"https://lab.example.org", "https://demo.example.org"}
        assert run("fetch", "-t", "either").exit_code == EXIT_ERROR

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

    def test_several_servers_in_summary(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        configured.secrets.tokens["https://other.example.org"] = TOKEN
        path = tmp_path / "out.tsv"
        result = ok(
            run(
                "fetch",
                "-t",
                "particles",
                "-s",
                "demo",
                "-s",
                "https://other.example.org",
                "-o",
                str(path),
            )
        )
        assert "from 'demo', https://other.example.org" in result.stderr
        assert read(path)[1][0][0].startswith("demo/demo1")


class TestDisplayDetails:
    def test_explain_choices_and_unknowns(
        self, configured: Servers, user_config: Path
    ) -> None:
        user_config.write_text(
            CONFIG
            + TEMPLATES
            + """
[templates.choosy]
server = { choices = ["demo", "lab"], select = "all" }
items = [
    { host = { choices = ["demo1", "demo2"] } },
    { host = { choices = ["demo1"], select = "first" }, key = "k" },
]
"""
        )
        result = ok(run("fetch", "-t", "choosy", "-i", "1", *TIMES, "--explain"))
        rows = table_rows(result.stdout)
        assert ["choosy", "demo or lab (all)", "host=demo1 or demo2 (prompt)"] in rows
        assert ["", "", "host=demo1 (first),key=k"] in rows
        assert ["command line", "(asked)", "1"] in rows

    def test_explain_nothing_given(self, configured: Servers) -> None:
        result = ok(run("fetch", *TIMES, "--explain"))
        assert ["command line", "(asked)", "(asked)"] in table_rows(result.stdout)

    def test_dry_run_several_servers(self, configured: Servers) -> None:
        result = ok(
            run("fetch", "-s", "demo", "-s", "lab", "-i", "20004", *TIMES, "--dry-run")
        )
        assert [row[0] for row in table_rows(result.stdout)] == ["demo", "lab"]

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
