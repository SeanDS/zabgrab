"""What the user answered at prompts during a fetch.

Used to show the equivalent command ("Next time: zabgrab fetch ...") and to save
the fetch as a template with ``--save-template``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .choice import Choice
from .item import ItemSpec


@dataclass
class Answers:
    """Answers given at prompts, in the form the command line or a template takes."""

    template: str | None = None
    """The template chosen instead of an unknown one."""
    settings: dict[str, Any] = field(default_factory=dict)
    """Settings by config key, e.g. ``{"from": "24 hours ago", "format": ["mat"]}``."""
    server: str | None = None
    """The server name or URL chosen."""
    items: dict[str, tuple[ItemSpec, ...]] = field(default_factory=dict)
    """Request label → the items chosen, replacing the request's own."""
    all_items: bool = False
    """The user chose to fetch every match despite ``max_items`` (``--yes``)."""

    def __bool__(self) -> bool:
        return bool(
            self.template
            or self.settings
            or self.server
            or self.items
            or self.all_items
        )


def config_value(value: Any) -> Any:
    """``value`` as it's written in a config file (enums as strings, choices as
    tables, tuples as lists)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Choice):
        return {
            "choices": [config_value(v) for v in value.choices],
            "select": value.select.value,
        }
    if isinstance(value, list | tuple):
        return [config_value(v) for v in value]
    return value
