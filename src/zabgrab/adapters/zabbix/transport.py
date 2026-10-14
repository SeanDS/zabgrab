"""HTTP transport for the Zabbix JSON-RPC API."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import aiohttp

from ...errors import ConnectionFailed, Redirected
from ...model.names import frontend_url

API_PATH = "api_jsonrpc.php"


def api_endpoint(url: str) -> str:
    """The JSON-RPC endpoint for a server URL.

    The URL may be the frontend's base URL (``https://example.org/zabbix``) or the
    endpoint itself (``.../api_jsonrpc.php``).
    """
    if urlsplit(url).path.endswith(".php"):
        return url
    return f"{url.rstrip('/')}/{API_PATH}"


class Transport(Protocol):
    """Sends JSON-RPC request bodies and returns decoded responses."""

    async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any: ...

    async def close(self) -> None: ...


class HttpTransport:
    """Transport over HTTP(S) using aiohttp.

    Network problems are raised as :class:`ConnectionFailed` with a hint.
    """

    def __init__(self, url: str, *, verify_ssl: bool = True, timeout: float = 30.0):
        self.endpoint = api_endpoint(url)
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=aiohttp.TCPConnector(ssl=self.verify_ssl),
            )
        return self._session

    async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        try:
            try:
                return await self._send(body, headers)
            except aiohttp.ServerDisconnectedError:
                # The server closed an idle keep-alive connection that was reused
                # (e.g. while the user was typing a token). The request didn't
                # reach the server, so it's safe to send it again on a new one.
                return await self._send(body, headers)
        except aiohttp.ClientConnectorCertificateError as exc:
            raise ConnectionFailed(
                f"SSL certificate verification failed for {self.endpoint}: "
                f"{exc.certificate_error}",
                hint="If the server uses a self-signed certificate, set "
                "verify_ssl = false for it in your config (this is insecure).",
            ) from exc
        except aiohttp.ClientError as exc:
            raise ConnectionFailed(
                f"Can't connect to {self.endpoint}: {exc}",
                hint="Check the server URL and your network connection.",
            ) from exc
        except TimeoutError as exc:
            raise ConnectionFailed(
                f"No response from {self.endpoint} within {self.timeout:g}s.",
                hint="Check the server is reachable, or increase its timeout in your "
                "config.",
            ) from exc

    async def _send(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        async with self._client().post(
            self.endpoint, json=body, headers=headers, allow_redirects=False
        ) as response:
            if 300 <= response.status < 400:
                # Following a redirect would turn the POST into a GET (losing
                # the request) or could send credentials to another host.
                target = urljoin(self.endpoint, response.headers.get("Location", ""))
                raise Redirected(
                    f"{self.endpoint} redirects to {target}.",
                    target=frontend_url(target),
                    hint=f"Use {frontend_url(target)} as the server URL instead "
                    "(with --server, or as the server's url in your config).",
                )
            if response.status != 200:
                raise ConnectionFailed(
                    f"{self.endpoint} returned HTTP {response.status} "
                    f"{response.reason or ''}".rstrip()
                    + ".",
                    hint="Check the server URL points at the Zabbix frontend, "
                    "e.g. https://example.org/zabbix.",
                )
            try:
                return await response.json(content_type=None)
            except ValueError:
                raise ConnectionFailed(
                    f"{self.endpoint} didn't return a Zabbix API response.",
                    hint="Check the server URL points at the Zabbix frontend, "
                    "e.g. https://example.org/zabbix.",
                ) from None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
