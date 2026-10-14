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

    Patterns (``host``, ``name``, ``key``, ``tag``) may contain ``*`` wildcards. All
    given patterns must match. ``tag`` is ``"name"`` or ``"name:value"``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    itemid: PositiveInt | None = None
    host: HostChoice | None = None
    name: NonEmptyStr | None = None
    key: NonEmptyStr | None = None
    tag: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> ItemSpec:
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
