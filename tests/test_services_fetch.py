"""Downloading item data."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from zabgrab.adapters.zabbix import ZabbixAPI
from zabgrab.errors import AuthenticationFailed
from zabgrab.model.config import Agg, Resolution, ServerConfig
from zabgrab.model.series import FetchResult
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.fetch import RETRY_DELAYS, Job, chunks, fetch, skip_reason
from zabgrab.services.ports import ZabbixClient

from .fakes import RecordingUI
from .fakezabbix import T0, TOKEN, FakeTransport, FlakyTransport, seeded

SERVER = Server("demo", ServerConfig(url="https://demo.example.org"))
START = datetime.fromtimestamp(T0, UTC)
DAY = 86_400


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(T0 + seconds, UTC)


async def client(transport: FakeTransport) -> ZabbixAPI:
    api = ZabbixAPI(transport)
    await api.login_token(TOKEN)
    return api


async def items(api: ZabbixClient, *itemids: int) -> list[Job]:
    found = await api.items(itemids=list(itemids))
    by_id = {item.itemid: item for item in found}
    return [Job(SERVER, by_id[i], ("t",)) for i in itemids]


def run_fetch(
    *itemids: int,
    end: float = 600,
    resolution: Resolution = Resolution.RAW,
    agg: tuple[Agg, ...] = (Agg.MIN, Agg.AVG, Agg.MAX),
    ui: RecordingUI | None = None,
    transport: FakeTransport | None = None,
    concurrency: int = 4,
    sleeps: list[float] | None = None,
) -> FetchResult:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    async def scenario() -> FetchResult:
        api = await client(transport or FakeTransport(seeded()))
        jobs = await items(api, *itemids)
        return await fetch(
            jobs,
            {SERVER: api},
            start=START,
            end=at(end),
            resolution=resolution,
            agg=agg,
            concurrency=concurrency,
            ui=ui or RecordingUI(),
            sleep=sleep,
        )

    return asyncio.run(scenario())


class TestChunks:
    def test_single(self) -> None:
        assert chunks(START, at(600), Resolution.RAW) == [(T0, T0 + 600)]

    def test_split_without_overlap(self) -> None:
        assert chunks(START, at(2 * DAY + 5), Resolution.RAW) == [
            (T0, T0 + DAY - 1),
            (T0 + DAY, T0 + 2 * DAY - 1),
            (T0 + 2 * DAY, T0 + 2 * DAY + 5),
        ]

    def test_trends_use_longer_chunks(self) -> None:
        assert len(chunks(START, at(40 * DAY), Resolution.HOURLY)) == 2

    def test_fractional_seconds_are_floored(self) -> None:
        assert chunks(at(0.7), at(10.9), Resolution.RAW) == [(T0, T0 + 10)]


class TestSkipReason:
    def test_binary(self) -> None:
        item = Item(1, 1, "h", "h", "n", "k", ValueType.BINARY)
        assert skip_reason(item, Resolution.RAW) == "binary items aren't supported"


class TestRaw:
    def test_numeric(self) -> None:
        result = run_fetch(20001, 20004)
        floats, unsigned = result.series
        assert floats.columns == {
            "time": [T0, T0 + 60.25, T0 + 120],
            "value": [1.5, 2.5, 3.5],
        }
        assert floats.requests == ("t",)
        assert unsigned.columns["value"] == [100, 160]
        assert all(isinstance(v, int) for v in unsigned.columns["value"])
        assert result.points == 5

    def test_text(self) -> None:
        (series,) = run_fetch(20006).series
        assert series.columns == {"time": [T0], "value": ["7.0.5"]}

    def test_log(self) -> None:
        (series,) = run_fetch(20005).series
        assert series.columns == {
            "time": [T0, T0 + 1],
            "value": ["kernel: boot", 'tab\there, comma, "quote"\nnewline ✓ 𝄞'],
            "log_time": [T0 - 5, None],
            "source": ["kern", ""],
            "severity": [1, 0],
            "event_id": [7, 0],
        }

    def test_across_chunks(self) -> None:
        (series,) = run_fetch(20003, end=2 * DAY).series
        assert series.columns["time"] == [T0, T0 + DAY + 60]

    def test_time_range_is_respected(self) -> None:
        (series,) = run_fetch(20001, end=60).series
        assert series.columns["value"] == [1.5]

    def test_empty(self) -> None:
        (series,) = run_fetch(20007).series
        assert series.points == 0


class TestTrends:
    def test_selected_statistics(self) -> None:
        (series,) = run_fetch(
            20001, end=DAY, resolution=Resolution.HOURLY, agg=(Agg.AVG, Agg.COUNT)
        ).series
        # Sorted by time, although the API returns trends in no particular order.
        assert series.columns == {
            "time": [T0, T0 + 3600],
            "avg": [2.0, 2.5],
            "count": [60, 58],
        }

    def test_all_statistics(self) -> None:
        (series,) = run_fetch(20001, end=DAY, resolution=Resolution.HOURLY).series
        assert series.columns["min"] == [1.0, 1.5]
        assert series.columns["max"] == [3.0, 3.5]

    def test_text_items_are_skipped(self) -> None:
        result = run_fetch(20001, 20005, 20006, resolution=Resolution.HOURLY)
        assert [s.item.itemid for s in result.series] == [20001]
        assert [(s.item.itemid, s.reason) for s in result.skipped] == [
            (20005, "log items have no hourly trends"),
            (20006, "char items have no hourly trends"),
        ]


class TestProgressAndConcurrency:
    def test_progress(self) -> None:
        ui = RecordingUI()
        run_fetch(20001, 20003, end=2 * DAY, ui=ui)
        (progress,) = ui.progresses
        assert progress.total == 6  # 2 items x 3 chunks
        assert progress.done == 6
        assert progress.labels == ["demo1: Particles 0.3um", "demo2: Particles 0.3um"]
        assert progress.active == set()
        assert progress.finished

    def test_concurrency_limits_active_items(self) -> None:
        ui = RecordingUI()
        run_fetch(20001, 20002, 20003, 20004, 20006, concurrency=2, ui=ui)
        assert ui.progresses[0].max_active == 2

    def test_skipped_items_are_not_counted(self) -> None:
        ui = RecordingUI()
        run_fetch(20001, 20005, resolution=Resolution.HOURLY, ui=ui)
        assert ui.progresses[0].total == 1


class TestErrors:
    def test_retry_then_succeed(self) -> None:
        zabbix = seeded()
        transport = FlakyTransport(zabbix, 0)
        sleeps: list[float] = []

        async def scenario() -> FetchResult:
            api = await client(transport)
            jobs = await items(api, 20001)
            transport.failures = 2
            return await fetch(
                jobs,
                {SERVER: api},
                start=START,
                end=at(600),
                resolution=Resolution.RAW,
                agg=(),
                concurrency=1,
                ui=RecordingUI(),
                sleep=_recorder(sleeps),
            )

        result = asyncio.run(scenario())
        assert sleeps == list(RETRY_DELAYS[:2])
        assert result.series[0].points == 3

    def test_gives_up_after_retries(self) -> None:
        transport = FlakyTransport(seeded(), 0)
        sleeps: list[float] = []

        async def scenario() -> FetchResult:
            api = await client(transport)
            jobs = await items(api, 20001, 20004)
            transport.failures = len(RETRY_DELAYS) + 1
            return await fetch(
                jobs,
                {SERVER: api},
                start=START,
                end=at(600),
                resolution=Resolution.RAW,
                agg=(),
                concurrency=1,
                ui=RecordingUI(),
                sleep=_recorder(sleeps),
            )

        result = asyncio.run(scenario())
        assert [s.item.itemid for s in result.failed] == [20001]
        assert result.failed[0].reason == "Can't connect: connection reset."
        assert [s.item.itemid for s in result.series] == [20004]

    def test_api_errors_fail_the_item_without_retrying(self) -> None:
        zabbix = seeded()
        sleeps: list[float] = []
        zabbix.errors["history.get"] = {"code": -32602, "message": "Invalid params."}
        result = run_fetch(20001, transport=FakeTransport(zabbix), sleeps=sleeps)
        assert sleeps == []
        assert result.series == []
        assert "Invalid params" in result.failed[0].reason

    def test_rejected_login_stops_everything(self) -> None:
        zabbix = seeded()

        async def scenario() -> Any:
            api = await client(FakeTransport(zabbix))
            jobs = await items(api, 20001, 20004)
            zabbix.tokens.clear()
            return await fetch(
                jobs,
                {SERVER: api},
                start=START,
                end=at(600),
                resolution=Resolution.RAW,
                agg=(),
                concurrency=1,
                ui=RecordingUI(),
            )

        with pytest.raises(ExceptionGroup) as exc_info:
            asyncio.run(scenario())
        assert exc_info.group_contains(AuthenticationFailed)


def _recorder(sleeps: list[float]) -> Any:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleep
