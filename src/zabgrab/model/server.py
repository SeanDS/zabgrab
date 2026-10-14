"""A server chosen for a run."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .config import ServerConfig


@dataclass(frozen=True)
class Server:
    """A Zabbix server: a configured one (with a name) or a literal URL."""

    name: str | None
    config: ServerConfig

    @property
    def url(self) -> str:
        return self.config.url

    @property
    def label(self) -> str:
        """How to refer to the server in messages."""
        return self.name or self.url

    @property
    def frontend_url(self) -> str:
        """The web frontend's base URL (without ``api_jsonrpc.php``)."""
        parts = urlsplit(self.url)
        path = parts.path
        if path.endswith(".php"):
            path = path.rsplit("/", 1)[0]
        return urlunsplit(parts._replace(path=path.rstrip("/"), query="", fragment=""))

    @property
    def token_page(self) -> str:
        """The frontend page where users create API tokens."""
        return f"{self.frontend_url}/zabbix.php?action=user.token.list"
