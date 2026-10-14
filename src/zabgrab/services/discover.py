"""Finding hosts and items."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from ..errors import ConnectionFailed
from ..model.choice import Choice, Select
from ..model.item import ItemSpec
from ..model.server import Server
from ..model.zabbix import Item
from .ports import UI, Option, ZabbixClient
from .servers import ServerSelection

T = TypeVar("T")


async def on_servers(
    selection: ServerSelection,
    connect: Callable[[Server], Awaitable[ZabbixClient]],
    action: Callable[[ZabbixClient], Awaitable[T]],
    ui: UI,
) -> list[tuple[Server, T]]:
    """Run ``action`` on each selected server, closing connections afterwards.

    With failover, servers that can't be reached are skipped (with a warning) until
    one works.
    """
    results: list[tuple[Server, T]] = []
    for index, server in enumerate(selection.servers):
        try:
            client = await connect(server)
        except ConnectionFailed as err:
            if not selection.failover or index == len(selection.servers) - 1:
                raise
            ui.warning(f"{err.message} Trying the next server.")
            continue
        try:
            results.append((server, await action(client)))
        finally:
            await client.close()
        if selection.failover:
            break
    return results


async def find_items(
    client: ZabbixClient,
    specs: Sequence[ItemSpec],
    ui: UI,
    *,
    limit: int | None = None,
) -> list[Item]:
    """Items matching any of ``specs`` (all items if there are none).

    Items matched by more than one spec are returned once.
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
