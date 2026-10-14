"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from zabgrab.adapters.configfiles import Environment

from .cli_support import CONFIG, Servers


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def env(tmp_path: Path, fake_home: Path) -> Environment:
    """A POSIX environment with a fake home and a working directory outside it.

    The system config path is still ``/etc/zabgrab/config.toml``; tests that need a
    system config monkeypatch :func:`system_config_path`.
    """
    cwd = tmp_path / "work"
    cwd.mkdir()
    return Environment(variables={}, cwd=cwd, home=fake_home, windows=False)


@pytest.fixture(autouse=True)
def _isolate_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_home: Path
) -> None:
    """Keep tests away from the real user's config, terminal and CI settings."""
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    for name in ("ZABGRAB_CONFIG", "ZABGRAB_NO_INPUT", "CI", "NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    # Never look at the real /etc.
    monkeypatch.setattr(
        "zabgrab.adapters.configfiles.system_config_path",
        lambda env: tmp_path / "etc" / "zabgrab" / "config.toml",
    )


# CLI fixtures (see tests/cli_support.py).


@pytest.fixture
def user_config(fake_home: Path) -> Path:
    path = fake_home / ".config" / "zabgrab" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(CONFIG)
    return path


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch, user_config: Path) -> Servers:
    fake = Servers()
    monkeypatch.setattr("zabgrab.cli.app.http_client", fake.factory)
    monkeypatch.setattr("zabgrab.cli.app.KeyringSecrets", lambda: fake.secrets)
    monkeypatch.setattr("zabgrab.cli.app.SystemClock", lambda: fake.clock)
    return fake


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zabgrab.cli.app.is_interactive", lambda *args, **kwargs: True)
