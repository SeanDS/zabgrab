"""Validation of user-defined names and server references.

Attributes
----------
NAME_PATTERN : re.Pattern
    What template and server names must match.
NAME_RULE : str
    The rule, for error messages.
Name, Url, ServerRef : type
    Annotated string types that validate a name, an HTTP(S) URL, or either.
"""

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
    """Check a template or server name.

    Parameters
    ----------
    name : str
        The name.

    Returns
    -------
    str
        ``name``, unchanged.

    Raises
    ------
    ValueError
        If it isn't a valid name.
    """
    if not is_valid_name(name):
        raise ValueError(f"invalid name {name!r}: {NAME_RULE}")
    return name


def is_url(value: str) -> bool:
    """Whether ``value`` is an absolute HTTP(S) URL."""
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def validate_url(value: str) -> str:
    """Check a server URL.

    Parameters
    ----------
    value : str
        The URL.

    Returns
    -------
    str
        ``value``, unchanged.

    Raises
    ------
    ValueError
        If it isn't an absolute HTTP(S) URL.
    """
    if not is_url(value):
        raise ValueError(
            f"invalid URL {value!r}: expected e.g. 'https://zabbix.example.org'"
        )
    return value


def frontend_url(url: str) -> str:
    """Get the web frontend's base URL.

    Parameters
    ----------
    url : str
        The frontend URL, or its API URL (``.../api_jsonrpc.php``).

    Returns
    -------
    str
        The URL without the API script, trailing slash, query or fragment.
    """
    parts = urlsplit(url)
    path = parts.path
    if path.endswith(".php"):
        path = path.rsplit("/", 1)[0]
    return parts._replace(path=path.rstrip("/"), query="", fragment="").geturl()


def validate_server_ref(value: str) -> str:
    """Check a server reference: a server's name or a URL.

    Parameters
    ----------
    value : str
        The reference.

    Returns
    -------
    str
        ``value``, unchanged.

    Raises
    ------
    ValueError
        If it's neither a valid name nor an absolute HTTP(S) URL.
    """
    if is_valid_name(value) or is_url(value):
        return value
    raise ValueError(
        f"invalid server {value!r}: expected the name of a configured server or a "
        "URL including the scheme, e.g. 'https://zabbix.example.org'"
    )


Name = Annotated[str, AfterValidator(validate_name)]
Url = Annotated[str, AfterValidator(validate_url)]
ServerRef = Annotated[str, AfterValidator(validate_server_ref)]
