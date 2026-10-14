"""Rendering of command results for the terminal."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Any

import tomlkit
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from tomlkit.items import Array, InlineTable
from tomlkit.items import Item as TomlItem

from ..adapters.configfiles import ConfigLayer, LayerKind, LoadedConfig
from ..model.choice import Choice
from ..model.config import Resolution
from ..model.item import ItemSpec
from ..model.itemspec import format_item_spec
from ..model.query import CUSTOM, Query
from ..model.server import Server
from ..model.zabbix import Host, Item
from ..services.fetch import Job, skip_reason
from ..services.tokens import CheckResult, CheckState, TokenStatus
from .console import TomlHighlighter


class DataTable(Table):
    """A table whose string cells are always plain text, never rich markup.

    Cells often hold data from servers or config (e.g. the item key
    ``vfs.fs.size[/,pfree]``), which must be shown exactly as it is.
    """

    def add_row(self, *renderables: RenderableType | None, **kwargs: Any) -> None:
        super().add_row(
            *(Text(cell) if isinstance(cell, str) else cell for cell in renderables),
            **kwargs,
        )


def layers_table(layers: Iterable[ConfigLayer]) -> Table:
    """Table of config layers, lowest priority first."""
    table = DataTable(
        title="Config files (lowest to highest priority)", title_justify="left"
    )
    table.add_column("Layer", style="bold")
    table.add_column("Path", style="cyan", overflow="fold")
    table.add_column("Status")

    for layer in layers:
        if layer.kind is LayerKind.BUILTIN:
            path, status = "-", Text("always used", style="green")
        elif layer.path is None:
            path, status = "(no zabgrab.toml found)", Text("not found", style="dim")
        else:
            path = str(layer.path)
            if layer.skipped:
                status = Text("skipped (--isolated)", style="yellow")
            elif layer.exists:
                status = Text("loaded", style="green")
            elif layer.kind is LayerKind.EXPLICIT:
                status = Text("missing", style="red")
            else:
                status = Text("not found", style="dim")
        table.add_row(str(layer.kind), path, status)

    return table


ConfigLine = tuple[str | None, str]
"""A line of TOML and the source of its value (``None`` for structural lines)."""

SOURCE_SEPARATOR = " │ "

#: Colour of config lines by the layer they came from.
LAYER_STYLES = {
    LayerKind.BUILTIN: "bold grey50",
    LayerKind.SYSTEM: "bold red1",
    LayerKind.USER: "bold dark_orange",
    LayerKind.PROJECT: "bold gold1",
    LayerKind.EXPLICIT: "bold tan",
}
"""Styles of the source column in ``config show --sources``, by layer. They avoid
the colours used to highlight TOML, so the two can't be confused."""


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


def highlighted_toml(text: str) -> Text:
    """TOML with its sections, keys, values and comments highlighted."""
    return Text("\n").join(_highlight_toml_line(line) for line in text.splitlines())


def _highlight_toml_line(line: str) -> Text:
    if line.lstrip().startswith("#"):
        return Text(line, style="toml.comment")
    return TomlHighlighter()(Text(line))


def config_with_sources(loaded: LoadedConfig) -> Text:
    """The merged config as TOML, each line prefixed by the source of its value.

    The source column is as wide as the widest source, and styled by the layer the
    value came from (see :data:`LAYER_STYLES`). The TOML is highlighted as by
    :func:`highlighted_toml`.
    """
    kinds = {layer.source: layer.kind for layer in loaded.layers if layer.active}
    lines = config_lines(loaded)
    width = max((len(source) for source, _ in lines if source), default=0)
    rendered = []
    for source, line in lines:
        text = Text()
        text.append(
            f"{source or '':<{width}}",
            style=LAYER_STYLES[kinds[source]] if source else "",
        )
        text.append(SOURCE_SEPARATOR, style="dim")
        text.append_text(_highlight_toml_line(line))
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
    table = DataTable()
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
    table = DataTable()
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
    table = DataTable()
    table.add_column("Server", style="bold")
    table.add_column("URL", style="cyan", overflow="fold")
    table.add_column("Keyring")
    table.add_column("Environment")
    if results is not None:
        table.add_column("Check")

    for index, status in enumerate(statuses):
        environment: str | Text = ""
        if status.env_var is not None:
            environment = Text(
                f"${status.env_var} ({'set' if status.env_set else 'not set'})",
                style="green" if status.env_set else "dim",
            )
        cells: list[str | Text] = [
            status.server.name or "",
            status.server.url,
            Text("stored", style="green") if status.stored else Text("-", style="dim"),
            environment,
        ]
        if results is not None:
            result = results[index]
            text = f"{result.state} {result.detail}".strip()
            cells.append(Text(text, style=_CHECK_STYLES[result.state]))
        table.add_row(*cells)
    return table


def format_time(time: datetime, tz: tzinfo | None) -> str:
    """A time for display, in ``tz`` (``None`` for local time)."""
    return time.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z").rstrip()


def explain_table(query: Query) -> Table:
    """The resolved settings, and where each came from."""
    table = DataTable(title="Settings", title_justify="left")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_column("Source", style="magenta")

    def row(key: str, value: str) -> None:
        table.add_row(key, value, query.sources.get(key, ""))

    row("from", f"{query.from_text} ({format_time(query.start, query.tz)})")
    row("to", f"{query.to_text} ({format_time(query.end, query.tz)})")
    row("resolution", str(query.resolution))
    row("agg", ", ".join(map(str, query.agg)))
    row("format", ", ".join(map(str, query.formats)) or "(from --output, or asked)")
    row("output_dir", query.output_dir)
    row("output_pattern", query.output_pattern)
    row("timezone", query.timezone)
    row("jobs", str(query.jobs))
    row("max_items", str(query.max_items))
    return table


def requests_table(query: Query) -> Table:
    """What will be fetched from where, before items are looked up."""
    table = DataTable(title="Requests", title_justify="left")
    table.add_column("From", style="bold")
    table.add_column("Server")
    table.add_column("Items")
    for request in query.requests:
        server = request.server
        if isinstance(server, Choice):
            shown = f"{' or '.join(server.choices)} ({server.select})"
        else:
            shown = server or "(asked)"
        items = [format_item_spec_safe(spec) for spec in request.items] or ["(asked)"]
        table.add_row(
            "command line" if request.label == CUSTOM else request.label,
            shown,
            "\n".join(items),
        )
    return table


def format_item_spec_safe(spec: ItemSpec) -> str:
    """An item spec for display (including host choices)."""
    try:
        return format_item_spec(spec)
    except ValueError:
        assert isinstance(spec.host, Choice)
        hosts = " or ".join(spec.host.choices)
        parts = [f"host={hosts} ({spec.host.select})"]
        if any((spec.name, spec.key, spec.tag)):
            parts.append(format_item_spec(spec.model_copy(update={"host": None})))
        return ",".join(parts)


def plan_table(jobs: Sequence[Job], resolution: Resolution) -> Table:
    """The items that would be fetched (for --dry-run)."""
    multiple = len({job.server for job in jobs}) > 1
    table = DataTable()
    if multiple:
        table.add_column("Server", style="magenta")
    table.add_column("Item ID", style="cyan", justify="right")
    table.add_column("Host")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("From")
    table.add_column("Status")
    for job in jobs:
        reason = skip_reason(job.item, resolution)
        table.add_row(
            *([job.server.label] if multiple else []),
            str(job.item.itemid),
            job.item.host,
            job.item.name,
            job.item.value_type.label,
            ", ".join(
                "command line" if label == CUSTOM else label for label in job.requests
            ),
            Text(f"skip: {reason}", style="yellow")
            if reason
            else Text("fetch", style="green"),
        )
    return table
