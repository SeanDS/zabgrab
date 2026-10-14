"""Adapters for the system clock and the user's editor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click


class SystemClock:
    """The real current time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ClickEditor:
    """Opens files in ``$VISUAL`` / ``$EDITOR`` (with a platform fallback)."""

    def edit(self, path: Path) -> None:
        click.edit(filename=str(path))
