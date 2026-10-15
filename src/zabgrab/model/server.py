"""A server chosen for a run."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ServerConfig
from .names import frontend_url


@dataclass(frozen=True)
class Server:
    """A Zabbix server: a configured one (with a name) or a literal URL.

    Attributes
    ----------
    name : str or None
        The name in the config (``[servers.NAME]``), or ``None`` for a URL given
        directly.
    config : ServerConfig
        The server's settings (defaults for a URL given directly).
    """

    name: str | None
    config: ServerConfig

    @property
    def url(self) -> str:
        """The server's API URL, as configured or given."""
        return self.config.url

    @property
    def label(self) -> str:
        """How to refer to the server in messages."""
        return self.name or self.url

    @property
    def display(self) -> str:
        """How to refer to the server in sentences: a quoted name, or the URL."""
        return f"'{self.name}'" if self.name else self.url

    @property
    def frontend_url(self) -> str:
        """The web frontend's base URL (without ``api_jsonrpc.php``)."""
        return frontend_url(self.url)

    @property
    def token_page(self) -> str:
        """The frontend page where users create API tokens."""
        return f"{self.frontend_url}/zabbix.php?action=user.token.list"
