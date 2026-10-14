"""Rendering of command results for the terminal."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import tomlkit
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from tomlkit.items import Array, InlineTable
from tomlkit.items import Item as TomlItem

from ..adapters.configfiles import ConfigLayer, LayerKind, LoadedConfig
from ..model.server import Server
from ..model.zabbix import Host, Item
from ..services.tokens import CheckResult, CheckState, TokenStatus


def layers_table(layers: Iterable[ConfigLayer]) -> Table:
    """Table of config layers, lowest priority first."""
    table = Table(
        title="Config files (lowest to highest priority)", title_justify="left"
    )
    table.add_column("Layer", style="bold")
    table.add_column("Path", style="cyan", overflow="fold")
    table.add_column("Status")

    for layer in layers:
        if layer.kind is LayerKind.BUILTIN:
            path, status = "-", "[green]always used[/]"
        elif layer.path is None:
            path, status = "(no zabgrab.toml found)", "[dim]not found[/]"
        else:
            path = str(layer.path)
            if layer.skipped:
                status = "[yellow]skipped (--isolated)[/]"
            elif layer.exists:
                status = "[green]loaded[/]"
            elif layer.kind is LayerKind.EXPLICIT:
                status = "[red]missing[/]"
            else:
                status = "[dim]not found[/]"
        table.add_row(str(layer.kind), path, status)

    return table


ConfigLine = tuple[str | None, str]
"""A line of TOML and the source of its value (``None`` for structural lines)."""

SOURCE_SEPARATOR = " │ "

#: Colour of config lines by the layer they came from.
LAYER_STYLES = {
    LayerKind.BUILTIN: "bright_black",
    LayerKind.SYSTEM: "blue",
    LayerKind.USER: "green",
    LayerKind.PROJECT: "yellow",
    LayerKind.EXPLICIT: "magenta",
}


def config_lines(loaded: LoadedConfig) -> list[ConfigLine]:
    """The merged config as lines of TOML, each with the source of its value.

    ``[defaults]`` keys have individual sources; each server and template takes the
    source of the file that defined it.
    """
    data = loaded.config.model_dump(mode="json", by_alias=True, exclude_none=True)
    lines: list[ConfigLine] = [
        (None, "# Effective zabgrab configuration (all layers merged)."),
        (None, ""),
        (None, "[defaults]"),
    ]

    for key, value in data["defaults"].items():
        source = loaded.source_of("defaults", key)
        lines += [(source, line) for line in _dump({key: _item(value)})]

    for section in ("servers", "templates"):
        for name, entry in data[section].items():
            table = tomlkit.table()
            for key, value in entry.items():
                table.add(key, _item(value))
            outer = tomlkit.table(is_super_table=True)
            outer.add(name, table)
            source = loaded.source_of(section, name)
            lines.append((None, ""))
            lines += [(source, line) for line in _dump({section: outer})]

    return lines


def config_to_toml(loaded: LoadedConfig) -> str:
    """The merged config as TOML."""
    return "".join(f"{line}\n" for _, line in config_lines(loaded))


def config_with_sources(loaded: LoadedConfig) -> Text:
    """The merged config as TOML, each line prefixed by the source of its value.

    The source column is as wide as the widest source. Each line is coloured by the
    layer it came from (see :data:`LAYER_STYLES`), with the source in bold.
    """
    kinds = {layer.source: layer.kind for layer in loaded.layers if layer.active}
    lines = config_lines(loaded)
    width = max((len(source) for source, _ in lines if source), default=0)
    rendered = []
    for source, line in lines:
        style = LAYER_STYLES[kinds[source]] if source else ""
        text = Text()
        text.append(f"{source or '':<{width}}", style=f"bold {style}".strip())
        text.append(SOURCE_SEPARATOR, style="dim")
        text.append(line, style=style)
        rendered.append(text)
    return Text("\n").join(rendered)


def _dump(data: Mapping[str, Any]) -> list[str]:
    document = tomlkit.document()
    for key, value in data.items():
        document.add(key, value)
    return tomlkit.dumps(document).splitlines()


def _item(value: Any) -> TomlItem:
    """Convert a value to a TOML item, using inline tables for nested tables."""
    if isinstance(value, Mapping):
        table: InlineTable = tomlkit.inline_table()
        for key, sub in value.items():
            table.append(key, _item(sub))
        return table
    if isinstance(value, list):
        array: Array = tomlkit.array()
        array.extend(_item(sub) for sub in value)
        if any(isinstance(sub, Mapping) for sub in value):
            array.multiline(True)
        return array
    item: TomlItem = tomlkit.item(value)
    return item


def format_age(age: timedelta) -> str:
    """A compact age such as ``45s``, ``12m``, ``3h`` or ``9d``."""
    seconds = max(0, int(age.total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _shorten(text: str, width: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def hosts_table(rows: Sequence[tuple[Server, Host]]) -> Table:
    """Table of hosts, with a server column if there's more than one server."""
    multiple = len({server for server, _ in rows}) > 1
    table = Table()
    if multiple:
        table.add_column("Server", style="magenta")
    table.add_column("Host ID", style="cyan", justify="right")
    table.add_column("Host", style="bold")
    table.add_column("Name")
    table.add_column("Groups")
    table.add_column("Status")

    for server, host in rows:
        cells = [
            str(host.hostid),
            host.host,
            host.name,
            ", ".join(host.groups),
            "enabled" if host.enabled else "disabled",
        ]
        table.add_row(
            *([server.label] if multiple else []),
            *cells,
            style=None if host.enabled else "dim",
        )
    return table


def items_table(rows: Sequence[tuple[Server, Item]], *, now: datetime) -> Table:
    """Table of items, with a server column if there's more than one server."""
    multiple = len({server for server, _ in rows}) > 1
    table = Table()
    if multiple:
        table.add_column("Server", style="magenta")
    table.add_column("Item ID", style="cyan", justify="right")
    table.add_column("Host")
    table.add_column("Name", style="bold")
    table.add_column("Key", overflow="fold")
    table.add_column("Type")
    table.add_column("Units")
    table.add_column("Last value")
    table.add_column("Age", justify="right")

    for server, item in rows:
        cells = [
            str(item.itemid),
            item.host_name,
            item.name + ("" if item.enabled else " (disabled)"),
            item.key,
            item.value_type.label,
            item.units,
            "" if item.last_value is None else _shorten(item.last_value),
            "" if item.last_clock is None else format_age(now - item.last_clock),
        ]
        table.add_row(
            *([server.label] if multiple else []),
            *cells,
            style=None if item.enabled else "dim",
        )
    return table


_CHECK_STYLES = {
    CheckState.VALID: "green",
    CheckState.REJECTED: "red",
    CheckState.MISSING: "yellow",
    CheckState.ERROR: "yellow",
}


def tokens_table(
    statuses: Sequence[TokenStatus], results: Sequence[CheckResult] | None = None
) -> Table:
    """Table of servers and where their tokens come from."""
    table = Table()
    table.add_column("Server", style="bold")
    table.add_column("URL", style="cyan", overflow="fold")
    table.add_column("Keyring")
    table.add_column("Environment")
    if results is not None:
        table.add_column("Check")

    for index, status in enumerate(statuses):
        if status.env_var is None:
            environment = ""
        elif status.env_set:
            environment = f"[green]${status.env_var} (set)[/]"
        else:
            environment = f"[dim]${status.env_var} (not set)[/]"
        cells = [
            status.server.name or "",
            status.server.url,
            "[green]stored[/]" if status.stored else "[dim]-[/]",
            environment,
        ]
        if results is not None:
            result = results[index]
            text = f"{result.state} {result.detail}".strip()
            cells.append(f"[{_CHECK_STYLES[result.state]}]{escape(text)}[/]")
        table.add_row(*cells)
    return table
