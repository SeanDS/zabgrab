"""Item specifications: how the user selects Zabbix items."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from .choice import Choice, choice_or

NonEmptyStr = Annotated[str, Field(min_length=1)]

if TYPE_CHECKING:
    HostChoice = str | Choice[str]
else:
    HostChoice = choice_or(NonEmptyStr)


class ItemSpec(BaseModel):
    """Selects one item by ID, or any number of items by pattern.

    Patterns match like the Zabbix API's searches: the whole value, ignoring case,
    with ``*`` matching any text (so ``*CPU*`` finds ``CPU`` anywhere). All given
    patterns must match.

    Attributes
    ----------
    itemid : int or None
        A single item's ID; can't be combined with patterns.
    host : str or Choice of str or None
        A pattern for the host's technical or visible name, or a choice of them.
    name : str or None
        A pattern for the item's name.
    key : str or None
        A pattern for the item's key.
    tag : str or None
        A tag the item must have: ``"name"`` or ``"name:value"``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    itemid: PositiveInt | None = None
    host: HostChoice | None = None
    name: NonEmptyStr | None = None
    key: NonEmptyStr | None = None
    tag: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> ItemSpec:
        """Require either an item ID or at least one pattern, but not both."""
        patterns = {
            field: value
            for field, value in (
                ("host", self.host),
                ("name", self.name),
                ("key", self.key),
                ("tag", self.tag),
            )
            if value is not None
        }
        if self.itemid is not None and patterns:
            raise ValueError(
                "'itemid' identifies a single item and can't be combined with "
                + ", ".join(repr(field) for field in patterns)
            )
        if self.itemid is None and not patterns:
            raise ValueError(
                "an item needs at least one of 'itemid', 'host', 'name', 'key' or 'tag'"
            )
        return self
