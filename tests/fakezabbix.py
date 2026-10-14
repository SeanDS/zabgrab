"""A fake Zabbix 7 server.

:class:`FakeZabbix` implements the subset of the JSON-RPC API that zabgrab uses, over
in-memory data. It can be used directly through :class:`FakeTransport` (fast, no
network) or served over HTTP with :func:`serve` to test the real transport.

Search semantics mirror Zabbix: case-insensitive, matching anywhere in the value,
with ``*`` as a wildcard when ``searchWildcardsEnabled`` is set.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

NOT_AUTHORIZED = {
    "code": -32602,
    "message": "Invalid params.",
    "data": "Not authorized.",
}
BAD_LOGIN = {
    "code": -32500,
    "message": "Application error.",
    "data": "Incorrect user name or password or account is temporarily blocked.",
}
SESSION_TERMINATED = {
    "code": -32602,
    "message": "Invalid params.",
    "data": "Session terminated, re-login, please.",
}


@dataclass
class FakeHost:
    hostid: int
    host: str
    name: str
    groups: list[str] = field(default_factory=list)
    status: int = 0


@dataclass
class FakeItem:
    itemid: int
    hostid: int
    name: str
    key: str
    value_type: int
    units: str = ""
    status: int = 0
    lastvalue: str = ""
    lastclock: int = 0
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class Call:
    method: str
    params: Any
    auth: str | None


@dataclass
class FakeZabbix:
    """In-memory Zabbix server state and API implementation."""

    version: str = "7.0.5"
    tokens: set[str] = field(default_factory=set)
    users: dict[str, str] = field(default_factory=dict)
    hosts: list[FakeHost] = field(default_factory=list)
    items: list[FakeItem] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    #: Errors to return for a method instead of handling it.
    errors: dict[str, Any] = field(default_factory=dict)
    sessions: set[str] = field(default_factory=set)

    def methods(self) -> list[str]:
        """Names of the methods called so far."""
        return [call.method for call in self.calls]

    def handle(self, body: dict[str, Any], auth: str | None) -> dict[str, Any]:
        """Handle a JSON-RPC request body and return the response body."""
        method, params = body["method"], body.get("params")
        self.calls.append(Call(method, params, auth))
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": body.get("id")}

        if method in self.errors:
            return reply | {"error": self.errors[method]}

        handler = getattr(self, "_" + method.replace(".", "_"), None)
        if handler is None:
            return reply | {
                "error": {
                    "code": -32601,
                    "message": "Method not found.",
                    "data": f'Incorrect API "{method}".',
                }
            }

        unauthenticated = method in (
            "apiinfo.version",
            "user.login",
            "user.checkAuthentication",
        )
        if not unauthenticated and auth not in self.tokens | self.sessions:
            return reply | {"error": NOT_AUTHORIZED}

        try:
            return reply | {"result": handler(params or {}, auth)}
        except _Error as err:
            return reply | {"error": err.error}

    # Methods.

    def _apiinfo_version(self, params: Any, auth: str | None) -> str:
        if auth is not None:
            raise _Error(
                {
                    "code": -32602,
                    "message": "Invalid params.",
                    "data": 'The "apiinfo.version" method must be called without '
                    'the "auth" parameter.',
                }
            )
        return self.version

    def _user_login(self, params: dict[str, Any], auth: str | None) -> str:
        if self.users.get(params.get("username", "")) != params.get("password"):
            raise _Error(BAD_LOGIN)
        session = secrets.token_hex(16)
        self.sessions.add(session)
        return session

    def _user_logout(self, params: Any, auth: str | None) -> bool:
        self.sessions.discard(auth or "")
        return True

    def _user_checkAuthentication(
        self, params: dict[str, Any], auth: str | None
    ) -> dict[str, Any]:
        token = params.get("token") or params.get("sessionid")
        if token not in self.tokens | self.sessions:
            raise _Error(SESSION_TERMINATED)
        return {"userid": "1", "username": "Admin"}

    def _hostgroup_get(self, params: dict[str, Any], auth: str | None) -> list[Any]:
        names = sorted({group for host in self.hosts for group in host.groups})
        rows = [{"groupid": str(i), "name": name} for i, name in enumerate(names, 1)]
        return [row for row in rows if _search(row, params)]

    def _host_get(self, params: dict[str, Any], auth: str | None) -> list[Any]:
        group_names = {
            group["groupid"]: group["name"] for group in self._hostgroup_get({}, auth)
        }
        rows = []
        for host in self.hosts:
            row: dict[str, Any] = {
                "hostid": str(host.hostid),
                "host": host.host,
                "name": host.name,
                "status": str(host.status),
            }
            if "groupids" in params:
                wanted = {group_names[g] for g in params["groupids"]}
                if not wanted & set(host.groups):
                    continue
            if not _search(row, params):
                continue
            if "selectHostGroups" in params:
                row["hostgroups"] = [{"name": group} for group in host.groups]
            rows.append(row)
        return _finish(rows, params)

    def _item_get(self, params: dict[str, Any], auth: str | None) -> list[Any]:
        hosts = {host.hostid: host for host in self.hosts}
        rows = []
        for item in self.items:
            row: dict[str, Any] = {
                "itemid": str(item.itemid),
                "hostid": str(item.hostid),
                "name": item.name,
                "key_": item.key,
                "value_type": str(item.value_type),
                "units": item.units,
                "status": str(item.status),
                "lastvalue": item.lastvalue,
                "lastclock": str(item.lastclock),
            }
            if "itemids" in params and row["itemid"] not in params["itemids"]:
                continue
            if "hostids" in params and row["hostid"] not in params["hostids"]:
                continue
            if not _search(row, params) or not _tags_match(item.tags, params):
                continue
            if "selectHosts" in params:
                host = hosts[item.hostid]
                row["hosts"] = [{"host": host.host, "name": host.name}]
            rows.append(row)
        return _finish(rows, params)


class _Error(Exception):
    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error


def _pattern(text: str, wildcards: bool) -> re.Pattern[str]:
    parts = text.split("*") if wildcards else [text]
    return re.compile(".*".join(re.escape(part) for part in parts), re.IGNORECASE)


def _search(row: dict[str, Any], params: dict[str, Any]) -> bool:
    search = params.get("search") or {}
    if not search:
        return True
    wildcards = bool(params.get("searchWildcardsEnabled"))
    results = [
        _pattern(value, wildcards).search(str(row.get(name, ""))) is not None
        for name, value in search.items()
    ]
    return any(results) if params.get("searchByAny") else all(results)


def _tags_match(tags: dict[str, str], params: dict[str, Any]) -> bool:
    for condition in params.get("tags", []):
        name = condition["tag"]
        if name not in tags:
            return False
        if condition.get("operator") == 1 and tags[name] != condition.get("value"):
            return False
    return True


def _finish(rows: list[dict[str, Any]], params: dict[str, Any]) -> list[Any]:
    if "sortfield" in params:
        rows.sort(key=lambda row: str(row[params["sortfield"]]).lower())
    if "limit" in params:
        rows = rows[: int(params["limit"])]
    return rows


class FakeTransport:
    """A :class:`~zabgrab.adapters.zabbix.Transport` calling a :class:`FakeZabbix`
    directly."""

    def __init__(self, server: FakeZabbix) -> None:
        self.server = server
        self.closed = False

    async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        authorization = headers.get("Authorization")
        auth = authorization.removeprefix("Bearer ") if authorization else None
        return self.server.handle(body, auth)

    async def close(self) -> None:
        self.closed = True


@asynccontextmanager
async def serve(server: FakeZabbix) -> AsyncIterator[str]:
    """Serve a :class:`FakeZabbix` over HTTP; yields the frontend base URL."""

    async def endpoint(request: web.Request) -> web.Response:
        authorization = request.headers.get("Authorization")
        auth = authorization.removeprefix("Bearer ") if authorization else None
        return web.json_response(server.handle(await request.json(), auth))

    app = web.Application()
    app.router.add_post("/zabbix/api_jsonrpc.php", endpoint)
    test_server = TestServer(app)
    await test_server.start_server()
    try:
        yield str(test_server.make_url("/zabbix"))
    finally:
        await test_server.close()


TOKEN = "valid-token"
USERNAME = "Admin"
PASSWORD = "zabbix"


def seeded() -> FakeZabbix:
    """A server with a few hosts and items of every value type."""
    return FakeZabbix(
        tokens={TOKEN},
        users={USERNAME: PASSWORD},
        hosts=[
            FakeHost(10001, "demo1", "DEMO 1", ["Lab", "DEMO"]),
            FakeHost(10002, "demo2", "DEMO 2", ["Lab", "DEMO"]),
            FakeHost(10003, "web01", "Web server", ["Servers"], status=1),
        ],
        items=[
            FakeItem(
                20001,
                10001,
                "Particles 0.3um",
                "particles[0.3]",
                0,
                "#/m3",
                lastvalue="1234.5",
                lastclock=1_760_000_000,
                tags={"area": "lab"},
            ),
            FakeItem(20002, 10001, "Particles 0.5um", "particles[0.5]", 0, "#/m3"),
            FakeItem(20003, 10002, "Particles 0.3um", "particles[0.3]", 0, "#/m3"),
            FakeItem(20004, 10001, "Uptime", "system.uptime", 3, "s"),
            FakeItem(20005, 10001, "Syslog", "log[/var/log/syslog]", 2),
            FakeItem(20006, 10001, "Agent version", "agent.version", 1),
            FakeItem(20007, 10003, "Description", "web.description", 4, status=1),
            FakeItem(
                20008,
                10002,
                "Interface eth0 in",
                "net.if.in[eth0,bytes]",
                3,
                "B",
                tags={"area": "office"},
            ),
        ],
    )
