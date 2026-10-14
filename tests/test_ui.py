"""Terminal UI: selection parsing, interactivity detection, messages and prompts."""

from __future__ import annotations

import io
from datetime import timedelta
from typing import ClassVar

import pytest

from zabgrab.errors import Cancelled, InputRequired
from zabgrab.services.ports import Option
from zabgrab.ui.console import RichUI, is_interactive
from zabgrab.ui.render import format_age
from zabgrab.ui.selection import parse_selection


class TestParseSelection:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1", [0]),
            (" 2 ", [1]),
            ("1,3", [0, 2]),
            ("3,1", [2, 0]),
            ("2-4", [1, 2, 3]),
            ("1,3,5-6", [0, 2, 4, 5]),
            ("1,1,1-2", [0, 1]),
            ("all", [0, 1, 2, 3, 4, 5]),
            ("ALL", [0, 1, 2, 3, 4, 5]),
        ],
    )
    def test_valid(self, text: str, expected: list[int]) -> None:
        assert parse_selection(text, 6) == expected

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "at least one"),
            ("x", "isn't a number"),
            ("0", "out of range"),
            ("7", "out of range"),
            ("2-9", "out of range"),
            ("4-2", "backwards"),
            ("1,,2", "isn't a number"),
        ],
    )
    def test_invalid(self, text: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            parse_selection(text, 6)


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestIsInteractive:
    def test_terminal(self) -> None:
        assert is_interactive({}, stdin=TTY(), stderr=TTY())

    def test_not_a_terminal(self) -> None:
        assert not is_interactive({}, stdin=io.StringIO(), stderr=TTY())
        assert not is_interactive({}, stdin=TTY(), stderr=io.StringIO())

    def test_no_input_flag(self) -> None:
        assert not is_interactive({}, no_input=True, stdin=TTY(), stderr=TTY())

    @pytest.mark.parametrize(
        "variables", [{"ZABGRAB_NO_INPUT": "1"}, {"CI": "true"}, {"CI": "1"}]
    )
    def test_environment(self, variables: dict[str, str]) -> None:
        assert not is_interactive(variables, stdin=TTY(), stderr=TTY())

    @pytest.mark.parametrize("variables", [{"ZABGRAB_NO_INPUT": "0"}, {"CI": "false"}])
    def test_environment_disabled(self, variables: dict[str, str]) -> None:
        assert is_interactive(variables, stdin=TTY(), stderr=TTY())


def make_ui(
    capsys: pytest.CaptureFixture[str], *, interactive: bool = True, verbosity: int = 0
) -> RichUI:
    return RichUI(interactive=interactive, verbosity=verbosity, color=False)


class TestMessages:
    def test_levels_go_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        ui = make_ui(capsys, verbosity=1)
        ui.debug("dbg")
        ui.info("inf")
        ui.success("ok")
        ui.warning("careful")
        ui.error("bad", details=["one", "two"], hint="do this")
        out, err = capsys.readouterr()
        assert out == ""
        assert err.splitlines() == [
            "dbg",
            "inf",
            "ok",
            "Warning: careful",
            "Error: bad",
            "  • one",
            "  • two",
            "  Fix: do this",
        ]

    @pytest.mark.parametrize(
        ("verbosity", "expected"),
        [
            (0, ["inf", "Warning: w", "Error: e"]),
            (-1, ["Warning: w", "Error: e"]),
            (-2, ["Error: e"]),
            (-5, ["Error: e"]),
        ],
    )
    def test_verbosity(
        self, capsys: pytest.CaptureFixture[str], verbosity: int, expected: list[str]
    ) -> None:
        ui = make_ui(capsys, verbosity=verbosity)
        ui.debug("d")
        ui.info("inf")
        ui.warning("w")
        ui.error("e")
        assert capsys.readouterr().err.splitlines() == expected

    def test_messages_are_not_markup(self, capsys: pytest.CaptureFixture[str]) -> None:
        make_ui(capsys).info("key particle.count[*] and [bold]x[/bold]")
        assert capsys.readouterr().err == "key particle.count[*] and [bold]x[/bold]\n"

    def test_print_data_goes_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        make_ui(capsys).print_data("a = [1]\n")
        assert capsys.readouterr() == ("a = [1]\n", "")


class TestPrompts:
    OPTIONS: ClassVar[list[Option[int]]] = [
        Option("first", 1),
        Option("second", 2, "the second"),
        Option("third", 3),
    ]

    def test_non_interactive_raises(self, capsys: pytest.CaptureFixture[str]) -> None:
        ui = make_ui(capsys, interactive=False)
        with pytest.raises(InputRequired, match="Which"):
            ui.choose("Which?", self.OPTIONS, fix="Use --x.")
        with pytest.raises(InputRequired):
            ui.choose_many("Which?", self.OPTIONS, fix="Use --x.")
        with pytest.raises(InputRequired):
            ui.prompt("Name?", fix="Use --name.", parse=str)

    @pytest.mark.parametrize(("default", "answer"), [(True, "yes"), (False, "no")])
    def test_non_interactive_confirm_assumes_default(
        self, capsys: pytest.CaptureFixture[str], default: bool, answer: str
    ) -> None:
        ui = make_ui(capsys, interactive=False)
        assert ui.confirm("Sure?", fix="Use --yes.", default=default) is default
        assert capsys.readouterr().err == (
            f"Warning: Sure? Running non-interactively, so assuming {answer}. "
            "Use --yes.\n"
        )

    def test_choose(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("9\nx\n2\n"))
        assert make_ui(capsys).choose("Which?", self.OPTIONS, fix="") == 2
        err = capsys.readouterr().err
        assert "2. second  the second" in err
        assert "9 is out of range" in err

    def test_choose_default(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
        assert make_ui(capsys).choose("Which?", self.OPTIONS, fix="", default=2) == 3

    def test_choose_rejects_multiple(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("1,2\n1\n"))
        assert make_ui(capsys).choose("Which?", self.OPTIONS, fix="") == 1
        assert "exactly one" in capsys.readouterr().err

    def test_choose_many(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("3,1-2\n"))
        assert make_ui(capsys).choose_many("Which?", self.OPTIONS, fix="") == [3, 1, 2]

    def test_prompt_reasks_on_parse_error(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("abc\n42\n"))
        assert make_ui(capsys).prompt("Number?", fix="", parse=int) == 42
        assert "invalid literal" in capsys.readouterr().err

    def test_confirm(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
        assert make_ui(capsys).confirm("Sure?", fix="") is False

    def test_eof_cancels(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        with pytest.raises(Cancelled):
            make_ui(capsys).confirm("Sure?", fix="")


def test_success_hidden_when_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    make_ui(capsys, verbosity=-1).success("done")
    assert capsys.readouterr().err == ""


def test_forced_colour(capsys: pytest.CaptureFixture[str]) -> None:
    RichUI(interactive=False, color=True).warning("careful")
    assert "\x1b[" in capsys.readouterr().err


def test_choose_many_reasks(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("5\nall\n"))
    options = [Option("a", 1), Option("b", 2)]
    assert make_ui(capsys).choose_many("Which?", options, fix="") == [1, 2]
    assert "out of range" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (-5, "0s"),
        (0, "0s"),
        (59, "59s"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h"),
        (86399, "23h"),
        (86400, "1d"),
        (10 * 86400, "10d"),
    ],
)
def test_format_age(seconds: int, text: str) -> None:
    assert format_age(timedelta(seconds=seconds)) == text


def test_progress_renders_on_terminals(capsys: pytest.CaptureFixture[str]) -> None:
    ui = RichUI(interactive=False, color=True)
    with ui.progress("Downloading", 2) as progress:
        item = progress.add_item("demo1: Particles", 2)
        progress.advance(item)
        progress.advance(item)
        progress.remove_item(item)
    err = capsys.readouterr().err
    assert "Downloading" in err
    assert "2/2" in err


def test_progress_hidden_when_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    ui = RichUI(interactive=False, color=True, verbosity=-1)
    with ui.progress("Downloading", 1) as progress:
        progress.advance(progress.add_item("x", 1))
    assert capsys.readouterr().err == ""
