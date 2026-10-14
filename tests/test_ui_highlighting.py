"""Highlighting of useful details in messages and prompts."""

from __future__ import annotations

import io

import pytest
from rich.text import Text

from zabgrab.services.ports import Option
from zabgrab.ui.console import MessageHighlighter, RichUI
from zabgrab.ui.render import highlighted_toml


def highlights(message: str) -> list[tuple[str, str]]:
    text = Text(message)
    MessageHighlighter().highlight(text)
    return [
        (message[span.start : span.end], str(span.style).removeprefix("zabgrab."))
        for span in sorted(text.spans, key=lambda span: span.start)
    ]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "http://z.org/api_jsonrpc.php redirects to https://z.org/api_jsonrpc.php.",
            [
                ("http://z.org/api_jsonrpc.php", "url"),
                ("https://z.org/api_jsonrpc.php", "url"),
            ],
        ),
        (
            "Can't connect to http://127.0.0.1:1: refused",
            [("http://127.0.0.1:1", "url")],
        ),
        (
            "Invalid config file /home/me/.config/zabgrab/config.toml.",
            [
                ("/home/me/.config/zabgrab/config.toml", "path"),
            ],
        ),
        (
            "Unknown server 'dmeo'. Did you mean 'demo'?",
            [
                ("'dmeo'", "quoted"),
                ("'demo'", "quoted"),
            ],
        ),
        (
            "'x' isn't a number; use e.g. '1,3'.",
            [("'x'", "quoted"), ("'1,3'", "quoted")],
        ),
        (
            "Store it with `zabgrab token add demo`.",
            [("`zabgrab token add demo`", "code")],
        ),
        ("$DEMO_TOKEN is not set.", [("$DEMO_TOKEN", "envvar")]),
        (
            "Pass --yes or -y to skip, not built-in.",
            [("--yes", "option"), ("-y", "option")],
        ),
        (
            'Set token_env = "DEMO" in [servers.demo], or verify_ssl = false.',
            [
                ('token_env = "DEMO"', "toml"),
                ("[servers.demo]", "toml"),
                ("verify_ssl = false", "toml"),
            ],
        ),
        ("Showing only the first 500 results.", [("500", "number")]),
        (
            "The server runs Zabbix 6.4.12; zabgrab needs 7.0 or later.",
            [
                ("6.4.12", "number"),
                ("7.0", "number"),
            ],
        ),
        (
            "Expected '=' (at line 2, column 7)",
            [
                ("'='", "quoted"),
                ("2", "number"),
                ("7", "number"),
            ],
        ),
        ("No numbers in host demo1, key particles[0.3] or claude-503.", []),
    ],
)
def test_highlights(message: str, expected: list[tuple[str, str]]) -> None:
    assert highlights(message) == expected


def coloured_ui() -> RichUI:
    return RichUI(interactive=True, color=True)


def test_error_lines(capsys: pytest.CaptureFixture[str]) -> None:
    coloured_ui().error("Bad 'x'.", details=["at line 3"], hint="Use `y`.")
    lines = capsys.readouterr().err.splitlines()
    assert "\x1b[1;35m'x'" in lines[0]
    assert lines[1].startswith("\x1b[2m  • ")
    assert lines[2].startswith("\x1b[1m  Fix: ")
    assert "\x1b[32m`y`" in lines[2]


def test_prompts_are_highlighted_and_not_markup(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n1\n"))
    ui = coloured_ui()
    assert ui.confirm("Use https://z.org [bold]instead[/bold]?", fix="") is True
    ui.choose("Which?", [Option("a", 1, "see https://a.org")], fix="")
    err = capsys.readouterr().err
    assert "\x1b[4;36mhttps://z.org" in err
    # Shown literally (as a config-style snippet), not interpreted as markup.
    assert "[bold]\x1b[0minstead[/bold]?" in err
    assert "\x1b[2;4;36mhttps://a.org" in err


def toml_highlights(text: str) -> list[tuple[str, str]]:
    rendered = highlighted_toml(text)
    return [
        (text[span.start : span.end], str(span.style).removeprefix("toml."))
        for span in sorted(rendered.spans, key=lambda span: span.start)
    ]


def test_toml_highlighting() -> None:
    text = (
        '# Comment = with "quotes"\n'
        "[servers.demo]\n"
        'url = "https://x"\n'
        "verify_ssl = false\n"
        "timeout = 30.0\n"
        'items = [{host = "demo1", name = "a = b, \\"c\\""}, {itemid = 5}]\n'
    )
    assert toml_highlights(text) == [
        ('# Comment = with "quotes"', "comment"),
        ("[servers.demo]", "section"),
        ("url", "key"),
        ('"https://x"', "string"),
        ("verify_ssl", "key"),
        ("false", "literal"),
        ("timeout", "key"),
        ("30.0", "literal"),
        ("items", "key"),
        ("host", "key"),
        ('"demo1"', "string"),
        ("name", "key"),
        ('"a = b, \\"c\\""', "string"),
        ("itemid", "key"),
        ("5", "literal"),
    ]


def test_config_show_is_highlighted() -> None:
    from click.testing import CliRunner

    from zabgrab.cli import cli

    result = CliRunner().invoke(cli, ["--isolated", "--color", "config", "show"])
    assert "\x1b[1;34m[defaults]\x1b[0m" in result.stdout
    assert "\x1b[36mjobs\x1b[0m = \x1b[35m6\x1b[0m" in result.stdout
    assert '\x1b[36mtimezone\x1b[0m = \x1b[32m"local"\x1b[0m' in result.stdout
