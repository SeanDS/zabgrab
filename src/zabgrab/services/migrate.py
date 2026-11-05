"""Moving a file's items to new IDs or servers, and retiring items (``migrate``).

When a Zabbix server is replaced without its history, the same items come back on
the new server under new IDs. A file identifies items by server and ID, so without
help its data from before and after would look like different items. A migration
rewrites the file's items to their new server and IDs, recording where each came
from, so the file reads as one series per item again. Items that can't be found
any more are retired: kept, but no longer updated.

The plan is worked out first (:func:`plan_migration`), shown and confirmed, then
applied to the groups (:func:`apply_migration`), which the caller writes back.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, TypeVar

from ..errors import ZabgrabError
from ..model.config import ServerConfig
from ..model.item import ItemSpec
from ..model.series import Series
from ..model.server import Server
from ..model.zabbix import Item
from .dataset import Group
from .ports import ZabbixClient
from .records import (
    Identity,
    active_entries,
    identity,
    latest_entries,
    retired_items,
)

G = TypeVar("G", bound=Group)

#: How a mapping was found: looked up by host and key, or given by the user.
MATCHED = "matched"
GIVEN = "given"


@dataclass(frozen=True)
class Move:
    """An item moving to a new ID (and perhaps a new server).

    Attributes
    ----------
    old : Identity
        The item's server URL and ID in the file.
    old_server_name : str or None
        Its server's name in the config when it was fetched.
    old_hostid : int or None
        Its host's ID on the old server.
    label : str
        ``host: name``, for messages.
    server : Server
        The server it moves to.
    item : Item
        The item on that server.
    how : str
        :data:`MATCHED` or :data:`GIVEN`.
    """

    old: Identity
    old_server_name: str | None
    old_hostid: int | None
    label: str
    server: Server
    item: Item
    how: str

    @property
    def new(self) -> Identity:
        """The item's new server URL and ID."""
        return self.server.url, self.item.itemid


@dataclass(frozen=True)
class Retirement:
    """An item to retire.

    Attributes
    ----------
    item : Identity
        The item's server URL and ID.
    label : str
        ``host: name``, for messages.
    reason : str
        Why, e.g. ``"not found on 'labnew'"``.
    """

    item: Identity
    label: str
    reason: str


@dataclass
class MigrationPlan:
    """What a migration will change.

    Attributes
    ----------
    target : Server or None
        The server the items move to (``--server``), if any.
    sources : list of Server
        The servers the moving items come from.
    moves : list of Move
        The items moving to new IDs or servers.
    retirements : list of Retirement
        The items to retire.
    unretired : list of tuple of Identity and str
        The items to bring back, with their labels.
    unchanged : list of tuple of Identity and str
        Items found on the target server under the same ID (nothing to change).
    names : dict of str to str or None
        The file's servers' names in the config, by URL (see :func:`server_names`).
    """

    target: Server | None
    sources: list[Server] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    retirements: list[Retirement] = field(default_factory=list)
    unretired: list[tuple[Identity, str]] = field(default_factory=list)
    unchanged: list[tuple[Identity, str]] = field(default_factory=list)
    names: dict[str, str | None] = field(default_factory=dict)

    @property
    def changes(self) -> bool:
        """Whether the plan changes anything."""
        return bool(self.moves or self.retirements or self.unretired)


def parse_mapping(text: str) -> tuple[int, int]:
    """Parse ``--map OLD=NEW``.

    Parameters
    ----------
    text : str
        The option's value.

    Returns
    -------
    tuple of int
        The old and new item IDs.

    Raises
    ------
    ValueError
        If it isn't two item IDs separated by ``=``.

    Examples
    --------
    >>> parse_mapping("41207=90311")
    (41207, 90311)
    """
    old, equals, new = text.partition("=")
    if not equals or not old.strip().isdigit() or not new.strip().isdigit():
        raise ValueError(f"{text!r} isn't OLD=NEW, two item IDs (e.g. 41207=90311).")
    return int(old), int(new)


def read_mapping_file(text: str, name: str) -> list[tuple[int, int]]:
    r"""Read a file of item ID mappings, one per line.

    Each line holds the old and new item IDs, separated by a comma, a tab or
    spaces. Blank lines and lines starting with ``#`` are ignored, as is a first
    line of column headings.

    Parameters
    ----------
    text : str
        The file's contents.
    name : str
        The file's name, for errors.

    Returns
    -------
    list of tuple of int
        The old and new IDs, in order.

    Raises
    ------
    ValueError
        If a line isn't two item IDs.

    Examples
    --------
    >>> text = "old,new\n# moved in May\n41207,90311\n41208 90312\n"
    >>> read_mapping_file(text, "mapping.csv")
    [(41207, 90311), (41208, 90312)]
    """
    mappings = []
    first = True
    for number, line in enumerate(text.splitlines(), start=1):
        cells = [cell for cell in re.split(r"[,\t ]+", line.strip()) if cell]
        if not cells or cells[0].startswith("#"):
            continue
        if len(cells) == 2 and all(cell.isdigit() for cell in cells):
            mappings.append((int(cells[0]), int(cells[1])))
        elif not (first and not any(char.isdigit() for char in line)):
            raise ValueError(
                f"Line {number} of {name} isn't an old and a new item ID: {line!r}."
            )
        # Otherwise it's a first line of column headings.
        first = False
    return mappings


def find_items(
    groups: Sequence[Group], itemids: Iterable[int], option: str
) -> dict[int, Identity]:
    """Find the items a file holds with the given IDs.

    Parameters
    ----------
    groups : sequence of Group
        The file's groups.
    itemids : iterable of int
        Item IDs given on the command line.
    option : str
        The option they were given with, for errors.

    Returns
    -------
    dict of int to Identity
        Each ID's item.

    Raises
    ------
    ZabgrabError
        If the file has no item with an ID, or items with it on several servers.
    """
    known = latest_entries(groups)
    found = {}
    for itemid in itemids:
        matching = [key for key in known if key[1] == itemid]
        if not matching:
            raise ZabgrabError(
                f"{option}: the file has no item {itemid}.",
                hint="zabgrab info FILE lists its items.",
            )
        if len(matching) > 1:
            raise ZabgrabError(
                f"{option}: the file has item {itemid} on several servers "
                f"({', '.join(url for url, _ in matching)}).",
                hint="Migrate the items of one server at a time, with --server.",
            )
        found[itemid] = matching[0]
    return found


async def match_items(
    entries: Sequence[Mapping[str, Any]], client: ZabbixClient
) -> dict[Identity, Item]:
    """Look items up on a server by their host's technical name and their key.

    Zabbix keeps item keys unique within a host, so a host and key find at most one
    item.

    Parameters
    ----------
    entries : sequence of mapping
        The items' metadata entries (with ``host`` and ``key``).
    client : ZabbixClient
        A connection to the server to look on.

    Returns
    -------
    dict of Identity to Item
        The items found, by the identity of the entry they match.
    """
    by_host: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        by_host.setdefault(str(entry["host"]), []).append(entry)
    found = {}
    for host, wanted in by_host.items():
        # The pattern also finds visible names and wildcard matches: keep the host
        # whose technical name is exactly this one.
        hosts = [h for h in await client.hosts(pattern=host) if h.host == host]
        if not hosts:
            continue
        keys = {
            item.key: item for item in await client.items(hostids=[hosts[0].hostid])
        }
        for entry in wanted:
            if entry["key"] in keys:
                found[identity(entry)] = keys[entry["key"]]
    return found


async def look_up(
    server: Server,
    connect: Callable[[Server], Awaitable[ZabbixClient]],
    *,
    entries: Sequence[Mapping[str, Any]],
    itemids: Iterable[int],
) -> tuple[dict[Identity, Item], dict[int, Item]]:
    """Look up what a migration needs on its target server.

    Parameters
    ----------
    server : Server
        The target server.
    connect : callable
        Returns a logged-in client for a server.
    entries : sequence of mapping
        Items to match by host and key (see :func:`match_items`); none for no
        matching.
    itemids : iterable of int
        New item IDs given by the user, which must exist on the server.

    Returns
    -------
    tuple of dict
        The matched items by the identity of their entries, and the given items
        by ID.

    Raises
    ------
    ZabgrabError
        If a given item ID isn't on the server.
    """
    client = await connect(server)
    try:
        matched = await match_items(entries, client) if entries else {}
        wanted = sorted(set(itemids))
        given = (
            {item.itemid: item for item in await client.items(itemids=wanted)}
            if wanted
            else {}
        )
    finally:
        await client.close()
    missing = [itemid for itemid in wanted if itemid not in given]
    if missing:
        raise ZabgrabError(
            f"{server.display} has no item {', '.join(map(str, missing))}.",
            hint="Check the new IDs (zabgrab items --server ... lists them).",
        )
    return matched, given


def plan_migration(
    groups: Sequence[Group],
    *,
    target: Server | None,
    matched: Mapping[Identity, Item],
    given: Mapping[Identity, tuple[Server, Item]],
    retire: Iterable[Identity] = (),
    unretire: Iterable[Identity] = (),
) -> MigrationPlan:
    """Work out a migration: which items move, which retire, which come back.

    With a target server, every active item (one ``update`` would fetch, or one
    being brought back) moves to the item given for it (``--map``), else to the
    one matched by host and key, and is retired if there's neither. Items given a
    new ID move too, active or not.

    Parameters
    ----------
    groups : sequence of Group
        The file's groups, in order.
    target : Server or None
        The server the items move to (``--server``), if any.
    matched : mapping of Identity to Item
        Items found on the target server by host and key.
    given : mapping of Identity to tuple of Server and Item
        Items given new IDs, with the server and item they move to.
    retire, unretire : iterable of Identity
        Items to retire, or to bring back.

    Returns
    -------
    MigrationPlan
        The plan.

    Raises
    ------
    ZabgrabError
        If an item is both moved and retired, retired twice or brought back
        without being retired, two items would get the same ID, or a group would
        hold an item twice.
    """
    entries = latest_entries(groups)
    retired = retired_items(groups)
    retire, unretire = list(dict.fromkeys(retire)), list(dict.fromkeys(unretire))
    labels = {key: _label(entry) for key, entry in entries.items()}
    for key in retire:
        if key in given:
            raise ZabgrabError(f"Item {key[1]} can't be both mapped and retired.")
        if key in retired:
            raise ZabgrabError(f"Item {key[1]} ({labels[key]}) is already retired.")
    for key in unretire:
        if key not in retired:
            raise ZabgrabError(f"Item {key[1]} ({labels[key]}) isn't retired.")

    names = server_names(groups)
    plan = MigrationPlan(target, names=names)
    plan.retirements = [Retirement(key, labels[key], "--retire") for key in retire]
    plan.unretired = [(key, labels[key]) for key in unretire]
    hostids = _hostids(groups)

    def move(key: Identity, server: Server, item: Item, how: str) -> None:
        if (server.url, item.itemid) == key:
            plan.unchanged.append((key, labels[key]))
        else:
            plan.moves.append(
                Move(
                    key,
                    names.get(key[0]),
                    hostids.get(key),
                    labels[key],
                    server,
                    item,
                    how,
                )
            )

    if target is not None:
        # Items brought back now are active again, so they move too.
        active = list(
            dict.fromkeys(
                [*(identity(e) for e in active_entries(groups[-1].metadata)), *unretire]
            )
        )
        urls = list(dict.fromkeys(url for url, _ in active))
        if len(urls) > 1:
            raise ZabgrabError(
                f"The file's items come from several servers ({', '.join(urls)}), "
                "so they can't all move to one.",
                hint="Map them with --map instead, without --server.",
            )
        for key in active:
            if key in given or key in retire:
                continue
            if key in matched:
                move(key, target, matched[key], MATCHED)
            else:
                plan.retirements.append(
                    Retirement(key, labels[key], f"not found on {target.display}")
                )
    for key, (server, item) in given.items():
        move(key, server, item, GIVEN)

    plan.sources = _sources(plan.moves, names)
    _check_new_ids(groups, plan)
    return plan


def apply_migration(
    groups: Sequence[G],
    plan: MigrationPlan,
    *,
    at: datetime,
    command: Sequence[str],
    version: str,
) -> list[G]:
    """Apply a migration to a file's groups.

    Moved items get their new server and IDs in every group, keeping their data,
    with their earlier server and IDs added to their ``history``; retired items
    are marked, and unretired ones unmarked (and listed in the last group's
    ``resume`` if it no longer has them). Every group's ``migrations`` log gains an
    entry describing the migration.

    Parameters
    ----------
    groups : sequence of Group
        The file's groups, in order (not changed).
    plan : MigrationPlan
        The migration.
    at : datetime
        When the migration is made.
    command : sequence of str
        The command line.
    version : str
        zabgrab's version.

    Returns
    -------
    list of Group
        Changed copies of the groups, to write back to the file.
    """
    stamp = at.isoformat()
    moves = {move.old: move for move in plan.moves}
    retire = {retirement.item for retirement in plan.retirements}
    unretire = {key for key, _ in plan.unretired}
    # Items given by ID in the stored requests follow the move, if unambiguous.
    old_ids = Counter(key[1] for key in moves)
    new_ids = {
        key[1]: move.item.itemid for key, move in moves.items() if old_ids[key[1]] == 1
    }
    log = _log_entry(plan, stamp, command, version)

    result = []
    for number, group in enumerate(groups):
        metadata = copy.deepcopy(group.metadata)
        if number == len(groups) - 1:
            _resume(metadata, groups, unretire)
        for entry in [*metadata.get("items", []), *metadata.get("resume", [])]:
            key = identity(entry)
            if key in retire:
                entry["retired"] = {"at": stamp}
            if key in unretire:
                entry.pop("retired", None)
            if key in moves:
                move = moves[key]
                entry.setdefault("history", []).append(
                    {
                        "server": key[0],
                        "server_name": move.old_server_name,
                        "itemid": key[1],
                        "hostid": move.old_hostid,
                        "until": stamp,
                    }
                )
                entry["server"], entry["itemid"] = move.new
        if plan.moves:
            _move_servers(metadata, plan)
            _move_requests(metadata, plan.target, new_ids)
        metadata.setdefault("migrations", []).append(copy.deepcopy(log))

        changed = copy.copy(group)
        changed.metadata = metadata
        changed.series = [_moved(series, moves) for series in group.series]
        result.append(changed)
    return result


def _label(entry: Mapping[str, Any]) -> str:
    """Name an item entry: ``host: name``."""
    return f"{entry.get('host', '?')}: {entry.get('name', '?')}"


def server_names(groups: Sequence[Group]) -> dict[str, str | None]:
    """Get the config names a file's servers were fetched with.

    Parameters
    ----------
    groups : sequence of Group
        The file's groups.

    Returns
    -------
    dict of str to str or None
        Each server's name (``None`` if it had none), by URL.
    """
    names: dict[str, str | None] = {}
    for group in groups:
        for server in group.metadata.get("servers", []):
            names[server["url"]] = server.get("name")
    for group in groups:
        for series in group.series:
            names.setdefault(series.server.url, series.server.name)
    return names


def _hostids(groups: Sequence[Group]) -> dict[Identity, int]:
    """Get each item's host ID, from its data."""
    return {
        (series.server.url, series.item.itemid): series.item.hostid
        for group in groups
        for series in group.series
    }


def _sources(moves: Sequence[Move], names: Mapping[str, str | None]) -> list[Server]:
    """List the servers items move from (by URL and stored name)."""
    urls = dict.fromkeys(move.old[0] for move in moves if move.old[0] != move.new[0])
    return [Server(names.get(url), ServerConfig(url=url)) for url in urls]


def _check_new_ids(groups: Sequence[Group], plan: MigrationPlan) -> None:
    """Refuse moves that would give two items the same ID."""
    taken = Counter(move.new for move in plan.moves)
    for key, count in taken.items():
        if count > 1:
            olds = [str(m.old[1]) for m in plan.moves if m.new == key]
            raise ZabgrabError(
                f"Items {' and '.join(olds)} can't both become item {key[1]}."
            )
    moves = {move.old: move.new for move in plan.moves}
    for group in groups:
        after = Counter(
            moves.get(key, key)
            for key in (
                (series.server.url, series.item.itemid) for series in group.series
            )
        )
        for key, count in after.items():
            if count > 1:
                raise ZabgrabError(
                    f"Group {group.name!r} already holds item {key[1]}, so another "
                    "item can't become it.",
                    hint="Map each item to a different new ID.",
                )


def _resume(
    metadata: dict[str, Any], groups: Sequence[Group], unretire: set[Identity]
) -> None:
    """List brought-back items the last group no longer has, for ``update``."""
    listed = {
        identity(e) for e in [*metadata.get("items", []), *metadata.get("resume", [])]
    }
    entries = latest_entries(groups)
    for key in unretire:
        if key not in listed:
            entry = copy.deepcopy(entries[key])
            entry.pop("retired", None)
            metadata.setdefault("resume", []).append(entry)


def _move_servers(metadata: dict[str, Any], plan: MigrationPlan) -> None:
    """Update a group's list of servers to those its items now come from."""
    used = {
        entry["server"]
        for key in ("items", "resume", "skipped", "failed")
        for entry in metadata.get(key, [])
    }
    servers = [s for s in metadata.get("servers", []) if s.get("url") in used]
    listed = {s.get("url") for s in servers}
    for move in plan.moves:
        if move.server.url in used and move.server.url not in listed:
            servers.append({"name": move.server.name, "url": move.server.url})
            listed.add(move.server.url)
    metadata["servers"] = servers


def _move_requests(
    metadata: dict[str, Any], target: Server | None, new_ids: Mapping[int, int]
) -> None:
    """Point a group's stored requests at the new server and item IDs."""
    for request in metadata.get("query", {}).get("requests", []):
        if target is not None:
            request["server"] = target.name or target.url
        for spec in request.get("items", []):
            if spec.get("itemid") in new_ids:
                spec["itemid"] = new_ids[spec["itemid"]]


def _moved(series: Series, moves: Mapping[Identity, Move]) -> Series:
    """Move a series to its item's new server and ID, if it moves."""
    move = moves.get((series.server.url, series.item.itemid))
    if move is None:
        return series
    item = replace(series.item, itemid=move.item.itemid, hostid=move.item.hostid)
    return replace(series, server=move.server, item=item)


def _log_entry(
    plan: MigrationPlan, stamp: str, command: Sequence[str], version: str
) -> dict[str, Any]:
    """Describe a migration for the groups' ``migrations`` logs."""
    target = plan.target
    return {
        "at": stamp,
        "command": list(command),
        "zabgrab": version,
        "servers": {
            "from": [{"name": s.name, "url": s.url} for s in plan.sources],
            "to": None if target is None else {"name": target.name, "url": target.url},
        },
        "moved": [
            {
                "label": move.label,
                "how": move.how,
                "from": {
                    "server": move.old[0],
                    "server_name": move.old_server_name,
                    "itemid": move.old[1],
                    "hostid": move.old_hostid,
                },
                "to": {
                    "server": move.server.url,
                    "server_name": move.server.name,
                    "itemid": move.item.itemid,
                    "hostid": move.item.hostid,
                },
            }
            for move in plan.moves
        ],
        "retired": [
            {
                "server": r.item[0],
                "server_name": plan.names.get(r.item[0]),
                "itemid": r.item[1],
                "label": r.label,
                "reason": r.reason,
            }
            for r in plan.retirements
        ],
        "unretired": [
            {
                "server": key[0],
                "server_name": plan.names.get(key[0]),
                "itemid": key[1],
                "label": label,
            }
            for key, label in plan.unretired
        ],
    }


def specs_by_id(specs: Iterable[ItemSpec], itemids: Iterable[int]) -> list[int]:
    """List the item IDs that item specs select directly, among some IDs.

    Used to note config templates that still select moved items by their old IDs.

    Parameters
    ----------
    specs : iterable of ItemSpec
        A template's item specs.
    itemids : iterable of int
        The moved items' old IDs.

    Returns
    -------
    list of int
        The IDs both select, in order.
    """
    wanted = set(itemids)
    return [
        spec.itemid
        for spec in specs
        if spec.itemid is not None and spec.itemid in wanted
    ]
