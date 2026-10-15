"""Continuing a file's fetches from where they left off (``zabgrab update``).

An update repeats the file's last fetch from where it ended, with the same items
and settings, using the metadata stored with it.
"""

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
    """What an update will fetch.

    Attributes
    ----------
    query : Query
        The last fetch's query, continued from where it ended.
    last_group : str
        The name of the group being continued.
    servers : dict of str to Server
        The server to use for each server URL in the group.
    items : dict of str to list of (int, tuple of str)
        Item IDs fetched before, with their request labels, by server URL.
    """

    query: Query
    last_group: str
    servers: dict[str, Server] = field(default_factory=dict)
    items: dict[str, list[tuple[int, tuple[str, ...]]]] = field(default_factory=dict)


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

    Parameters
    ----------
    groups : sequence of Group
        The file's groups, in order.
    config : Config
        The config, to find the servers by name (and the ``jobs`` and other
        defaults).
    ui : UI
        Warns about servers no longer in the config.
    now : datetime
        The current time.
    end : datetime or None
        The end of the update (``--to``), else ``now``.
    end_text : str or None
        ``--to`` as given.
    server : Server or None
        A server to use instead of the stored one (``--server``).
    file_format : Format
        The file's format.

    Returns
    -------
    UpdatePlan or None
        The plan, or ``None`` if there's nothing to fetch (the file is up to date).

    Raises
    ------
    ZabgrabError
        If there are no groups, or the last one lacks the metadata needed.
    """
    if not groups:
        raise ZabgrabError("The file has no data to update.")
    last = groups[-1]
    try:
        stored = last.metadata["query"]
        resolution = Resolution(stored["resolution"])
        agg = tuple(Agg(a) for a in stored.get("agg", []))
        stored_requests = stored.get("requests", [])
        requests = tuple(_request(r) for r in stored_requests)
        servers_named = [_server_ref(r) for r in stored_requests]
        entries = last.metadata["items"]
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ZabgrabError(
            f"Group {last.name!r} doesn't have the metadata needed to update it.",
            details=[str(exc)],
        ) from None

    # Raw values have sub-second times, so continue just after the last group's
    # end rather than a whole second later.
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
        template=next(iter(stored.get("templates", ())), None),
        # Every request stores the query's server; groups from before fetches
        # used one server can have different ones, and then there's none.
        server=servers_named[0] if len(set(map(str, servers_named))) == 1 else None,
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
    """Look up the items fetched before, by ID, to download them again.

    Parameters
    ----------
    plan : UpdatePlan
        The update.
    ui : UI
        Warns about items that no longer exist.
    connect : callable
        Returns a logged-in client for a server.

    Returns
    -------
    Plan
        The download jobs, with the servers' connections (close it when done).
    """
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


def _server(url: str, name: str | None, config: Config, ui: UI) -> Server:
    """Get a stored server: the configured one by name, else its URL."""
    if name and name in config.servers:
        return Server(name, config.servers[name])
    if name:
        ui.warning(
            f"Server {name!r} isn't in your config any more; using {url} instead."
        )
    return Server(None, ServerConfig(url=url))


def _request(data: Mapping[str, Any]) -> Request:
    """Read a stored request."""
    return Request(
        str(data["label"]),
        tuple(ItemSpec.model_validate(spec) for spec in data.get("items", [])),
    )


def _server_ref(data: Mapping[str, Any]) -> str | Choice[str] | None:
    """Read a stored request's server reference."""
    server = data.get("server")
    if isinstance(server, dict):
        return Choice[str].model_validate(server)
    return None if server is None else str(server)
