"""Working out which items to fetch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..errors import Cancelled, InputRequired
from ..model.answers import Answers
from ..model.choice import Choice
from ..model.item import ItemSpec
from ..model.query import Query, Request
from ..model.server import Server
from ..model.zabbix import Item
from .discover import find_items
from .fetch import Job
from .ports import UI, Option, ZabbixClient

SelectServer = Callable[[str | Choice[str] | None], Server]
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
    select_server: SelectServer,
    connect: Connect,
    yes: bool = False,
) -> Plan:
    """Resolve the query's server and each request's items into download jobs.

    Items matched by several requests are fetched once. The connection is kept in
    the plan for downloading; close it when done.
    """
    plan = Plan()
    jobs: dict[int, Job] = {}
    server = select_server(query.server)
    if _asked(query.server, server):
        query.answers.server = server.label
    client = await connect(server)
    plan.clients[server] = client
    try:
        for request in query.requests:
            items = await _items(client, request, ui, query.answers)
            items = _limit(
                items,
                query.max_items,
                ui,
                yes=yes,
                label=request.label,
                answers=query.answers,
            )
            for item in items:
                previous = jobs.get(item.itemid)
                labels = (
                    (request.label,)
                    if previous is None
                    else (
                        *previous.requests,
                        request.label,
                    )
                )
                jobs[item.itemid] = Job(server, item, labels)
    except BaseException:
        await plan.close()
        raise
    plan.jobs = list(jobs.values())
    return plan


def _asked(configured: str | Choice[str] | None, server: Server) -> bool:
    """Whether choosing the server involved asking the user."""
    if isinstance(configured, str):
        # Asked only if the name was unknown and the user chose another.
        return configured not in (server.name, server.url)
    return True


async def _items(
    client: ZabbixClient, request: Request, ui: UI, answers: Answers
) -> list[Item]:
    if request.items:
        items = await find_items(client, request.items, ui)
        if not items:
            ui.warning(f"Nothing matched the items of {_describe(request.label)}.")
        return items
    items, specs = await pick_items(client, ui)
    answers.items[request.label] = specs
    return items


async def pick_items(
    client: ZabbixClient, ui: UI
) -> tuple[list[Item], tuple[ItemSpec, ...]]:
    """Ask the user to search for and pick items.

    Returns the items and specs that select them: the search pattern if every match
    was picked, otherwise the picked items' IDs.
    """
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
    chosen = ui.choose_many(
        "Which items?",
        [Option(f"{i.host}: {i.name}", i, f"{i.key} ({i.itemid})") for i in found],
        fix=ITEMS_FIX,
    )
    if len(chosen) == len(found) < PICKER_LIMIT:
        return chosen, (ItemSpec(name=pattern),)
    return chosen, _ids(chosen)


def _limit(
    items: list[Item],
    max_items: int,
    ui: UI,
    *,
    yes: bool,
    label: str,
    answers: Answers,
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
        chosen = ui.choose_many(
            "Which items?",
            [Option(f"{i.host}: {i.name}", i, f"{i.key} ({i.itemid})") for i in items],
            fix="",
        )
        answers.items[label] = _ids(chosen)
        return chosen
    answers.all_items = True
    return items


def _ids(items: list[Item]) -> tuple[ItemSpec, ...]:
    return tuple(ItemSpec(itemid=item.itemid) for item in items)


def _describe(label: str) -> str:
    return "the command line" if label.startswith("__") else f"template {label!r}"


def _non_empty(text: str) -> str:
    if not text.strip():
        raise ValueError("Please enter a search, e.g. 'CPU' or 'Particles *'.")
    return text.strip()
