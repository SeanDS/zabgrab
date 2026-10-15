"""Conversion between item specs and their command line form.

Forms accepted by ``--item``:

- ``12345``: an item ID
- ``host=demo1,name=Particles *``: comma-separated ``field=value`` pairs, with fields
  ``itemid``, ``host``, ``name``, ``key`` and ``tag``
- ``Particles *``: anything else without ``=`` is a name pattern

Values may contain commas (common in item keys such as ``net.if.in[eth0,bytes]``);
only a comma followed by ``field=`` starts a new pair.

Attributes
----------
FIELDS : tuple of str
    The fields an item spec can have.
EXAMPLE : str
    Examples of the command line form, for messages.
"""

from __future__ import annotations

import difflib
import re

from pydantic import ValidationError

from .choice import Choice
from .item import ItemSpec

FIELDS = ("itemid", "host", "name", "key", "tag")
EXAMPLE = "e.g. '12345', 'host=myhost,name=CPU*' or 'key=system.cpu.load[*]'"

_PAIR_SPLIT = re.compile(rf",(?=\s*(?:{'|'.join(FIELDS)})\s*=)")
_LOOKS_LIKE_FIELD = re.compile(r",\s*([A-Za-z_]+)\s*=")


def parse_item_spec(text: str) -> ItemSpec:
    """Parse the command line form of an item spec.

    Parameters
    ----------
    text : str
        An ``--item`` value (see the module docstring).

    Returns
    -------
    ItemSpec
        The item spec.

    Raises
    ------
    ValueError
        With a message suitable for showing to the user.
    """
    text = text.strip()
    if not text:
        raise ValueError(f"Empty item; expected {EXAMPLE}.")
    if text.isdigit():
        return _validate({"itemid": text})
    if "=" not in text:
        return _validate({"name": text})

    fields: dict[str, str] = {}
    for pair in _PAIR_SPLIT.split(text):
        field, equals, value = pair.partition("=")
        field = field.strip()
        if not equals or field not in FIELDS:
            raise ValueError(
                f"Can't parse {pair.strip()!r} in item {text!r}: expected "
                f"field=value with field one of {', '.join(FIELDS)}; {EXAMPLE}."
            )
        if field in fields:
            raise ValueError(f"Field {field!r} given twice in item {text!r}.")
        _check_for_typos(value, text)
        fields[field] = value.strip()

    return _validate(fields)


def format_item_spec(spec: ItemSpec) -> str:
    """Write an item spec in its command line form.

    The inverse of :func:`parse_item_spec`.

    Parameters
    ----------
    spec : ItemSpec
        The item spec.

    Returns
    -------
    str
        The ``--item`` value that gives ``spec``.

    Raises
    ------
    ValueError
        If the spec's host is a choice, which has no command line form.
    """
    if spec.itemid is not None:
        return str(spec.itemid)
    if isinstance(spec.host, Choice):
        raise ValueError(
            "Item specs with a choice of hosts can't be written as --item."
        )

    values = {
        "host": spec.host,
        "name": spec.name,
        "key": spec.key,
        "tag": spec.tag,
    }
    return ",".join(f"{field}={value}" for field, value in values.items() if value)


def _check_for_typos(value: str, text: str) -> None:
    """Reject a value with a mistyped field in it.

    A value containing ``,word=``, where ``word`` is close to a field name, is
    almost certainly a mistyped field rather than part of the value.

    Parameters
    ----------
    value : str
        A field's value.
    text : str
        The whole item spec, for the message.

    Raises
    ------
    ValueError
        If the value looks like it contains a mistyped field.
    """
    for match in _LOOKS_LIKE_FIELD.finditer(value):
        if suggestion := difflib.get_close_matches(match[1], FIELDS, n=1):
            raise ValueError(
                f"Unknown field {match[1]!r} in item {text!r} (did you mean "
                f"{suggestion[0]!r}?)."
            )


def _validate(fields: dict[str, str]) -> ItemSpec:
    """Build an item spec, turning validation errors into one plain message."""
    try:
        return ItemSpec.model_validate(fields)
    except ValidationError as exc:
        messages = "; ".join(
            error["msg"].removeprefix("Value error, ") for error in exc.errors()
        )
        raise ValueError(f"Invalid item: {messages}.") from None
