"""Keyring token storage."""

from __future__ import annotations

import json

import pytest
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from zabgrab.adapters.secrets import ENTRY, KeyringSecrets
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


def test_round_trip_uses_one_entry(store: Store) -> None:
    secrets = KeyringSecrets()
    assert secrets.get_token("https://a") is None
    secrets.set_token("https://b", "t1")
    secrets.set_token("https://a", "t0k3n")
    assert secrets.get_token("https://a") == "t0k3n"
    assert secrets.urls() == ["https://a", "https://b"]
    assert list(store) == [("zabgrab", ENTRY)]
    assert json.loads(store["zabgrab", ENTRY]) == {
        "https://a": "t0k3n",
        "https://b": "t1",
    }


def test_delete(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "t")
    secrets.set_token("https://b", "t")
    assert secrets.delete_token("https://a") is True
    assert secrets.urls() == ["https://b"]
    assert secrets.delete_token("https://a") is False


def test_deleting_the_last_token_removes_the_entry(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "t")
    secrets.delete_token("https://a")
    assert store == {}


def test_delete_all(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "t")
    secrets.set_token("https://b", "t")
    assert secrets.delete_all() == 2
    assert store == {}
    assert secrets.delete_all() == 0


@pytest.mark.parametrize(
    ("stored", "urls"),
    [
        ("not json", []),
        ('["a list"]', []),
        ('{"https://a": "t", "https://b": 5}', ["https://a"]),
    ],
)
def test_corrupt_entry(store: Store, stored: str, urls: list[str]) -> None:
    store["zabgrab", ENTRY] = stored
    assert KeyringSecrets().urls() == urls


def test_delete_all_clears_corrupt_entry(store: Store) -> None:
    store["zabgrab", ENTRY] = "not json"
    assert KeyringSecrets().delete_all() == 0
    assert store == {}


def raise_no_keyring(*args: object) -> None:
    raise NoKeyringError("No recommended backend was available.")


def test_missing_backend_reads_as_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.get_password", raise_no_keyring)
    assert KeyringSecrets().get_token("https://a") is None
    assert KeyringSecrets().urls() == []


def test_missing_backend_fails_to_save(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("keyring.set_password", raise_no_keyring)
    with pytest.raises(ZabgrabError, match="Couldn't save the token") as exc_info:
        KeyringSecrets().set_token("https://a", "t")
    assert isinstance(exc_info.value.__cause__, KeyringError)
    assert exc_info.value.hint is not None
    assert "token_env" in exc_info.value.hint


def test_missing_backend_fails_to_delete(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    KeyringSecrets().set_token("https://a", "t")
    monkeypatch.setattr("keyring.delete_password", raise_no_keyring)
    with pytest.raises(ZabgrabError, match="Couldn't delete the token"):
        KeyringSecrets().delete_token("https://a")
