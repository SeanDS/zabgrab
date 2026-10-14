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
    """Items to fetch from one server (or choice of servers).

    Each template is a request; items given on the command line form another.
    """

    label: str
    """The template's name, or :data:`CUSTOM`."""
    server: str | Choice[str] | None
    """A server name or URL, a choice of them, or ``None`` (ask)."""
    items: tuple[ItemSpec, ...]


@dataclass(frozen=True)
class Query:
    """Everything needed to fetch and save data, with where each value came from."""

    requests: tuple[Request, ...]
    templates: tuple[str, ...]
    custom: bool
    """Items were added, or data-affecting template values changed, on the command
    line (or there were no templates)."""
    start: datetime
    end: datetime
    from_text: str
    to_text: str
    resolution: Resolution
    agg: tuple[Agg, ...]
    formats: tuple[Format, ...]
    """Output formats; empty if not set (then taken from the output file name)."""
    output_dir: str
    output_pattern: str
    timezone: str
    tz: tzinfo | None
    """For displaying times (``None`` for local time)."""
    jobs: int
    max_items: int
    mat_text_warn_entries: int
    sources: dict[str, str] = field(default_factory=dict)
    """Where each setting came from, by config key."""
    answers: Answers = field(default_factory=Answers, compare=False)
    """What the user answered at prompts (filled in as the fetch goes)."""
