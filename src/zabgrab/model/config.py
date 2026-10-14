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

from .choice import Choice, choice_or
from .item import ItemSpec
from .names import Name, ServerRef, Url


class Format(StrEnum):
    """Output file formats."""

    MAT = "mat"
    H5 = "h5"
    TSV = "tsv"
    CSV = "csv"


class Resolution(StrEnum):
    """Data resolution: raw history or Zabbix's hourly trends."""

    RAW = "raw"
    HOURLY = "1h"


class Agg(StrEnum):
    """Hourly trend statistics."""

    MIN = "min"
    AVG = "avg"
    MAX = "max"
    COUNT = "count"


class AuthMethod(StrEnum):
    """How to authenticate with a Zabbix server."""

    TOKEN = "token"
    PASSWORD = "password"


def _validate_timezone(value: str) -> str:
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
    if len(set(values)) != len(values):
        raise ValueError("duplicate values")
    return values


def _validate_env_name(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError(f"invalid environment variable name {value!r}")
    return value


Timezone = Annotated[str, AfterValidator(_validate_timezone)]
AggList = Annotated[list[Agg], Field(min_length=1), AfterValidator(_validate_unique)]
EnvName = Annotated[str, AfterValidator(_validate_env_name)]
NonEmptyStr = Annotated[str, Field(min_length=1)]

if TYPE_CHECKING:
    ServerChoice = str | Choice[str]
    FormatChoice = Format | Choice[Format]
else:
    ServerChoice = choice_or(ServerRef)
    FormatChoice = choice_or(Format)

_MODEL_CONFIG = ConfigDict(
    extra="forbid", frozen=True, validate_by_name=True, validate_by_alias=True
)


class ServerConfig(BaseModel):
    """A Zabbix server."""

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
    is ``--from``, ``output_dir`` is ``--output-dir``, and so on).
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
    """A named, reusable fetch."""

    description: NonEmptyStr | None = None
    items: list[ItemSpec] = Field(default_factory=list)


class Config(BaseModel):
    """A config file, or the merged result of several."""

    model_config = _MODEL_CONFIG

    defaults: Defaults = Field(default_factory=Defaults)
    servers: dict[Name, ServerConfig] = Field(default_factory=dict)
    templates: dict[Name, Template] = Field(default_factory=dict)


#: Values used when no config layer sets them. Keys are ``[defaults]`` keys.
BUILTIN_DEFAULTS: dict[str, Any] = {
    "from": "1 hour ago",
    "to": "now",
    "resolution": "raw",
    "agg": ["min", "avg", "max"],
    "format": "mat",
    "output_dir": ".",
    "output_pattern": "{hosts}_{templates}_{from}--{to}",
    "timezone": "local",
    "jobs": 6,
    "max_items": 50,
    "mat_text_warn_entries": 50_000,
}
