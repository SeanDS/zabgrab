"""Working out which items to fetch from which servers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..errors import Cancelled, ConnectionFailed, InputRequired
from ..model.choice import Choice
from ..model.item import ItemSpec
from ..model.query import Query, Request
from ..model.server import Server
from ..model.zabbix import Item
from .discover import find_items
from .fetch import Job
from .ports import UI, Option, ZabbixClient
from .servers import ServerSelection

SelectServers = Callable[[str | Choice[str] | None], ServerSelection]
Connect = Callable[[Server], Awaitable[ZabbixClient]]

PICKER_LIMIT = 100
ITEMS_FIX = (
    "Pass items with --item (e.g. --item 12345 or --item 'host=myhost,name=CPU *'), "
    "or use a template with -t NAME."
)


@dataclass
class Plan:
    """The items to fetch, and connections to their servers."""

    jobs: list[Job] = field(default_factory=list)
    clients: dict[Server, ZabbixClient] = field(default_factory=dict)

    async def close(self) -> None:
        for client in self.clients.values():
            await client.close()


async def collect(
    query: Query,
    ui: UI,
    *,
    select_servers: SelectServers,
    connect: Connect,
    yes: bool = False,
) -> Plan:
    """Resolve each request's servers and items into download jobs.

    Items matched by several requests (on the same server) are fetched once.
    Connections are kept in the plan for downloading; close it when done.
    """
    plan = Plan()
    jobs: dict[tuple[str, int], Job] = {}
    try:
        for request in query.requests:
            selection = select_servers(request.server)
            for index, server in enumerate(selection.servers):
                try:
                    client = await _client(plan, server, connect)
                except ConnectionFailed as err:
                    if not selection.failover or index == len(selection.servers) - 1:
                        raise
                    ui.warning(f"{err.message} Trying the next server.")
                    continue
                items = await _items(client, request, ui)
                items = _limit(items, query.max_items, ui, yes=yes, label=request.label)
                for item in items:
                    key = (server.url, item.itemid)
                    if key in jobs:
                        previous = jobs[key]
                        jobs[key] = Job(
                            server, item, (*previous.requests, request.label)
                        )
                    else:
                        jobs[key] = Job(server, item, (request.label,))
                if selection.failover:
                    break
    except BaseException:
        await plan.close()
        raise
    plan.jobs = list(jobs.values())
    return plan


async def _client(plan: Plan, server: Server, connect: Connect) -> ZabbixClient:
    for known, client in plan.clients.items():
        if known.url == server.url:
            return client
    client = await connect(server)
    plan.clients[server] = client
    return client


async def _items(client: ZabbixClient, request: Request, ui: UI) -> list[Item]:
    if request.items:
        items = await find_items(client, request.items, ui)
        if not items:
            ui.warning(f"Nothing matched the items of {_describe(request.label)}.")
        return items
    return await pick_items(client, ui)


async def pick_items(client: ZabbixClient, ui: UI) -> list[Item]:
    """Ask the user to search for and pick items."""
    if not ui.interactive:
        raise InputRequired("Which items?", fix=ITEMS_FIX)
    while True:
        pattern = ui.prompt(
            "Search for items by name (* matches anything)",
            fix=ITEMS_FIX,
            parse=_non_empty,
        )
        found = await find_items(
            client, [ItemSpec(name=pattern)], ui, limit=PICKER_LIMIT
        )
        if found:
            break
        ui.warning(f"No items match {pattern!r}; try another search.")
    if len(found) == PICKER_LIMIT:
        ui.info(f"Showing the first {PICKER_LIMIT} matches.")
    return ui.choose_many(
        "Which items?",
        [Option(f"{i.host}: {i.name}", i, f"{i.key} ({i.itemid})") for i in found],
        fix=ITEMS_FIX,
    )


def _limit(
    items: list[Item], max_items: int, ui: UI, *, yes: bool, label: str
) -> list[Item]:
    if len(items) <= max_items or yes:
        return items
    question = (
        f"{len(items)} items match {_describe(label)}, more than max_items "
        f"({max_items})."
    )
    if not ui.interactive:
        ui.warning(f"{question} Fetching all of them; pass --max-items to change this.")
        return items
    choice = ui.choose(
        question,
        [
            Option(f"Fetch all {len(items)}", "all"),
            Option("Choose which", "choose"),
            Option("Cancel", "cancel"),
        ],
        fix="",
        default=0,
    )
    if choice == "cancel":
        raise Cancelled()
    if choice == "choose":
        return ui.choose_many(
            "Which items?",
            [Option(f"{i.host}: {i.name}", i, f"{i.key} ({i.itemid})") for i in items],
            fix="",
        )
    return items


def _describe(label: str) -> str:
    return "the command line" if label.startswith("__") else f"template {label!r}"


def _non_empty(text: str) -> str:
    if not text.strip():
        raise ValueError("Please enter a search, e.g. 'CPU' or 'Particles *'.")
    return text.strip()
