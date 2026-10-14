"""Validation of user-defined names and server references."""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator

NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
NAME_RULE = (
    "names must start with a letter and contain only letters, digits, '_' and '-' "
    "(names starting with '_' are reserved)"
)


def is_valid_name(name: str) -> bool:
    """Whether ``name`` is allowed as a template or server name."""
    return NAME_PATTERN.fullmatch(name) is not None


def validate_name(name: str) -> str:
    """Return ``name`` if it is a valid template or server name, else raise."""
    if not is_valid_name(name):
        raise ValueError(f"invalid name {name!r}: {NAME_RULE}")
    return name


def is_url(value: str) -> bool:
    """Whether ``value`` is an absolute HTTP(S) URL."""
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def validate_url(value: str) -> str:
    """Return ``value`` if it is an absolute HTTP(S) URL, else raise."""
    if not is_url(value):
        raise ValueError(
            f"invalid URL {value!r}: expected e.g. 'https://zabbix.example.org'"
        )
    return value


def validate_server_ref(value: str) -> str:
    """Return ``value`` if it is a server name or an absolute HTTP(S) URL."""
    if is_valid_name(value) or is_url(value):
        return value
    raise ValueError(
        f"invalid server {value!r}: expected the name of a configured server or a "
        "URL including the scheme, e.g. 'https://zabbix.example.org'"
    )


Name = Annotated[str, AfterValidator(validate_name)]
Url = Annotated[str, AfterValidator(validate_url)]
ServerRef = Annotated[str, AfterValidator(validate_server_ref)]
