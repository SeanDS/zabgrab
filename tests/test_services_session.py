"""Connecting and logging in."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from zabgrab.adapters.zabbix import ZabbixAPI
from zabgrab.errors import (
    AuthenticationFailed,
    ConnectionFailed,
    InputRequired,
    ZabgrabError,
)
from zabgrab.model.config import AuthMethod, ServerConfig
from zabgrab.model.server import Server
from zabgrab.services.ports import ZabbixClient
from zabgrab.services.session import connect

from .fakes import RecordingUI
from .fakezabbix import PASSWORD, TOKEN, USERNAME, FakeTransport, FakeZabbix, seeded

URL = "https://demo.example.org"


@dataclass
class MemorySecrets:
    tokens: dict[str, str] = field(default_factory=dict)
    fail: bool = False

    def get_token(self, url: str) -> str | None:
        return self.tokens.get(url)

    def set_token(self, url: str, token: str) -> None:
        if self.fail:
            raise ZabgrabError("Couldn't save the token.", hint="Use token_env.")
        self.tokens[url] = token


@dataclass
class Harness:
    zabbix: FakeZabbix = field(default_factory=seeded)
    secrets: MemorySecrets = field(default_factory=MemorySecrets)
    env: dict[str, str] = field(default_factory=dict)
    transports: list[FakeTransport] = field(default_factory=list)

    def factory(self, config: ServerConfig) -> ZabbixClient:
        transport = FakeTransport(self.zabbix)
        self.transports.append(transport)
        return ZabbixAPI(transport)

    def connect(self, server: Server, ui: RecordingUI) -> ZabbixClient:
        return asyncio.run(
            connect(
                server,
                factory=self.factory,
                ui=ui,
                secrets=self.secrets,
                env=self.env,
            )
        )

    def last_auth(self) -> str | None:
        return self.zabbix.calls[-1].auth


def server(**config: Any) -> Server:
    return Server("demo", ServerConfig(url=URL, **config))


def hosts(client: ZabbixClient) -> list[str]:
    return [host.host for host in asyncio.run(client.hosts())]


class TestToken:
    def test_from_environment(self) -> None:
        harness = Harness(env={"DEMO_TOKEN": TOKEN})
        ui = RecordingUI()
        client = harness.connect(server(token_env="DEMO_TOKEN"), ui)
        assert hosts(client) == ["demo1", "demo2", "web01"]
        assert harness.last_auth() == TOKEN
        assert ui.questions == []
        assert "with the API token from $DEMO_TOKEN" in ui.levels("debug")[-1]

    def test_from_keyring(self) -> None:
        harness = Harness(secrets=MemorySecrets({URL: TOKEN}))
        client = harness.connect(server(), RecordingUI())
        hosts(client)
        assert harness.last_auth() == TOKEN

    def test_unset_environment_variable_falls_back_to_keyring(self) -> None:
        harness = Harness(secrets=MemorySecrets({URL: TOKEN}))
        ui = RecordingUI()
        hosts(harness.connect(server(token_env="DEMO_TOKEN"), ui))
        assert harness.last_auth() == TOKEN
        assert ui.levels("warning") == []

    def test_unset_environment_variable_without_keyring_warns(self) -> None:
        ui = RecordingUI(interactive=False)
        with pytest.raises(InputRequired):
            Harness().connect(server(token_env="DEMO_TOKEN"), ui)
        assert ui.levels("warning") == ["$DEMO_TOKEN is not set."]

    def test_rejected_non_interactive(self) -> None:
        harness = Harness(env={"DEMO_TOKEN": "old"})
        with pytest.raises(AuthenticationFailed) as exc_info:
            harness.connect(
                server(token_env="DEMO_TOKEN"), RecordingUI(interactive=False)
            )
        hint = exc_info.value.hint
        assert hint is not None
        assert hint.startswith("The API token from $DEMO_TOKEN was rejected.")
        assert harness.transports[0].closed

    def test_rejected_interactive_paste_new(self) -> None:
        harness = Harness(secrets=MemorySecrets({URL: "old"}))
        ui = RecordingUI(answers=[0, TOKEN, False])
        client = harness.connect(server(), ui)
        hosts(client)
        assert ui.levels("error") == [
            "The API token from your keyring was rejected for demo."
        ]
        assert harness.secrets.tokens == {URL: "old"}

    def test_missing_non_interactive(self) -> None:
        harness = Harness()
        with pytest.raises(InputRequired) as exc_info:
            harness.connect(server(), RecordingUI(interactive=False))
        fix = exc_info.value.fix
        assert f"{URL}/zabbix.php?action=user.token.list" in fix
        assert 'token_env = "ZABGRAB_DEMO_TOKEN" in [servers.demo]' in fix

    def test_missing_non_interactive_literal_url(self) -> None:
        harness = Harness()
        literal = Server(None, ServerConfig(url=URL, token_env="MY_TOKEN"))
        with pytest.raises(InputRequired) as exc_info:
            harness.connect(literal, RecordingUI(interactive=False))
        assert 'token_env = "MY_TOKEN" in [servers.myserver]' in exc_info.value.fix

    def test_paste_retries_and_saves(self) -> None:
        harness = Harness()
        ui = RecordingUI(answers=[0, "wrong", TOKEN, True])
        client = harness.connect(server(), ui)
        hosts(client)
        assert ui.levels("error") == [
            "That token was rejected; check it and try again."
        ]
        assert ui.secret_questions == ["API token", "API token"]
        assert harness.secrets.tokens == {URL: TOKEN}
        assert ui.levels("success") == ["Token saved."]

    def test_paste_save_fails(self) -> None:
        harness = Harness(secrets=MemorySecrets(fail=True))
        ui = RecordingUI(answers=[0, TOKEN, True])
        harness.connect(server(), ui)
        assert ui.levels("warning") == ["Couldn't save the token. Use token_env."]

    def test_paste_empty_is_rejected(self) -> None:
        ui = RecordingUI(answers=[0, "  "])
        with pytest.raises(ValueError, match="Please enter a value"):
            Harness().connect(server(), ui)

    def test_choose_password_instead(self) -> None:
        harness = Harness()
        ui = RecordingUI(answers=[1, USERNAME, PASSWORD])
        client = harness.connect(server(), ui)
        hosts(client)
        assert harness.last_auth() in harness.zabbix.sessions


class TestPassword:
    def test_prompts_for_username_and_password(self) -> None:
        harness = Harness()
        ui = RecordingUI(answers=[USERNAME, PASSWORD])
        client = harness.connect(server(auth=AuthMethod.PASSWORD), ui)
        hosts(client)
        assert ui.questions == ["Username", f"Password for {USERNAME}"]
        assert ui.secret_questions == [f"Password for {USERNAME}"]

    def test_configured_username(self) -> None:
        harness = Harness()
        ui = RecordingUI(answers=[PASSWORD])
        harness.connect(server(auth=AuthMethod.PASSWORD, username=USERNAME), ui)
        assert ui.questions == [f"Password for {USERNAME}"]

    def test_retries(self) -> None:
        harness = Harness()
        ui = RecordingUI(answers=[USERNAME, "wrong", USERNAME, PASSWORD])
        harness.connect(server(auth=AuthMethod.PASSWORD), ui)
        assert ui.levels("error") == ["Incorrect username or password; try again."]

    def test_non_interactive(self) -> None:
        with pytest.raises(InputRequired) as exc_info:
            Harness().connect(
                server(auth=AuthMethod.PASSWORD), RecordingUI(interactive=False)
            )
        assert exc_info.value.fix.startswith("Password login needs an interactive")


class TestConnection:
    def test_ssl_warning(self) -> None:
        harness = Harness(secrets=MemorySecrets({URL: TOKEN}))
        ui = RecordingUI()
        harness.connect(server(verify_ssl=False), ui)
        assert ui.levels("warning") == [
            "SSL certificate verification is disabled for demo."
        ]

    def test_version_failure_closes_client(self) -> None:
        harness = Harness()
        harness.zabbix.errors["apiinfo.version"] = {"code": 1, "message": "boom"}
        with pytest.raises(ZabgrabError):
            harness.connect(server(), RecordingUI())
        assert harness.transports[0].closed

    def test_connection_failure_propagates(self) -> None:
        class Unreachable(FakeTransport):
            async def post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
                raise ConnectionFailed("Can't connect.")

        def factory(config: ServerConfig) -> ZabbixClient:
            return ZabbixAPI(Unreachable(seeded()))

        with pytest.raises(ConnectionFailed):
            asyncio.run(
                connect(
                    server(),
                    factory=factory,
                    ui=RecordingUI(),
                    secrets=MemorySecrets(),
                    env={},
                )
            )
