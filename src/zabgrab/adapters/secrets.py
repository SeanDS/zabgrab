"""API token storage in the operating system's keyring."""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

from .. import PROGRAM
from ..errors import ZabgrabError


class KeyringSecrets:
    """Stores tokens in the system keyring, keyed by server URL.

    A missing or broken keyring backend is treated as "no token stored" when
    reading, and as an error when saving.
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
        except KeyringError as exc:
            raise ZabgrabError(
                f"Couldn't save the token in the system keyring: {exc}",
                hint="Set token_env for the server in your config and put the token "
                "in that environment variable instead.",
            ) from exc
