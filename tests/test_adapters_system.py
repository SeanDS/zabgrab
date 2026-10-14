"""System clock and editor adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from zabgrab.adapters.system import ClickEditor, SystemClock


def test_system_clock_is_aware_utc() -> None:
    before = datetime.now(UTC)
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert before <= now <= datetime.now(UTC)


def test_click_editor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr("click.edit", lambda filename: calls.append(filename))
    ClickEditor().edit(tmp_path / "x.toml")
    assert calls == [str(tmp_path / "x.toml")]
