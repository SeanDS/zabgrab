"""Sorting ``zabgrab items`` by its table's columns."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from zabgrab.model.config import ServerConfig
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.ui.render import ITEM_SORT_KEYS, items_table, sort_items

A = Server("b-server", ServerConfig(url="https://a.example.org"))
B = Server("A-server", ServerConfig(url="https://b.example.org"))


def item(itemid: int, **fields: object) -> Item:
    values: dict[str, object] = {
        "hostid": 1,
        "host": "host",
        "host_name": "Host",
        "name": "Name",
        "key": "key",
        "value_type": ValueType.FLOAT,
    } | fields
    return Item(itemid, **values)  # type: ignore[arg-type]


def at(minute: int) -> datetime:
    return datetime(2026, 10, 14, 12, minute, tzinfo=UTC)


def ids(rows: list[tuple[Server, Item]]) -> list[int]:
    return [i.itemid for _, i in rows]


def test_keys_match_table_columns() -> None:
    table = items_table([(A, item(1)), (B, item(2))], now=at(0))
    assert [column.header for column in table.columns] == list(ITEM_SORT_KEYS)


def test_default_is_item_id_keeping_server_order() -> None:
    rows = [(A, item(3)), (B, item(1)), (A, item(1)), (B, item(20))]
    assert [(s, i.itemid) for s, i in sort_items(rows)] == [
        (B, 1),
        (A, 1),
        (A, 3),
        (B, 20),
    ]


@pytest.mark.parametrize(
    ("column", "items", "expected"),
    [
        ("Host", [item(1, host_name="b"), item(2, host_name="A")], [2, 1]),
        ("Name", [item(1, name="b"), item(2, name="A")], [2, 1]),
        ("Key", [item(1, key="b"), item(2, key="A")], [2, 1]),
        ("Units", [item(1, units="s"), item(2, units="B")], [2, 1]),
        (
            "Type",
            [item(1, value_type=ValueType.TEXT), item(2, value_type=ValueType.CHAR)],
            [2, 1],
        ),
        # Numbers in numeric order, then text, then no value; ties by item ID.
        (
            "Last value",
            [
                item(1),
                item(2, last_value="b"),
                item(3, last_value="10"),
                item(4, last_value="9.5"),
                item(5, last_value="A"),
                item(6, last_value="-1"),
                item(7, last_value="10"),
            ],
            [6, 4, 3, 7, 5, 2, 1],
        ),
        # Newest first, then no value.
        (
            "Age",
            [item(1), item(2, last_clock=at(1)), item(3, last_clock=at(5))],
            [3, 2, 1],
        ),
    ],
)
def test_columns(column: str, items: list[Item], expected: list[int]) -> None:
    assert ids(sort_items([(A, i) for i in items], column)) == expected


def test_server() -> None:
    rows = [(A, item(1)), (B, item(2)), (A, item(0))]
    assert [(s, i.itemid) for s, i in sort_items(rows, "Server")] == [
        (B, 2),
        (A, 0),
        (A, 1),
    ]
