"""Downloading item data: chunking, concurrency, retries and progress."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, TypeVar

from ..errors import AuthenticationFailed, ConnectionFailed, ZabbixError
from ..model.config import Agg, Resolution
from ..model.series import FetchResult, Series, Skipped
from ..model.server import Server
from ..model.zabbix import HistoryRow, Item, TrendRow, ValueType
from .ports import UI, ZabbixClient

T = TypeVar("T")

#: Length of each request's time window, in seconds. Splitting a long range bounds
#: the size of responses and lets progress bars move.
CHUNK_SECONDS = {Resolution.RAW: 86_400, Resolution.HOURLY: 30 * 86_400}

#: Delays before retrying a request that failed because of the network.
RETRY_DELAYS = (1.0, 2.0, 4.0)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Job:
    """One item to download from one server."""

    server: Server
    item: Item
    requests: tuple[str, ...]
    """Labels of the requests (templates or ``__custom``) that matched the item."""

    @property
    def label(self) -> str:
        return f"{self.item.host}: {self.item.name}"


def chunks(
    start: datetime, end: datetime, resolution: Resolution
) -> list[tuple[int, int]]:
    """Split a time range into inclusive ``(start, end)`` windows of whole seconds."""
    first, last = math.floor(start.timestamp()), math.floor(end.timestamp())
    size = CHUNK_SECONDS[resolution]
    return [(t, min(t + size - 1, last)) for t in range(first, last + 1, size)]


def skip_reason(item: Item, resolution: Resolution) -> str | None:
    """Why ``item`` can't be fetched at ``resolution``, if it can't."""
    if item.value_type is ValueType.BINARY:
        return "binary items aren't supported"
    if resolution is Resolution.HOURLY and not item.value_type.numeric:
        return f"{item.value_type.label} items have no hourly trends"
    return None


async def fetch(
    jobs: Sequence[Job],
    clients: Mapping[Server, ZabbixClient],
    *,
    start: datetime,
    end: datetime,
    resolution: Resolution,
    agg: Sequence[Agg],
    concurrency: int,
    ui: UI,
    sleep: Sleep = asyncio.sleep,
) -> FetchResult:
    """Download every job's data, ``concurrency`` items at a time.

    Items that can't be fetched at this resolution are skipped; items whose
    download fails (after retrying network errors) are reported as failed, and the
    others still downloaded. A rejected login stops everything.
    """
    result = FetchResult()
    runnable = []
    for job in jobs:
        reason = skip_reason(job.item, resolution)
        if reason:
            result.skipped.append(Skipped(job.server, job.item, reason))
        else:
            runnable.append(job)

    windows = chunks(start, end, resolution)
    first, last = start.timestamp(), end.timestamp()
    semaphore = asyncio.Semaphore(concurrency)
    series: dict[int, Series] = {}

    with ui.progress("Downloading", len(runnable) * len(windows)) as progress:

        async def run(index: int, job: Job) -> None:
            async with semaphore:
                bar = progress.add_item(job.label, len(windows))
                try:
                    rows: list[Any] = []
                    for window in windows:
                        rows += await _with_retries(
                            partial(
                                _download, clients[job.server], job, window, resolution
                            ),
                            sleep,
                        )
                        progress.advance(bar)
                except AuthenticationFailed:
                    raise
                except ZabbixError as err:
                    result.failed.append(Skipped(job.server, job.item, err.message))
                else:
                    if resolution is Resolution.RAW:
                        # The API works in whole seconds; trim to the exact range.
                        rows = [row for row in rows if first <= row.time <= last]
                    series[index] = Series(
                        job.server,
                        job.item,
                        resolution,
                        _columns(job.item, rows, resolution, agg),
                        job.requests,
                    )
                finally:
                    progress.remove_item(bar)

        async with asyncio.TaskGroup() as group:
            for index, job in enumerate(runnable):
                group.create_task(run(index, job))

    result.series = [series[index] for index in sorted(series)]
    return result


async def _download(
    client: ZabbixClient, job: Job, window: tuple[int, int], resolution: Resolution
) -> list[Any]:
    if resolution is Resolution.HOURLY:
        return list(await client.trends(job.item, *window))
    return list(await client.history(job.item, *window))


async def _with_retries(call: Callable[[], Awaitable[T]], sleep: Sleep) -> T:
    for delay in RETRY_DELAYS:
        try:
            return await call()
        except ConnectionFailed:
            await sleep(delay)
    return await call()


def _columns(
    item: Item, rows: list[Any], resolution: Resolution, agg: Sequence[Agg]
) -> dict[str, list[Any]]:
    if resolution is Resolution.HOURLY:
        trends: list[TrendRow] = rows
        columns: dict[str, list[Any]] = {"time": [row.time for row in trends]}
        for statistic in agg:
            if statistic is Agg.COUNT:
                columns["count"] = [row.count for row in trends]
            else:
                columns[str(statistic)] = [
                    float(getattr(row, statistic)) for row in trends
                ]
        return columns

    history: list[HistoryRow] = rows
    convert: Callable[[str], Any] = {
        ValueType.FLOAT: float,
        ValueType.UNSIGNED: int,
    }.get(item.value_type, str)
    columns = {
        "time": [row.time for row in history],
        "value": [convert(row.value) for row in history],
    }
    if item.value_type is ValueType.LOG:
        columns["log_time"] = [row.log_time for row in history]
        columns["source"] = [row.source for row in history]
        columns["severity"] = [row.severity for row in history]
        columns["event_id"] = [row.event_id for row in history]
    return columns
