"""Rendering of command results for the terminal."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import tomlkit
from rich.table import Table
from tomlkit.items import Array, InlineTable, Item

from ..adapters.configfiles import ConfigLayer, LayerKind, LoadedConfig


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


def _item(value: Any) -> Item:
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
    item: Item = tomlkit.item(value)
    return item
