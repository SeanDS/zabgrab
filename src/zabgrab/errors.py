"""Exceptions shared by all layers.

Every error that is expected to reach the user derives from :class:`ZabgrabError`.
These errors carry a human-readable message and, where possible, a ``hint`` telling
the user how to fix the problem. The CLI renders them without a traceback (unless
``--debug`` is given) and exits with :attr:`ZabgrabError.exit_code`.

Attributes
----------
EXIT_OK, EXIT_ERROR, EXIT_USAGE : int
    Exit codes for success, errors and command line mistakes.
EXIT_PARTIAL : int
    Exit code when some items couldn't be fetched but others were.
EXIT_INTERRUPTED : int
    Exit code when the user cancelled (as for Ctrl-C).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3
EXIT_INTERRUPTED = 130


class ZabgrabError(Exception):
    """An error with a message intended for the user.

    Parameters
    ----------
    message : str
        What went wrong, as a sentence.
    details : sequence of str, optional
        Further lines, e.g. one per problem found.
    hint : str, optional
        How to fix it.

    Attributes
    ----------
    exit_code : int
        The exit code to use when this error ends the program.
    reported : bool
        Set when the error has already been shown to the user, so the CLI exits
        without repeating it.
    """

    exit_code = EXIT_ERROR

    def __init__(
        self, message: str, *, details: Sequence[str] = (), hint: str | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = tuple(details)
        self.hint = hint
        self.reported = False


class ConfigError(ZabgrabError):
    """A config file could not be read, parsed or validated.

    Parameters
    ----------
    message, details, hint
        As for :class:`ZabgrabError`.
    path : Path, optional
        The config file.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        details: Sequence[str] = (),
        hint: str | None = None,
    ) -> None:
        super().__init__(message, details=details, hint=hint)
        self.path = path


class InputRequired(ZabgrabError):
    """Input was needed from the user but the session is not interactive.

    Parameters
    ----------
    question : str
        What would have been asked.
    fix : str
        How to supply the input without a prompt, e.g. a flag to add or a config
        snippet.
    """

    def __init__(self, question: str, *, fix: str) -> None:
        super().__init__(
            f"Input required but running non-interactively: {question}", hint=fix
        )
        self.question = question
        self.fix = fix


class Cancelled(ZabgrabError):
    """The user cancelled at a prompt."""

    exit_code = EXIT_INTERRUPTED

    def __init__(self) -> None:
        super().__init__("Cancelled.")


class ZabbixError(ZabgrabError):
    """A problem talking to a Zabbix server."""


class ConnectionFailed(ZabbixError):
    """The server couldn't be reached, or didn't respond like a Zabbix server."""


class Redirected(ConnectionFailed):
    """The API endpoint redirected elsewhere (e.g. HTTP to HTTPS).

    Parameters
    ----------
    message, hint
        As for :class:`ZabgrabError`.
    target : str
        The frontend base URL the server redirected to.
    """

    def __init__(self, message: str, *, target: str, hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.target = target


class ZabbixAPIError(ZabbixError):
    """The Zabbix API returned an error.

    Parameters
    ----------
    message, hint
        As for :class:`ZabgrabError`.
    code : int, optional
        The JSON-RPC error code.
    data : str, optional
        The error's details from the server.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: str = "",
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.code = code
        self.data = data


class AuthenticationFailed(ZabbixAPIError):
    """The server rejected the credentials."""


class UnsupportedServer(ZabbixError):
    """The server runs a Zabbix version zabgrab doesn't support."""
