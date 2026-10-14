"""Interfaces between the services and the outside world.

Services receive implementations of these protocols as arguments, so that tests can
substitute fakes (a scripted UI, a frozen clock, an in-memory Zabbix server, ...).
Services never print, prompt or perform I/O except through these.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generic, Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Option(Generic[T]):
    """One option offered by :meth:`UI.choose` / :meth:`UI.choose_many`."""

    label: str
    value: T
    description: str | None = None


class UI(Protocol):
    """User interaction.

    Messages are plain text; the implementation decides how to style them.

    Every method that asks for input takes a ``fix`` argument: a message telling the
    user how to provide the answer without being asked (e.g. a flag or config
    snippet). When the session is not interactive, these methods raise
    :class:`zabgrab.errors.InputRequired` carrying ``fix`` instead of prompting.
    """

    @property
    def interactive(self) -> bool:
        """Whether the user can be prompted."""
        ...

    def debug(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

    def success(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(
        self, message: str, *, details: Sequence[str] = (), hint: str | None = None
    ) -> None: ...

    def confirm(self, question: str, *, fix: str, default: bool = True) -> bool:
        """Ask a yes/no question."""
        ...

    def prompt(
        self,
        question: str,
        *,
        fix: str,
        parse: Callable[[str], T],
        default: str | None = None,
    ) -> T:
        """Ask for free text. ``parse`` converts the answer, raising ``ValueError``
        (whose message is shown before asking again) if it's invalid."""
        ...

    def choose(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        fix: str,
        default: int | None = None,
    ) -> T:
        """Ask the user to pick one option. ``default`` is an index."""
        ...

    def choose_many(
        self, question: str, options: Sequence[Option[T]], *, fix: str
    ) -> list[T]:
        """Ask the user to pick one or more options."""
        ...


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """The current time, timezone-aware."""
        ...


class Editor(Protocol):
    """Opens a file for the user to edit."""

    def edit(self, path: Path) -> None: ...
