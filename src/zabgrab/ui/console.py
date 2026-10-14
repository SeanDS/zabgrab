"""Terminal implementation of the :class:`~zabgrab.services.ports.UI` port.

Diagnostics and prompts go to stderr; data (tables, TOML, ...) goes to stdout via
:meth:`RichUI.print_data`, so piping stdout always gives clean output.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO, TypeVar

from rich.console import Console, RenderableType
from rich.highlighter import RegexHighlighter
from rich.prompt import Confirm, Prompt
from rich.text import Text
from rich.theme import Theme

from ..errors import Cancelled, InputRequired
from ..services.ports import Option
from .selection import parse_selection

T = TypeVar("T")

THEME = Theme(
    {
        "zabgrab.debug": "dim",
        "zabgrab.success": "green",
        "zabgrab.warning": "yellow",
        "zabgrab.error": "bold red",
        "zabgrab.hint": "bold",
        "zabgrab.quoted": "bold",
        "zabgrab.url": "cyan",
        "zabgrab.path": "cyan",
        "zabgrab.code": "cyan",
    }
)


class MessageHighlighter(RegexHighlighter):
    """Highlights URLs, paths, `code` and quoted strings in plain-text messages."""

    base_style = "zabgrab."
    highlights = [  # noqa: RUF012 - rich's API uses a mutable class attribute
        r"(?P<url>https?://[^\s'\"]+)",
        r"(?P<code>`[^`]+`)",
        r"(?P<quoted>'[^'\s][^']*')",
        r"(?:^|\s)(?P<path>~?/[^\s'\"]+)",
    ]


def is_interactive(
    variables: Mapping[str, str],
    *,
    no_input: bool = False,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
) -> bool:
    """Whether the user can be prompted.

    Requires stdin and stderr to be terminals, and none of ``--no-input``,
    ``$ZABGRAB_NO_INPUT`` or ``$CI`` to be set.
    """
    if no_input or variables.get("ZABGRAB_NO_INPUT", "") not in ("", "0"):
        return False
    if variables.get("CI", "") not in ("", "0", "false"):
        return False
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    return stdin.isatty() and stderr.isatty()


class RichUI:
    """User interaction via rich.

    Verbosity: ``0`` shows info and above, each ``-v`` adds a level (``1`` shows
    debug), each ``-q`` removes one (``-1`` hides info, ``-2`` hides warnings).
    Errors are always shown.
    """

    def __init__(
        self,
        *,
        interactive: bool,
        verbosity: int = 0,
        color: bool | None = None,
        debug: bool = False,
    ) -> None:
        self._interactive = interactive
        self.verbosity = verbosity
        self.debug_mode = debug

        options: dict[str, Any] = {"theme": THEME}
        if color is True:
            options["force_terminal"] = True
        elif color is False:
            options["color_system"] = None
        self.err = Console(stderr=True, highlighter=MessageHighlighter(), **options)
        self.out = Console(**options)

    @property
    def interactive(self) -> bool:
        return self._interactive

    # Messages.

    def _message(self, message: str, style: str | None, prefix: str = "") -> None:
        text = Text(prefix, style=style or "") if prefix else Text()
        text.append_text(self.err.highlighter(Text(message, style=style or "")))
        self.err.print(text, soft_wrap=True)

    def debug(self, message: str) -> None:
        if self.verbosity >= 1 or self.debug_mode:
            self._message(message, "zabgrab.debug")

    def info(self, message: str) -> None:
        if self.verbosity >= 0:
            self._message(message, None)

    def success(self, message: str) -> None:
        if self.verbosity >= 0:
            self._message(message, "zabgrab.success")

    def warning(self, message: str) -> None:
        if self.verbosity >= -1:
            self._message(message, "zabgrab.warning", "Warning: ")

    def error(
        self, message: str, *, details: Sequence[str] = (), hint: str | None = None
    ) -> None:
        self._message(message, "zabgrab.error", "Error: ")
        for detail in details:
            self._message(detail, None, "  • ")
        if hint:
            self._message(hint, None, "  Fix: ")

    def print_exception(self) -> None:
        """Print the current exception's traceback (for ``--debug``)."""
        self.err.print_exception()

    # Data.

    def print_data(self, data: RenderableType) -> None:
        """Print command output (not diagnostics) to stdout."""
        if isinstance(data, str):
            self.out.print(data, markup=False, highlight=False, soft_wrap=True, end="")
        elif isinstance(data, Text):
            self.out.print(data, highlight=False, soft_wrap=True)
        else:
            self.out.print(data)

    # Prompts.

    def _require_interactive(self, question: str, fix: str) -> None:
        if not self._interactive:
            raise InputRequired(question, fix=fix)

    def _ask(self, ask: Callable[[], T]) -> T:
        try:
            return ask()
        except (EOFError, KeyboardInterrupt):
            self.err.print()
            raise Cancelled() from None

    def confirm(self, question: str, *, fix: str, default: bool = True) -> bool:
        self._require_interactive(question, fix)
        return self._ask(
            lambda: Confirm.ask(question, console=self.err, default=default)
        )

    def prompt(
        self,
        question: str,
        *,
        fix: str,
        parse: Callable[[str], T],
        default: str | None = None,
        secret: bool = False,
    ) -> T:
        self._require_interactive(question, fix)
        while True:
            answer = self._ask(
                lambda: Prompt.ask(
                    question, console=self.err, default=default, password=secret
                )
            )
            try:
                return parse(answer or "")
            except ValueError as exc:
                self._message(str(exc), "zabgrab.error")

    def _show_options(self, question: str, options: Sequence[Option[Any]]) -> None:
        self.err.print(Text(question, style="bold"))
        width = len(str(len(options)))
        for number, option in enumerate(options, start=1):
            line = Text(f"  {number:>{width}}. ")
            line.append(option.label, style="bold")
            if option.description:
                line.append(f"  {option.description}", style="dim")
            self.err.print(line, soft_wrap=True)

    def choose(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        fix: str,
        default: int | None = None,
    ) -> T:
        self._require_interactive(question, fix)
        self._show_options(question, options)

        def parse(answer: str) -> int:
            indices = parse_selection(answer, len(options))
            if len(indices) != 1:
                raise ValueError("Choose exactly one option.")
            return indices[0]

        while True:
            answer = self._ask(
                lambda: Prompt.ask(
                    f"Choose 1-{len(options)}",
                    console=self.err,
                    default=None if default is None else str(default + 1),
                )
            )
            try:
                return options[parse(answer or "")].value
            except ValueError as exc:
                self._message(str(exc), "zabgrab.error")

    def choose_many(
        self, question: str, options: Sequence[Option[T]], *, fix: str
    ) -> list[T]:
        self._require_interactive(question, fix)
        self._show_options(question, options)
        while True:
            answer = self._ask(
                lambda: Prompt.ask("Choose (e.g. 1,3,5-8 or all)", console=self.err)
            )
            try:
                return [
                    options[i].value
                    for i in parse_selection(answer or "", len(options))
                ]
            except ValueError as exc:
                self._message(str(exc), "zabgrab.error")
