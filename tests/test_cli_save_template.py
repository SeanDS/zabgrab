"""``zabgrab fetch --save-template`` and the "Next time" hint, end to end."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from zabgrab.errors import EXIT_ERROR

from .cli_support import CONFIG, Servers, run, table_rows

TEMPLATES = """
# Particle counters.
[templates.particles]
server = "demo"   # the DEMO server
items = [{ host = "demo1", name = "Particles *" }]
from = "2 hours ago"
to = "now"

[templates.either]
server = { choices = ["lab", "demo"] }
items = [{ itemid = 20004 }]
from = "2 hours ago"
to = "now"
"""

TIMES = ("--from", "2 hours ago", "--to", "now")


@pytest.fixture
def configured(servers: Servers, user_config: Path) -> Servers:
    user_config.write_text(CONFIG + TEMPLATES, encoding="utf-8")
    return servers


def ok(result: Result) -> Result:
    assert result.exit_code == 0, result.output
    return result


def saved(path: Path, name: str) -> dict[str, Any]:
    template: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))[
        "templates"
    ][name]
    return template


def item_ids(result: Result) -> list[str]:
    return [row[0] for row in table_rows(result.stdout)]


class TestSaveTemplate:
    def test_round_trip(self, configured: Servers, user_config: Path) -> None:
        before = user_config.read_text(encoding="utf-8")
        args = ("fetch", "-s", "demo", "-i", "host=demo1,name=Particles *", *TIMES)
        result = ok(run(*args, "--dry-run", "--save-template", "mine"))

        assert f"Saved template 'mine' to {user_config}:" in result.stderr
        assert "[templates.mine]" in result.stderr
        assert "Run it again with `zabgrab fetch -t mine`." in result.stderr
        # The rest of the file, comments included, is untouched.
        after = user_config.read_text(encoding="utf-8")
        assert after.startswith(before)
        assert saved(user_config, "mine") == {
            "server": "demo",
            "items": [{"host": "demo1", "name": "Particles *"}],
            "from": "2 hours ago",
            "to": "now",
        }
        again = ok(run("fetch", "-t", "mine", "--dry-run"))
        assert item_ids(again) == item_ids(result) == ["20001", "20002"]

    def test_from_a_template_with_overrides(
        self, configured: Servers, user_config: Path
    ) -> None:
        ok(
            run(
                "fetch",
                "-t",
                "particles",
                "--to",
                "1 hour ago",
                "--resolution",
                "1h",
                "--agg",
                "max",
                "--explain",
                "--save-template",
                "hourly",
            )
        )
        assert saved(user_config, "hourly") == {
            "server": "demo",
            "items": [{"host": "demo1", "name": "Particles *"}],
            "from": "2 hours ago",
            "to": "1 hour ago",
            "resolution": "1h",
            "agg": ["max"],
        }

    def test_existing_template_needs_force(
        self, configured: Servers, user_config: Path
    ) -> None:
        args = ("fetch", "-s", "lab", "-i", "20004", *TIMES, "--explain")
        result = run(*args, "--save-template", "particles")
        assert result.exit_code == EXIT_ERROR
        assert f"Template 'particles' already exists in {user_config}." in (
            result.stderr
        )
        assert "Pass --force to replace it" in result.stderr
        assert "Settings" not in result.stdout  # stopped before doing anything

        ok(run(*args, "--save-template", "particles", "--force"))
        assert saved(user_config, "particles")["server"] == "lab"
        assert "# Particle counters." in user_config.read_text(encoding="utf-8")

    def test_existing_template_in_another_file(
        self, configured: Servers, tmp_path: Path
    ) -> None:
        result = run(
            "fetch",
            "-t",
            "particles",
            "--explain",
            "--save-template",
            "either",
            "--save-to",
            str(tmp_path / "other.toml"),
        )
        assert result.exit_code == EXIT_ERROR
        assert "Template 'either' already exists in" in result.stderr

    @pytest.mark.parametrize(
        ("save_to", "path"),
        [
            ("project", Path("zabgrab.toml")),
            ("system", Path("etc/zabgrab/config.toml")),
            ("custom/file.toml", Path("custom/file.toml")),
        ],
    )
    def test_save_to(
        self, configured: Servers, tmp_path: Path, save_to: str, path: Path
    ) -> None:
        args = ("fetch", "-t", "particles", "--explain", "--save-template", "copy")
        ok(run(*args, "--save-to", save_to))
        assert saved(tmp_path / path, "copy")["server"] == "demo"

    def test_invalid_name(self, configured: Servers) -> None:
        result = run("fetch", "-t", "particles", "--save-template", "_x")
        assert result.exit_code == 2
        assert "Invalid value for '--save-template': '_x'" in result.stderr

    @pytest.mark.parametrize("option", [["--save-to", "user"], ["--force"]])
    def test_options_need_save_template(
        self, configured: Servers, option: list[str]
    ) -> None:
        result = run("fetch", "-t", "particles", *option)
        assert result.exit_code == 2
        assert f"{option[0]} only applies with --save-template." in result.stderr

    def test_server_choice_is_kept(
        self, configured: Servers, user_config: Path
    ) -> None:
        ok(run("fetch", "-t", "either", "--explain", "--save-template", "copy"))
        assert saved(user_config, "copy")["server"] == {
            "choices": ["lab", "demo"],
            "select": "prompt",
        }

    def test_server_chosen_is_saved(
        self, configured: Servers, user_config: Path, interactive: None
    ) -> None:
        ok(
            run(
                "fetch", "-t", "either", "--dry-run", "--save-template", "copy",
                input="2\n",
            )
        )  # fmt: skip
        assert saved(user_config, "copy")["server"] == "demo"

    def test_answers_are_saved(
        self, configured: Servers, user_config: Path, interactive: None
    ) -> None:
        # Pick the one item matching "uptime", and give the end time at a prompt.
        result = ok(
            run(
                "fetch", "-s", "demo", "--from", "2 hours ago", "--format", "tsv",
                "--save-template", "uptime", input="now\nuptime\n1\nn\n",
            )
        )  # fmt: skip
        assert "Next time" not in result.stderr
        assert saved(user_config, "uptime") == {
            "server": "demo",
            "items": [{"name": "uptime"}],
            "from": "2 hours ago",
            "to": "now",
            "format": "tsv",
        }

    def test_items_picked_from_several(
        self, configured: Servers, user_config: Path, interactive: None
    ) -> None:
        ok(
            run(
                "fetch", "-s", "demo", *TIMES, "--dry-run",
                "--save-template", "some", input="particles*\n1,3\n",
            )
        )  # fmt: skip
        assert saved(user_config, "some")["items"] == [
            {"itemid": 20001},
            {"itemid": 20002},
        ]


class TestNextTime:
    def test_not_shown_without_answers(self, configured: Servers) -> None:
        result = ok(run("fetch", "-t", "particles", "--dry-run"))
        assert "Next time" not in result.stderr

    def test_picked_items_and_chosen_format(
        self, configured: Servers, interactive: None
    ) -> None:
        result = ok(run("fetch", "-s", "demo", *TIMES, "-o", input="uptime\n1\n4\n"))
        assert (
            "Next time: zabgrab --no-color fetch -s demo --from '2 hours ago' --to "
            "now -o --format csv --item name=uptime" in result.stderr
        )
        assert "Or add --save-template NAME to save it as a template." in (
            result.stderr
        )

    def test_corrected_template_and_server(
        self, configured: Servers, interactive: None
    ) -> None:
        result = ok(
            run(
                "fetch", "-t", "partciles", "-s", "dmeo", "--dry-run",
                input="y\ny\n",
            )
        )  # fmt: skip
        assert (
            "Next time: zabgrab --no-color fetch --dry-run --template particles "
            "--server demo" in result.stderr
        )

    def test_server_asked_for(self, configured: Servers, interactive: None) -> None:
        result = ok(run("fetch", "-i", "20004", *TIMES, "--dry-run", input="1\n"))
        assert "--dry-run --server demo" in result.stderr

    def test_fetch_all_despite_max_items(
        self, configured: Servers, interactive: None
    ) -> None:
        result = ok(
            run(
                "fetch", "-s", "demo", "-i", "host=demo1", *TIMES, "--max-items",
                "2", "--dry-run", input="\n",
            )
        )  # fmt: skip
        assert "--max-items 2 --dry-run --yes" in result.stderr

    def test_items_chosen_from_a_template(
        self, configured: Servers, interactive: None
    ) -> None:
        result = ok(
            run(
                "fetch", "-t", "particles", "--max-items", "1", "--dry-run",
                input="2\n1\n",
            )
        )  # fmt: skip
        assert (
            "The items chosen from template 'particles' can't be given on the "
            "command line." in result.stderr
        )

    def test_format_choice_in_a_template(
        self, configured: Servers, user_config: Path, interactive: None
    ) -> None:
        user_config.write_text(
            CONFIG
            + TEMPLATES
            + '[defaults]\nformat = { choices = ["csv", "tsv"], select = "prompt" }\n',
            encoding="utf-8",
        )
        result = ok(run("fetch", "-t", "particles", "--dry-run", input="1,2\n"))
        assert "The formats chosen (csv, tsv) can't be given" in result.stderr
