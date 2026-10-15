"""A fake Zabbix 7 server.

:class:`FakeZabbix` implements the subset of the JSON-RPC API that zabgrab uses, over
in-memory data. It can be used directly through :class:`FakeTransport` (fast, no
network) or served over HTTP with :func:`serve` to test the real transport.

Search semantics mirror Zabbix: case-insensitive, matching anywhere in the value,
with ``*`` as a wildcard when ``searchWildcardsEnabled`` is set.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from zabgrab.errors import ConnectionFailed, Redirected

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
    history: str = "31d"
    trends: str = "365d"
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
    #: Raw history rows by item ID, in API form (all values strings).
    history: dict[int, list[dict[str, str]]] = field(default_factory=dict)
    #: Hourly trend rows by item ID, in API form.
    trends: dict[int, list[dict[str, str]]] = field(default_factory=dict)
    #: Items whose history.get/trend.get calls fail.
    failing_items: set[int] = field(default_factory=set)

    def add_history(self, itemid: int, *points: tuple[float, Any]) -> None:
        """Add raw values as (POSIX time, value) pairs."""
        for time, value in points:
            clock = int(time)
            self.history.setdefault(itemid, []).append(
                {
                    "itemid": str(itemid),
                    "clock": str(clock),
                    "ns": str(round((time - clock) * 1e9)),
                    "value": str(value),
                }
            )

    def add_log(
        self,
        itemid: int,
        clock: int,
        value: str,
        *,
        timestamp: int,
        source: str,
        severity: int,
        logeventid: int,
    ) -> None:
        self.history.setdefault(itemid, []).append(
            {
                "itemid": str(itemid),
                "clock": str(clock),
                "ns": "0",
                "value": value,
                "timestamp": str(timestamp),
                "source": source,
                "severity": str(severity),
                "logeventid": str(logeventid),
            }
        )

    def add_trend(
        self, itemid: int, clock: int, num: int, low: float, avg: float, high: float
    ) -> None:
        self.trends.setdefault(itemid, []).append(
            {
                "itemid": str(itemid),
                "clock": str(clock),
                "num": str(num),
                "value_min": str(low),
                "value_avg": str(avg),
                "value_max": str(high),
            }
        )

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
                "history": item.history,
                "trends": item.trends,
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

    def _check_failing(self, params: dict[str, Any]) -> None:
        if self.failing_items & set(map(int, params.get("itemids", []))):
            raise _Error(
                {
                    "code": -32500,
                    "message": "Application error.",
                    "data": "Item broken.",
                }
            )

    def _history_get(self, params: dict[str, Any], auth: str | None) -> list[Any]:
        self._check_failing(params)
        types = {item.itemid: item.value_type for item in self.items}
        rows = [
            row
            for itemid in map(int, params.get("itemids", []))
            # Zabbix returns nothing if the history type doesn't match the item's.
            if types.get(itemid) == int(params.get("history", 3))
            for row in self.history.get(itemid, [])
            if _in_range(int(row["clock"]), params)
        ]
        if params.get("sortfield") == "clock":
            rows.sort(key=lambda row: int(row["clock"]))
        return rows[: int(params["limit"])] if "limit" in params else rows

    def _trend_get(self, params: dict[str, Any], auth: str | None) -> list[Any]:
        self._check_failing(params)
        rows = [
            row
            for itemid in map(int, params.get("itemids", []))
            for row in self.trends.get(itemid, [])
            if _in_range(int(row["clock"]), params)
        ]
        # The real API doesn't sort trends.
        return list(reversed(rows))


def _in_range(clock: int, params: dict[str, Any]) -> bool:
    return (
        int(params.get("time_from", 0)) <= clock <= int(params.get("time_till", 2**63))
    )


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
        # Yield to the event loop, like real I/O, so concurrency is exercised.
        await asyncio.sleep(0)
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


#: Start of the seeded data (2025-10-09T08:00:00Z, on the hour).
T0 = 1_759_996_800


def seeded() -> FakeZabbix:
    """A server with a few hosts and items of every value type, and data."""
    server = FakeZabbix(
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
    server.add_history(20001, (T0, 1.5), (T0 + 60.25, 2.5), (T0 + 120, 3.5))
    server.add_history(20002, (T0 + 30, 10.0))
    server.add_history(20003, (T0, 7.0), (T0 + 86_400 + 60, 8.0))
    server.add_history(20004, (T0, 100), (T0 + 60, 160))
    server.add_log(
        20005,
        T0,
        "kernel: boot",
        timestamp=T0 - 5,
        source="kern",
        severity=1,
        logeventid=7,
    )
    server.add_log(
        20005,
        T0 + 1,
        'tab\there, comma, "quote"\nnewline ✓ 𝄞',
        timestamp=0,
        source="",
        severity=0,
        logeventid=0,
    )
    server.add_history(20006, (T0, "7.0.5"))
    server.add_trend(20001, T0, 60, 1.0, 2.0, 3.0)
    server.add_trend(20001, T0 + 3600, 58, 1.5, 2.5, 3.5)
    return server


class FlakyTransport(FakeTransport):
    """A transport whose first ``failures`` requests fail like a network error."""

    def __init__(self, server: FakeZabbix, failures: int) -> None:
        super().__init__(server)
        self.failures = failures

    async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        if self.failures:
            self.failures -= 1
            raise ConnectionFailed("Can't connect: connection reset.")
        return await super().post(body, headers)


class RedirectingTransport(FakeTransport):
    """A transport for a URL that redirects, like a real server's HTTP to HTTPS."""

    def __init__(self, server: FakeZabbix, url: str, target: str) -> None:
        super().__init__(server)
        self.url = url
        self.target = target

    async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        endpoint = f"{self.url}/api_jsonrpc.php"
        raise Redirected(
            f"{endpoint} redirects to {self.target}/api_jsonrpc.php.",
            target=self.target,
            hint=f"Use {self.target} as the server URL instead (with --server, or as "
            "the server's url in your config).",
        )
