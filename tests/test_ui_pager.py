"""Printing long output, through a pager in a terminal."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from zabgrab.ui import console
from zabgrab.ui.console import RichUI, pager_command

LINES = [("Heading", "bold"), ("", "dim"), ("a long line of data", "")]


def command(code: str) -> str:
    """Make a command line running Python code, as $PAGER would hold."""
    args = [sys.executable, "-c", code]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make stdout look like a 10-column terminal."""
    monkeypatch.setattr(console, "_isatty", lambda file: True)
    monkeypatch.setenv("COLUMNS", "10")


class TestNotATerminal:
    def test_lines_as_they_are(self, capsys: pytest.CaptureFixture[str]) -> None:
        RichUI(interactive=False, color=False).page(LINES)
        assert capsys.readouterr() == ("Heading\n\na long line of data\n", "")

    def test_styles_with_colour(self, capsys: pytest.CaptureFixture[str]) -> None:
        RichUI(interactive=False, color=True).page(LINES)
        assert capsys.readouterr().out == (
            "\x1b[1mHeading\x1b[0m\n\na long line of data\n"
        )


@pytest.mark.usefixtures("terminal")
class TestTerminal:
    def test_cut_to_width_without_pager(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        RichUI(interactive=True, color=False).page(LINES, pager=False)
        assert capsys.readouterr().out == "Heading\n\na long li…\n"

    def test_pager(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shown = tmp_path / "shown.txt"
        code = (
            "import os, sys; "
            f"open({str(shown)!r}, 'w', encoding='utf-8')"
            ".write(os.environ['LESS'] + '|' + sys.stdin.read())"
        )
        monkeypatch.setenv("PAGER", command(code))
        monkeypatch.delenv("LESS", raising=False)
        RichUI(interactive=True, color=False).page(LINES)
        # Whole lines, for the pager to scroll sideways; less's options are set.
        assert shown.read_text(encoding="utf-8") == (
            "FRSX|Heading\n\na long line of data\n"
        )
        assert capsys.readouterr().out == ""

    def test_pager_closed_early(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAGER", command("pass"))
        lines = [("x" * 100, "")] * 50_000  # more than a pipe holds
        RichUI(interactive=True, color=False).page(lines)
        assert capsys.readouterr().out == ""

    def test_pager_that_cant_start(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAGER", "zabgrab-no-such-pager")
        RichUI(interactive=True, color=False).page(LINES)
        assert capsys.readouterr().out == "Heading\n\na long li…\n"


class TestPagerCommand:
    def test_pager_variable(self) -> None:
        expected = "less -R" if os.name == "nt" else ["less", "-R"]
        assert pager_command({"PAGER": " less -R "}) == expected

    def test_empty_means_none(self) -> None:
        assert pager_command({"PAGER": ""}) is None

    @pytest.mark.parametrize(
        ("found", "expected"), [("/usr/bin/less", ["less"]), (None, None)]
    )
    def test_less_if_installed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        found: str | None,
        expected: list[str] | None,
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: found)
        assert pager_command({}) == expected

    def test_process_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGER", "")
        assert pager_command() is None
