"""Finding hosts and items."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from ..model.choice import Choice, Select
from ..model.item import ItemSpec
from ..model.server import Server
from ..model.zabbix import Item
from .ports import UI, Option, ZabbixClient

T = TypeVar("T")


async def on_servers(
    servers: Sequence[Server],
    connect: Callable[[Server], Awaitable[ZabbixClient]],
    action: Callable[[ZabbixClient], Awaitable[T]],
) -> list[tuple[Server, T]]:
    """Run an action on each server, closing connections afterwards.

    Parameters
    ----------
    servers : sequence of Server
        The servers.
    connect : callable
        Returns a logged-in client for a server.
    action : callable
        What to do with each client.

    Returns
    -------
    list of (Server, T)
        Each server with its action's result.
    """
    results: list[tuple[Server, T]] = []
    for server in servers:
        client = await connect(server)
        try:
            results.append((server, await action(client)))
        finally:
            await client.close()
    return results


async def find_items(
    client: ZabbixClient,
    specs: Sequence[ItemSpec],
    ui: UI,
    *,
    limit: int | None = None,
) -> list[Item]:
    """Find the items matching any of a list of item specs.

    Parameters
    ----------
    client : ZabbixClient
        The server to search.
    specs : sequence of ItemSpec
        The item specs (all items if empty).
    ui : UI
        Warns about IDs and hosts that match nothing, and asks about host choices.
    limit : int, optional
        The most items to return per spec.

    Returns
    -------
    list of Item
        The items, each once even if several specs matched it.
    """
    if not specs:
        return await client.items(limit=limit)

    found: dict[int, Item] = {}
    for spec in specs:
        for item in await _find(client, spec, ui, limit=limit):
            if item.itemid in found:
                ui.debug(f"Item {item.itemid} matched more than once; using it once.")
            found.setdefault(item.itemid, item)
    return list(found.values())


async def _find(
    client: ZabbixClient, spec: ItemSpec, ui: UI, *, limit: int | None
) -> list[Item]:
    """Find the items matching one item spec (see find_items)."""
    if spec.itemid is not None:
        items = await client.items(itemids=[spec.itemid])
        if not items:
            ui.warning(f"No item with ID {spec.itemid}.")
        return items

    hostids: list[int] | None = None
    if spec.host is not None:
        hostids = []
        for pattern in await _host_patterns(client, spec.host, ui):
            hosts = await client.hosts(pattern=pattern)
            if not hosts:
                ui.warning(f"No hosts match {pattern!r}.")
            hostids.extend(host.hostid for host in hosts)

    return await client.items(
        hostids=hostids, name=spec.name, key=spec.key, tag=spec.tag, limit=limit
    )


async def _host_patterns(
    client: ZabbixClient, host: str | Choice[str], ui: UI
) -> list[str]:
    """Get the host patterns to search for: all, the first that matches, or asked."""
    if isinstance(host, str):
        return [host]
    if host.select is Select.ALL:
        return list(host.choices)
    if host.select is Select.FIRST:
        for pattern in host.choices:
            if await client.hosts(pattern=pattern, limit=1):
                return [pattern]
        return list(host.choices[:1])
    return ui.choose_many(
        "Which hosts?",
        [Option(pattern, pattern) for pattern in host.choices],
        fix='Use --item \'host=NAME,...\', or set select = "all" or "first" for '
        "this host choice in your config.",
    )
