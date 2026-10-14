"""Keyring token storage."""

from __future__ import annotations

import json

import pytest
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from zabgrab.adapters.secrets import RESET_SCAN, KeyringSecrets
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


def entry(url: str, token: str) -> str:
    return json.dumps({"url": url, "token": token})


def urls_by_entry(store: Store) -> dict[str, str]:
    return {
        name: json.loads(value)["url"] for (_, name), value in sorted(store.items())
    }


def test_round_trip(store: Store) -> None:
    secrets = KeyringSecrets()
    assert secrets.get_token("https://a") is None
    secrets.set_token("https://b", "t1")
    secrets.set_token("https://a", "t0k3n")
    assert secrets.get_token("https://a") == "t0k3n"
    assert secrets.get_token("https://b") == "t1"
    assert secrets.urls() == ["https://a", "https://b"]
    assert store == {
        ("zabgrab", "token1"): entry("https://b", "t1"),
        ("zabgrab", "token2"): entry("https://a", "t0k3n"),
    }


def test_replacing_a_token_keeps_its_entry(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "old")
    secrets.set_token("https://b", "t")
    secrets.set_token("https://a", "new")
    assert store["zabgrab", "token1"] == entry("https://a", "new")
    assert len(store) == 2


@pytest.mark.parametrize(
    ("deleted", "remaining"),
    [
        ("https://a", {"token1": "https://c", "token2": "https://b"}),
        ("https://b", {"token1": "https://a", "token2": "https://c"}),
        ("https://c", {"token1": "https://a", "token2": "https://b"}),
    ],
)
def test_delete_keeps_entries_contiguous(
    store: Store, deleted: str, remaining: dict[str, str]
) -> None:
    secrets = KeyringSecrets()
    for url in ("https://a", "https://b", "https://c"):
        secrets.set_token(url, f"token for {url}")
    assert secrets.delete_token(deleted) is True
    assert urls_by_entry(store) == remaining
    assert secrets.get_token(deleted) is None
    for url in remaining.values():
        assert secrets.get_token(url) == f"token for {url}"


def test_delete_missing(store: Store) -> None:
    assert KeyringSecrets().delete_token("https://a") is False


def test_deleting_the_last_token_leaves_nothing(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "t")
    secrets.delete_token("https://a")
    assert store == {}


def test_reading_stops_at_the_first_gap(store: Store) -> None:
    store["zabgrab", "token1"] = entry("https://a", "t")
    store["zabgrab", "token3"] = entry("https://c", "t")
    assert KeyringSecrets().urls() == ["https://a"]


def test_unreadable_entries_occupy_their_number(store: Store) -> None:
    store["zabgrab", "token1"] = "not json"
    store["zabgrab", "token2"] = '{"url": 5}'
    store["zabgrab", "token3"] = entry("https://c", "t")
    secrets = KeyringSecrets()
    assert secrets.urls() == ["https://c"]
    secrets.set_token("https://d", "t")
    assert store["zabgrab", "token4"] == entry("https://d", "t")


def test_delete_all(store: Store) -> None:
    secrets = KeyringSecrets()
    secrets.set_token("https://a", "t")
    secrets.set_token("https://b", "t")
    assert secrets.delete_all() == 2
    assert store == {}
    assert secrets.delete_all() == 0


def test_delete_all_reaches_past_gaps(store: Store) -> None:
    store["zabgrab", "token1"] = entry("https://a", "t")
    store["zabgrab", "token5"] = "leftover"
    store["zabgrab", f"token{5 + RESET_SCAN}"] = "far leftover"
    store["zabgrab", f"token{5 + 2 * RESET_SCAN + 1}"] = "beyond reach"
    store["other", "token2"] = "someone else's"
    assert KeyringSecrets().delete_all() == 1
    assert store == {
        ("zabgrab", f"token{5 + 2 * RESET_SCAN + 1}"): "beyond reach",
        ("other", "token2"): "someone else's",
    }


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
