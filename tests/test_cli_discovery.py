"""``zabgrab hosts`` and ``zabgrab items`` end to end, against fake servers."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from zabgrab.adapters.zabbix import HttpTransport, ZabbixAPI
from zabgrab.cli import cli, http_client
from zabgrab.errors import EXIT_ERROR
from zabgrab.model.config import ServerConfig
from zabgrab.services.ports import ZabbixClient

from .fakes import FrozenClock
from .fakezabbix import TOKEN, FakeTransport, FakeZabbix, seeded

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
    secrets: dict[str, str] = field(default_factory=dict)

    def factory(self, config: ServerConfig) -> ZabbixClient:
        if config.url not in self.by_url:
            self.by_url[config.url] = seeded()
        return ZabbixAPI(FakeTransport(self.by_url[config.url]))


@dataclass
class MemorySecrets:
    tokens: dict[str, str]

    def get_token(self, url: str) -> str | None:
        return self.tokens.get(url)

    def set_token(self, url: str, token: str) -> None:
        self.tokens[url] = token


@pytest.fixture
def user_config(fake_home: Path) -> Path:
    path = fake_home / ".config" / "zabgrab" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(CONFIG)
    return path


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch, user_config: Path) -> Servers:
    fake = Servers()
    monkeypatch.setattr("zabgrab.cli.http_client", fake.factory)
    monkeypatch.setattr(
        "zabgrab.cli.KeyringSecrets", lambda: MemorySecrets(fake.secrets)
    )
    monkeypatch.setattr("zabgrab.cli.SystemClock", lambda: FrozenClock(NOW))
    return fake


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zabgrab.cli.is_interactive", lambda *args, **kwargs: True)


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


class TestHosts:
    def test_lists_hosts(self, servers: Servers) -> None:
        result = run("hosts", "--server", "demo")
        assert result.exit_code == 0, result.output
        assert table_rows(result.stdout) == [
            ["10001", "demo1", "DEMO 1", "Lab, DEMO", "enabled"],
            ["10002", "demo2", "DEMO 2", "Lab, DEMO", "enabled"],
            ["10003", "web01", "Web server", "Servers", "disabled"],
        ]

    def test_pattern_and_group(self, servers: Servers) -> None:
        result = run("hosts", "-s", "demo", "demo", "--group", "Lab")
        assert [row[1] for row in table_rows(result.stdout)] == ["demo1", "demo2"]

    def test_multiple_servers_add_column(self, servers: Servers) -> None:
        result = run("hosts", "-s", "demo", "-s", "lab", "web")
        assert table_rows(result.stdout) == [
            ["demo", "10003", "web01", "Web server", "Servers", "disabled"],
            ["lab", "10003", "web01", "Web server", "Servers", "disabled"],
        ]

    def test_no_results(self, servers: Servers) -> None:
        result = run("hosts", "-s", "demo", "nothing")
        assert result.exit_code == 0
        assert result.stdout == ""
        assert "No hosts found." in result.stderr

    def test_limit_warning(self, servers: Servers) -> None:
        result = run("hosts", "-s", "demo", "--limit", "2")
        assert len(table_rows(result.stdout)) == 2
        assert "Showing only the first 2 results from demo" in result.stderr

    def test_default_server_from_config(
        self, servers: Servers, user_config: Path
    ) -> None:
        user_config.write_text(CONFIG + '\n[defaults]\nserver = "lab"\n')
        result = run("hosts", "web")
        assert result.exit_code == 0, result.output
        assert list(servers.by_url) == ["https://lab.example.org"]

    def test_no_server_non_interactive(self, servers: Servers) -> None:
        result = run("hosts")
        assert result.exit_code == EXIT_ERROR
        assert "Input required but running non-interactively" in result.stderr
        assert "--server NAME or --server URL" in result.stderr

    def test_no_server_interactive_choice(
        self, servers: Servers, interactive: None
    ) -> None:
        result = run("hosts", "web", input="2\n")
        assert result.exit_code == 0, result.output
        assert "1. demo" in result.stderr
        assert "3. Custom…" in result.stderr
        assert list(servers.by_url) == ["https://lab.example.org"]

    @pytest.mark.filterwarnings("ignore::getpass.GetPassWarning")
    def test_custom_server_saved_to_config(
        self, servers: Servers, interactive: None, user_config: Path
    ) -> None:
        # Choose "Custom…", enter a URL, save it with the suggested name, then
        # paste a token and don't keep it.
        answers = "3\nzabbix.new.org\ny\n\n1\n" + TOKEN + "\nn\n"
        result = run("hosts", "web", input=answers)
        assert result.exit_code == 0, result.output
        saved = tomllib.loads(user_config.read_text())["servers"]["new"]
        assert saved == {"url": "https://zabbix.new.org"}
        assert user_config.read_text().startswith(CONFIG)

    def test_bad_token_non_interactive(self, servers: Servers) -> None:
        result = CliRunner(env={"DEMO_TOKEN": "nope"}).invoke(
            cli, ["hosts", "-s", "demo"]
        )
        assert result.exit_code == EXIT_ERROR
        assert "Session terminated" in result.stderr
        assert "The API token from $DEMO_TOKEN was rejected" in result.stderr

    def test_missing_token_non_interactive(self, servers: Servers) -> None:
        result = run("hosts", "-s", "demo", tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert "$DEMO_TOKEN is not set" in result.stderr
        assert "export DEMO_TOKEN" in result.stderr

    def test_token_from_keyring(self, servers: Servers) -> None:
        servers.secrets["https://demo.example.org"] = TOKEN
        result = run("hosts", "-s", "demo", tokens=False)
        assert result.exit_code == 0, result.output

    def test_unknown_server(self, servers: Servers) -> None:
        result = run("hosts", "-s", "dmeo")
        assert result.exit_code == EXIT_ERROR
        assert "Unknown server 'dmeo'" in result.stderr
        assert "Did you mean 'demo'?" in result.stderr


class TestItems:
    def test_lists_items(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "particles", "--host", "demo1")
        assert result.exit_code == 0, result.output
        assert table_rows(result.stdout) == [
            [
                "20001",
                "DEMO 1",
                "Particles 0.3um",
                "particles[0.3]",
                "float",
                "#/m3",
                "1234.5",
                "5m",
            ],
            [
                "20002",
                "DEMO 1",
                "Particles 0.5um",
                "particles[0.5]",
                "float",
                "#/m3",
                "",
                "",
            ],
        ]

    def test_disabled_items_are_marked(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "--key", "web.*")
        (row,) = table_rows(result.stdout)
        assert row[2] == "Description (disabled)"
        assert row[4] == "text"

    def test_tag(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "--tag", "area:office")
        assert [row[0] for row in table_rows(result.stdout)] == ["20008"]

    def test_item_specs(self, servers: Servers) -> None:
        result = run(
            "items",
            "-s",
            "demo",
            "--item",
            "20004",
            "-i",
            "host=demo2,key=net.if.in[eth0,bytes]",
        )
        assert [row[0] for row in table_rows(result.stdout)] == ["20004", "20008"]

    def test_invalid_item_spec(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "--item", "hots=x")
        assert result.exit_code == 2
        assert "Can't parse 'hots=x'" in result.stderr

    def test_everything_with_limit(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "--limit", "3")
        assert len(table_rows(result.stdout)) == 3

    def test_no_results(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "nothing")
        assert result.stdout == ""
        assert "No items found." in result.stderr

    def test_multiple_servers(self, servers: Servers) -> None:
        result = run("items", "-s", "demo", "-s", "lab", "--key", "system.uptime")
        assert [row[:2] for row in table_rows(result.stdout)] == [
            ["demo", "20004"],
            ["lab", "20004"],
        ]

    def test_long_values_are_shortened(self, servers: Servers) -> None:
        servers.by_url["https://demo.example.org"] = zabbix = seeded()
        zabbix.items[0].lastvalue = "x" * 100
        result = run("items", "-s", "demo", "--item", "20001")
        assert table_rows(result.stdout)[0][6] == "x" * 39 + "…"


def test_http_client_uses_server_settings() -> None:
    client = http_client(
        ServerConfig(url="https://x.example.org/zabbix", verify_ssl=False, timeout=5)
    )
    assert isinstance(client, ZabbixAPI)
    transport = client._transport
    assert isinstance(transport, HttpTransport)
    assert transport.endpoint == "https://x.example.org/zabbix/api_jsonrpc.php"
    assert (transport.verify_ssl, transport.timeout) == (False, 5)
