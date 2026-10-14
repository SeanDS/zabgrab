"""API token storage in the operating system's keyring."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from .. import PROGRAM
from ..errors import ZabgrabError

#: Tokens are stored one per keyring entry, named ``token1``, ``token2``, ... with no
#: gaps (keyring backends can't be listed, so zabgrab reads entries in order until it
#: finds a missing one). Each entry holds ``{"url": ..., "token": ...}`` as JSON.
PREFIX = "token"

#: How many consecutive missing entries :meth:`KeyringSecrets.delete_all` checks
#: past the last entry it finds, to catch anything left beyond a gap.
RESET_SCAN = 256


@dataclass(frozen=True)
class _Entry:
    number: int
    url: str | None
    """``None`` if the entry is unreadable (it still occupies its number)."""
    token: str | None
    raw: str


def _name(number: int) -> str:
    return f"{PREFIX}{number}"


class KeyringSecrets:
    """Stores tokens in the system keyring, keyed by server URL.

    A missing or broken keyring backend is treated as "no tokens stored" when
    reading, and as an error when changing anything.
    """

    service = PROGRAM

    def get_token(self, url: str) -> str | None:
        entry = _find(url, self._entries())
        return entry.token if entry else None

    def set_token(self, url: str, token: str) -> None:
        entries = self._entries()
        existing = _find(url, entries)
        number = existing.number if existing else len(entries) + 1
        value = json.dumps({"url": url, "token": token})
        self._change("save the token in", lambda: self._set(number, value))

    def delete_token(self, url: str) -> bool:
        entries = self._entries()
        entry = _find(url, entries)
        if entry is None:
            return False

        def delete() -> None:
            # Keep the numbering contiguous: move the last entry into the gap.
            last = entries[-1]
            if last.number != entry.number:
                self._set(entry.number, last.raw)
            keyring.delete_password(self.service, _name(last.number))

        self._change("delete the token from", delete)
        return True

    def urls(self) -> list[str]:
        return sorted(entry.url for entry in self._entries() if entry.url)

    def delete_all(self) -> int:
        count = len(self.urls())

        def delete() -> None:
            number, missing = 1, 0
            while missing < RESET_SCAN:
                if keyring.get_password(self.service, _name(number)) is None:
                    missing += 1
                else:
                    missing = 0
                    with contextlib.suppress(PasswordDeleteError):
                        keyring.delete_password(self.service, _name(number))
                number += 1

        self._change("delete the tokens from", delete)
        return count

    def _entries(self) -> list[_Entry]:
        entries = []
        number = 1
        while True:
            try:
                raw = keyring.get_password(self.service, _name(number))
            except KeyringError:
                break
            if raw is None:
                break
            entries.append(_parse(number, raw))
            number += 1
        return entries

    def _set(self, number: int, value: str) -> None:
        keyring.set_password(self.service, _name(number), value)

    def _change(self, action: str, change: Callable[[], None]) -> None:
        try:
            change()
        except KeyringError as exc:
            raise ZabgrabError(
                f"Couldn't {action} the system keyring: {exc}",
                hint="Set token_env for the server in your config and put the token "
                "in that environment variable instead.",
            ) from exc


def _find(url: str, entries: list[_Entry]) -> _Entry | None:
    for entry in entries:
        if entry.url == url:
            return entry
    return None


def _parse(number: int, raw: str) -> _Entry:
    try:
        data = json.loads(raw)
    except ValueError:
        return _Entry(number, None, None, raw)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("url"), str)
        or not isinstance(data.get("token"), str)
    ):
        return _Entry(number, None, None, raw)
    return _Entry(number, data["url"], data["token"], raw)
