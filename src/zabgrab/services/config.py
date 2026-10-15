"""Config loading with interactive repair of invalid files."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from ..errors import ConfigError
from .ports import UI, Editor

T = TypeVar("T")


def load_with_repair(load: Callable[[], T], ui: UI, editor: Editor) -> T:
    """Call ``load``; if a config file is invalid, offer to open it for editing.

    Interactive sessions are shown the error and offered the editor, after which
    loading is retried. Otherwise (or if the user declines) the error is raised.

    Parameters
    ----------
    load : callable
        Loads the config.
    ui : UI
        Shows the error and asks about editing.
    editor : Editor
        Opens the file.

    Returns
    -------
    T
        What ``load`` returns.

    Raises
    ------
    ConfigError
        If a file is invalid and isn't fixed (marked as reported if the user was
        already shown it).
    """
    while True:
        try:
            return load()
        except ConfigError as err:
            if err.path is None or not ui.interactive:
                raise

            ui.error(err.message, details=err.details)
            if not ui.confirm(
                f"Open {err.path} in your editor to fix it?",
                fix=err.hint or f"Edit {err.path}.",
            ):
                err.reported = True
                raise

            editor.edit(err.path)
