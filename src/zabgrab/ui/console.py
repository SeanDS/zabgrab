"""Terminal implementation of the :class:`~zabgrab.services.ports.UI` port.

Diagnostics and prompts go to stderr; data (tables, TOML, ...) goes to stdout via
:meth:`RichUI.print_data`, so piping stdout always gives clean output.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TextIO, TypeVar

from rich.console import Console, RenderableType
from rich.highlighter import RegexHighlighter
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.progress import Progress as RichProgress
from rich.prompt import Confirm, Prompt
from rich.text import Text
from rich.theme import Theme

from ..errors import Cancelled, InputRequired
from ..services.ports import Option, Progress
from .selection import parse_selection

T = TypeVar("T")

THEME = Theme(
    {
        "zabgrab.debug": "dim",
        "zabgrab.success": "green",
        "zabgrab.warning": "yellow",
        "zabgrab.error": "bold red",
        "zabgrab.hint": "bold",
        # Highlighted details in messages (see MessageHighlighter): locations are
        # cyan, things to type green, names and values magenta, numbers bold.
        "zabgrab.url": "cyan underline",
        "zabgrab.path": "cyan",
        "zabgrab.code": "green",
        "zabgrab.option": "green",
        "zabgrab.toml": "green",
        "zabgrab.quoted": "bold magenta",
        "zabgrab.envvar": "bold magenta",
        "zabgrab.number": "bold",
    }
)


class MessageHighlighter(RegexHighlighter):
    """Highlights the specific, useful details in plain-text messages.

    Later patterns take precedence where matches overlap.
    """

    base_style = "zabgrab."
    highlights = [  # noqa: RUF012 - rich's API uses a mutable class attribute
        # Counts, IDs, versions, line numbers (not parts of names, paths or URLs).
        r"(?<![\w.$/:'\[-])(?P<number>\d+(?:\.\d+)*)(?![\w/:'\]-])",
        # 'quoted' names and values (not apostrophes inside words).
        r"(?<!\w)(?P<quoted>'[^'\s][^']*')(?!\w)",
        r"(?P<envvar>\$[A-Za-z_][A-Za-z0-9_]*)",
        # Command line options.
        r"(?<![\w/.-])(?P<option>--[a-z][a-z0-9-]*|-[a-zA-Z])(?![\w-])",
        # Config snippets: [servers.name] and key = "value".
        r"(?P<toml>\[[A-Za-z][\w.-]*\])",
        r"(?P<toml>\b[a-z_]+ = (?:\"[^\"]*\"|true|false|\d+))",
        r"(?:^|(?<=[\s(]))(?P<path>~?/[^\s'\"]*[^\s'\".,;:!?)])",
        r"(?P<url>https?://[^\s'\"]*[^\s'\".,;:!?)])",
        r"(?P<code>`[^`]+`)",
    ]


def non_interactive_answer(question: str, default: bool, fix: str) -> str:
    """The warning given when a yes/no question is answered by its default."""
    answer = "yes" if default else "no"
    return f"{question} Running non-interactively, so assuming {answer}. {fix}".strip()


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
        self._highlighter = MessageHighlighter()
        self.err = Console(stderr=True, **options)
        self.out = Console(**options)

    @property
    def interactive(self) -> bool:
        return self._interactive

    # Messages.

    def highlight(self, message: str, style: str = "") -> Text:
        """``message`` as plain text (never markup) with its details highlighted."""
        return self._highlighter(Text(message, style=style))

    def _message(
        self,
        message: str,
        style: str | None,
        prefix: str = "",
        prefix_style: str | None = None,
    ) -> None:
        # Style the parts separately: a style given to Text() would apply to all
        # of it.
        text = Text()
        text.append(prefix, style=prefix_style or style or "")
        text.append_text(self.highlight(message, style or ""))
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
            self._message(detail, None, "  • ", "dim")
        if hint:
            self._message(hint, None, "  Fix: ", "zabgrab.hint")

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
        if not self._interactive:
            self.warning(non_interactive_answer(question, default, fix))
            return default
        return self._ask(
            lambda: Confirm.ask(
                self.highlight(question), console=self.err, default=default
            )
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
                    self.highlight(question),
                    console=self.err,
                    default=default,
                    password=secret,
                )
            )
            try:
                return parse(answer or "")
            except ValueError as exc:
                self._message(str(exc), "zabgrab.error")

    def _show_options(self, question: str, options: Sequence[Option[Any]]) -> None:
        self.err.print(self.highlight(question, "bold"), soft_wrap=True)
        width = len(str(len(options)))
        for number, option in enumerate(options, start=1):
            line = Text(f"  {number:>{width}}. ")
            line.append(option.label, style="bold")
            if option.description:
                line.append("  ")
                line.append_text(self.highlight(option.description, "dim"))
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

    # Progress.

    @contextmanager
    def progress(self, description: str, total: int) -> Iterator[Progress]:
        bars = RichProgress(
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.err,
            disable=self.verbosity < 0 or not self.err.is_terminal,
        )
        overall = bars.add_task(description, total=total)
        with bars:
            yield _RichProgress(bars, overall)


class _RichProgress:
    def __init__(self, bars: RichProgress, overall: TaskID) -> None:
        self.bars = bars
        self.overall = overall

    def add_item(self, label: str, total: int) -> int:
        return int(self.bars.add_task(f"  {label}", total=total))

    def advance(self, item: int) -> None:
        self.bars.advance(TaskID(item))
        self.bars.advance(self.overall)

    def remove_item(self, item: int) -> None:
        self.bars.remove_task(TaskID(item))
