"""Config file schema.

Every config file (and the merged result of all of them) validates against
:class:`Config`. All settings are optional in the schema; the values used when
nothing is configured live in :data:`BUILTIN_DEFAULTS`, which is treated as the
lowest config layer so that every resolved value has a recorded source.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
)

from .choice import Choice, Select, choice_or
from .item import ItemSpec
from .names import Name, ServerRef, Url


class Format(StrEnum):
    """Output file formats.

    Attributes
    ----------
    H5, MAT
        HDF5 and MATLAB v7.3 (the same layout), which can hold several fetches.
    TSV, CSV
        Tab- and comma-separated values, with ``#`` comment lines of metadata.
    """

    H5 = "h5"
    MAT = "mat"
    TSV = "tsv"
    CSV = "csv"


class Resolution(StrEnum):
    """Data resolution.

    Attributes
    ----------
    RAW
        Every value collected (Zabbix's history).
    HOURLY
        Hourly statistics (Zabbix's trends), for numeric items only.
    """

    RAW = "raw"
    HOURLY = "1h"


class Agg(StrEnum):
    """Hourly trend statistics: the minimum, average, maximum and count."""

    MIN = "min"
    AVG = "avg"
    MAX = "max"
    COUNT = "count"


class AuthMethod(StrEnum):
    """How to authenticate with a Zabbix server."""

    TOKEN = "token"
    PASSWORD = "password"


def _validate_timezone(value: str) -> str:
    """Accept ``"local"`` or a known IANA time zone name."""
    if value == "local":
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"unknown timezone {value!r}: use 'local' or an IANA name such as "
            "'UTC' or 'Europe/London'"
        ) from None
    return value


def _validate_unique(values: list[Agg]) -> list[Agg]:
    """Reject repeated statistics."""
    if len(set(values)) != len(values):
        raise ValueError("duplicate values")
    return values


def _validate_env_name(value: str) -> str:
    """Accept only valid environment variable names."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError(f"invalid environment variable name {value!r}")
    return value


Timezone = Annotated[str, AfterValidator(_validate_timezone)]
AggList = Annotated[list[Agg], Field(min_length=1), AfterValidator(_validate_unique)]
EnvName = Annotated[str, AfterValidator(_validate_env_name)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


def _ask_only(value: Any) -> Any:
    """Allow a choice of servers only as a question, since a fetch uses one."""
    if isinstance(value, Choice) and value.select is not Select.PROMPT:
        raise ValueError(
            "a choice of servers is only for choosing one when asked (zabgrab "
            "fetches from one server at a time), so its select must be 'prompt'"
        )
    return value


if TYPE_CHECKING:
    ServerChoice = str | Choice[str]
    FormatChoice = Format | Choice[Format]
else:
    ServerChoice = Annotated[choice_or(ServerRef), AfterValidator(_ask_only)]
    FormatChoice = choice_or(Format)

_MODEL_CONFIG = ConfigDict(
    extra="forbid", frozen=True, validate_by_name=True, validate_by_alias=True
)


class ServerConfig(BaseModel):
    """A Zabbix server (a ``[servers.NAME]`` table).

    Attributes
    ----------
    url : str
        The frontend or API URL.
    auth : AuthMethod
        Log in with an API token (default) or a username and password.
    token_env : str or None
        An environment variable holding the token (else the keyring is used).
    username : str or None
        The default username for password logins.
    verify_ssl : bool
        Whether to check the server's TLS certificate.
    timeout : float
        Seconds to wait for each API request.
    """

    model_config = _MODEL_CONFIG

    url: Url
    auth: AuthMethod = AuthMethod.TOKEN
    token_env: EnvName | None = None
    username: NonEmptyStr | None = None
    verify_ssl: bool = True
    timeout: PositiveFloat = 30.0


class FetchSettings(BaseModel):
    """Settings shared by ``[defaults]`` and templates.

    Each field corresponds to a ``zabgrab fetch`` option of the same name (``from``
    is ``--from``, ``output_dir`` is ``--output-dir``, and so on), except
    ``mat_text_warn_entries``, which is config only. Unset fields are ``None``.
    """

    model_config = _MODEL_CONFIG

    server: ServerChoice | None = None
    from_: NonEmptyStr | None = Field(default=None, alias="from")
    to: NonEmptyStr | None = None
    resolution: Resolution | None = None
    agg: AggList | None = None
    format: FormatChoice | None = None
    output_dir: NonEmptyStr | None = None
    output_pattern: NonEmptyStr | None = None
    timezone: Timezone | None = None
    jobs: PositiveInt | None = None
    max_items: PositiveInt | None = None
    mat_text_warn_entries: NonNegativeInt | None = None


class Defaults(FetchSettings):
    """The ``[defaults]`` table: settings applied to every run."""


class Template(FetchSettings):
    """A named, reusable fetch (a ``[templates.NAME]`` table).

    Attributes
    ----------
    description : str or None
        What the template is for.
    items : list of ItemSpec
        The items to fetch.
    """

    description: NonEmptyStr | None = None
    items: list[ItemSpec] = Field(default_factory=list)


class Config(BaseModel):
    """A config file, or the merged result of several.

    Attributes
    ----------
    defaults : Defaults
        Settings for every fetch.
    servers : dict of str to ServerConfig
        The configured servers, by name.
    templates : dict of str to Template
        The templates, by name.
    """

    model_config = _MODEL_CONFIG

    defaults: Defaults = Field(default_factory=Defaults)
    servers: dict[Name, ServerConfig] = Field(default_factory=dict)
    templates: dict[Name, Template] = Field(default_factory=dict)


#: Values used when no config layer sets them. Keys are ``[defaults]`` keys.
#:
#: ``from``, ``to`` and ``format`` deliberately have no built-in value: when nothing
#: sets them, the user is asked (or the format is taken from the output file name).
BUILTIN_DEFAULTS: dict[str, Any] = {
    "resolution": "raw",
    "agg": ["min", "avg", "max"],
    "output_dir": ".",
    "output_pattern": "{hosts}_{template}_{from}--{to}",
    "timezone": "local",
    "jobs": 6,
    "max_items": 50,
    # Each text entry costs about 480 bytes and 60 us to write in a .mat file
    # (MATLAB loads around 100,000 a second); see benchmarks/mat_text.py.
    "mat_text_warn_entries": 200_000,
}
