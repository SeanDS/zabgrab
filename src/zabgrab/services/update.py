"""Continuing a file's fetches from where they left off."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..errors import ZabgrabError
from ..model.choice import Choice
from ..model.config import Agg, Config, Format, Resolution, ServerConfig
from ..model.item import ItemSpec
from ..model.query import Query, Request
from ..model.server import Server
from .collect import Plan
from .dataset import Group
from .fetch import Job
from .ports import UI, ZabbixClient


@dataclass
class UpdatePlan:
    """What an update will fetch."""

    query: Query
    last_group: str
    servers: dict[str, Server] = field(default_factory=dict)
    """The server to use for each server URL in the file."""
    items: dict[str, list[tuple[int, tuple[str, ...]]]] = field(default_factory=dict)
    """Item IDs (with their request labels) fetched before, by server URL."""


def plan_update(
    groups: Sequence[Group],
    *,
    config: Config,
    ui: UI,
    now: datetime,
    end: datetime | None,
    end_text: str | None,
    server: Server | None,
    file_format: Format,
) -> UpdatePlan | None:
    """Work out an update from the last group's metadata.

    Returns ``None`` if there's nothing to fetch (the file is up to date).
    """
    if not groups:
        raise ZabgrabError("The file has no data to update.")
    last = groups[-1]
    try:
        stored = last.metadata["query"]
        resolution = Resolution(stored["resolution"])
        agg = tuple(Agg(a) for a in stored.get("agg", []))
        requests = tuple(_request(r) for r in stored.get("requests", []))
        entries = last.metadata["items"]
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ZabgrabError(
            f"Group {last.name!r} doesn't have the metadata needed to update it.",
            details=[str(exc)],
        ) from None

    start = datetime.fromtimestamp(last.t_to, UTC) + timedelta(microseconds=1)
    end = end or now
    if start >= end:
        return None

    names = {s.get("url"): s.get("name") for s in last.metadata.get("servers", [])}
    servers = {
        url: server or _server(url, names.get(url), config, ui)
        for url in dict.fromkeys(entry["server"] for entry in entries)
    }
    items: dict[str, list[tuple[int, tuple[str, ...]]]] = {}
    for entry in entries:
        items.setdefault(entry["server"], []).append(
            (int(entry["itemid"]), tuple(entry.get("requests", ())))
        )

    defaults = config.defaults
    timezone = stored.get("timezone", "local")
    query = Query(
        requests=requests,
        templates=tuple(stored.get("templates", ())),
        custom=bool(stored.get("custom", False)),
        start=start,
        end=end,
        from_text=start.isoformat(),
        to_text=end_text or "now",
        resolution=resolution,
        agg=agg,
        formats=(file_format,),
        output_dir=".",
        output_pattern="",
        timezone=timezone,
        tz=None if timezone == "local" else ZoneInfo(timezone),
        jobs=defaults.jobs or 1,
        max_items=defaults.max_items or 1,
        mat_text_warn_entries=defaults.mat_text_warn_entries or 0,
        sources={
            "from": f"end of group {last.name!r}",
            "to": "command line (--to)" if end_text else "now",
            "resolution": f"group {last.name!r}",
            "agg": f"group {last.name!r}",
            "timezone": f"group {last.name!r}",
        },
    )
    return UpdatePlan(query, last.name, servers, items)


async def collect_previous_items(
    plan: UpdatePlan,
    ui: UI,
    *,
    connect: Callable[[Server], Awaitable[ZabbixClient]],
) -> Plan:
    """Download jobs for the items fetched before, by item ID."""
    result = Plan()
    try:
        for url, entries in plan.items.items():
            server = plan.servers[url]
            client = await connect(server)
            result.clients[server] = client
            found = {
                item.itemid: item
                for item in await client.items(
                    itemids=[itemid for itemid, _ in entries]
                )
            }
            for itemid, requests in entries:
                if itemid in found:
                    result.jobs.append(Job(server, found[itemid], requests))
                else:
                    ui.warning(f"Item {itemid} no longer exists on {server.display}.")
    except BaseException:
        await result.close()
        raise
    return result


def server_for(plan: UpdatePlan, ref: str | Choice[str] | None) -> Server | None:
    """The server an original request's server reference now maps to, if known."""
    if isinstance(ref, str):
        for url, server in plan.servers.items():
            if ref in (url, server.name):
                return server
    return None


def _server(url: str, name: str | None, config: Config, ui: UI) -> Server:
    if name and name in config.servers:
        return Server(name, config.servers[name])
    if name:
        ui.warning(
            f"Server {name!r} isn't in your config any more; using {url} instead."
        )
    return Server(None, ServerConfig(url=url))


def _request(data: Mapping[str, Any]) -> Request:
    server = data.get("server")
    if isinstance(server, dict):
        server = Choice[str].model_validate(server)
    return Request(
        str(data["label"]),
        server,
        tuple(ItemSpec.model_validate(spec) for spec in data.get("items", [])),
    )
