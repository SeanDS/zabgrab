"""Rendering of command results for the terminal."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import tomlkit
from rich.table import Table
from tomlkit.items import Array, InlineTable
from tomlkit.items import Item as TomlItem

from ..adapters.configfiles import ConfigLayer, LayerKind, LoadedConfig
from ..model.server import Server
from ..model.zabbix import Host, Item


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


def config_to_toml(loaded: LoadedConfig, *, sources: bool = False) -> str:
    """The merged config as TOML, optionally commented with each value's source."""
    data = loaded.config.model_dump(mode="json", by_alias=True, exclude_none=True)
    doc = tomlkit.document()
    doc.add(tomlkit.comment("Effective zabgrab configuration (all layers merged)."))

    defaults = tomlkit.table()
    for key, value in data["defaults"].items():
        defaults.add(key, _item(value))
        if sources and (source := loaded.source_of("defaults", key)):
            defaults[key].comment(f"from {source}")
    doc.add("defaults", defaults)

    for section in ("servers", "templates"):
        if not data[section]:
            continue
        outer = tomlkit.table(is_super_table=True)
        for name, entry in data[section].items():
            table = tomlkit.table()
            if sources and (source := loaded.source_of(section, name)):
                table.add(tomlkit.comment(f"from {source}"))
            for key, value in entry.items():
                table.add(key, _item(value))
            outer.add(name, table)
        doc.add(section, outer)

    return tomlkit.dumps(doc)


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
