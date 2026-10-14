"""Templates and equivalent commands from fetches (``--save-template``, "Next time")."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from zabgrab.model.answers import Answers, config_value
from zabgrab.model.choice import Choice, Select
from zabgrab.model.config import BUILTIN_DEFAULTS, Agg, Config, Format, Resolution
from zabgrab.model.item import ItemSpec
from zabgrab.model.query import CUSTOM, Query, Request
from zabgrab.services.resolve import CliRequest
from zabgrab.services.templates import equivalent_command, template_table

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)

CONFIG = Config.model_validate(
    {
        "defaults": {**BUILTIN_DEFAULTS, "server": "demo"},
        "templates": {
            "a": {
                "server": "demo",
                "items": [{"host": "demo1", "name": "Particles *"}],
                "from": "2 hours ago",
                "format": {"choices": ["csv", "tsv"], "select": "all"},
                "jobs": 2,
            },
        },
    }
)


def table(
    *requests: Request,
    template: str | None = None,
    server: Any = None,
    sources: dict[str, str] | None = None,
    values: dict[str, Any] | None = None,
    resolution: Resolution = Resolution.RAW,
    answers: Answers | None = None,
) -> dict[str, Any]:
    query = Query(
        requests=requests,
        template=template,
        server=server,
        custom=True,
        start=NOW,
        end=NOW,
        from_text="now",
        to_text="now",
        resolution=resolution,
        agg=(Agg.AVG,),
        formats=(),
        output_dir=".",
        output_pattern="x",
        timezone="local",
        tz=None,
        jobs=6,
        max_items=50,
        mat_text_warn_entries=1,
        sources=sources or {},
        answers=answers or Answers(),
    )
    cli = CliRequest(template=template, values=values or {})
    return template_table(cli, query, CONFIG)


def custom(*items: ItemSpec) -> Request:
    return Request(CUSTOM, items)


def from_template(name: str) -> Request:
    return Request(name, tuple(CONFIG.templates[name].items))


class TestTemplateTable:
    def test_command_line_only(self) -> None:
        assert table(
            custom(ItemSpec(host="demo1", name="CPU *")),
            server="lab",
            values={"from": "24 hours ago", "to": "now", "jobs": 3},
        ) == {
            "server": "lab",
            "items": [{"host": "demo1", "name": "CPU *"}],
            "from": "24 hours ago",
            "to": "now",
            "jobs": 3,
        }

    def test_no_items(self) -> None:
        assert table(server="lab") == {"server": "lab"}

    def test_defaults_server_is_left_out(self) -> None:
        sources = {"server": "[defaults] in built-in"}
        result = table(custom(ItemSpec(itemid=1)), server="demo", sources=sources)
        assert result == {"items": [{"itemid": 1}]}

    def test_template_with_command_line_winning(self) -> None:
        result = table(
            from_template("a"),
            custom(ItemSpec(itemid=8)),
            template="a",
            server="demo",
            values={"jobs": 4},
        )
        assert result == {
            "server": "demo",
            "items": [{"host": "demo1", "name": "Particles *"}, {"itemid": 8}],
            "from": "2 hours ago",
            "format": {"choices": ["csv", "tsv"], "select": "all"},
            "jobs": 4,
        }

    def test_duplicate_items_are_saved_once(self) -> None:
        spec = ItemSpec(itemid=8)
        assert table(custom(spec), Request("x", (spec,)))["items"] == [{"itemid": 8}]

    def test_answers(self) -> None:
        answers = Answers(
            settings={"to": "now", "format": ["mat"]},
            server="lab",
            items={CUSTOM: (ItemSpec(name="uptime"),)},
        )
        assert table(custom(), answers=answers) == {
            "server": "lab",
            "items": [{"name": "uptime"}],
            "to": "now",
            "format": "mat",
        }

    def test_several_formats_answered(self) -> None:
        answers = Answers(settings={"format": ["csv", "tsv"]})
        assert table(custom(ItemSpec(itemid=1)), answers=answers)["format"] == {
            "choices": ["csv", "tsv"],
            "select": "all",
        }

    def test_server_choice_and_host_choice(self) -> None:
        spec = ItemSpec(host=Choice(choices=["h1", "h2"], select=Select.ALL))
        assert table(custom(spec), server=Choice(choices=["demo", "lab"])) == {
            "server": {"choices": ["demo", "lab"], "select": "prompt"},
            "items": [{"host": {"choices": ["h1", "h2"], "select": "all"}}],
        }

    @pytest.mark.parametrize(
        ("resolution", "saved"), [(Resolution.RAW, False), (Resolution.HOURLY, True)]
    )
    def test_agg_only_with_hourly_trends(
        self, resolution: Resolution, saved: bool
    ) -> None:
        result = table(
            custom(ItemSpec(itemid=1)),
            values={"agg": ["avg"]},
            resolution=resolution,
        )
        assert ("agg" in result) is saved


def test_config_value() -> None:
    assert config_value((Agg.MIN, Agg.MAX)) == ["min", "max"]
    assert config_value(Format.MAT) == "mat"
    assert config_value(Choice(choices=[Format.CSV])) == {
        "choices": ["csv"],
        "select": "prompt",
    }
    assert config_value(3) == 3


class TestEquivalentCommand:
    def command(self, args: list[str], **answers: Any) -> str:
        return equivalent_command(["fetch", *args], Answers(**answers)).command

    def test_no_answers(self) -> None:
        assert self.command(["-t", "a"]) == "zabgrab fetch -t a"

    def test_settings_are_added_or_replace_the_given_ones(self) -> None:
        assert (
            self.command(
                ["--from", "bad", "--to=bad", "-o", "x"],
                settings={"from": "2 hours ago", "to": "now"},
            )
            == "zabgrab fetch -o x --from '2 hours ago' --to now"
        )

    def test_lists_repeat_the_option(self) -> None:
        assert self.command(
            [], settings={"agg": ["min", "max"], "format": ["csv"]}
        ) == ("zabgrab fetch --agg min --agg max --format csv")

    def test_several_formats_are_noted(self) -> None:
        result = equivalent_command(
            ["fetch"], Answers(settings={"format": ["csv", "tsv"]})
        )
        assert result.command == "zabgrab fetch"
        assert result.notes == [
            "The formats chosen (csv, tsv) can't be given on the command line; set "
            "format in a template or in [defaults]."
        ]

    @pytest.mark.parametrize("given", [["-s", "dmeo"], ["--server=dmeo"], ["-sdmeo"]])
    def test_server_replaces_the_given_one(self, given: list[str]) -> None:
        assert self.command([*given, "-i", "1"], server="demo") == (
            "zabgrab fetch -i 1 --server demo"
        )

    def test_items(self) -> None:
        specs = (ItemSpec(itemid=1), ItemSpec(host="h", name="CPU *"))
        assert self.command(["-i", "host=h"], items={CUSTOM: specs}) == (
            "zabgrab fetch --item 1 --item 'host=h,name=CPU *'"
        )

    def test_template_items_are_noted(self) -> None:
        result = equivalent_command(
            ["fetch", "-t", "a"], Answers(items={"a": (ItemSpec(itemid=1),)})
        )
        assert result.command == "zabgrab fetch -t a"
        assert result.notes == [
            "The items chosen from template 'a' can't be given on the command line."
        ]

    def test_all_items(self) -> None:
        assert self.command(["-t", "a"], all_items=True) == "zabgrab fetch -t a --yes"
        assert self.command(["-y"], all_items=True) == "zabgrab fetch -y"

    @pytest.mark.parametrize("given", [["-t", "x"], ["--template=x"], ["-tx"]])
    def test_template_replaces_the_given_one(self, given: list[str]) -> None:
        assert self.command([*given, "-o", "x"], template="a") == (
            "zabgrab fetch -o x --template a"
        )
