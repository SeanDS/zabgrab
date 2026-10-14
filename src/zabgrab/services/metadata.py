"""Metadata saved with fetched data: enough to understand and repeat the fetch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .. import __version__
from ..model.choice import Choice
from ..model.config import Template
from ..model.query import Query
from ..model.series import FetchResult, Series, Skipped
from ..model.server import Server
from ..model.zabbix import Item

FORMAT_VERSION = 1


def build_metadata(
    query: Query,
    result: FetchResult,
    *,
    templates: Mapping[str, Template],
    command: Sequence[str],
    created: datetime,
) -> dict[str, Any]:
    """Metadata describing a fetch. Never includes credentials."""
    servers: dict[str, Server] = {}
    entries: list[Series | Skipped] = [*result.series, *result.skipped, *result.failed]
    for entry in entries:
        servers.setdefault(entry.server.url, entry.server)
    return {
        "zabgrab": {"version": __version__, "format_version": FORMAT_VERSION},
        "created": created.isoformat(),
        "command": list(command),
        "query": {
            "templates": list(query.templates),
            "custom": query.custom,
            "from": query.from_text,
            "to": query.to_text,
            "start": query.start.isoformat(),
            "end": query.end.isoformat(),
            "timezone": query.timezone,
            "resolution": str(query.resolution),
            "agg": [str(agg) for agg in query.agg],
            "sources": dict(query.sources),
            "requests": [
                {
                    "label": request.label,
                    "server": _server(request.server),
                    "items": [
                        spec.model_dump(mode="json", exclude_none=True)
                        for spec in request.items
                    ],
                }
                for request in query.requests
            ],
        },
        "templates": {
            name: templates[name].model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            for name in query.templates
            if name in templates
        },
        "servers": [{"name": s.name, "url": url} for url, s in servers.items()],
        "items": [
            {
                **_item(series.server.url, series.item),
                "requests": list(series.requests),
                "columns": list(series.columns),
                "points": series.points,
            }
            for series in result.series
        ],
        "skipped": [_skipped(s) for s in result.skipped],
        "failed": [_skipped(s) for s in result.failed],
    }


def _server(server: str | Choice[str] | None) -> Any:
    if isinstance(server, Choice):
        return server.model_dump(mode="json")
    return server


def _item(url: str, item: Item) -> dict[str, Any]:
    return {
        "server": url,
        "itemid": item.itemid,
        "host": item.host,
        "host_name": item.host_name,
        "name": item.name,
        "key": item.key,
        "units": item.units,
        "value_type": item.value_type.label,
    }


def _skipped(skipped: Skipped) -> dict[str, Any]:
    return {**_item(skipped.server.url, skipped.item), "reason": skipped.reason}
