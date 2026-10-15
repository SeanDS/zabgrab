"""Helpers for writing files safely."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def replacing(path: Path, *, copy: bool = False) -> Iterator[Path]:
    """Write a file via a temporary file, so it's never left half-written.

    The temporary file is created next to ``path`` (so replacing it is atomic) and
    replaces ``path`` when the ``with`` block ends without an error. If it fails,
    the temporary file is deleted and ``path`` is left as it was.

    Parameters
    ----------
    path : Path
        The file to write. Its directory is created if needed.
    copy : bool, optional
        Start the temporary file as a copy of ``path``, to change it rather than
        write it from scratch.

    Yields
    ------
    Path
        The temporary file to write to.

    Raises
    ------
    OSError
        If the file can't be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
    try:
        if copy:
            shutil.copy2(path, temporary)
        yield temporary
        match_permissions(temporary, path)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def match_permissions(temporary: Path, path: Path) -> None:
    """Give a temporary file the permissions ``path`` should end up with.

    Temporary files are private (0600). When one replaces ``path``, it should keep
    ``path``'s permissions, or get the usual ones for new files (from the umask).

    Parameters
    ----------
    temporary : Path
        The temporary file, about to replace ``path``.
    path : Path
        The file being written (which may not exist yet).
    """
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        # The umask can only be read by setting it, so set it back straight away.
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    temporary.chmod(mode)
