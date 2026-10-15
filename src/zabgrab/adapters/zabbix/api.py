"""Zabbix 7 JSON-RPC API client.

Attributes
----------
MIN_VERSION : tuple of int
    The oldest Zabbix version supported.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from ...errors import AuthenticationFailed, UnsupportedServer, ZabbixAPIError
from ...model.zabbix import (
    HistoryRow,
    Host,
    Item,
    TrendRow,
    ValueType,
    parse_period,
)
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
    "history",
    "trends",
]

# Zabbix tag operators.
_TAG_EQUALS = 1
_TAG_EXISTS = 4


class ZabbixAPI:
    """A client for one Zabbix server.

    Patterns passed to the search methods follow the Zabbix web UI's filters: they
    are case-insensitive, match anywhere in the value, and ``*`` matches any text.

    Parameters
    ----------
    transport : zabgrab.adapters.zabbix.transport.Transport
        Sends the requests (HTTP in normal use; a fake in tests).
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self._auth: str | None = None
        self._logout_on_close = False

    async def __aenter__(self) -> ZabbixAPI:
        """Use the client as an async context manager, closing it on exit."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client (see :meth:`close`)."""
        await self.close()

    async def call(self, method: str, params: Any, *, auth: bool = True) -> Any:
        """Call an API method and return its result.

        Parameters
        ----------
        method : str
            The API method, e.g. ``"item.get"``.
        params : Any
            The method's parameters.
        auth : bool, optional
            Whether to send the token or session (if logged in).

        Returns
        -------
        Any
            The ``result`` of the response.

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
        """Get the server's API version (e.g. ``"7.0.5"``)."""
        return str(await self.call("apiinfo.version", [], auth=False))

    async def check_version(self) -> str:
        """Get the server's version, checking zabgrab supports it.

        Returns
        -------
        str
            The version, e.g. ``"7.0.5"``.

        Raises
        ------
        UnsupportedServer
            If the server is older than :data:`MIN_VERSION`.
        """
        version = await self.version()
        if _version_tuple(version) < MIN_VERSION:
            raise UnsupportedServer(
                f"The server runs Zabbix {version}; zabgrab needs Zabbix "
                f"{'.'.join(map(str, MIN_VERSION))} or later."
            )
        return version

    async def login_token(self, token: str) -> None:
        """Authenticate with an API token, checking it's valid.

        Parameters
        ----------
        token : str
            The API token.

        Raises
        ------
        AuthenticationFailed
            If the server rejects the token.
        """
        await self.call("user.checkAuthentication", {"token": token}, auth=False)
        self._auth = token
        self._logout_on_close = False

    async def login_password(self, username: str, password: str) -> None:
        """Authenticate with a username and password.

        The session is logged out again when the client is closed.

        Parameters
        ----------
        username, password : str
            The credentials.

        Raises
        ------
        AuthenticationFailed
            If the server rejects them.
        """
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
        """Find hosts.

        Parameters
        ----------
        pattern : str, optional
            A pattern for the technical or visible name (all hosts if not given).
        group : str, optional
            Only hosts in a host group matching this pattern.
        limit : int, optional
            The most hosts to return.

        Returns
        -------
        list of Host
            The hosts, sorted by visible name.
        """
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
        """Find items on hosts (not templates) matching all of the given filters.

        Parameters
        ----------
        itemids, hostids : sequence of int, optional
            Only these items, or items on these hosts. Empty matches nothing.
        name, key : str, optional
            Patterns for the item's name and key.
        tag : str, optional
            ``"name"`` (the tag exists) or ``"name:value"`` (with exactly that
            value).
        limit : int, optional
            The most items to return.

        Returns
        -------
        list of Item
            The items, sorted by name.
        """
        if (itemids is not None and not itemids) or (
            hostids is not None and not hostids
        ):
            return []

        params: dict[str, Any] = {
            "output": _ITEM_FIELDS,
            "selectHosts": ["host", "name"],
            "selectTags": ["tag", "value"],
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

    async def history(self, item: Item, start: int, end: int) -> list[HistoryRow]:
        """Get an item's raw values.

        Parameters
        ----------
        item : Item
            The item.
        start, end : int
            The time range in POSIX seconds (inclusive).

        Returns
        -------
        list of HistoryRow
            The values, oldest first.
        """
        rows = await self.call(
            "history.get",
            {
                "output": "extend",
                "history": int(item.value_type),
                "itemids": [str(item.itemid)],
                "time_from": start,
                "time_till": end,
                "sortfield": "clock",
                "sortorder": "ASC",
            },
        )
        return sorted((_history_row(row) for row in rows), key=lambda row: row.time)

    async def trends(self, item: Item, start: int, end: int) -> list[TrendRow]:
        """Get an item's hourly trends.

        Parameters
        ----------
        item : Item
            The item (numeric).
        start, end : int
            The range of hour starts, in POSIX seconds (inclusive).

        Returns
        -------
        list of TrendRow
            The hours, oldest first.
        """
        rows = await self.call(
            "trend.get",
            {
                "output": ["clock", "num", "value_min", "value_avg", "value_max"],
                "itemids": [str(item.itemid)],
                "time_from": start,
                "time_till": end,
            },
        )
        return sorted((_trend_row(row) for row in rows), key=lambda row: row.time)


def _api_error(method: str, error: Any) -> ZabbixAPIError:
    """Turn an API error response into an exception (rejected credentials apart)."""
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
    """Get (major, minor) from a version such as ``"7.0.5"`` or ``"7.2.0rc1"``."""
    parts = []
    for part in version.split(".")[:2]:
        digits = "".join(itertools.takewhile(str.isdigit, part))
        parts.append(int(digits or 0))
    return tuple(parts)


def _host(row: dict[str, Any]) -> Host:
    """Convert a ``host.get`` row."""
    return Host(
        hostid=int(row["hostid"]),
        host=row["host"],
        name=row.get("name") or row["host"],
        enabled=str(row.get("status", "0")) == "0",
        groups=tuple(group["name"] for group in row.get("hostgroups", [])),
    )


def _item(row: dict[str, Any]) -> Item:
    """Convert an ``item.get`` row (``lastclock`` 0 means no value yet)."""
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
        history=parse_period(row.get("history", "")),
        trends=parse_period(row.get("trends", "")),
        tags=tuple((tag["tag"], tag.get("value", "")) for tag in row.get("tags", [])),
    )


def _history_row(row: dict[str, Any]) -> HistoryRow:
    """Convert a ``history.get`` row (log fields are absent for other types)."""
    log_time = int(row.get("timestamp") or 0)
    return HistoryRow(
        time=int(row["clock"]) + int(row.get("ns") or 0) / 1e9,
        value=str(row["value"]),
        log_time=log_time or None,
        source=str(row.get("source", "")),
        severity=int(row.get("severity") or 0),
        event_id=int(row.get("logeventid") or 0),
    )


def _trend_row(row: dict[str, Any]) -> TrendRow:
    """Convert a ``trend.get`` row."""
    return TrendRow(
        time=int(row["clock"]),
        count=int(row["num"]),
        min=str(row["value_min"]),
        avg=str(row["value_avg"]),
        max=str(row["value_max"]),
    )
