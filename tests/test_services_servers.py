"""Choosing servers."""

from __future__ import annotations

from typing import Any

import pytest

from zabgrab.errors import InputRequired, ZabgrabError
from zabgrab.model.choice import Choice, Select
from zabgrab.model.config import Config, ServerConfig
from zabgrab.model.server import Server
from zabgrab.services.servers import (
    ServerSelection,
    parse_url,
    resolve_server,
    select_servers,
    suggest_name,
)

from .fakes import RecordingUI

CONFIG = Config.model_validate(
    {
        "servers": {
            "demo": {"url": "https://demo.example.org"},
            "lab": {"url": "https://lab.example.org/zabbix"},
        }
    }
)
DEMO = Server("demo", CONFIG.servers["demo"])
LAB = Server("lab", CONFIG.servers["lab"])


def literal(url: str) -> Server:
    return Server(None, ServerConfig(url=url))


class SavedServers(list[tuple[str, str]]):
    def __call__(self, name: str, url: str) -> None:
        self.append((name, url))


def choice(*choices: str, select: Select = Select.PROMPT) -> Choice[str]:
    return Choice[str](choices=list(choices), select=select)


class TestServerModel:
    @pytest.mark.parametrize(
        ("url", "frontend"),
        [
            ("https://z.org", "https://z.org"),
            ("https://z.org/zabbix/", "https://z.org/zabbix"),
            ("https://z.org/zabbix/api_jsonrpc.php", "https://z.org/zabbix"),
        ],
    )
    def test_frontend_url(self, url: str, frontend: str) -> None:
        server = literal(url)
        assert server.frontend_url == frontend
        assert server.token_page == f"{frontend}/zabbix.php?action=user.token.list"

    def test_label(self) -> None:
        assert DEMO.label == "demo"
        assert literal("https://x.org").label == "https://x.org"


class TestCli:
    def test_names_and_urls(self) -> None:
        selection = select_servers(
            ["lab", "https://x.org"], "demo", CONFIG, RecordingUI()
        )
        assert selection == ServerSelection([LAB, literal("https://x.org")])

    def test_cli_overrides_configured_choice(self) -> None:
        selection = select_servers(["demo"], choice("lab"), CONFIG, RecordingUI())
        assert selection.servers == [DEMO]


class TestConfigured:
    def test_name(self) -> None:
        assert select_servers([], "demo", CONFIG, RecordingUI()).servers == [DEMO]

    def test_choice_all(self) -> None:
        selection = select_servers(
            [], choice("demo", "lab", select=Select.ALL), CONFIG, RecordingUI()
        )
        assert selection == ServerSelection([DEMO, LAB])

    def test_choice_first(self) -> None:
        selection = select_servers(
            [], choice("lab", "demo", select=Select.FIRST), CONFIG, RecordingUI()
        )
        assert selection == ServerSelection([LAB, DEMO], failover=True)

    def test_choice_prompt(self) -> None:
        ui = RecordingUI(answers=[[1]])
        selection = select_servers([], choice("demo", "lab"), CONFIG, ui)
        assert selection.servers == [LAB]
        assert ui.questions == ["Which servers?"]

    def test_choice_prompt_custom(self) -> None:
        ui = RecordingUI(answers=[[0, 2], "other.org", False])
        selection = select_servers([], choice("demo", "lab"), CONFIG, ui)
        assert selection.servers == [DEMO, literal("https://other.org")]

    def test_choice_prompt_non_interactive(self) -> None:
        with pytest.raises(InputRequired) as exc_info:
            select_servers(
                [], choice("demo", "lab"), CONFIG, RecordingUI(interactive=False)
            )
        assert "--server NAME" in exc_info.value.fix


class TestMissing:
    def test_choose_configured(self) -> None:
        ui = RecordingUI(answers=[1])
        assert select_servers([], None, CONFIG, ui).servers == [LAB]

    def test_custom_without_saving(self) -> None:
        saved = SavedServers()
        ui = RecordingUI(answers=[2, "zabbix.new.org:8080/zabbix", False])
        selection = select_servers([], None, CONFIG, ui, save=saved)
        assert selection.servers == [literal("https://zabbix.new.org:8080/zabbix")]
        assert saved == []

    def test_custom_and_save(self) -> None:
        saved = SavedServers()
        ui = RecordingUI(answers=[2, "https://zabbix.new.org", True, "newbie"])
        selection = select_servers([], None, CONFIG, ui, save=saved)
        assert selection.servers == [
            Server("newbie", ServerConfig(url="https://zabbix.new.org"))
        ]
        assert saved == [("newbie", "https://zabbix.new.org")]
        assert "Saved server 'newbie'" in ui.levels("success")[0]

    def test_custom_without_save_callback_does_not_offer(self) -> None:
        ui = RecordingUI(answers=[2, "https://zabbix.new.org"])
        select_servers([], None, CONFIG, ui)
        assert ui.questions == ["Which Zabbix server?", "Zabbix server URL"]

    def test_no_servers_configured_asks_for_url(self) -> None:
        ui = RecordingUI(answers=["https://a.org", False])
        selection = select_servers([], None, Config(), ui, save=SavedServers())
        assert selection.servers == [literal("https://a.org")]
        assert ui.questions[0] == "Zabbix server URL"

    def test_non_interactive_with_servers(self) -> None:
        with pytest.raises(InputRequired) as exc_info:
            select_servers([], None, CONFIG, RecordingUI(interactive=False))
        assert "--server NAME or --server URL" in exc_info.value.fix
        assert "[servers.myserver]" not in exc_info.value.fix

    def test_non_interactive_without_servers(self) -> None:
        with pytest.raises(InputRequired) as exc_info:
            select_servers([], None, Config(), RecordingUI(interactive=False))
        assert "[servers.myserver]" in exc_info.value.fix


class TestUnknownName:
    def test_did_you_mean_accepted(self) -> None:
        ui = RecordingUI(answers=[True])
        assert resolve_server("dmeo", CONFIG, ui) == DEMO
        assert ui.questions == ["Unknown server 'dmeo'. Did you mean 'demo'?"]

    def test_did_you_mean_declined(self) -> None:
        ui = RecordingUI(answers=[False, 1])
        assert resolve_server("dmeo", CONFIG, ui) == LAB
        assert ui.levels("warning") == ["Unknown server 'dmeo'."]

    def test_no_close_match_interactive(self) -> None:
        ui = RecordingUI(answers=[0])
        assert resolve_server("zzz", CONFIG, ui) == DEMO
        assert ui.questions == ["Which Zabbix server?"]

    @pytest.mark.parametrize(
        ("ref", "config", "hint"),
        [
            ("dmeo", CONFIG, "Did you mean 'demo'? Configured servers: demo, lab."),
            ("zzz", CONFIG, "Configured servers: demo, lab."),
            ("zzz", Config(), "No servers are configured."),
        ],
    )
    def test_non_interactive(self, ref: str, config: Config, hint: str) -> None:
        with pytest.raises(ZabgrabError, match=f"Unknown server '{ref}'") as exc_info:
            resolve_server(ref, config, RecordingUI(interactive=False))
        assert exc_info.value.hint is not None
        assert exc_info.value.hint.startswith(hint)


class TestParseUrl:
    @pytest.mark.parametrize(
        ("text", "url"),
        [
            ("zabbix.org", "https://zabbix.org"),
            (" http://zabbix.org:8080/z ", "http://zabbix.org:8080/z"),
        ],
    )
    def test_valid(self, text: str, url: str) -> None:
        assert parse_url(text) == url

    @pytest.mark.parametrize("text", ["", "ftp://x.org", "https://"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ValueError, match="frontend URL"):
            parse_url(text)


class TestServerName:
    @pytest.mark.parametrize(
        ("url", "existing", "name"),
        [
            ("https://zabbix.demo.org", [], "demo"),
            ("https://monitor.lab.org", [], "monitor"),
            ("https://zabbix", [], "zabbix"),
            ("https://demo.org", ["demo"], "demo-2"),
            ("https://demo.org", ["demo", "demo-2"], "demo-3"),
            ("https://10.0.0.1:8080", [], "server-10"),
            ("https://[::1]", [], "server---1"),
        ],
    )
    def test_suggest_name(self, url: str, existing: list[str], name: str) -> None:
        assert suggest_name(url, existing) == name

    def test_name_is_validated(self) -> None:
        saved = SavedServers()
        answers: list[Any] = [2, "https://new.org", True, "bad name", "demo", "ok"]
        ui = RecordingUI(answers=answers)
        with pytest.raises(ValueError, match="Names must start"):
            select_servers([], None, CONFIG, ui, save=saved)

    @pytest.mark.parametrize(
        ("answer", "message"),
        [("bad name", "Names must start"), ("demo", "already exists")],
    )
    def test_invalid_names(self, answer: str, message: str) -> None:
        ui = RecordingUI(answers=[2, "https://new.org", True, answer])
        with pytest.raises(ValueError, match=message):
            select_servers([], None, CONFIG, ui, save=SavedServers())
