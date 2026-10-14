"""Tab- and comma-separated output.

Layout:

- ``#`` comment lines with the metadata as JSON (numpy and pandas skip them with
  ``comment="#"``)
- a header row, then columns per item: ``time`` and ``value`` (plus log fields for
  log items), or the selected trend statistics
- shorter series are padded with empty cells across all of their columns
- times are ISO 8601 with the offset of the configured timezone

Text values containing the separator, quotes or newlines are quoted, so multi-line
log entries need a CSV-aware reader such as pandas.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from itertools import zip_longest
from pathlib import Path
from typing import Any

from ...errors import ZabgrabError
from ...model.series import FetchResult, Series
from ..files import match_permissions

TIME_COLUMNS = frozenset({"time", "log_time"})


def write_delimited(
    path: Path,
    result: FetchResult,
    metadata: Mapping[str, Any],
    *,
    delimiter: str,
    tz: tzinfo | None,
) -> None:
    """Write ``result`` to ``path``, atomically."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
                _write(file, result, metadata, delimiter=delimiter, tz=tz)
            match_permissions(Path(temporary), path)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ZabgrabError(f"Can't write {path}: {exc.strerror or exc}") from exc


def _write(
    file: Any,
    result: FetchResult,
    metadata: Mapping[str, Any],
    *,
    delimiter: str,
    tz: tzinfo | None,
) -> None:
    file.write("# zabgrab data. The metadata below is JSON; data starts after it.\n")
    for line in json.dumps(metadata, indent=2, ensure_ascii=False).splitlines():
        file.write(f"# {line}\n")

    writer = csv.writer(file, delimiter=delimiter, lineterminator="\n")
    labels = _labels(result.series)
    writer.writerow(
        f"{label} {column}"
        for label, series in zip(labels, result.series, strict=True)
        for column in series.columns
    )

    blanks = [[""] * len(series.columns) for series in result.series]
    rows = zip_longest(*(_rows(series, tz) for series in result.series))
    for row in rows:
        writer.writerow(
            cell
            for cells, blank in zip(row, blanks, strict=True)
            for cell in (blank if cells is None else cells)
        )


def _labels(all_series: list[Series]) -> list[str]:
    several_servers = len({series.server.url for series in all_series}) > 1
    labels = [
        (f"{series.server.label}/" if several_servers else "")
        + f"{series.item.host}: {series.item.name}"
        for series in all_series
    ]
    counts = Counter(labels)
    return [
        f"{label} ({series.item.itemid})" if counts[label] > 1 else label
        for label, series in zip(labels, all_series, strict=True)
    ]


def _rows(series: Series, tz: tzinfo | None) -> Any:
    columns = [
        [_cell(name, value, tz) for value in values]
        for name, values in series.columns.items()
    ]
    return zip(*columns, strict=True)


def _cell(column: str, value: Any, tz: tzinfo | None) -> str:
    if value is None:
        return ""
    if column in TIME_COLUMNS:
        return datetime.fromtimestamp(value, UTC).astimezone(tz).isoformat()
    return str(value)
