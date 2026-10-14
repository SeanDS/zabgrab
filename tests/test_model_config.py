"""Config schema validation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from zabgrab.adapters.configfiles import format_validation_errors
from zabgrab.model.choice import Choice, Select
from zabgrab.model.config import Agg, Config, Format
from zabgrab.model.item import ItemSpec

FULL_CONFIG: dict[str, Any] = {
    "defaults": {"timezone": "UTC", "format": "h5", "jobs": 4},
    "servers": {
        "demo": {
            "url": "https://zabbix.demo.example.org",
            "auth": "token",
            "token_env": "ZABGRAB_DEMO_TOKEN",
            "verify_ssl": False,
            "timeout": 10,
        },
        "lab-2": {"url": "http://zabbix.lab2.example.org:8080/zabbix"},
    },
    "templates": {
        "particles": {
            "description": "Particle counters",
            "server": {"choices": ["demo", "lab-2"]},
            "items": [
                {"host": "demo1", "name": "Particles *"},
                {"key": "particle.count[*]"},
                {"itemid": 12345},
                {"host": {"choices": ["a", "b"]}, "tag": "area:lab"},
            ],
            "from": "24 hours ago",
            "to": "now",
            "resolution": "1h",
            "agg": ["avg", "max"],
            "format": {"choices": ["mat", "tsv"], "select": "all"},
        },
        "direct": {"server": "https://other.example.org", "items": [{"itemid": 1}]},
    },
}


def errors(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as exc_info:
        Config.model_validate(data)
    return format_validation_errors(exc_info.value)


def test_full_config_is_valid() -> None:
    config = Config.model_validate(FULL_CONFIG)

    template = config.templates["particles"]
    assert template.server == Choice[str](choices=["demo", "lab-2"])
    assert template.from_ == "24 hours ago"
    assert template.agg == [Agg.AVG, Agg.MAX]
    assert isinstance(template.format, Choice)
    assert template.format.choices == [Format.MAT, Format.TSV]
    assert template.items[2] == ItemSpec(itemid=12345)
    assert template.items[3].host == Choice[str](choices=["a", "b"])
    assert config.templates["direct"].server == "https://other.example.org"
    assert config.servers["demo"].verify_ssl is False


def test_empty_config_is_valid() -> None:
    config = Config.model_validate({})
    assert config.templates == {}
    assert config.defaults.from_ is None


@pytest.mark.parametrize("select", ["all", "first"])
@pytest.mark.parametrize("table", ["defaults", "template"])
def test_server_choices_are_only_asked(select: str, table: str) -> None:
    server = {"choices": ["a", "b"], "select": select}
    data = (
        {"defaults": {"server": server}}
        if table == "defaults"
        else {"templates": {"t": {"server": server}}}
    )
    where = "defaults.server" if table == "defaults" else "templates.t.server"
    assert errors(data) == [
        f"{where}: a choice of servers is only for choosing one when asked (zabgrab "
        "fetches from one server at a time), so its select must be 'prompt'"
    ]


def test_choice_defaults_to_prompt() -> None:
    config = Config.model_validate({"defaults": {"server": {"choices": ["a"]}}})
    assert config.defaults.server == Choice[str](choices=["a"], select=Select.PROMPT)


@pytest.mark.parametrize(
    "name", ["_private", "__custom", "1st", "has space", "a.b", ""]
)
def test_invalid_template_names(name: str) -> None:
    (message,) = errors({"templates": {name: {}}})
    assert message.startswith(f"templates: invalid name {name!r}")
    assert "reserved" in message


@pytest.mark.parametrize("name", ["a", "Particles", "lab-2", "x_y_9"])
def test_valid_names(name: str) -> None:
    Config.model_validate(
        {"servers": {name: {"url": "https://x"}}, "templates": {name: {}}}
    )


def test_unknown_key_suggests_close_match() -> None:
    assert errors({"templates": {"t": {"items": [{"nmae": "x"}]}}}) == [
        "templates.t.items[0]: unknown key 'nmae' (did you mean 'name'?)"
    ]


def test_unknown_top_level_key() -> None:
    assert errors({"template": {}}) == [
        "unknown key 'template' (did you mean 'templates'?)"
    ]


def test_missing_server_url() -> None:
    assert errors({"servers": {"s": {}}}) == ["servers.s: missing required key 'url'"]


@pytest.mark.parametrize("url", ["zabbix.example.org", "ftp://x.org", "https://"])
def test_invalid_server_url(url: str) -> None:
    (message,) = errors({"servers": {"s": {"url": url}}})
    assert message.startswith(f"servers.s.url: invalid URL {url!r}")


@pytest.mark.parametrize("server", ["no spaces", "zabbix.example.org", "_x"])
def test_invalid_server_reference(server: str) -> None:
    (message,) = errors({"templates": {"t": {"server": server}}})
    assert message.startswith(f"templates.t.server: invalid server {server!r}")


def test_invalid_server_reference_in_choice() -> None:
    (message,) = errors({"templates": {"t": {"server": {"choices": ["ok", "not ok"]}}}})
    assert message.startswith("templates.t.server.choices[1]: invalid server")


def test_duplicate_choices() -> None:
    (message,) = errors({"defaults": {"server": {"choices": ["a", "a"]}}})
    assert message == "defaults.server: duplicate choice 'a'"


def test_empty_choices() -> None:
    (message,) = errors({"defaults": {"server": {"choices": []}}})
    assert message.startswith("defaults.server.choices:")


def test_invalid_enum_value_reports_input() -> None:
    (message,) = errors({"defaults": {"format": "xls"}})
    assert message.startswith("defaults.format: Input should be")
    assert message.endswith("(got 'xls')")


def test_duplicate_agg() -> None:
    (message,) = errors({"defaults": {"agg": ["avg", "avg"]}})
    assert message == "defaults.agg: duplicate values"


@pytest.mark.parametrize("timezone", ["local", "UTC", "Europe/London"])
def test_valid_timezones(timezone: str) -> None:
    Config.model_validate({"defaults": {"timezone": timezone}})


def test_invalid_timezone() -> None:
    (message,) = errors({"defaults": {"timezone": "Mars/Olympus"}})
    assert message.startswith("defaults.timezone: unknown timezone 'Mars/Olympus'")


@pytest.mark.parametrize("value", ["1BAD", "has-dash", ""])
def test_invalid_token_env(value: str) -> None:
    (message,) = errors({"servers": {"s": {"url": "https://x", "token_env": value}}})
    assert "invalid environment variable name" in message


@pytest.mark.parametrize("jobs", [0, -1])
def test_jobs_must_be_positive(jobs: int) -> None:
    (message,) = errors({"defaults": {"jobs": jobs}})
    assert message.startswith("defaults.jobs:")


class TestItemSpec:
    def test_itemid_alone(self) -> None:
        assert ItemSpec(itemid=5).itemid == 5

    def test_itemid_with_pattern_is_invalid(self) -> None:
        with pytest.raises(ValidationError, match="can't be combined with 'key'"):
            ItemSpec.model_validate({"itemid": 5, "key": "x"})

    def test_empty_spec_is_invalid(self) -> None:
        with pytest.raises(ValidationError, match="needs at least one of"):
            ItemSpec.model_validate({})

    def test_host_only(self) -> None:
        assert ItemSpec.model_validate({"host": "h"}).host == "h"

    def test_empty_pattern_is_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ItemSpec.model_validate({"name": ""})

    def test_itemid_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ItemSpec.model_validate({"itemid": 0})
