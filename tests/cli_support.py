"""Scaffolding for CLI tests against fake Zabbix servers."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest import mock

from click.testing import CliRunner, Result

from zabgrab.adapters.zabbix import ZabbixAPI
from zabgrab.cli import cli
from zabgrab.model.config import ServerConfig
from zabgrab.services.ports import ZabbixClient

from .fakes import FrozenClock, MemorySecrets
from .fakezabbix import (
    TOKEN,
    FakeTransport,
    FakeZabbix,
    FlakyTransport,
    RedirectingTransport,
    seeded,
)

NOW = datetime.fromtimestamp(1_760_000_000 + 300, UTC)

# UK summer time's name, as the system gives it (the tests run in UK time).
BST = "GMT Daylight Time" if sys.platform == "win32" else "BST"

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
    #: URLs that can't be reached.
    down: set[str] = field(default_factory=set)
    #: The time commands see as now; change it to let time pass between runs.
    clock: FrozenClock = field(default_factory=lambda: FrozenClock(NOW))

    def factory(self, config: ServerConfig) -> ZabbixClient:
        if config.url in self.down:
            return ZabbixAPI(FlakyTransport(seeded(), failures=10**9))
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
    args = ("--no-color", *args)
    # As if run from a shell: the "Next time" hint repeats the command line.
    with mock.patch.object(sys, "argv", ["zabgrab", *args]):
        return CliRunner(env=env).invoke(cli, args, input=input)


def table_rows(output: str) -> list[list[str]]:
    """Cells of the data rows of a rich table."""
    return [
        [cell.strip() for cell in line.strip("│").split("│")]
        for line in output.splitlines()
        if line.startswith("│")
    ]
