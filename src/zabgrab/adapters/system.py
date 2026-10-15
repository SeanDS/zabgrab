"""Adapters for the system clock and the user's editor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click


class SystemClock:
    """The real current time."""

    def now(self) -> datetime:
        """Get the current time, as an aware UTC datetime."""
        return datetime.now(UTC)


class ClickEditor:
    """Opens files in ``$VISUAL`` / ``$EDITOR`` (with a platform fallback)."""

    def edit(self, path: Path) -> None:
        """Open ``path`` in the editor and wait for it to close."""
        click.edit(filename=str(path))
