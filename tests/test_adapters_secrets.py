"""Keyring token storage."""

from __future__ import annotations

import pytest
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from zabgrab.adapters.secrets import INDEX, KeyringSecrets
from zabgrab.errors import ZabgrabError

Store = dict[tuple[str, str], str]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    stored: Store = {}
    monkeypatch.setattr(
        "keyring.get_password", lambda service, user: stored.get((service, user))
    )

    def set_password(service: str, user: str, password: str) -> None:
        stored[service, user] = password

    def delete_password(service: str, user: str) -> None:
        if (service, user) not in stored:
            raise PasswordDeleteError("Password not found")
        del stored[service, user]

    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.delete_password", delete_password)
    return stored


def test_round_trip(store: Store) -> None:
    secrets = KeyringSecrets()
    assert secrets.get_token("https://a") is None
    secrets.set_token("https://b", "t1")
    secrets.set_token("https://a", "t0k3n")
    secrets.set_token("https://a", "t0k3n")
    assert secrets.get_token("https://a") == "t0k3n"
    assert secrets.urls() == ["https://a", "https://b"]
    assert store[("zabgrab", "https://a")] == "t0k3n"


def test_delete(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "t")
    secrets.set_token("https://b", "t")
    assert secrets.delete_token("https://a") is True
    assert secrets.get_token("https://a") is None
    assert secrets.urls() == ["https://b"]
    assert secrets.delete_token("https://a") is False


def test_delete_removes_stale_index_entries(store: Store) -> None:
    store["zabgrab", INDEX] = '["https://gone"]'
    assert KeyringSecrets().delete_token("https://gone") is False
    assert KeyringSecrets().urls() == []


@pytest.mark.parametrize("index", ["not json", '{"a": 1}', '["https://a", 5]'])
def test_corrupt_index(store: Store, index: str) -> None:
    store["zabgrab", INDEX] = index
    assert KeyringSecrets().urls() == (["https://a"] if index.startswith("[") else [])


def raise_no_keyring(*args: object) -> None:
    raise NoKeyringError("No recommended backend was available.")


def test_missing_backend_reads_as_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.get_password", raise_no_keyring)
    assert KeyringSecrets().get_token("https://a") is None
    assert KeyringSecrets().urls() == []


def test_missing_backend_fails_to_save(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.set_password", raise_no_keyring)
    with pytest.raises(ZabgrabError, match="Couldn't save the token") as exc_info:
        KeyringSecrets().set_token("https://a", "t")
    assert isinstance(exc_info.value.__cause__, KeyringError)
    assert exc_info.value.hint is not None
    assert "token_env" in exc_info.value.hint


def test_missing_backend_fails_to_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.delete_password", raise_no_keyring)
    with pytest.raises(ZabgrabError, match="Couldn't delete the token"):
        KeyringSecrets().delete_token("https://a")
