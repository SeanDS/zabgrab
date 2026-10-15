"""Benchmark text (cell arrays) in .mat files written by zabgrab.

Run with: python benchmarks/mat_text.py [ENTRIES ...]

Prints write time, read time and file size per text entry, compared with .h5.
Used to set the default mat_text_warn_entries and the estimates shown when it's
exceeded.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from zabgrab.adapters.formats.hdf5 import read_fetches, write_fetch
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import FetchResult, Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType

SERVER = Server("bench", ServerConfig(url="https://bench.example.org"))


def log_series(entries: int) -> FetchResult:
    """Make a fetch of one log item with ``entries`` lines."""
    item = Item(1, 1, "host", "Host", "Syslog", "log[/var/log/syslog]", ValueType.TEXT)
    columns = {
        "time": [float(n) for n in range(entries)],
        "value": [
            f"Oct 14 12:00:{n % 60:02d} host kernel: message {n}"
            for n in range(entries)
        ],
    }
    return FetchResult(series=[Series(SERVER, item, Resolution.RAW, columns)])


def main(sizes: list[int]) -> None:
    """Time writing and reading each number of entries, as .mat and .h5."""
    columns = ("entries", "format", "write s", "read s", "MB", "bytes/entry")
    print(" ".join(f"{column:>11}" for column in columns))
    for entries in sizes:
        data = log_series(entries)
        for suffix in (".mat", ".h5"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"bench{suffix}"
                start = time.perf_counter()
                write_fetch(path, data, {}, mat=suffix == ".mat", t_from=0, t_to=1)
                written = time.perf_counter()
                read_fetches(path)
                read = time.perf_counter()
                size = path.stat().st_size
            values = (
                f"{entries}",
                suffix,
                f"{written - start:.2f}",
                f"{read - written:.2f}",
                f"{size / 1e6:.1f}",
                f"{size / entries:.0f}",
            )
            print(" ".join(f"{value:>11}" for value in values))


if __name__ == "__main__":
    main([int(arg) for arg in sys.argv[1:]] or [1_000, 10_000, 50_000, 100_000])
