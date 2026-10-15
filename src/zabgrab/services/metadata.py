"""Metadata saved with fetched data: enough to understand and repeat the fetch.

Files written by zabgrab store it as JSON (an HDF5 dataset, or ``#`` comment lines
in delimited files). ``update`` relies on its ``query`` and ``items``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .. import __version__
from ..model.choice import Choice
from ..model.config import Template
from ..model.query import Query
from ..model.series import FORMAT_VERSION, FetchResult, Series, Skipped
from ..model.server import Server
from ..model.zabbix import Item
from .dataset import Dataset


def build_metadata(
    query: Query,
    result: FetchResult,
    *,
    template: Template | None,
    command: Sequence[str],
    created: datetime,
) -> dict[str, Any]:
    """Describe a fetch. Never includes credentials.

    Parameters
    ----------
    query : Query
        What was fetched, and where each setting came from.
    result : FetchResult
        The data, and items skipped or failed.
    template : Template or None
        The template used, copied in so the fetch can be understood (and updated
        with ``--rematch``) without the config.
    command : sequence of str
        The command line.
    created : datetime
        When the fetch was made.

    Returns
    -------
    dict
        The metadata, ready for JSON.
    """
    servers: dict[str, Server] = {}
    entries: list[Series | Skipped] = [*result.series, *result.skipped, *result.failed]
    for entry in entries:
        servers.setdefault(entry.server.url, entry.server)
    return {
        "zabgrab": {"version": __version__, "format_version": FORMAT_VERSION},
        "created": created.isoformat(),
        "command": list(command),
        "query": {
            # A list, from when fetches could use several templates.
            "templates": [query.template] if query.template else [],
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
                    "server": _server(query.server),
                    "items": [
                        spec.model_dump(mode="json", exclude_none=True)
                        for spec in request.items
                    ],
                }
                for request in query.requests
            ],
        },
        # A table of templates, from when fetches could use several.
        "templates": {
            query.template: template.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        }
        if query.template and template is not None
        else {},
        "servers": [{"name": s.name, "url": url} for url, s in servers.items()],
        "items": [_series(series) for series in result.series],
        "skipped": [_skipped(s) for s in result.skipped],
        "failed": [_skipped(s) for s in result.failed],
    }


def _server(server: str | Choice[str] | None) -> Any:
    """Convert the query's server for JSON (a choice as a table)."""
    if isinstance(server, Choice):
        return server.model_dump(mode="json")
    return server


def _series(series: Series) -> dict[str, Any]:
    """Describe a fetched series: its item and what was stored."""
    return {
        **_item(series.server.url, series.item),
        "requests": list(series.requests),
        "columns": list(series.columns),
        "points": series.points,
    }


def _item(url: str, item: Item) -> dict[str, Any]:
    """Describe an item and its server."""
    return {
        "server": url,
        "itemid": item.itemid,
        "host": item.host,
        "host_name": item.host_name,
        "name": item.name,
        "key": item.key,
        "units": item.units,
        "value_type": item.value_type.label,
        "tags": [{"tag": tag, "value": value} for tag, value in item.tags],
    }


def _skipped(skipped: Skipped) -> dict[str, Any]:
    """Describe an item that wasn't fetched, and why."""
    return {**_item(skipped.server.url, skipped.item), "reason": skipped.reason}


def export_metadata(
    dataset: Dataset,
    *,
    source: str,
    filters: Mapping[str, Any],
    command: Sequence[str],
    created: datetime,
) -> dict[str, Any]:
    """Describe data exported from a file.

    The last group's query is kept, so the export can be updated like the
    original, with the merged items and a record of how it was exported.

    Parameters
    ----------
    dataset : Dataset
        The merged data; it must cover some time.
    source : str
        The file it came from.
    filters : mapping
        The selection options used.
    command : sequence of str
        The command line.
    created : datetime
        When the export was made.

    Returns
    -------
    dict
        The metadata, ready for JSON.
    """
    metadata = copy.deepcopy(dataset.last_metadata)
    metadata["zabgrab"] = {"version": __version__, "format_version": FORMAT_VERSION}
    metadata["created"] = created.isoformat()
    metadata["command"] = list(command)
    query = metadata.setdefault("query", {})
    query["start"] = _iso(dataset.coverage[0][0])
    query["end"] = _iso(dataset.coverage[-1][1])
    metadata["items"] = [_series(series) for series in dataset.series]
    metadata["skipped"] = []
    metadata["failed"] = []
    metadata["export"] = {
        "source": source,
        "groups": dataset.groups,
        "filters": dict(filters),
        "coverage": [[_iso(a), _iso(b)] for a, b in dataset.coverage],
    }
    return metadata


def _iso(seconds: float) -> str:
    """Format POSIX seconds as ISO 8601 in UTC."""
    return datetime.fromtimestamp(seconds, UTC).isoformat()
