"""API token storage in the operating system's keyring."""

from __future__ import annotations

import json

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from .. import PROGRAM
from ..errors import ZabgrabError

#: Keyring entry holding the (non-secret) list of URLs with stored tokens, because
#: keyring backends can't be enumerated. URLs can't start with "_", so it can't clash.
INDEX = "_urls"


class KeyringSecrets:
    """Stores tokens in the system keyring, keyed by server URL.

    A missing or broken keyring backend is treated as "no tokens stored" when
    reading, and as an error when changing anything.
    """

    service = PROGRAM

    def get_token(self, url: str) -> str | None:
        try:
            return keyring.get_password(self.service, url)
        except KeyringError:
            return None

    def set_token(self, url: str, token: str) -> None:
        try:
            keyring.set_password(self.service, url, token)
            self._set_index(sorted({*self.urls(), url}))
        except KeyringError as exc:
            raise ZabgrabError(
                f"Couldn't save the token in the system keyring: {exc}",
                hint="Set token_env for the server in your config and put the token "
                "in that environment variable instead.",
            ) from exc

    def delete_token(self, url: str) -> bool:
        try:
            try:
                keyring.delete_password(self.service, url)
                deleted = True
            except PasswordDeleteError:
                deleted = False
            if url in (urls := self.urls()):
                self._set_index([u for u in urls if u != url])
        except KeyringError as exc:
            raise ZabgrabError(
                f"Couldn't delete the token from the system keyring: {exc}"
            ) from exc
        return deleted

    def urls(self) -> list[str]:
        try:
            index = keyring.get_password(self.service, INDEX)
        except KeyringError:
            return []
        try:
            urls = json.loads(index or "[]")
        except ValueError:
            return []
        return (
            [url for url in urls if isinstance(url, str)]
            if isinstance(urls, list)
            else []
        )

    def _set_index(self, urls: list[str]) -> None:
        keyring.set_password(self.service, INDEX, json.dumps(urls))
