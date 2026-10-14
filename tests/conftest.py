"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from zabgrab.adapters.configfiles import Environment


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
