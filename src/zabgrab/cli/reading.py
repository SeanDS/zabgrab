"""Options and helpers shared by commands that read a file's merged data.

``zabgrab export`` and ``zabgrab plot`` select items, times and groups the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, TypeVar

import click

from ..adapters.dates import EXAMPLES, parse_time, timezone_for
from ..adapters.formats import hdf5
from ..errors import ZabgrabError
from ..services.dataset import Dataset, filter_dataset, merge, select_groups
from .app import AppContext

F = TypeVar("F", bound=Callable[..., Any])


def selection_options(function: F) -> F:
    """Add the ``--item``, ``--from``, ``--to`` and ``--group`` options.

    Parameters
    ----------
    function : callable
        The click command to add them to.

    Returns
    -------
    callable
        The command, taking ``items``, ``from_``, ``to`` and ``groups`` arguments.
    """
    for option in reversed(
        [
            click.option(
                "-i",
                "--item",
                "items",
                multiple=True,
                metavar="PATTERN",
                help="Only items whose ID, name, key or 'host: name' matches (* "
                "wildcards; repeat for several).",
            ),
            click.option(
                "--from",
                "from_",
                metavar="TIME",
                help=f"Only data from this time, {EXAMPLES}.",
            ),
            click.option("--to", metavar="TIME", help="Only data up to this time."),
            click.option(
                "--group",
                "groups",
                multiple=True,
                metavar="NAME",
                help="Only these fetch groups (repeat for several; default all).",
            ),
        ]
    ):
        function = option(function)
    return function


def read_dataset(
    app: AppContext,
    file: Path,
    *,
    verb: tuple[str, str],
    items: Sequence[str],
    from_: str | None,
    to: str | None,
    groups: Sequence[str],
) -> tuple[Dataset, tzinfo | None]:
    """Read a file's merged data, limited to the selected items, times and groups.

    Parameters
    ----------
    app : AppContext
        For the config (the time zone ``--from``/``--to`` are in), clock and UI.
    file : Path
        A ``.mat`` or ``.h5`` file written by zabgrab.
    verb : (str, str)
        What the command does, and its past participle, for messages (e.g.
        ``("export", "exported")``).
    items, from_, to, groups
        The ``--item``, ``--from``, ``--to`` and ``--group`` values.

    Returns
    -------
    Dataset
        The merged, filtered data.
    tzinfo or None
        The configured time zone (``None`` for local time).

    Raises
    ------
    ZabgrabError
        If ``file`` is a delimited file, or has no such groups.
    click.BadParameter
        If ``--from`` or ``--to`` can't be parsed.
    """
    if file.suffix.lower() in (".tsv", ".csv", ".txt"):
        raise ZabgrabError(
            f"Can't {verb[0]} {file}: only .mat and .h5 files can be {verb[1]}.",
            hint="Tab- and comma-separated files can be read directly.",
        )
    tz = timezone_for(app.config().config.defaults.timezone or "local")
    now = app.clock.now()

    def time(text: str | None, option: str) -> datetime | None:
        if text is None:
            return None
        try:
            return parse_time(text, now=now, tz=tz)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint=option) from None

    start, end = time(from_, "--from"), time(to, "--to")
    dataset = merge(select_groups(hdf5.read_fetches(file), groups), app.ui)
    return filter_dataset(dataset, items=items, start=start, end=end), tz
