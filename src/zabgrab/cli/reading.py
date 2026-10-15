"""Options and helpers shared by commands that read a file's merged data.

``zabgrab export``, ``zabgrab info`` and ``zabgrab plot`` select items, times and
groups the same way.
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
from ..model.item import ItemSpec
from ..model.itemspec import EXAMPLE
from ..services.dataset import Dataset, filter_dataset, merge, select_groups
from .app import AppContext
from .discovery import ItemSpecType

F = TypeVar("F", bound=Callable[..., Any])


def selection_options(function: F) -> F:
    """Add the ``--item``, ``--match``, ``--from``, ``--to`` and ``--group`` options.

    Parameters
    ----------
    function : callable
        The click command to add them to.

    Returns
    -------
    callable
        The command, taking ``items``, ``match``, ``from_``, ``to`` and ``groups``
        arguments.
    """
    for option in reversed(
        [
            click.option(
                "-i",
                "--item",
                "items",
                type=ItemSpecType(),
                multiple=True,
                help=f"Only these items, selected as for fetch (repeat for several); "
                f"{EXAMPLE}.",
            ),
            click.option(
                "-m",
                "--match",
                multiple=True,
                metavar="PATTERN",
                help="Only items whose whole ID, name, key or 'host: name' matches "
                "this pattern ('*' wildcards, any case; repeat for several).",
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
    items: Sequence[ItemSpec],
    match: Sequence[str],
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
    items, match, from_, to, groups
        The ``--item``, ``--match``, ``--from``, ``--to`` and ``--group`` values.
        Items are kept if any ``--item`` or ``--match`` selects them.

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
    check_readable(file, verb)
    start, end, tz = time_range(app, from_, to)
    dataset = merge(select_groups(hdf5.read_fetches(file), groups), app.ui)
    selected = filter_dataset(
        dataset, items=items, patterns=match, start=start, end=end
    )
    return selected, tz


def check_readable(file: Path, verb: tuple[str, str]) -> None:
    """Refuse delimited files, which commands reading merged data can't read.

    Parameters
    ----------
    file : Path
        The file.
    verb : (str, str)
        What the command does, and its past participle, for the message.

    Raises
    ------
    ZabgrabError
        If ``file`` is a delimited file.
    """
    if file.suffix.lower() in (".tsv", ".csv", ".txt"):
        raise ZabgrabError(
            f"Can't {verb[0]} {file}: only .mat and .h5 files can be {verb[1]}.",
            hint="Tab- and comma-separated files can be read directly.",
        )


def time_range(
    app: AppContext, from_: str | None, to: str | None
) -> tuple[datetime | None, datetime | None, tzinfo | None]:
    """Parse ``--from`` and ``--to``, in the configured time zone.

    Parameters
    ----------
    app : AppContext
        For the config (the time zone) and the clock.
    from_, to : str or None
        The options' values, if given.

    Returns
    -------
    start, end : datetime or None
        The times, if given.
    tz : tzinfo or None
        The configured time zone (``None`` for local time).

    Raises
    ------
    click.BadParameter
        If a time can't be parsed.
    """
    tz = timezone_for(app.config().config.defaults.timezone or "local")
    now = app.clock.now()

    def time(text: str | None, option: str) -> datetime | None:
        """Parse an optional time option."""
        if text is None:
            return None
        try:
            return parse_time(text, now=now, tz=tz)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint=option) from None

    return time(from_, "--from"), time(to, "--to"), tz


def nothing_selected(file: Path) -> str:
    """Suggest how to find what to select in a file, when nothing was.

    Parameters
    ----------
    file : Path
        The file.

    Returns
    -------
    str
        The hint.
    """
    return (
        f"Check --item, --match, --from and --to; `zabgrab info {file}` shows what "
        "the file holds."
    )
