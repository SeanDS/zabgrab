"""Resolving the command line, templates and config into a query."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any

import pytest

from zabgrab.adapters.dates import parse_time
from zabgrab.errors import InputRequired, ZabgrabError
from zabgrab.model.choice import Choice, Select
from zabgrab.model.config import BUILTIN_DEFAULTS, Agg, Config, Format, Resolution
from zabgrab.model.item import ItemSpec
from zabgrab.model.query import CUSTOM, Query, Request
from zabgrab.services.resolve import CliRequest, flag, resolve_query

from .fakes import RecordingUI

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def parser(text: str, tz: tzinfo | None) -> datetime:
    return parse_time(text, now=NOW, tz=tz)


def config(**data: Any) -> Config:
    data.setdefault("defaults", {})
    data["defaults"] = {**BUILTIN_DEFAULTS, **data["defaults"]}
    return Config.model_validate(data)


def defaults_source(key: str) -> str:
    return "/etc/zabgrab/config.toml" if key == "jobs" else "built-in"


TEMPLATES = {
    "particles": {
        "server": "demo",
        "items": [{"host": "demo1", "name": "Particles *"}],
        "from": "24 hours ago",
        "to": "now",
        "resolution": "1h",
    },
    "temps": {
        "server": "lab",
        "items": [{"key": "temp[*]"}],
        "from": "1 day ago",
    },
    "short": {"items": [{"itemid": 5}], "from": "1 hour ago"},
    "choice": {
        "server": {"choices": ["demo", "lab"]},
        "items": [{"itemid": 6}],
        "format": {"choices": ["csv", "tsv"], "select": "all"},
    },
    "empty": {},
}
CONFIG = config(templates=TEMPLATES, defaults={"server": "demo", "timezone": "UTC"})


def resolve(
    *templates: str,
    ui: RecordingUI | None = None,
    cfg: Config = CONFIG,
    items: tuple[ItemSpec, ...] = (),
    servers: tuple[str, ...] = (),
    **values: Any,
) -> Query:
    cli = CliRequest(
        templates=templates,
        items=items,
        servers=servers,
        values={key.replace("from_", "from"): value for key, value in values.items()},
    )
    return resolve_query(
        cli,
        cfg,
        ui or RecordingUI(),
        parse_time=parser,
        defaults_source=defaults_source,
    )


def test_flag() -> None:
    assert flag("from") == "--from"
    assert flag("output_dir") == "--output-dir"


class TestSettings:
    def test_from_template(self) -> None:
        query = resolve("particles")
        assert query.start == datetime(2026, 10, 13, 12, tzinfo=UTC)
        assert query.end == NOW
        assert (query.from_text, query.to_text) == ("24 hours ago", "now")
        assert query.resolution is Resolution.HOURLY
        assert query.sources["from"] == "template 'particles'"
        assert query.sources["agg"] == "built-in"
        assert query.sources["jobs"] == "[defaults] in /etc/zabgrab/config.toml"
        assert query.sources["timezone"] == "built-in"

    def test_cli_wins_and_logs_override(self) -> None:
        ui = RecordingUI()
        query = resolve("particles", ui=ui, from_="2 hours ago", jobs=3)
        assert query.from_text == "2 hours ago"
        assert query.jobs == 3
        assert query.sources["from"] == "command line (--from)"
        assert ui.levels("info") == [
            "--from '2 hours ago' overrides '24 hours ago' from template 'particles'."
        ]

    def test_cli_equal_to_template_is_not_an_override(self) -> None:
        ui = RecordingUI()
        resolve("particles", ui=ui, from_="1 day ago")
        assert ui.levels("info") == []

    def test_defaults(self) -> None:
        query = resolve(items=(ItemSpec(itemid=1),), from_="now", to="now")
        assert query.agg == (Agg.MIN, Agg.AVG, Agg.MAX)
        assert query.max_items == BUILTIN_DEFAULTS["max_items"]
        assert query.formats == ()

    def test_unset_setting_has_no_source(self) -> None:
        query = resolve(items=(ItemSpec(itemid=1),), from_="now", to="now")
        assert "format" not in query.sources

    def test_named_timezone(self) -> None:
        cfg = config(defaults={"timezone": "Europe/London"})
        query = resolve(
            cfg=cfg, items=(ItemSpec(itemid=1),), from_="2026-10-01 09:00", to="now"
        )
        assert query.start == datetime(2026, 10, 1, 8, tzinfo=UTC)
        assert str(query.tz) == "Europe/London"

    def test_local_timezone(self) -> None:
        cfg = config(defaults={"timezone": "local"})
        query = resolve(cfg=cfg, items=(ItemSpec(itemid=1),), from_="now", to="now")
        assert query.tz is None


class TestStackedTemplates:
    def test_equivalent_times_agree(self) -> None:
        # "24 hours ago" and "1 day ago" are the same time.
        query = resolve("particles", "temps")
        assert query.sources["from"] == "templates 'particles', 'temps'"

    def test_conflict_interactive(self) -> None:
        ui = RecordingUI(answers=[1])
        query = resolve("particles", "short", ui=ui)
        assert query.from_text == "1 hour ago"
        assert ui.levels("warning") == [
            "The templates set different values for from: template 'particles' sets "
            "'24 hours ago'; template 'short' sets '1 hour ago'."
        ]
        assert query.sources["from"] == "template 'short' (chosen at a prompt)"

    def test_conflict_non_interactive(self) -> None:
        with pytest.raises(InputRequired) as exc_info:
            resolve("particles", "short", ui=RecordingUI(interactive=False))
        assert exc_info.value.fix == "Pass --from to choose."

    def test_conflict_resolved_by_cli(self) -> None:
        ui = RecordingUI()
        query = resolve("particles", "short", ui=ui, from_="3 hours ago")
        assert query.from_text == "3 hours ago"
        assert ui.levels("info") == [
            "--from '3 hours ago' overrides '24 hours ago' from template 'particles', "
            "'1 hour ago' from template 'short'."
        ]

    def test_resolution_conflict(self) -> None:
        cfg = config(
            templates={
                "a": {
                    "items": [{"itemid": 1}],
                    "resolution": "raw",
                    "from": "now",
                    "to": "now",
                },
                "b": {"items": [{"itemid": 2}], "resolution": "1h"},
            }
        )
        with pytest.raises(InputRequired, match="Which resolution"):
            resolve("a", "b", cfg=cfg, ui=RecordingUI(interactive=False))


class TestTimes:
    def test_missing_times_are_asked_for(self) -> None:
        ui = RecordingUI(answers=["2 hours ago", "now"])
        query = resolve(items=(ItemSpec(itemid=1),), ui=ui)
        assert ui.questions == ["Start time (from)", "End time (to)"]
        assert (query.from_text, query.to_text) == ("2 hours ago", "now")

    def test_missing_times_non_interactive(self) -> None:
        with pytest.raises(InputRequired) as exc_info:
            resolve(items=(ItemSpec(itemid=1),), ui=RecordingUI(interactive=False))
        assert exc_info.value.fix.startswith("Pass --from, or set from in the template")

    def test_invalid_time_non_interactive(self) -> None:
        with pytest.raises(
            ZabgrabError, match="Invalid --from time 'garbage'"
        ) as exc_info:
            resolve(
                items=(ItemSpec(itemid=1),),
                ui=RecordingUI(interactive=False),
                from_="garbage",
                to="now",
            )
        assert exc_info.value.hint is not None
        assert "Can't understand the time" in exc_info.value.hint

    def test_invalid_time_interactive_is_asked_again(self) -> None:
        ui = RecordingUI(answers=["2 hours ago"])
        query = resolve(items=(ItemSpec(itemid=1),), ui=ui, from_="garbage", to="now")
        assert query.from_text == "2 hours ago"
        assert ui.levels("error")[0].startswith("Can't understand the time 'garbage'")

    def test_invalid_answer_reasks(self) -> None:
        ui = RecordingUI(answers=["garbage"])
        with pytest.raises(ValueError, match="Can't understand"):
            resolve(items=(ItemSpec(itemid=1),), ui=ui, to="now")

    def test_reversed_times_are_swapped(self) -> None:
        ui = RecordingUI()
        query = resolve(
            items=(ItemSpec(itemid=1),), ui=ui, from_="now", to="1 hour ago"
        )
        assert query.start < query.end
        assert (query.from_text, query.to_text) == ("1 hour ago", "now")
        assert "swapping them" in ui.levels("warning")[0]


class TestFormats:
    def test_single(self) -> None:
        query = resolve("particles", format="csv")
        assert query.formats == (Format.CSV,)

    def test_choice_all(self) -> None:
        query = resolve("choice", from_="now", to="now")
        assert query.formats == (Format.CSV, Format.TSV)

    def test_choice_first(self) -> None:
        cfg = config(
            defaults={"format": {"choices": ["tsv", "csv"], "select": "first"}}
        )
        query = resolve(cfg=cfg, items=(ItemSpec(itemid=1),), from_="now", to="now")
        assert query.formats == (Format.TSV,)

    def test_choice_prompt(self) -> None:
        cfg = config(defaults={"format": {"choices": ["tsv", "csv"]}})
        ui = RecordingUI(answers=[[1]])
        query = resolve(
            cfg=cfg, ui=ui, items=(ItemSpec(itemid=1),), from_="now", to="now"
        )
        assert query.formats == (Format.CSV,)


class TestRequests:
    def test_templates_keep_their_servers(self) -> None:
        query = resolve("particles", "temps")
        assert query.requests == (
            Request("particles", "demo", (ItemSpec(host="demo1", name="Particles *"),)),
            Request("temps", "lab", (ItemSpec(key="temp[*]"),)),
        )
        assert not query.custom

    def test_template_without_server_uses_defaults(self) -> None:
        query = resolve("short", to="now")
        assert query.requests[0].server == "demo"

    def test_cli_items_form_a_custom_request(self) -> None:
        query = resolve("particles", items=(ItemSpec(itemid=9),))
        assert query.requests[-1] == Request(CUSTOM, "demo", (ItemSpec(itemid=9),))
        assert query.custom

    def test_no_templates_is_custom(self) -> None:
        query = resolve(items=(ItemSpec(itemid=9),), from_="now", to="now")
        assert query.custom
        assert query.templates == ()

    def test_data_settings_on_cli_make_it_custom(self) -> None:
        assert resolve("particles", resolution="raw").custom
        assert not resolve("particles", format="csv", jobs=2).custom

    def test_cli_server_overrides(self) -> None:
        ui = RecordingUI()
        query = resolve("particles", "short", ui=ui, servers=("lab",), from_="now")
        assert [r.server for r in query.requests] == ["lab", "lab"]
        assert ui.levels("info")[-1] == (
            "--server lab overrides the server of template 'particles'."
        )
        assert query.custom

    def test_several_cli_servers(self) -> None:
        query = resolve("short", servers=("demo", "lab"), to="now")
        assert query.requests[0].server == Choice[str](
            choices=["demo", "lab"], select=Select.ALL
        )

    def test_cli_server_within_template_choice_is_not_custom(self) -> None:
        ui = RecordingUI()
        query = resolve("choice", ui=ui, servers=("lab",), from_="now", to="now")
        assert ui.levels("info") == []
        assert query.requests[0].server == "lab"

    def test_template_without_items_warns(self) -> None:
        ui = RecordingUI()
        resolve("empty", ui=ui, from_="now", to="now")
        assert ui.levels("warning") == ["Template 'empty' has no items."]


class TestUnknownTemplate:
    def test_did_you_mean(self) -> None:
        ui = RecordingUI(answers=[True])
        query = resolve("partcles", ui=ui)
        assert query.requests[0].items == (ItemSpec(host="demo1", name="Particles *"),)

    def test_choose_instead(self) -> None:
        ui = RecordingUI(answers=[False, 2])
        query = resolve("partcles", ui=ui, from_="now", to="now")
        assert query.requests[0].items == (ItemSpec(itemid=5),)

    def test_non_interactive(self) -> None:
        with pytest.raises(
            ZabgrabError, match="Unknown template 'partcles'"
        ) as exc_info:
            resolve("partcles", ui=RecordingUI(interactive=False))
        assert exc_info.value.hint is not None
        assert exc_info.value.hint.startswith("Did you mean 'particles'? Templates:")

    def test_no_templates_configured(self) -> None:
        with pytest.raises(ZabgrabError) as exc_info:
            resolve("x", cfg=config(), ui=RecordingUI())
        assert exc_info.value.hint is not None
        assert "No templates are configured" in exc_info.value.hint


class TestAggWithRawData:
    """agg only applies to hourly trends; choosing it for raw data is warned about."""

    IGNORED = (
        "is ignored: statistics are only available for hourly trends "
        "(--resolution 1h), and raw data is saved as it is."
    )
    CFG = config(
        templates={
            "a": {"items": [{"itemid": 1}], "agg": ["min"], "from": "now"},
            "b": {"items": [{"itemid": 2}], "agg": ["max"]},
        },
        defaults={"to": "now"},
    )

    def test_cli(self) -> None:
        ui = RecordingUI()
        query = resolve("a", cfg=self.CFG, ui=ui, agg=["avg"])
        assert ui.levels("warning") == [f"--agg {self.IGNORED}"]
        assert not query.custom  # the data is the same as the template's

    def test_template(self) -> None:
        ui = RecordingUI()
        resolve("a", cfg=self.CFG, ui=ui)
        assert ui.levels("warning") == [f"agg from template 'a' {self.IGNORED}"]

    def test_template_chosen_at_prompt(self) -> None:
        ui = RecordingUI(answers=[0])
        resolve("a", "b", cfg=self.CFG, ui=ui)
        assert ui.levels("warning")[-1] == f"agg from template 'a' {self.IGNORED}"

    @pytest.mark.parametrize(
        "cfg",
        [
            config(defaults={"agg": ["avg"], "from": "now", "to": "now"}),
            config(defaults={"from": "now", "to": "now"}),
        ],
        ids=["defaults", "built-in"],
    )
    def test_defaults_are_not_warned_about(self, cfg: Config) -> None:
        ui = RecordingUI()
        resolve(cfg=cfg, ui=ui, items=(ItemSpec(itemid=1),))
        assert ui.levels("warning") == []

    def test_hourly_trends(self) -> None:
        ui = RecordingUI()
        query = resolve("a", cfg=self.CFG, ui=ui, agg=["avg"], resolution="1h")
        assert ui.levels("warning") == []
        assert query.custom


class TestValueDisplay:
    CFG = config(
        templates={
            "a": {
                "items": [{"itemid": 1}],
                "agg": ["min", "max"],
                "format": {"choices": ["csv", "tsv"], "select": "all"},
                "from": "garbage",
            },
            "b": {"items": [{"itemid": 2}], "agg": ["avg"], "format": "csv"},
        }
    )

    def test_lists_and_choices_in_messages(self) -> None:
        ui = RecordingUI(answers=[0])
        query = resolve(
            "a",
            "b",
            cfg=self.CFG,
            ui=ui,
            agg=["count"],
            resolution="1h",
            from_="now",
            to="now",
        )
        assert query.agg == (Agg.COUNT,)
        assert query.formats == (Format.CSV, Format.TSV)
        assert ui.levels("info") == [
            "--from 'now' overrides 'garbage' from template 'a'.",
            "--agg count overrides min, max from template 'a', avg from template 'b'.",
        ]
        assert ui.levels("warning") == [
            "The templates set different values for format: template 'a' sets csv or "
            "tsv (all); template 'b' sets csv."
        ]

    def test_unparseable_template_time(self) -> None:
        with pytest.raises(ZabgrabError, match="Invalid --from time 'garbage'"):
            resolve("a", cfg=self.CFG, ui=RecordingUI(interactive=False), to="now")
