"""Interfaces between the services and the outside world.

Services receive implementations of these protocols as arguments, so that tests can
substitute fakes (a scripted UI, a frozen clock, an in-memory Zabbix server, ...).
Services never print, prompt or perform I/O except through these.

Attributes
----------
ClientFactory : type
    Creates an (unauthenticated) client for a server's config.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from ..model.config import ServerConfig
from ..model.zabbix import HistoryRow, Host, Item, TrendRow

T = TypeVar("T")


@dataclass(frozen=True)
class Option(Generic[T]):
    """One option offered by :meth:`UI.choose` or :meth:`UI.choose_many`.

    Attributes
    ----------
    label : str
        What the user sees.
    value : T
        What choosing it returns.
    description : str or None
        Extra detail shown next to the label.
    """

    label: str
    value: T
    description: str | None = None


class Progress(Protocol):
    """Reports progress of a download: an overall count plus one bar per item."""

    def add_item(self, label: str, total: int) -> int:
        """Start showing an item.

        Parameters
        ----------
        label : str
            What to call it.
        total : int
            How many steps it has.

        Returns
        -------
        int
            An ID for the other methods.
        """
        ...

    def advance(self, item: int) -> None:
        """Mark one step of an item (and of the overall total) as done."""
        ...

    def remove_item(self, item: int) -> None:
        """Stop showing an item."""
        ...


class UI(Protocol):
    """User interaction.

    Messages are plain text; the implementation decides how to style them. They
    go to stderr, so stdout carries only data.

    Every method that asks for input takes a ``fix`` argument: a message telling the
    user how to provide the answer without being asked (e.g. a flag or config
    snippet). When the session is not interactive, yes/no questions take their
    default answer with a warning; other questions raise
    :class:`zabgrab.errors.InputRequired` carrying ``fix``, since there's no answer
    to carry on with.
    """

    @property
    def interactive(self) -> bool:
        """Whether the user can be asked questions."""
        ...

    def debug(self, message: str) -> None:
        """Show a message only with ``-v`` or ``--debug``."""
        ...

    def info(self, message: str) -> None:
        """Show a message (hidden by ``-q``)."""
        ...

    def success(self, message: str) -> None:
        """Show that something worked (hidden by ``-q``)."""
        ...

    def warning(self, message: str) -> None:
        """Show a warning (hidden by ``-qq``)."""
        ...

    def error(
        self, message: str, *, details: Sequence[str] = (), hint: str | None = None
    ) -> None:
        """Show an error, with optional detail lines and a hint on how to fix it."""
        ...

    def confirm(self, question: str, *, fix: str, default: bool = True) -> bool:
        """Ask a yes/no question.

        Unlike the other questions, this doesn't fail when the session isn't
        interactive: it warns (including ``fix``) and returns ``default``.

        Parameters
        ----------
        question : str
            The question.
        fix : str
            How to choose without being asked.
        default : bool, optional
            The answer if the user just presses Enter, or can't be asked.

        Returns
        -------
        bool
            The answer.
        """
        ...

    def prompt(
        self,
        question: str,
        *,
        fix: str,
        parse: Callable[[str], T],
        default: str | None = None,
        secret: bool = False,
    ) -> T:
        """Ask for free text.

        Parameters
        ----------
        question : str
            The question.
        fix : str
            How to give the answer without being asked.
        parse : callable
            Converts the answer, raising ``ValueError`` (whose message is shown
            before asking again) if it's invalid.
        default : str, optional
            The answer if the user just presses Enter.
        secret : bool, optional
            Hide the input (e.g. for passwords).

        Returns
        -------
        T
            The parsed answer.

        Raises
        ------
        InputRequired
            If the session isn't interactive.
        """
        ...

    def choose(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        fix: str,
        default: int | None = None,
    ) -> T:
        """Ask the user to pick one option.

        Parameters
        ----------
        question : str
            The question.
        options : sequence of Option
            The options.
        fix : str
            How to choose without being asked.
        default : int, optional
            The index of the option chosen if the user just presses Enter.

        Returns
        -------
        T
            The chosen option's value.

        Raises
        ------
        InputRequired
            If the session isn't interactive.
        """
        ...

    def choose_many(
        self, question: str, options: Sequence[Option[T]], *, fix: str
    ) -> list[T]:
        """Ask the user to pick one or more options (e.g. ``1,3,5-8`` or ``all``).

        Parameters
        ----------
        question : str
            The question.
        options : sequence of Option
            The options.
        fix : str
            How to choose without being asked.

        Returns
        -------
        list of T
            The chosen options' values, in the order offered.

        Raises
        ------
        InputRequired
            If the session isn't interactive.
        """
        ...

    def progress(
        self, description: str, total: int
    ) -> AbstractContextManager[Progress]:
        """Show progress of ``total`` steps while the context is active."""
        ...


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Get the current time, timezone-aware."""
        ...


class Editor(Protocol):
    """Opens a file for the user to edit."""

    def edit(self, path: Path) -> None:
        """Open ``path`` and wait until the user is done."""
        ...


class SecretStore(Protocol):
    """Persistent storage for API tokens, keyed by server URL."""

    def get_token(self, url: str) -> str | None:
        """Get the token stored for a server URL, if any."""
        ...

    def set_token(self, url: str, token: str) -> None:
        """Store a token for a server URL, replacing any stored before."""
        ...

    def delete_token(self, url: str) -> bool:
        """Delete a stored token; return whether there was one."""
        ...

    def urls(self) -> list[str]:
        """Get the URLs of the servers with stored tokens."""
        ...

    def delete_all(self) -> int:
        """Delete every stored token, leaving no trace; return how many there were."""
        ...


class ZabbixClient(Protocol):
    """A connection to one Zabbix server.

    See :class:`zabgrab.adapters.zabbix.ZabbixAPI` for the real one.
    """

    async def check_version(self) -> str:
        """Get the server's version, raising if it isn't supported."""
        ...

    async def login_token(self, token: str) -> None:
        """Authenticate with an API token, raising if it's rejected."""
        ...

    async def login_password(self, username: str, password: str) -> None:
        """Authenticate with a username and password, raising if rejected."""
        ...

    async def close(self) -> None:
        """Log out of any password session and close the connection."""
        ...

    async def hosts(
        self,
        *,
        pattern: str | None = None,
        group: str | None = None,
        limit: int | None = None,
    ) -> list[Host]:
        """Find hosts by name pattern and host group pattern."""
        ...

    async def items(
        self,
        *,
        itemids: Sequence[int] | None = None,
        hostids: Sequence[int] | None = None,
        name: str | None = None,
        key: str | None = None,
        tag: str | None = None,
        limit: int | None = None,
    ) -> list[Item]:
        """Find items matching all of the given filters."""
        ...

    async def history(self, item: Item, start: int, end: int) -> list[HistoryRow]:
        """Get raw values between two POSIX times (inclusive), oldest first."""
        ...

    async def trends(self, item: Item, start: int, end: int) -> list[TrendRow]:
        """Get hourly trends for hours starting between two POSIX times (inclusive)."""
        ...


ClientFactory = Callable[[ServerConfig], ZabbixClient]
