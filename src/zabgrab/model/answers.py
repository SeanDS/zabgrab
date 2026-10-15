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
    """Answers given at prompts, in the form the command line or a template takes.

    Attributes
    ----------
    template : str or None
        The template chosen instead of an unknown one.
    settings : dict of str to Any
        Settings by config key, e.g. ``{"from": "24 hours ago", "format": ["mat"]}``.
    server : str or None
        The server name or URL chosen.
    items : dict of str to tuple of ItemSpec
        Request label to the items chosen, replacing the request's own.
    all_items : bool
        The user chose to fetch every match despite ``max_items`` (``--yes``).
    """

    template: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    server: str | None = None
    items: dict[str, tuple[ItemSpec, ...]] = field(default_factory=dict)
    all_items: bool = False

    def __bool__(self) -> bool:
        """Whether anything was answered."""
        return bool(
            self.template
            or self.settings
            or self.server
            or self.items
            or self.all_items
        )


def config_value(value: Any) -> Any:
    """Convert a setting's value to the form it takes in a config file.

    Parameters
    ----------
    value : Any
        A setting's value, as parsed (e.g. an enum or a :class:`Choice`).

    Returns
    -------
    Any
        The value with enums as strings, choices as tables and tuples as lists.
    """
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
