"""API token storage in the operating system's keyring."""

from __future__ import annotations

import contextlib
import json

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from .. import PROGRAM
from ..errors import ZabgrabError

#: The single keyring entry holding all tokens, as a JSON object mapping server URLs
#: to tokens. One entry (rather than one per server) means zabgrab can list and
#: remove its tokens, which keyring backends otherwise can't enumerate.
ENTRY = "tokens"


class KeyringSecrets:
    """Stores tokens in the system keyring, keyed by server URL.

    A missing or broken keyring backend is treated as "no tokens stored" when
    reading, and as an error when changing anything.
    """

    service = PROGRAM

    def get_token(self, url: str) -> str | None:
        return self._read().get(url)

    def set_token(self, url: str, token: str) -> None:
        tokens = self._read()
        tokens[url] = token
        self._write(tokens, "save the token in")

    def delete_token(self, url: str) -> bool:
        tokens = self._read()
        if tokens.pop(url, None) is None:
            return False
        self._write(tokens, "delete the token from")
        return True

    def urls(self) -> list[str]:
        return sorted(self._read())

    def delete_all(self) -> int:
        count = len(self._read())
        self._write({}, "delete the tokens from")
        return count

    def _read(self) -> dict[str, str]:
        try:
            stored = keyring.get_password(self.service, ENTRY)
        except KeyringError:
            return {}
        try:
            tokens = json.loads(stored or "{}")
        except ValueError:
            return {}
        if not isinstance(tokens, dict):
            return {}
        return {
            url: token
            for url, token in tokens.items()
            if isinstance(url, str) and isinstance(token, str)
        }

    def _write(self, tokens: dict[str, str], action: str) -> None:
        try:
            if tokens:
                keyring.set_password(self.service, ENTRY, json.dumps(tokens))
            else:
                # Leave nothing behind once the last token is gone.
                with contextlib.suppress(PasswordDeleteError):
                    keyring.delete_password(self.service, ENTRY)
        except KeyringError as exc:
            raise ZabgrabError(
                f"Couldn't {action} the system keyring: {exc}",
                hint="Set token_env for the server in your config and put the token "
                "in that environment variable instead.",
            ) from exc
