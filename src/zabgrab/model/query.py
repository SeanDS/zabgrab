"""A fully resolved fetch request."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from .answers import Answers
from .choice import Choice
from .config import Agg, Format, Resolution
from .item import ItemSpec

#: Label for items given on the command line rather than by a template. Template
#: names can't start with "_", so it can't clash.
CUSTOM = "__custom"


@dataclass(frozen=True)
class Request:
    """Items to fetch.

    The template is a request; items given on the command line form another.

    Attributes
    ----------
    label : str
        The template's name, or :data:`CUSTOM`.
    items : tuple of ItemSpec
        The items to fetch (empty: ask which).
    """

    label: str
    items: tuple[ItemSpec, ...]


@dataclass(frozen=True)
class Query:
    """Everything needed to fetch and save data, with where each value came from.

    Attributes
    ----------
    requests : tuple of Request
        The template's items and any items given on the command line.
    template : str or None
        The template used, if any.
    server : str or Choice of str or None
        The server to fetch from: a name or URL, a choice to ask about, or ``None``
        (ask which configured server to use).
    custom : bool
        Items were added, or data-affecting template values changed, on the
        command line (or there was no template). Marks automatic file names.
    start, end : datetime
        The time range, as aware UTC datetimes.
    from_text, to_text : str
        The time range as given (e.g. ``"24 hours ago"``).
    resolution : Resolution
        Raw history or hourly trends.
    agg : tuple of Agg
        The trend statistics to save (hourly trends only).
    formats : tuple of Format
        Output formats; empty if not set (then taken from the output file name).
    output_dir, output_pattern : str
        Where automatic file names go, and how they're built.
    timezone : str
        The ``timezone`` setting, ``"local"`` or an IANA name.
    tz : tzinfo or None
        The time zone for times (``None`` for local time).
    jobs : int
        How many items to download at once.
    max_items : int
        Ask before fetching more items than this.
    mat_text_warn_entries : int
        Warn before writing more text values than this to a ``.mat`` file.
    sources : dict of str to str
        Where each setting came from, by config key.
    answers : Answers
        What the user answered at prompts (filled in as the fetch goes).
    """

    requests: tuple[Request, ...]
    template: str | None
    server: str | Choice[str] | None
    custom: bool
    start: datetime
    end: datetime
    from_text: str
    to_text: str
    resolution: Resolution
    agg: tuple[Agg, ...]
    formats: tuple[Format, ...]
    output_dir: str
    output_pattern: str
    timezone: str
    tz: tzinfo | None
    jobs: int
    max_items: int
    mat_text_warn_entries: int
    sources: dict[str, str] = field(default_factory=dict)
    answers: Answers = field(default_factory=Answers, compare=False)
