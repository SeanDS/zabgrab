"""What a file's metadata records about its items beyond the data.

``zabgrab migrate`` marks items as retired, gives moved items a history of their
earlier servers and IDs, and logs each migration (see
:mod:`zabgrab.services.migrate`). These helpers read those records for ``update``,
``info`` and ``export``, and carry them into the groups and files written later.

Items are identified by their server's URL and item ID, as in the file
(:data:`Identity`).
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

#: An item as a file identifies it: its server's URL and its item ID.
Identity = tuple[str, int]

#: The item entry keys that describe the item's past rather than one fetch, so
#: they're kept when the item is written again (by ``update`` or ``export``).
CARRIED = ("history", "retired")


class HasMetadata(Protocol):
    """Anything with a fetch group's metadata."""

    metadata: dict[str, Any]


def identity(entry: Mapping[str, Any]) -> Identity:
    """Get the identity of an item entry in the metadata."""
    return str(entry["server"]), int(entry["itemid"])


def is_retired(entry: Mapping[str, Any]) -> bool:
    """Check whether an item entry is marked as retired."""
    return bool(entry.get("retired"))


def active_entries(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List the items a group's metadata says ``update`` should fetch next.

    These are the group's items that aren't retired, then any items brought back
    with ``migrate --unretire`` after the group stopped listing them (its
    ``resume`` entries).

    Parameters
    ----------
    metadata : mapping
        A group's metadata (normally the last group's).

    Returns
    -------
    list of dict
        The item entries, each item once.

    Examples
    --------
    >>> metadata = {
    ...     "items": [
    ...         {"server": "https://z", "itemid": 1},
    ...         {"server": "https://z", "itemid": 2, "retired": {"at": "2026"}},
    ...     ],
    ...     "resume": [{"server": "https://z", "itemid": 3}],
    ... }
    >>> [entry["itemid"] for entry in active_entries(metadata)]
    [1, 3]
    """
    found: dict[Identity, dict[str, Any]] = {}
    for entry in [*metadata.get("items", []), *metadata.get("resume", [])]:
        if not is_retired(entry):
            found.setdefault(identity(entry), entry)
    return list(found.values())


def latest_entries(groups: Sequence[HasMetadata]) -> dict[Identity, dict[str, Any]]:
    """Get the latest metadata entry of every item any group lists.

    Parameters
    ----------
    groups : sequence
        The file's groups, in order.

    Returns
    -------
    dict of Identity to dict
        Each item's entry from the last group listing it (in its ``items`` or
        ``resume``), in the order the items first appear.
    """
    entries: dict[Identity, dict[str, Any]] = {}
    for group in groups:
        for entry in [
            *group.metadata.get("items", []),
            *group.metadata.get("resume", []),
        ]:
            entries[identity(entry)] = entry
    return entries


def retired_items(groups: Sequence[HasMetadata]) -> set[Identity]:
    """Find the items that are retired (as their latest entries say).

    Parameters
    ----------
    groups : sequence
        The file's groups, in order.

    Returns
    -------
    set of Identity
        The retired items.
    """
    return {key for key, entry in latest_entries(groups).items() if is_retired(entry)}


def carry_records(
    entries: Iterable[dict[str, Any]], records: Mapping[Identity, Mapping[str, Any]]
) -> None:
    """Copy items' histories and retired marks into new item entries.

    Parameters
    ----------
    entries : iterable of dict
        The new entries (changed in place).
    records : mapping of Identity to mapping
        Earlier entries for the same items (e.g. from :func:`latest_entries`).
    """
    for entry in entries:
        earlier = records.get(identity(entry), {})
        for key in CARRIED:
            if key in earlier:
                entry[key] = copy.deepcopy(earlier[key])


def carry_forward(previous: Mapping[str, Any], metadata: dict[str, Any]) -> None:
    """Carry the migration log and items' histories into a new group's metadata.

    ``update`` writes each group's metadata afresh; this keeps the file's latest
    group a complete record of its items' pasts. Retired marks aren't carried,
    since ``update`` doesn't fetch retired items.

    Parameters
    ----------
    previous : mapping
        The metadata of the group being continued.
    metadata : dict
        The new group's metadata (changed in place).
    """
    if "migrations" in previous:
        metadata["migrations"] = copy.deepcopy(previous["migrations"])
    earlier = {
        identity(entry): entry
        for entry in [*previous.get("items", []), *previous.get("resume", [])]
    }
    for entry in metadata.get("items", []):
        if "history" in earlier.get(identity(entry), {}):
            entry["history"] = copy.deepcopy(earlier[identity(entry)]["history"])
