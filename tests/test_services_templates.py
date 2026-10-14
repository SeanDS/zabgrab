"""Templates and equivalent commands from fetches (``--save-template``, "Next time")."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from zabgrab.errors import ZabgrabError
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
            },
            "b": {"server": "demo", "items": [{"itemid": 7}], "jobs": 2},
        },
    }
)


def query(
    *requests: Request,
    templates: tuple[str, ...] = (),
    resolution: Resolution = Resolution.RAW,
    answers: Answers | None = None,
) -> Query:
    return Query(
        requests=requests,
        templates=templates,
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
        answers=answers or Answers(),
    )


def table(
    *requests: Request,
    values: dict[str, Any] | None = None,
    templates: tuple[str, ...] = (),
    **kwargs: Any,
) -> dict[str, Any]:
    cli = CliRequest(templates=templates, values=values or {})
    return template_table(cli, query(*requests, templates=templates, **kwargs), CONFIG)


def custom(server: Any = None, *items: ItemSpec) -> Request:
    return Request(CUSTOM, server, items)


def from_template(name: str) -> Request:
    template = CONFIG.templates[name]
    return Request(name, template.server, tuple(template.items))


class TestTemplateTable:
    def test_command_line_only(self) -> None:
        assert table(
            custom("lab", ItemSpec(host="demo1", name="CPU *")),
            values={"from": "24 hours ago", "to": "now", "jobs": 3},
        ) == {
            "server": "lab",
            "items": [{"host": "demo1", "name": "CPU *"}],
            "from": "24 hours ago",
            "to": "now",
            "jobs": 3,
        }

    def test_no_items(self) -> None:
        assert table(custom("lab")) == {"server": "lab"}

    def test_no_default_server(self) -> None:
        cli = CliRequest(templates=("a",))
        config = CONFIG.model_copy(
            update={"defaults": CONFIG.defaults.model_copy(update={"server": None})}
        )
        q = query(
            from_template("a"), custom(None, ItemSpec(itemid=1)), templates=("a",)
        )
        with pytest.raises(ZabgrabError) as exc_info:
            template_table(cli, q, config)
        assert exc_info.value.details == ("template 'a': demo; the command line: none",)

    def test_defaults_server_is_left_out(self) -> None:
        assert table(custom(None, ItemSpec(itemid=1))) == {"items": [{"itemid": 1}]}

    def test_templates_merge_with_command_line_winning(self) -> None:
        result = table(
            from_template("a"),
            from_template("b"),
            custom(None, ItemSpec(itemid=7), ItemSpec(itemid=8)),
            templates=("a", "b"),
            values={"jobs": 4},
        )
        assert result == {
            "server": "demo",
            "items": [
                {"host": "demo1", "name": "Particles *"},
                {"itemid": 7},
                {"itemid": 8},
            ],
            "from": "2 hours ago",
            "format": {"choices": ["csv", "tsv"], "select": "all"},
            "jobs": 4,
        }

    def test_answers(self) -> None:
        answers = Answers(
            settings={"to": "now", "format": ["mat"]},
            servers={CUSTOM: ("lab",)},
            items={CUSTOM: (ItemSpec(name="uptime"),)},
        )
        assert table(custom(None), answers=answers) == {
            "server": "lab",
            "items": [{"name": "uptime"}],
            "to": "now",
            "format": "mat",
        }

    def test_several_formats_answered(self) -> None:
        answers = Answers(settings={"format": ["csv", "tsv"]})
        assert table(custom(None, ItemSpec(itemid=1)), answers=answers)["format"] == {
            "choices": ["csv", "tsv"],
            "select": "all",
        }

    def test_several_servers_answered(self) -> None:
        answers = Answers(servers={CUSTOM: ("demo", "https://x.example.org")})
        assert table(custom(None, ItemSpec(itemid=1)), answers=answers)["server"] == {
            "choices": ["demo", "https://x.example.org"],
            "select": "all",
        }

    def test_server_choice_and_host_choice(self) -> None:
        spec = ItemSpec(host=Choice(choices=["h1", "h2"], select=Select.ALL))
        server = Choice(choices=["demo", "lab"], select=Select.FIRST)
        assert table(custom(server, spec)) == {
            "server": {"choices": ["demo", "lab"], "select": "first"},
            "items": [{"host": {"choices": ["h1", "h2"], "select": "all"}}],
        }

    @pytest.mark.parametrize(
        ("resolution", "saved"), [(Resolution.RAW, False), (Resolution.HOURLY, True)]
    )
    def test_agg_only_with_hourly_trends(
        self, resolution: Resolution, saved: bool
    ) -> None:
        result = table(
            custom(None, ItemSpec(itemid=1)),
            values={"agg": ["avg"]},
            resolution=resolution,
        )
        assert ("agg" in result) is saved

    def test_different_servers(self) -> None:
        with pytest.raises(ZabgrabError, match="different servers") as exc_info:
            table(from_template("a"), custom("lab", ItemSpec(itemid=1)))
        assert exc_info.value.details == ("template 'a': demo; the command line: lab",)

    def test_default_server_matching_a_templates(self) -> None:
        result = table(from_template("b"), custom(None, ItemSpec(itemid=1)))
        assert result["server"] == "demo"

    def test_different_server_choices(self) -> None:
        choice = Choice(choices=["demo", "lab"], select=Select.FIRST)
        with pytest.raises(ZabgrabError) as exc_info:
            table(from_template("a"), custom(choice, ItemSpec(itemid=1)))
        assert exc_info.value.details == (
            "template 'a': demo; the command line: demo or lab (first)",
        )


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
        assert self.command([*given, "-i", "1"], servers={CUSTOM: ("demo",)}) == (
            "zabgrab fetch -i 1 --server demo"
        )

    def test_several_servers(self) -> None:
        assert self.command([], servers={CUSTOM: ("demo", "lab")}) == (
            "zabgrab fetch --server demo --server lab"
        )

    def test_different_servers_are_noted(self) -> None:
        result = equivalent_command(
            ["fetch"], Answers(servers={"a": ("demo",), "b": ("lab",)})
        )
        assert result.command == "zabgrab fetch"
        assert result.notes == [
            "Different servers were chosen for different templates, which --server "
            "can't express."
        ]

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

    def test_renamed_templates(self) -> None:
        assert (
            self.command(
                ["-t", "x", "--template", "b", "--template=x", "-tx", "-o", "x"],
                templates={"x": "a"},
            )
            == "zabgrab fetch -t a --template b --template=a -ta -o x"
        )
