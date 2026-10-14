"""Finding items and running actions across servers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from zabgrab.adapters.zabbix import ZabbixAPI
from zabgrab.errors import ConnectionFailed
from zabgrab.model.choice import Choice, Select
from zabgrab.model.config import ServerConfig
from zabgrab.model.item import ItemSpec
from zabgrab.model.server import Server
from zabgrab.services.discover import find_items, on_servers
from zabgrab.services.ports import ZabbixClient
from zabgrab.services.servers import ServerSelection

from .fakes import RecordingUI
from .fakezabbix import TOKEN, FakeTransport, seeded


async def client() -> ZabbixClient:
    api = ZabbixAPI(FakeTransport(seeded()))
    await api.login_token(TOKEN)
    return api


def find(*specs: ItemSpec, ui: RecordingUI | None = None, **kwargs: Any) -> list[int]:
    async def scenario() -> list[int]:
        items = await find_items(await client(), specs, ui or RecordingUI(), **kwargs)
        return [item.itemid for item in items]

    return asyncio.run(scenario())


def hosts(*choices: str, select: Select = Select.PROMPT) -> Choice[str]:
    return Choice[str](choices=list(choices), select=select)


class TestFindItems:
    def test_no_specs_lists_everything(self) -> None:
        assert len(find()) == 8
        assert len(find(limit=3)) == 3

    def test_itemid(self) -> None:
        assert find(ItemSpec(itemid=20004)) == [20004]

    def test_missing_itemid_warns(self) -> None:
        ui = RecordingUI()
        assert find(ItemSpec(itemid=1), ui=ui) == []
        assert ui.levels("warning") == ["No item with ID 1."]

    def test_host_pattern(self) -> None:
        assert find(ItemSpec(host="DEMO 2")) == [20008, 20003]

    def test_host_and_name(self) -> None:
        assert find(ItemSpec(host="demo", name="0.3")) == [20001, 20003]

    def test_unmatched_host_warns(self) -> None:
        ui = RecordingUI()
        assert find(ItemSpec(host="nope", name="x"), ui=ui) == []
        assert ui.levels("warning") == ["No hosts match 'nope'."]

    def test_duplicates_are_merged(self) -> None:
        ui = RecordingUI()
        found = find(ItemSpec(itemid=20001), ItemSpec(name="Particles 0.3"), ui=ui)
        assert found == [20001, 20003]
        assert ui.levels("debug") == [
            "Item 20001 matched more than once; using it once."
        ]

    def test_host_choice_all(self) -> None:
        spec = ItemSpec(
            host=hosts("demo1", "demo2", select=Select.ALL), key="particles[0.3]"
        )
        assert find(spec) == [20001, 20003]

    def test_host_choice_first(self) -> None:
        spec = ItemSpec(
            host=hosts("nope", "demo2", select=Select.FIRST), name="Particles"
        )
        assert find(spec) == [20003]

    def test_host_choice_first_none_match(self) -> None:
        ui = RecordingUI()
        spec = ItemSpec(host=hosts("nope", "nada", select=Select.FIRST), name="x")
        assert find(spec, ui=ui) == []
        assert ui.levels("warning") == ["No hosts match 'nope'."]

    def test_host_choice_prompt(self) -> None:
        ui = RecordingUI(answers=[[1]])
        spec = ItemSpec(host=hosts("demo1", "demo2"), key="particles[0.3]")
        assert find(spec, ui=ui) == [20003]

    def test_host_choice_prompt_non_interactive(self) -> None:
        spec = ItemSpec(host=hosts("demo1", "demo2"), name="x")
        with pytest.raises(Exception, match="Which hosts"):
            find(spec, ui=RecordingUI(interactive=False))


def server(name: str) -> Server:
    return Server(name, ServerConfig(url=f"https://{name}.example.org"))


class TestOnServers:
    @staticmethod
    def run(
        selection: ServerSelection, down: set[str], ui: RecordingUI | None = None
    ) -> tuple[list[str], list[FakeTransport]]:
        transports: list[FakeTransport] = []

        async def connect(server: Server) -> ZabbixClient:
            if server.name in down:
                raise ConnectionFailed(f"Can't connect to {server.name}.")
            transport = FakeTransport(seeded())
            transports.append(transport)
            api = ZabbixAPI(transport)
            await api.login_token(TOKEN)
            return api

        async def action(client: ZabbixClient) -> int:
            return len(await client.hosts())

        results = asyncio.run(
            on_servers(selection, connect, action, ui or RecordingUI())
        )
        return [str(server.name) for server, _ in results], transports

    def test_all(self) -> None:
        names, transports = self.run(ServerSelection([server("a"), server("b")]), set())
        assert names == ["a", "b"]
        assert all(transport.closed for transport in transports)

    def test_failover_uses_first_working(self) -> None:
        ui = RecordingUI()
        selection = ServerSelection(
            [server("a"), server("b"), server("c")], failover=True
        )
        names, _ = self.run(selection, {"a"}, ui)
        assert names == ["b"]
        assert ui.levels("warning") == ["Can't connect to a. Trying the next server."]

    def test_failover_all_down(self) -> None:
        selection = ServerSelection([server("a"), server("b")], failover=True)
        with pytest.raises(ConnectionFailed, match="Can't connect to b"):
            self.run(selection, {"a", "b"})

    def test_without_failover_errors_propagate(self) -> None:
        with pytest.raises(ConnectionFailed, match="Can't connect to a"):
            self.run(ServerSelection([server("a"), server("b")]), {"a"})

    def test_client_closed_when_action_fails(self) -> None:
        transport = FakeTransport(seeded())

        async def connect(server: Server) -> ZabbixClient:
            return ZabbixAPI(transport)

        async def action(client: ZabbixClient) -> Any:
            return await client.hosts()  # not logged in

        with pytest.raises(Exception, match="Not authorized"):
            asyncio.run(
                on_servers(
                    ServerSelection([server("a")]), connect, action, RecordingUI()
                )
            )
        assert transport.closed
