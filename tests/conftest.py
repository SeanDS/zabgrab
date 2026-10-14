"""Shared fixtures."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    # Never look at (or write to) the real /etc, wherever the path is looked up.
    for module in ("zabgrab.adapters.configfiles", "zabgrab.cli.config"):
        monkeypatch.setattr(
            f"{module}.system_config_path",
            lambda env: tmp_path / "etc" / "zabgrab" / "config.toml",
        )


UK = ZoneInfo("Europe/London")


def pytest_configure(config: pytest.Config) -> None:
    """Windows can't change a running process's time zone, so it must be UK time.

    CI sets it with ``tzutil``. Elsewhere :func:`_local_time_zone` sets it.
    """
    in_uk_time = all(
        datetime(2026, month, 1, tzinfo=UTC).astimezone().utcoffset()
        == datetime(2026, month, 1, tzinfo=UK).utcoffset()
        for month in (1, 7)
    )
    if not hasattr(time, "tzset") and not in_uk_time:
        raise pytest.UsageError(
            'Run the tests in UK time: tzutil /s "GMT Standard Time"'
        )


@pytest.fixture(autouse=True)
def _local_time_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run every test in UK time, which expected local times are written in."""
    if not hasattr(time, "tzset"):
        yield  # see pytest_configure
        return
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(autouse=True)
def _modern_windows_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render like Windows Terminal, as on other systems.

    Without a real console (as under pytest or in CI), rich assumes an old Windows
    console: 16 colours and plain table header borders.
    """
    monkeypatch.setattr("rich.console.detect_legacy_windows", lambda: False)


@pytest.fixture
def locked_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory nothing can be created in.

    Root ignores directory permissions (as in CI containers), and Windows ignores
    POSIX modes, so then the directory also makes :func:`os.open` and
    :func:`os.mkdir` fail inside it.
    """
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    if os.name == "nt" or os.geteuid() == 0:
        real_open, real_mkdir = os.open, os.mkdir

        def refuse(path: Any) -> None:
            if Path(os.fsdecode(path)).is_relative_to(locked):
                raise PermissionError(13, "Permission denied", os.fsdecode(path))

        def fake_open(path: Any, *args: Any, **kwargs: Any) -> int:
            refuse(path)
            return real_open(path, *args, **kwargs)

        def fake_mkdir(path: Any, *args: Any, **kwargs: Any) -> None:
            refuse(path)
            real_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", fake_open)
        monkeypatch.setattr(os, "mkdir", fake_mkdir)
    yield locked
    locked.chmod(0o700)


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
