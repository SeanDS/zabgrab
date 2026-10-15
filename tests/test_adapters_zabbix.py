"""Zabbix API client and HTTP transport."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from zabgrab.adapters.zabbix import HttpTransport, ZabbixAPI, api_endpoint
from zabgrab.errors import (
    AuthenticationFailed,
    ConnectionFailed,
    UnsupportedServer,
    ZabbixAPIError,
)
from zabgrab.model.zabbix import ValueType

from .fakezabbix import (
    PASSWORD,
    TOKEN,
    USERNAME,
    FakeTransport,
    FakeZabbix,
    seeded,
    serve,
)

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    async def wrapper() -> T:
        return await coro

    return asyncio.run(wrapper())


async def logged_in(server: FakeZabbix) -> ZabbixAPI:
    api = ZabbixAPI(FakeTransport(server))
    await api.login_token(TOKEN)
    return api


@pytest.mark.parametrize(
    ("url", "endpoint"),
    [
        ("https://z.example.org", "https://z.example.org/api_jsonrpc.php"),
        (
            "https://z.example.org/zabbix/",
            "https://z.example.org/zabbix/api_jsonrpc.php",
        ),
        (
            "https://z.example.org/zabbix/api_jsonrpc.php",
            "https://z.example.org/zabbix/api_jsonrpc.php",
        ),
    ],
)
def test_api_endpoint(url: str, endpoint: str) -> None:
    assert api_endpoint(url) == endpoint


class TestSession:
    def test_version(self) -> None:
        api = ZabbixAPI(FakeTransport(seeded()))
        assert run(api.check_version()) == "7.0.5"

    @pytest.mark.parametrize("version", ["7.0.0", "7.4.1", "8.0.0alpha1", "7.0"])
    def test_supported_versions(self, version: str) -> None:
        server = FakeZabbix(version=version)
        assert run(ZabbixAPI(FakeTransport(server)).check_version()) == version

    @pytest.mark.parametrize("version", ["6.4.12", "6.0.0", "5"])
    def test_unsupported_versions(self, version: str) -> None:
        server = FakeZabbix(version=version)
        with pytest.raises(UnsupportedServer, match=f"runs Zabbix {version}"):
            run(ZabbixAPI(FakeTransport(server)).check_version())

    def test_version_is_sent_without_auth(self) -> None:
        server = seeded()

        async def scenario() -> None:
            api = await logged_in(server)
            await api.version()

        run(scenario())
        assert server.calls[-1].auth is None

    def test_token_login_sends_bearer_token(self) -> None:
        server = seeded()

        async def scenario() -> None:
            api = await logged_in(server)
            await api.hosts()

        run(scenario())
        assert server.calls[-1].auth == TOKEN

    def test_bad_token(self) -> None:
        api = ZabbixAPI(FakeTransport(seeded()))
        with pytest.raises(AuthenticationFailed, match="Session terminated"):
            run(api.login_token("wrong"))

    def test_password_login_and_logout(self) -> None:
        server = seeded()
        transport = FakeTransport(server)

        async def scenario() -> None:
            async with ZabbixAPI(transport) as api:
                await api.login_password(USERNAME, PASSWORD)
                assert len(server.sessions) == 1
                await api.hosts()

        run(scenario())
        assert server.methods()[-1] == "user.logout"
        assert server.sessions == set()
        assert transport.closed

    def test_bad_password(self) -> None:
        api = ZabbixAPI(FakeTransport(seeded()))
        with pytest.raises(AuthenticationFailed, match="Incorrect user name"):
            run(api.login_password(USERNAME, "wrong"))

    def test_token_session_close_does_not_logout(self) -> None:
        server = seeded()

        async def scenario() -> None:
            api = await logged_in(server)
            await api.close()

        run(scenario())
        assert "user.logout" not in server.methods()

    def test_unauthenticated_call(self) -> None:
        api = ZabbixAPI(FakeTransport(seeded()))
        with pytest.raises(AuthenticationFailed, match="Not authorized"):
            run(api.hosts())


class TestErrors:
    def test_api_error(self) -> None:
        server = seeded()
        server.errors["host.get"] = {
            "code": -32602,
            "message": "Invalid params.",
            "data": 'Invalid parameter "/": unexpected parameter "x".',
        }
        with pytest.raises(ZabbixAPIError) as exc_info:
            run(_hosts(server))
        err = exc_info.value
        assert not isinstance(err, AuthenticationFailed)
        assert err.code == -32602
        assert str(err) == (
            'Zabbix API error in host.get: Invalid params Invalid parameter "/": '
            'unexpected parameter "x".'
        )

    def test_error_without_data(self) -> None:
        server = seeded()
        server.errors["host.get"] = {"code": "weird", "message": "Oops."}
        with pytest.raises(ZabbixAPIError) as exc_info:
            run(_hosts(server))
        assert str(exc_info.value) == "Zabbix API error in host.get: Oops"
        assert exc_info.value.code is None

    def test_non_dict_error(self) -> None:
        server = seeded()
        server.errors["host.get"] = "broken"
        with pytest.raises(ZabbixAPIError, match="'broken'"):
            run(_hosts(server))

    def test_unknown_method(self) -> None:
        async def scenario() -> Any:
            api = await logged_in(seeded())
            return await api.call("nonsense.get", {})

        with pytest.raises(ZabbixAPIError, match="Method not found"):
            run(scenario())

    @pytest.mark.parametrize("response", [None, [], {"jsonrpc": "2.0"}])
    def test_malformed_response(self, response: Any) -> None:
        class Broken(FakeTransport):
            async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
                return response

        api = ZabbixAPI(Broken(seeded()))
        with pytest.raises(ZabbixAPIError, match="Malformed response"):
            run(api.version())


async def _hosts(server: FakeZabbix, **kwargs: Any) -> list[str]:
    api = await logged_in(server)
    return [host.host for host in await api.hosts(**kwargs)]


async def _items(server: FakeZabbix, **kwargs: Any) -> list[int]:
    api = await logged_in(server)
    return [item.itemid for item in await api.items(**kwargs)]


class TestHosts:
    def test_all_sorted_by_name(self) -> None:
        assert run(_hosts(seeded())) == ["demo1", "demo2", "web01"]

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("demo", ["demo1", "demo2"]),
            ("DEMO 2", ["demo2"]),
            ("d*2", ["demo2"]),
            ("server", ["web01"]),
            ("nothing", []),
        ],
    )
    def test_pattern_matches_either_name(
        self, pattern: str, expected: list[str]
    ) -> None:
        assert run(_hosts(seeded(), pattern=pattern)) == expected

    def test_group(self) -> None:
        assert run(_hosts(seeded(), group="Serv*")) == ["web01"]

    def test_unknown_group(self) -> None:
        server = seeded()
        assert run(_hosts(server, group="nope")) == []
        assert "host.get" not in server.methods()

    def test_limit(self) -> None:
        assert run(_hosts(seeded(), limit=2)) == ["demo1", "demo2"]

    def test_fields(self) -> None:
        async def scenario() -> Any:
            api = await logged_in(seeded())
            return await api.hosts(pattern="web")

        (host,) = run(scenario())
        assert host.hostid == 10003
        assert host.name == "Web server"
        assert host.groups == ("Servers",)
        assert not host.enabled


class TestItems:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"itemids": [20004, 20001]}, [20001, 20004]),
            ({"hostids": [10002]}, [20008, 20003]),
            ({"name": "particles"}, [20001, 20003, 20002]),
            ({"name": "particles*0.5"}, [20002]),
            ({"key": "particles[*]", "hostids": [10002]}, [20003]),
            ({"key": "net.if.in[eth0,bytes]"}, [20008]),
            ({"tag": "area"}, [20008, 20001]),
            ({"tag": "area:lab"}, [20001]),
            ({"name": "particles", "limit": 1}, [20001]),
            ({"itemids": []}, []),
            ({"hostids": []}, []),
        ],
    )
    def test_filters(self, kwargs: dict[str, Any], expected: list[int]) -> None:
        assert run(_items(seeded(), **kwargs)) == expected

    def test_empty_filters_make_no_request(self) -> None:
        server = seeded()
        run(_items(server, hostids=[]))
        assert "item.get" not in server.methods()

    def test_fields(self) -> None:
        async def scenario() -> Any:
            api = await logged_in(seeded())
            return await api.items(itemids=[20001, 20007])

        disabled, first = run(scenario())  # sorted by name
        assert first.host == "demo1"
        assert first.host_name == "DEMO 1"
        assert first.key == "particles[0.3]"
        assert first.value_type is ValueType.FLOAT
        assert first.units == "#/m3"
        assert first.last_value == "1234.5"
        assert first.last_clock == datetime.fromtimestamp(1_760_000_000, UTC)
        assert (first.history, first.trends) == (31 * 86_400, 365 * 86_400)
        assert (first.tags, disabled.tags) == ((("area", "lab"),), ())
        assert disabled.value_type is ValueType.TEXT
        assert disabled.last_value is None
        assert disabled.last_clock is None
        assert not disabled.enabled


class TestHttpTransport:
    def test_round_trip(self) -> None:
        async def scenario() -> tuple[str, list[str]]:
            async with serve(seeded()) as url, ZabbixAPI(HttpTransport(url)) as api:
                version = await api.check_version()
                await api.login_token(TOKEN)
                hosts = [host.host for host in await api.hosts(pattern="demo")]
            return version, hosts

        assert run(scenario()) == ("7.0.5", ["demo1", "demo2"])

    def test_retries_once_when_a_reused_connection_was_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def scenario() -> tuple[str, int]:
            async with serve(seeded()) as url:
                transport = HttpTransport(url)
                session = transport._client()
                real_post = session.post
                attempts = 0

                def post(*args: Any, **kwargs: Any) -> Any:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise aiohttp.ServerDisconnectedError()
                    return real_post(*args, **kwargs)

                monkeypatch.setattr(session, "post", post)
                async with ZabbixAPI(transport) as api:
                    return await api.version(), attempts

        assert run(scenario()) == ("7.0.5", 2)

    def test_gives_up_after_two_disconnects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        transport = HttpTransport("https://flaky.example.org")

        def post(*args: Any, **kwargs: Any) -> Any:
            raise aiohttp.ServerDisconnectedError()

        async def scenario() -> None:
            monkeypatch.setattr(transport._client(), "post", post)
            try:
                await ZabbixAPI(transport).version()
            finally:
                await transport.close()

        with pytest.raises(ConnectionFailed, match="Server disconnected"):
            run(scenario())

    def test_close_without_requests(self) -> None:
        run(HttpTransport("https://example.org").close())

    @staticmethod
    async def _raw_server(
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        transport_kwargs: dict[str, Any] | None = None,
    ) -> None:
        app = web.Application()
        app.router.add_post("/api_jsonrpc.php", handler)
        server = TestServer(app)
        await server.start_server()
        transport = HttpTransport(str(server.make_url("/")), **(transport_kwargs or {}))
        try:
            await ZabbixAPI(transport).version()
        finally:
            await transport.close()
            await server.close()

    def test_http_error(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            return web.Response(status=404, reason="Not Found")

        with pytest.raises(ConnectionFailed, match="returned HTTP 404 Not Found"):
            run(self._raw_server(handler))

    @pytest.mark.parametrize(
        ("location", "suggestion"),
        [
            ("https://z.example.org/api_jsonrpc.php", "https://z.example.org"),
            ("/zabbix/api_jsonrpc.php", "/zabbix"),
        ],
    )
    def test_redirect(self, location: str, suggestion: str) -> None:
        async def handler(request: web.Request) -> web.Response:
            raise web.HTTPFound(location)

        with pytest.raises(ConnectionFailed, match="redirects to") as exc_info:
            run(self._raw_server(handler))
        assert exc_info.value.hint is not None
        assert exc_info.value.hint.startswith("Use ")
        assert suggestion in exc_info.value.hint
        assert "api_jsonrpc.php" not in exc_info.value.hint

    def test_not_json(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            return web.Response(text="<html>login page</html>")

        with pytest.raises(ConnectionFailed, match="didn't return a Zabbix API"):
            run(self._raw_server(handler))

    def test_timeout(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            await asyncio.sleep(1)
            return web.Response(text="{}")  # pragma: no cover - never reached

        with pytest.raises(ConnectionFailed, match=r"No response .* within 0.05s"):
            run(self._raw_server(handler, {"timeout": 0.05}))

    def test_connection_refused(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        transport = HttpTransport(f"http://127.0.0.1:{port}")

        async def scenario() -> None:
            try:
                await ZabbixAPI(transport).version()
            finally:
                await transport.close()

        with pytest.raises(ConnectionFailed, match="Can't connect to") as exc_info:
            run(scenario())
        assert (
            exc_info.value.hint == "Check the server URL and your network connection."
        )

    def test_certificate_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        certificate_error = aiohttp.ClientConnectorCertificateError(
            connection_key=None,  # type: ignore[arg-type]
            certificate_error=Exception("self-signed certificate"),
        )

        def fail(*args: object, **kwargs: object) -> None:
            raise certificate_error

        transport = HttpTransport("https://self-signed.example.org")

        async def scenario() -> None:
            monkeypatch.setattr(transport._client(), "post", fail)
            try:
                await ZabbixAPI(transport).version()
            finally:
                await transport.close()

        with pytest.raises(
            ConnectionFailed, match="self-signed certificate"
        ) as exc_info:
            run(scenario())
        assert exc_info.value.hint is not None
        assert "verify_ssl = false" in exc_info.value.hint

    def test_verify_ssl_false_disables_verification(self) -> None:
        async def scenario() -> Any:
            transport = HttpTransport("https://x", verify_ssl=False)
            try:
                return transport._client().connector._ssl  # type: ignore[union-attr]
            finally:
                await transport.close()

        assert run(scenario()) is False
