"""Scaffolding for CLI tests against fake Zabbix servers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from click.testing import CliRunner, Result

from zabgrab.adapters.zabbix import ZabbixAPI
from zabgrab.cli import cli
from zabgrab.model.config import ServerConfig
from zabgrab.services.ports import ZabbixClient

from .fakes import MemorySecrets
from .fakezabbix import (
    TOKEN,
    FakeTransport,
    FakeZabbix,
    RedirectingTransport,
    seeded,
)

NOW = datetime.fromtimestamp(1_760_000_000 + 300, UTC)

CONFIG = """\
[servers.demo]
url = "https://demo.example.org"
token_env = "DEMO_TOKEN"

[servers.lab]
url = "https://lab.example.org"
token_env = "LAB_TOKEN"
"""


@dataclass
class Servers:
    by_url: dict[str, FakeZabbix] = field(default_factory=dict)
    secrets: MemorySecrets = field(default_factory=MemorySecrets)
    #: URLs that redirect, and where to.
    redirects: dict[str, str] = field(default_factory=dict)

    def factory(self, config: ServerConfig) -> ZabbixClient:
        if config.url in self.redirects:
            return ZabbixAPI(
                RedirectingTransport(seeded(), config.url, self.redirects[config.url])
            )
        if config.url not in self.by_url:
            self.by_url[config.url] = seeded()
        return ZabbixAPI(FakeTransport(self.by_url[config.url]))


def run(*args: str, input: str | None = None, tokens: bool = True) -> Result:
    env = {"COLUMNS": "250"}
    if tokens:
        env |= {"DEMO_TOKEN": TOKEN, "LAB_TOKEN": TOKEN}
    return CliRunner(env=env).invoke(cli, ["--no-color", *args], input=input)


def table_rows(output: str) -> list[list[str]]:
    """Cells of the data rows of a rich table."""
    return [
        [cell.strip() for cell in line.strip("│").split("│")]
        for line in output.splitlines()
        if line.startswith("│")
    ]
