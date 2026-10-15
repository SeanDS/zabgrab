"""Choice values: a list of options plus a rule for selecting between them.

Config keys that support choices accept either a plain value or a table such as
``{ choices = ["a", "b"], select = "all" }``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Generic, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

T = TypeVar("T")


class Select(StrEnum):
    """How to select between the options of a :class:`Choice`.

    Attributes
    ----------
    ALL
        Use every option, stacking the results.
    PROMPT
        Ask the user (an error if non-interactive). The only one allowed for
        servers, since a fetch uses one server.
    FIRST
        Use the first option that works (for hosts, the first with matching
        items).
    """

    ALL = "all"
    PROMPT = "prompt"
    FIRST = "first"


class Choice(BaseModel, Generic[T]):
    """A list of options and a selection rule.

    Attributes
    ----------
    choices : list
        The options, without duplicates.
    select : Select
        How to choose between them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    choices: list[T] = Field(min_length=1)
    select: Select = Select.PROMPT

    @model_validator(mode="after")
    def _unique(self) -> Choice[T]:
        """Reject duplicate options."""
        seen: list[T] = []
        for choice in self.choices:
            if choice in seen:
                raise ValueError(f"duplicate choice {choice!r}")
            seen.append(choice)
        return self


#: Tags used by the discriminated union below. They appear in validation error
#: locations and are stripped when errors are formatted for the user. They contain
#: characters that are invalid in names, so they can't clash with config keys.
SINGLE_TAG = "<value>"
CHOICE_TAG = "<choice>"
UNION_TAGS = frozenset({SINGLE_TAG, CHOICE_TAG})


def _discriminate(value: Any) -> str:
    """Tell a choice (a table) from a single value, for pydantic's union."""
    return CHOICE_TAG if isinstance(value, dict | Choice) else SINGLE_TAG


def choice_or(type_: Any) -> Any:
    """Make the type of a setting that takes a value or a choice of values.

    A generic type alias can't express this: ``Choice[T]`` is a concrete pydantic
    class, so substituting ``T`` later doesn't reach it.

    Parameters
    ----------
    type_ : type
        The type of a single value.

    Returns
    -------
    type
        A pydantic type accepting a ``type_`` or a :class:`Choice` of ``type_``.
    """
    return Annotated[
        Union[  # noqa: UP007
            Annotated[type_, Tag(SINGLE_TAG)],
            Annotated[Choice[type_], Tag(CHOICE_TAG)],
        ],
        Discriminator(_discriminate),
    ]
