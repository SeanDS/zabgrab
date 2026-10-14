"""Zabbix 7 JSON-RPC API client."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from ...errors import AuthenticationFailed, UnsupportedServer, ZabbixAPIError
from ...model.zabbix import Host, Item, ValueType
from .transport import Transport

MIN_VERSION = (7, 0)

# Fragments of error messages Zabbix returns for rejected credentials.
_AUTH_ERRORS = (
    "not authorized",
    "not authorised",
    "session terminated",
    "api token expired",
    "incorrect user name or password",
)

_ITEM_FIELDS = [
    "itemid",
    "hostid",
    "name",
    "key_",
    "value_type",
    "units",
    "status",
    "lastvalue",
    "lastclock",
]

# Zabbix tag operators.
_TAG_EQUALS = 1
_TAG_EXISTS = 4


class ZabbixAPI:
    """A client for one Zabbix server.

    Patterns passed to the search methods follow the Zabbix web UI's filters: they
    are case-insensitive, match anywhere in the value, and ``*`` matches any text.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self._auth: str | None = None
        self._logout_on_close = False

    async def __aenter__(self) -> ZabbixAPI:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def call(self, method: str, params: Any, *, auth: bool = True) -> Any:
        """Call an API method and return its result.

        Raises
        ------
        AuthenticationFailed
            If the server rejected the credentials.
        ZabbixAPIError
            For other API errors.
        ConnectionFailed
            If the server couldn't be reached (raised by the transport).
        """
        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._ids),
        }
        headers = {"Content-Type": "application/json-rpc"}
        if auth and self._auth is not None:
            headers["Authorization"] = f"Bearer {self._auth}"

        response = await self._transport.post(body, headers)

        if not isinstance(response, dict) or not (
            "result" in response or "error" in response
        ):
            raise ZabbixAPIError(f"Malformed response to {method}: {response!r}")
        if "error" in response:
            raise _api_error(method, response["error"])
        return response["result"]

    # Session.

    async def version(self) -> str:
        """The server's API version (e.g. ``"7.0.5"``)."""
        return str(await self.call("apiinfo.version", [], auth=False))

    async def check_version(self) -> str:
        """Return the server's version, raising if it's too old."""
        version = await self.version()
        if _version_tuple(version) < MIN_VERSION:
            raise UnsupportedServer(
                f"The server runs Zabbix {version}; zabgrab needs Zabbix "
                f"{'.'.join(map(str, MIN_VERSION))} or later."
            )
        return version

    async def login_token(self, token: str) -> None:
        """Authenticate with an API token, checking it's valid."""
        await self.call("user.checkAuthentication", {"token": token}, auth=False)
        self._auth = token
        self._logout_on_close = False

    async def login_password(self, username: str, password: str) -> None:
        """Authenticate with a username and password."""
        self._auth = str(
            await self.call(
                "user.login", {"username": username, "password": password}, auth=False
            )
        )
        self._logout_on_close = True

    async def close(self) -> None:
        """End any password session and close the connection."""
        try:
            if self._logout_on_close:
                self._logout_on_close = False
                await self.call("user.logout", [])
        finally:
            self._auth = None
            await self._transport.close()

    # Queries.

    async def hosts(
        self,
        *,
        pattern: str | None = None,
        group: str | None = None,
        limit: int | None = None,
    ) -> list[Host]:
        """Hosts whose technical or visible name matches ``pattern``, optionally
        only those in a host group matching ``group``."""
        params: dict[str, Any] = {
            "output": ["hostid", "host", "name", "status"],
            "selectHostGroups": ["name"],
            "sortfield": "name",
        }
        if pattern:
            params |= {
                "search": {"host": pattern, "name": pattern},
                "searchByAny": True,
                "searchWildcardsEnabled": True,
            }
        if group:
            groups = await self.call(
                "hostgroup.get",
                {
                    "output": ["groupid"],
                    "search": {"name": group},
                    "searchWildcardsEnabled": True,
                },
            )
            if not groups:
                return []
            params["groupids"] = [g["groupid"] for g in groups]
        if limit:
            params["limit"] = limit

        return [_host(row) for row in await self.call("host.get", params)]

    async def items(
        self,
        *,
        itemids: Sequence[int] | None = None,
        hostids: Sequence[int] | None = None,
        name: str | None = None,
        key: str | None = None,
        tag: str | None = None,
        limit: int | None = None,
    ) -> list[Item]:
        """Items on hosts (not templates) matching all of the given filters.

        ``tag`` is ``"name"`` (the tag exists) or ``"name:value"`` (exact value).
        An empty ``itemids`` or ``hostids`` matches nothing.
        """
        if (itemids is not None and not itemids) or (
            hostids is not None and not hostids
        ):
            return []

        params: dict[str, Any] = {
            "output": _ITEM_FIELDS,
            "selectHosts": ["host", "name"],
            "templated": False,
            "sortfield": "name",
        }
        if itemids is not None:
            params["itemids"] = [str(i) for i in itemids]
        if hostids is not None:
            params["hostids"] = [str(i) for i in hostids]
        search = {"name": name, "key_": key}
        if search := {field: value for field, value in search.items() if value}:
            params |= {"search": search, "searchWildcardsEnabled": True}
        if tag:
            tag_name, colon, value = tag.partition(":")
            params["tags"] = [
                {"tag": tag_name, "value": value, "operator": _TAG_EQUALS}
                if colon
                else {"tag": tag_name, "operator": _TAG_EXISTS}
            ]
        if limit:
            params["limit"] = limit

        return [_item(row) for row in await self.call("item.get", params)]


def _api_error(method: str, error: Any) -> ZabbixAPIError:
    if not isinstance(error, dict):
        return ZabbixAPIError(f"Zabbix API error in {method}: {error!r}")
    message = str(error.get("message", "")).rstrip(".")
    data = str(error.get("data", ""))
    code = error.get("code")
    text = f"Zabbix API error in {method}: {message}"
    if data:
        text += f" {data}"
    kind = (
        AuthenticationFailed
        if any(fragment in data.lower() for fragment in _AUTH_ERRORS)
        else ZabbixAPIError
    )
    return kind(text, code=code if isinstance(code, int) else None, data=data)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for part in version.split(".")[:2]:
        digits = "".join(itertools.takewhile(str.isdigit, part))
        parts.append(int(digits or 0))
    return tuple(parts)


def _host(row: dict[str, Any]) -> Host:
    return Host(
        hostid=int(row["hostid"]),
        host=row["host"],
        name=row.get("name") or row["host"],
        enabled=str(row.get("status", "0")) == "0",
        groups=tuple(group["name"] for group in row.get("hostgroups", [])),
    )


def _item(row: dict[str, Any]) -> Item:
    hosts = row.get("hosts") or [{}]
    clock = int(row.get("lastclock") or 0)
    return Item(
        itemid=int(row["itemid"]),
        hostid=int(row["hostid"]),
        host=hosts[0].get("host", ""),
        host_name=hosts[0].get("name") or hosts[0].get("host", ""),
        name=row["name"],
        key=row["key_"],
        value_type=ValueType(int(row["value_type"])),
        units=row.get("units", ""),
        enabled=str(row.get("status", "0")) == "0",
        last_value=row.get("lastvalue") if clock else None,
        last_clock=datetime.fromtimestamp(clock, UTC) if clock else None,
    )
