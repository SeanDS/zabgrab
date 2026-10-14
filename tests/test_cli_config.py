"""``zabgrab config`` commands and global CLI behaviour."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from zabgrab import __version__
from zabgrab.cli import cli
from zabgrab.errors import EXIT_ERROR

from .fakes import FakeEditor


@pytest.fixture
def user_config(fake_home: Path) -> Path:
    return fake_home / ".config" / "zabgrab" / "config.toml"


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch) -> FakeEditor:
    fake = FakeEditor()
    monkeypatch.setattr("zabgrab.cli.ClickEditor", lambda: fake)
    return fake


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zabgrab.cli.is_interactive", lambda *args, **kwargs: True)


def run(*args: str, input: str | None = None) -> Result:
    return CliRunner(env={"COLUMNS": "200"}).invoke(
        cli, ["--no-color", *args], input=input
    )


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_version() -> None:
    result = run("--version")
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_config_command() -> None:
    result = run("-h")
    assert result.exit_code == 0
    assert "config" in result.stdout


class TestPaths:
    def test_lists_layers(self, user_config: Path) -> None:
        write(user_config, "")
        result = run("config", "paths")
        assert result.exit_code == 0
        assert result.stderr == ""
        lines = result.stdout.splitlines()
        user_line = next(line for line in lines if "│ user" in line)
        assert str(user_config) in user_line
        assert "loaded" in user_line
        assert "not found" in next(line for line in lines if "│ system" in line)

    def test_works_with_invalid_config(self, user_config: Path) -> None:
        write(user_config, "not toml [")
        assert run("config", "paths").exit_code == 0

    def test_isolated(self, user_config: Path) -> None:
        write(user_config, "")
        result = run("--isolated", "config", "paths")
        assert "skipped (--isolated)" in result.stdout


class TestShow:
    def test_round_trips_as_toml(self, user_config: Path) -> None:
        write(
            user_config,
            "[defaults]\njobs = 2\n"
            '[servers.demo]\nurl = "https://demo.example.org"\n'
            '[templates.t]\nserver = { choices = ["demo"], select = "all" }\n'
            'items = [{ host = "h", name = "CPU *" }, { itemid = 7 }]\n'
            'from = "2 days ago"\n',
        )
        result = run("config", "show")
        assert result.exit_code == 0, result.output

        shown = tomllib.loads(result.stdout)
        assert shown["defaults"]["jobs"] == 2
        assert shown["defaults"]["format"] == "mat"
        assert shown["servers"]["demo"]["url"] == "https://demo.example.org"
        template = shown["templates"]["t"]
        assert template["server"] == {"choices": ["demo"], "select": "all"}
        assert template["items"] == [{"host": "h", "name": "CPU *"}, {"itemid": 7}]
        assert template["from"] == "2 days ago"

    def test_sources(self, user_config: Path) -> None:
        write(user_config, "[defaults]\njobs = 2\n[templates.t]\n")
        result = run("config", "show", "--sources")
        assert f"jobs = 2 # from {user_config}" in result.stdout
        assert 'format = "mat" # from built-in' in result.stdout
        assert f"[templates.t]\n# from {user_config}" in result.stdout

    def test_explicit_config_via_env(self, tmp_path: Path) -> None:
        extra = write(tmp_path / "extra.toml", "[defaults]\njobs = 3\n")
        result = CliRunner(env={"ZABGRAB_CONFIG": str(extra)}).invoke(
            cli, ["config", "show"]
        )
        assert tomllib.loads(result.stdout)["defaults"]["jobs"] == 3

    def test_invalid_config_non_interactive(self, user_config: Path) -> None:
        write(user_config, "[defaults]\njobs = 0\n")
        result = run("config", "show")
        assert result.exit_code == EXIT_ERROR
        assert result.stdout == ""
        assert f"Error: Invalid config file {user_config}." in result.stderr
        assert "defaults.jobs:" in result.stderr
        assert "Fix: Fix it with `zabgrab config edit --target user`" in result.stderr

    def test_invalid_config_interactive_repair(
        self, user_config: Path, editor: FakeEditor, interactive: None
    ) -> None:
        write(user_config, "[defaults]\njobs = 0\n")
        editor.contents.append("[defaults]\njobs = 4\n")
        result = run("config", "show", input="y\n")
        assert result.exit_code == 0, result.output
        assert editor.opened == [user_config]
        assert tomllib.loads(result.stdout)["defaults"]["jobs"] == 4

    def test_invalid_config_interactive_declined(
        self, user_config: Path, editor: FakeEditor, interactive: None
    ) -> None:
        write(user_config, "[defaults]\njobs = 0\n")
        result = run("config", "show", input="n\n")
        assert result.exit_code == EXIT_ERROR
        # Reported once, not again on exit.
        assert result.stderr.count("Invalid config file") == 1


class TestEdit:
    def test_non_interactive(self, user_config: Path) -> None:
        result = run("config", "edit")
        assert result.exit_code == EXIT_ERROR
        assert "needs an interactive terminal" in result.stderr
        assert f"Edit {user_config} directly" in result.stderr
        assert not user_config.exists()

    def test_creates_starter_file(
        self, user_config: Path, editor: FakeEditor, interactive: None
    ) -> None:
        result = run("config", "edit")
        assert result.exit_code == 0, result.output
        assert editor.opened == [user_config]
        assert user_config.read_text().startswith("# zabgrab configuration")
        assert "is valid" in result.stderr

    def test_project_target(
        self, editor: FakeEditor, interactive: None, tmp_path: Path
    ) -> None:
        result = run("config", "edit", "--target", "project")
        assert result.exit_code == 0, result.output
        assert editor.opened == [tmp_path / "zabgrab.toml"]

    def test_reopens_until_valid(
        self, user_config: Path, editor: FakeEditor, interactive: None
    ) -> None:
        editor.contents.extend(["[defaults]\njobs = 0\n", "[defaults]\njobs = 1\n"])
        result = run("config", "edit", input="y\n")
        assert result.exit_code == 0, result.output
        assert editor.opened == [user_config, user_config]

    def test_gives_up_when_declined(
        self, user_config: Path, editor: FakeEditor, interactive: None
    ) -> None:
        editor.contents.append("[defaults]\njobs = 0\n")
        result = run("config", "edit", input="n\n")
        assert result.exit_code == EXIT_ERROR
        assert result.stderr.count("Invalid config file") == 1


class TestErrorHandling:
    def test_unexpected_errors_are_summarised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("zabgrab.cli.config.layers_table", boom)
        result = run("config", "paths")
        assert result.exit_code == EXIT_ERROR
        assert "Unexpected error: RuntimeError: kaboom" in result.stderr
        assert "--debug" in result.stderr
        assert "Traceback" not in result.stderr

    def test_debug_shows_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("zabgrab.cli.config.layers_table", boom)
        result = run("--debug", "config", "paths")
        assert "Traceback" in result.stderr
        assert "kaboom" in result.stderr
