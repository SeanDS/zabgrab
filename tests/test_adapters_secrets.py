"""Keyring token storage."""

from __future__ import annotations

import pytest
from keyring.errors import KeyringError, NoKeyringError

from zabgrab.adapters.secrets import KeyringSecrets
from zabgrab.errors import ZabgrabError


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        "keyring.get_password", lambda service, user: stored.get((service, user))
    )

    def set_password(service: str, user: str, password: str) -> None:
        stored[service, user] = password

    monkeypatch.setattr("keyring.set_password", set_password)
    return stored


def test_round_trip(store: dict[tuple[str, str], str]) -> None:
    secrets = KeyringSecrets()
    assert secrets.get_token("https://a") is None
    secrets.set_token("https://a", "t0k3n")
    assert secrets.get_token("https://a") == "t0k3n"
    assert store == {("zabgrab", "https://a"): "t0k3n"}


def raise_no_keyring(*args: object) -> None:
    raise NoKeyringError("No recommended backend was available.")


def test_missing_backend_reads_as_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.get_password", raise_no_keyring)
    assert KeyringSecrets().get_token("https://a") is None


def test_missing_backend_fails_to_save(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.set_password", raise_no_keyring)
    with pytest.raises(ZabgrabError, match="Couldn't save the token") as exc_info:
        KeyringSecrets().set_token("https://a", "t")
    assert isinstance(exc_info.value.__cause__, KeyringError)
    assert exc_info.value.hint is not None
    assert "token_env" in exc_info.value.hint
