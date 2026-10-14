"""Write sample zabgrab files for checking in MATLAB.

Run from the repository root:

    python tests/matlab/generate.py [--large ENTRIES]

This writes ``tests/matlab/files/``:

- ``basic.mat``, ``basic.h5``: every value type, odd text, an empty item, two
  servers sharing an item ID, and three groups (an appended group that repeats and
  changes a point, and a custom-named group)
- ``trends.mat``, ``trends.h5``: hourly trends
- ``large.mat``: a log item with many entries, to time MATLAB's ``load``
- ``expected.json``: what the files contain, for ``check_files.m``

Then, in MATLAB, from ``tests/matlab``: ``check_files``.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zabgrab.adapters.formats.hdf5 import write_fetch
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import FetchResult, Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType

HERE = Path(__file__).parent
FILES = HERE / "files"
CREATED = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
T0 = 1_760_000_000.0

DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))
OTHER = Server(None, ServerConfig(url="https://other.example.org"))


def item(
    itemid: int, value_type: ValueType, name: str, *, key: str = "k", units: str = ""
) -> Item:
    return Item(itemid, 10001, "demo1", "DEMO 1 ✓", name, key, value_type, units)


FLOAT = item(
    1, ValueType.FLOAT, "Particles 0.3µm ✓", key="particles[0.3]", units="#/m³"
)
UNSIGNED = item(2, ValueType.UNSIGNED, "Big counter", key="counter", units="B")
LOG = item(3, ValueType.LOG, "Syslog", key="log[/var/log/syslog,,,]")
CHAR = item(4, ValueType.CHAR, "Agent version", key="agent.version")
EMPTY = item(5, ValueType.TEXT, "Never set", key="nothing")
SAME_ID = item(1, ValueType.FLOAT, "Same ID, other server")

GROUP_1 = FetchResult(
    series=[
        Series(
            DEMO,
            FLOAT,
            Resolution.RAW,
            {"time": [T0, T0 + 60.25, T0 + 120], "value": [1.5, 2.5, 3.5]},
            ("particles",),
        ),
        Series(
            DEMO,
            UNSIGNED,
            Resolution.RAW,
            {"time": [T0, T0 + 1, T0 + 2], "value": [0, 2**53 + 1, 2**64 - 1]},
        ),
        Series(
            DEMO,
            LOG,
            Resolution.RAW,
            {
                "time": [T0, T0 + 1, T0 + 2],
                "value": [
                    "kernel: boot",
                    'tab\there, comma, "quote"\nnew line',
                    "𝄞 outside the BMP, ✓",
                ],
                "log_time": [int(T0) - 5, None, int(T0) + 2],
                "source": ["kern", "", "systemd"],
                "severity": [1, 0, 4],
                "event_id": [7, 0, 9],
            },
        ),
        Series(DEMO, CHAR, Resolution.RAW, {"time": [T0], "value": ["7.0.5"]}),
        Series(DEMO, EMPTY, Resolution.RAW, {"time": [], "value": []}),
        Series(OTHER, SAME_ID, Resolution.RAW, {"time": [T0], "value": [42.0]}),
    ]
)
# An update: repeats the last point of item 1 with a new value, and adds one.
GROUP_2 = FetchResult(
    series=[
        Series(
            DEMO,
            FLOAT,
            Resolution.RAW,
            {"time": [T0 + 120, T0 + 180], "value": [30.5, 4.5]},
            ("particles",),
        )
    ]
)
GROUP_3 = FetchResult(
    series=[
        Series(DEMO, CHAR, Resolution.RAW, {"time": [T0 + 300], "value": ["7.0.6"]})
    ]
)
TRENDS = FetchResult(
    series=[
        Series(
            DEMO,
            FLOAT,
            Resolution.HOURLY,
            {
                "time": [T0, T0 + 3600],
                "min": [1.0, 1.5],
                "avg": [2.0, 2.5],
                "max": [3.0, 3.5],
                "count": [60, 58],
            },
        )
    ]
)


def metadata(resolution: str, created: datetime) -> dict[str, Any]:
    return {"query": {"resolution": resolution}, "created": created.isoformat()}


def write(
    path: Path, groups: list[tuple[FetchResult, str | None]], resolution: str
) -> list[dict[str, Any]]:
    path.unlink(missing_ok=True)
    expected = []
    for index, (result, name) in enumerate(groups):
        created = CREATED.replace(minute=index)
        group = write_fetch(
            path,
            result,
            metadata(resolution, created),
            mat=path.suffix == ".mat",
            t_from=T0 + 1000 * index,
            t_to=T0 + 1000 * (index + 1),
            append=index > 0,
            group=name,
            created=CREATED,
        )
        expected.append(_expected_group(group, result, index))
    return expected


def _expected_group(name: str, result: FetchResult, index: int) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for series in result.series:
        key = f"item_{series.item.itemid}"
        if key in items:
            key += "_2"
        items[key] = {
            "itemid": series.item.itemid,
            "hostid": series.item.hostid,
            "value_type": int(series.item.value_type),
            "host": series.item.host,
            "host_name": series.item.host_name,
            "name": series.item.name,
            "key": series.item.key,
            "units": series.item.units,
            "server": series.server.url,
            "server_name": series.server.name or "",
            # uint64 values as strings, since JSON numbers are doubles in MATLAB.
            "columns": {
                column: [str(v) for v in values]
                if series.item.value_type is ValueType.UNSIGNED and column == "value"
                else values
                for column, values in series.columns.items()
            },
        }
    return {
        "name": name,
        "t_from": T0 + 1000 * index,
        "t_to": T0 + 1000 * (index + 1),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--large", type=int, default=100_000, help="entries in large.mat"
    )
    args = parser.parse_args()

    FILES.mkdir(exist_ok=True)
    basic = [(GROUP_1, None), (GROUP_2, None), (GROUP_3, "custom_name")]
    expected = {
        "basic.mat": write(FILES / "basic.mat", basic, "raw"),
        "basic.h5": write(FILES / "basic.h5", basic, "raw"),
        "trends.mat": write(FILES / "trends.mat", [(TRENDS, None)], "1h"),
        "trends.h5": write(FILES / "trends.h5", [(TRENDS, None)], "1h"),
    }
    merged = {
        "time": [T0, T0 + 60.25, T0 + 120, T0 + 180],
        "value": [1.5, 2.5, 30.5, 4.5],
    }
    large = FetchResult(
        series=[
            Series(
                DEMO,
                item(6, ValueType.TEXT, "Large log"),
                Resolution.RAW,
                {
                    "time": [T0 + n for n in range(args.large)],
                    "value": [f"entry {n}" for n in range(args.large)],
                },
            )
        ]
    )
    write(FILES / "large.mat", [(large, None)], "raw")
    (FILES / "expected.json").write_text(
        json.dumps(
            {"files": expected, "merged_item_1": merged, "large_entries": args.large},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"Wrote sample files to {FILES}.")


if __name__ == "__main__":
    main()
