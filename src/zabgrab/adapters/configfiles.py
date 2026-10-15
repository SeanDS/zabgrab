"""Config file discovery, loading and layering.

Layers, lowest to highest priority:

1. built-in defaults (:data:`zabgrab.model.config.BUILTIN_DEFAULTS`)
2. system: ``/etc/zabgrab/config.toml`` (Windows: ``%PROGRAMDATA%``)
3. user: ``$XDG_CONFIG_HOME/zabgrab/config.toml``, default ``~/.config/...`` on
   Linux and macOS (Windows: ``%APPDATA%`` unless ``XDG_CONFIG_HOME`` is set)
4. project: the nearest ``zabgrab.toml`` in the working directory or its parents,
   stopping at the home directory
5. explicit: ``--config PATH`` / ``$ZABGRAB_CONFIG``

``--isolated`` skips layers 2-4.

Merge rules: each key in ``[defaults]`` is taken from the highest layer that sets it.
Named entries (``servers.X``, ``templates.X``) are replaced whole by higher layers.

Attributes
----------
CONFIG_NAME : str
    The file name of system and user config files.
PROJECT_CONFIG_NAME : str
    The file name of project config files.
BUILTIN_SOURCE : str
    The source label of built-in values.
NAMED_SECTIONS : tuple of str
    The tables whose entries are replaced whole by higher layers.
"""

from __future__ import annotations

import difflib
import os
import tomllib
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, ValidationError
from tomlkit.items import Table

from .. import PROGRAM
from ..errors import ConfigError
from ..model.choice import UNION_TAGS, Choice
from ..model.config import (
    BUILTIN_DEFAULTS,
    Config,
    Defaults,
    ServerConfig,
    Template,
)
from ..model.item import ItemSpec
from .files import replacing

CONFIG_NAME = "config.toml"
PROJECT_CONFIG_NAME = f"{PROGRAM}.toml"
BUILTIN_SOURCE = "built-in"
NAMED_SECTIONS = ("servers", "templates")


class LayerKind(StrEnum):
    """Config layers, in increasing order of priority."""

    BUILTIN = "built-in"
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"
    EXPLICIT = "explicit"


@dataclass(frozen=True)
class Environment:
    """The parts of the process environment that affect config discovery.

    Attributes
    ----------
    variables : mapping of str to str
        Environment variables.
    cwd, home : Path
        The working and home directories.
    windows : bool
        Whether to use Windows locations.
    """

    variables: Mapping[str, str]
    cwd: Path
    home: Path
    windows: bool

    @classmethod
    def current(cls) -> Environment:
        """Get the running process's environment."""
        return cls(
            variables=dict(os.environ),
            cwd=Path.cwd(),
            home=Path.home(),
            windows=os.name == "nt",
        )


@dataclass(frozen=True)
class ConfigLayer:
    """One config layer and its file, if any.

    Attributes
    ----------
    kind : LayerKind
        Which layer.
    path : Path or None
        Its file (``None`` for built-in values, or no project file).
    exists : bool
        Whether the file exists.
    skipped : bool
        Whether ``--isolated`` skips it.
    """

    kind: LayerKind
    path: Path | None
    exists: bool
    skipped: bool = False

    @property
    def active(self) -> bool:
        """Whether this layer contributes to the config."""
        return self.exists and not self.skipped

    @property
    def source(self) -> str:
        """Label recorded as the source of values from this layer."""
        return BUILTIN_SOURCE if self.path is None else str(self.path)


@dataclass
class LoadedConfig:
    """The merged config, plus where each part of it came from.

    Attributes
    ----------
    config : Config
        The merged config.
    layers : tuple of ConfigLayer
        Every layer, lowest priority first.
    sources : dict of tuple of str to str
        Maps ``("defaults", key)``, ``("servers", name)`` and
        ``("templates", name)`` to the source label of the layer that provided the
        value.
    """

    config: Config
    layers: tuple[ConfigLayer, ...]
    sources: dict[tuple[str, ...], str] = field(default_factory=dict)

    def source_of(self, *path: str) -> str | None:
        """Get the source label of a ``[defaults]`` key or a named entry.

        Parameters
        ----------
        *path : str
            ``"defaults", key``, ``"servers", name`` or ``"templates", name``.

        Returns
        -------
        str or None
            The file path, ``"built-in"``, or ``None`` if nothing sets it.
        """
        return self.sources.get(path)


def system_config_path(env: Environment) -> Path:
    """Get the path of the system-wide config file."""
    if env.windows:
        base = Path(env.variables.get("PROGRAMDATA", r"C:\ProgramData"))
    else:
        base = Path("/etc")
    return base / PROGRAM / CONFIG_NAME


def user_config_path(env: Environment) -> Path:
    """Get the path of the user's config file."""
    xdg = env.variables.get("XDG_CONFIG_HOME", "")
    # The XDG spec says relative paths must be ignored.
    if xdg and Path(xdg).is_absolute():
        base = Path(xdg)
    elif env.windows:
        base = Path(env.variables.get("APPDATA", str(env.home / "AppData" / "Roaming")))
    else:
        base = env.home / ".config"
    return base / PROGRAM / CONFIG_NAME


def find_project_config(env: Environment) -> Path | None:
    """Find the nearest ``zabgrab.toml`` in the working directory or its parents.

    The search stops before the home directory, so a file there is never treated as
    a project config.

    Parameters
    ----------
    env : Environment
        Where to start, and the home directory.

    Returns
    -------
    Path or None
        The project config file, if there is one.
    """
    for directory in (env.cwd, *env.cwd.parents):
        if directory == env.home:
            break
        candidate = directory / PROJECT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def project_config_target(env: Environment) -> Path:
    """Get where the project config is, or would be created."""
    return find_project_config(env) or env.cwd / PROJECT_CONFIG_NAME


def discover_layers(
    env: Environment, *, explicit: Path | None = None, isolated: bool = False
) -> list[ConfigLayer]:
    """Find every config layer.

    Parameters
    ----------
    env : Environment
        Where to look.
    explicit : Path, optional
        A config file given with ``--config`` or ``$ZABGRAB_CONFIG``.
    isolated : bool, optional
        Skip the system, user and project layers (``--isolated``).

    Returns
    -------
    list of ConfigLayer
        The layers, lowest priority first, including ones without a file.
    """
    system = system_config_path(env)
    user = user_config_path(env)
    project = find_project_config(env)

    layers = [
        ConfigLayer(LayerKind.BUILTIN, None, exists=True),
        ConfigLayer(LayerKind.SYSTEM, system, system.is_file(), skipped=isolated),
        ConfigLayer(LayerKind.USER, user, user.is_file(), skipped=isolated),
        ConfigLayer(LayerKind.PROJECT, project, project is not None, skipped=isolated),
    ]

    if explicit is not None:
        explicit = env.cwd / explicit.expanduser()
        layers.append(ConfigLayer(LayerKind.EXPLICIT, explicit, explicit.is_file()))

    return layers


def load_config(layers: Iterable[ConfigLayer]) -> LoadedConfig:
    """Read, validate and merge config layers.

    Each file is validated on its own first, so errors point at the file that
    caused them.

    Parameters
    ----------
    layers : iterable of ConfigLayer
        The layers, lowest priority first.

    Returns
    -------
    LoadedConfig
        The merged config and where each value came from.

    Raises
    ------
    ConfigError
        If a file can't be read or is invalid, or an explicitly given file doesn't
        exist.
    """
    layers = tuple(layers)
    merged: dict[str, Any] = {}
    sources: dict[tuple[str, ...], str] = {}

    for layer in layers:
        if layer.kind is LayerKind.EXPLICIT and not layer.exists:
            raise ConfigError(
                f"Config file {layer.path} does not exist.",
                path=None,
                hint="Check the path given by --config or $ZABGRAB_CONFIG.",
            )
        if not layer.active:
            continue

        if layer.path is None:
            raw: dict[str, Any] = {"defaults": dict(BUILTIN_DEFAULTS)}
        else:
            raw = read_config_file(layer.path, kind=layer.kind)

        _merge_layer(merged, sources, raw, layer.source)

    try:
        config = Config.model_validate(merged)
    except ValidationError as exc:  # pragma: no cover - each layer was validated
        raise ConfigError(
            "Invalid merged config.", details=format_validation_errors(exc)
        ) from exc

    return LoadedConfig(config=config, layers=layers, sources=sources)


def read_config_file(path: Path, *, kind: LayerKind | None = None) -> dict[str, Any]:
    """Read and validate a single config file.

    Parameters
    ----------
    path : Path
        The file.
    kind : LayerKind, optional
        Its layer, for the hint on how to fix it.

    Returns
    -------
    dict
        The file's contents, as parsed TOML.

    Raises
    ------
    ConfigError
        If the file can't be read, isn't valid TOML, or isn't a valid config.
    """
    hint = edit_hint(path, kind)

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"Can't read config file {path}.", path=path, details=[str(exc)]
        ) from exc

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Invalid TOML in config file {path}.",
            path=path,
            details=[str(exc)],
            hint=hint,
        ) from exc

    try:
        Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid config file {path}.",
            path=path,
            details=format_validation_errors(exc),
            hint=hint,
        ) from exc

    return raw


def edit_hint(path: Path, kind: LayerKind | None) -> str:
    """Get a hint telling the user how to edit a config file."""
    if kind in (LayerKind.SYSTEM, LayerKind.USER, LayerKind.PROJECT):
        return f"Fix it with `zabgrab config edit --target {kind}`, or edit {path}."
    return f"Edit {path} to fix it."


def _merge_layer(
    merged: dict[str, Any],
    sources: dict[tuple[str, ...], str],
    raw: Mapping[str, Any],
    source: str,
) -> None:
    """Merge one layer into ``merged``, recording each value's source."""
    for key, value in raw.get("defaults", {}).items():
        merged.setdefault("defaults", {})[key] = value
        sources["defaults", key] = source

    for section in NAMED_SECTIONS:
        for name, entry in raw.get(section, {}).items():
            merged.setdefault(section, {})[name] = entry
            sources[section, name] = source


def _known_keys() -> set[str]:
    """Get every key a config file can have, for "did you mean" suggestions."""
    keys: set[str] = set()
    models: tuple[type[BaseModel], ...] = (
        Config,
        Defaults,
        Template,
        ServerConfig,
        ItemSpec,
        Choice,
    )
    for model in models:
        for name, info in model.model_fields.items():
            keys.add(info.alias or name)
    return keys


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Describe a validation error for users.

    Parameters
    ----------
    exc : pydantic.ValidationError
        The error.

    Returns
    -------
    list of str
        One line per problem, like ``templates.x.items[1]: unknown key 'nmae'
        (did you mean 'name'?)``.
    """
    known = _known_keys()
    lines = []

    for error in exc.errors(include_url=False):
        loc = [part for part in error["loc"] if part not in UNION_TAGS]
        kind = error["type"]
        message = error["msg"].removeprefix("Value error, ")

        if "[key]" in loc:
            # Invalid dict key (a template or server name). The message names it.
            loc = loc[: loc.index("[key]") - 1]
        elif kind == "extra_forbidden" and loc:
            unknown = str(loc.pop())
            message = f"unknown key {unknown!r}"
            if suggestions := difflib.get_close_matches(unknown, known, n=1):
                message += f" (did you mean {suggestions[0]!r}?)"
        elif kind == "missing" and loc:
            message = f"missing required key {loc.pop()!r}"
        elif kind not in ("value_error", "assertion_error"):
            message += f" (got {error['input']!r})"

        lines.append(f"{_format_loc(loc)}: {message}" if loc else message)

    return lines


def _format_loc(loc: Iterable[int | str]) -> str:
    """Write an error location as a dotted key path, e.g. ``items[1].name``."""
    text = ""
    for part in loc:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text


def add_server(path: Path, name: str, url: str) -> None:
    """Add ``[servers.NAME]`` to a config file, keeping its existing formatting.

    The file (and its directory) is created if needed.

    Parameters
    ----------
    path : Path
        The config file.
    name : str
        The server's name.
    url : str
        The server's URL.

    Raises
    ------
    ConfigError
        If the file can't be parsed, already defines the server, or can't be
        written.
    """
    document = _read_document(path)
    servers = _named_section(document, "servers", path)
    if name in servers:
        raise ConfigError(f"Server {name!r} already exists in {path}.", path=path)

    server = tomlkit.table()
    server.add("url", url)
    servers.add(name, server)
    write_atomically(path, tomlkit.dumps(document))


def add_template(
    path: Path, name: str, table: Mapping[str, Any], *, replace: bool = False
) -> str:
    """Add ``[templates.NAME]`` to a config file, keeping its existing formatting.

    The file (and its directory) is created if needed.

    Parameters
    ----------
    path : Path
        The config file.
    name : str
        The template's name.
    table : mapping
        The template's settings, as config values (see
        :func:`zabgrab.services.templates.template_table`).
    replace : bool, optional
        Replace a template with the same name (``--force``).

    Returns
    -------
    str
        The TOML of the template as written, for showing to the user.

    Raises
    ------
    ConfigError
        If the file can't be parsed or written, or already defines the template and
        ``replace`` isn't set.
    """
    document = _read_document(path)
    templates = _named_section(document, "templates", path)
    if name in templates and not replace:
        raise ConfigError(
            f"Template {name!r} already exists in {path}.",
            path=path,
            hint="Pass --force to replace it.",
        )

    template = _toml_table(table)
    if name in templates:
        templates[name] = template
    else:
        templates.add(name, template)
    write_atomically(path, tomlkit.dumps(document))

    shown = tomlkit.document()
    wrapper = tomlkit.table(is_super_table=True)
    wrapper.add(name, _toml_table(table))
    shown.add("templates", wrapper)
    return tomlkit.dumps(shown).strip()


def _read_document(path: Path, *, create: bool = True) -> tomlkit.TOMLDocument:
    """Parse a config file for editing.

    Parameters
    ----------
    path : Path
        The config file.
    create : bool, optional
        Treat a missing file as empty (it will be created), rather than an error.

    Raises
    ------
    ConfigError
        If the file can't be read or parsed.
    """
    try:
        text = "" if create and not path.exists() else path.read_text(encoding="utf-8")
        return tomlkit.parse(text)
    except (OSError, UnicodeDecodeError, tomlkit.exceptions.ParseError) as exc:
        raise ConfigError(
            f"Can't update config file {path}.", path=path, details=[str(exc)]
        ) from exc


def _named_section(document: tomlkit.TOMLDocument, section: str, path: Path) -> Table:
    """Get the ``servers`` or ``templates`` table, adding it if needed.

    Raises
    ------
    ConfigError
        If the document has the key but it isn't a table.
    """
    table = document.get(section)
    if table is None:
        table = tomlkit.table(is_super_table=True)
        document.add(section, table)
    if not isinstance(table, Table):
        raise ConfigError(f"'{section}' in {path} isn't a table.", path=path)
    return table


def _toml_table(values: Mapping[str, Any]) -> Table:
    """Build a TOML table of config values (see _toml_value)."""
    table = tomlkit.table()
    for key, value in values.items():
        table.add(key, _toml_value(value))
    return table


def _toml_value(value: Any) -> Any:
    """Convert a config value for tomlkit.

    Choices become inline tables, and lists of tables (items) go one per line.
    """
    if isinstance(value, Mapping):
        inline = tomlkit.inline_table()
        inline.update({k: _toml_value(v) for k, v in value.items()})
        return inline
    if isinstance(value, list):
        array = tomlkit.array()
        array.extend(_toml_value(v) for v in value)
        if any(isinstance(v, Mapping) for v in value):
            array.multiline(True)
        return array
    return value


def set_server_url(path: Path, name: str, url: str) -> None:
    """Change the URL of ``[servers.NAME]`` in a config file, keeping its formatting.

    Parameters
    ----------
    path : Path
        The config file that defines the server.
    name : str
        The server's name.
    url : str
        The new URL.

    Raises
    ------
    ConfigError
        If the file can't be parsed or written, or doesn't define the server.
    """
    document = _read_document(path, create=False)
    servers = document.get("servers")
    server = servers.get(name) if isinstance(servers, MutableMapping) else None
    if not isinstance(server, MutableMapping) or "url" not in server:
        raise ConfigError(f"Server {name!r} isn't defined in {path}.", path=path)
    server["url"] = url
    write_atomically(path, tomlkit.dumps(document))


def write_atomically(path: Path, text: str) -> None:
    """Write a config file via a temporary file, so it's never left half-written.

    Parameters
    ----------
    path : Path
        The file (created, with its directory, if needed).
    text : str
        The new contents.

    Raises
    ------
    ConfigError
        If the file can't be written.
    """
    try:
        with replacing(path) as temporary:
            temporary.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Can't write config file {path}.", path=path, details=[str(exc)]
        ) from exc
