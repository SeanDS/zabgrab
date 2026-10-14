"""Helpers for writing files safely."""

from __future__ import annotations

import os
from pathlib import Path


def match_permissions(temporary: Path, path: Path) -> None:
    """Give a temporary file the permissions ``path`` should end up with.

    Temporary files are private (0600). When one replaces ``path``, it should keep
    ``path``'s permissions, or get the usual ones for new files (from the umask).
    """
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    temporary.chmod(mode)
