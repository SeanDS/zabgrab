"""Turning command line options, templates and config into a :class:`Query`."""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from ..errors import ZabgrabError
from ..model.answers import Answers, config_value
from ..model.choice import Choice, Select
from ..model.config import Agg, Config, Format, Resolution, Template
from ..model.item import ItemSpec
from ..model.query import CUSTOM, Query, Request
from .ports import UI, Option

TimeParser = Callable[[str, tzinfo | None], datetime]
"""Parses a user-supplied time in a timezone (``None`` for local) to aware UTC."""

DefaultsSource = Callable[[str], str | None]
"""Where a ``[defaults]`` key's value came from (a file path or ``built-in``)."""

#: Settings resolved from the command line, templates and [defaults], in the order
#: they're resolved (the timezone is needed to compare and parse times).
SETTINGS = (
    "timezone",
    "from",
    "to",
    "resolution",
    "agg",
    "format",
    "output_dir",
    "output_pattern",
    "jobs",
    "max_items",
    "mat_text_warn_entries",
)

#: Settings that change which data is fetched (used to mark a query as custom).
DATA_SETTINGS = frozenset({"from", "to", "resolution", "agg"})


@dataclass(frozen=True)
class CliRequest:
    """What was given on the command line."""

    templates: tuple[str, ...] = ()
    items: tuple[ItemSpec, ...] = ()
    servers: tuple[str, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)
    """Settings given explicitly, by config key (e.g. ``"from"``)."""


def flag(key: str) -> str:
    """The command line option for a config key."""
    return "--" + key.replace("_", "-")


def resolve_query(
    cli: CliRequest,
    config: Config,
    ui: UI,
    *,
    parse_time: TimeParser,
    defaults_source: DefaultsSource,
) -> Query:
    """Combine the command line, templates and config into a :class:`Query`.

    Precedence: the command line, then the templates (which must agree with each
    other), then ``[defaults]`` (including built-in values). Missing times are asked
    for. Overrides of template values are logged.
    """
    answers = Answers()
    templates = [find_template(name, config, ui, answers) for name in cli.templates]
    resolver = _Resolver(cli, config, templates, ui, defaults_source, answers)

    timezone = resolver.setting("timezone")
    tz = None if timezone == "local" else ZoneInfo(timezone)

    def time_key(value: Any) -> Any:
        try:
            return parse_time(value, tz)
        except ValueError:
            return value

    from_text = resolver.setting("from", compare=time_key)
    to_text = resolver.setting("to", compare=time_key)
    from_text, start = _time("from", from_text, ui, parse_time, tz, answers)
    to_text, end = _time("to", to_text, ui, parse_time, tz, answers)
    if start > end:
        ui.warning(
            f"The start time ({from_text!r}) is after the end time ({to_text!r}); "
            "swapping them."
        )
        start, end, from_text, to_text = end, start, to_text, from_text

    resolution = Resolution(resolver.setting("resolution"))
    agg = tuple(Agg(agg) for agg in resolver.setting("agg"))
    data_settings = set(DATA_SETTINGS)
    if resolution is Resolution.RAW:
        data_settings.discard("agg")
        _warn_agg_ignored(resolver.sources["agg"], ui)

    formats = resolver.setting("format")
    custom = bool(cli.items) or not templates or bool(data_settings & set(cli.values))
    requests, server_custom = _requests(cli, config, templates, ui)

    return Query(
        requests=requests,
        templates=tuple(name for name, _ in templates),
        custom=custom or server_custom,
        start=start,
        end=end,
        from_text=from_text,
        to_text=to_text,
        resolution=resolution,
        agg=agg,
        formats=_formats(formats, ui, answers),
        output_dir=resolver.setting("output_dir"),
        output_pattern=resolver.setting("output_pattern"),
        timezone=timezone,
        tz=tz,
        jobs=resolver.setting("jobs"),
        max_items=resolver.setting("max_items"),
        mat_text_warn_entries=resolver.setting("mat_text_warn_entries"),
        sources=resolver.sources,
        answers=answers,
    )


def _warn_agg_ignored(source: str, ui: UI) -> None:
    """Warn when a chosen agg won't be used because the data is raw.

    An agg in ``[defaults]`` (or built in) is a preference for hourly trends, so
    it's only a mistake when it was given on the command line or by a template.
    """
    if source.startswith("command line"):
        setter = flag("agg")
    elif source.startswith("template"):
        setter = f"agg from {source.removesuffix(' (chosen at a prompt)')}"
    else:
        return
    ui.warning(
        f"{setter} is ignored: statistics are only available for hourly trends "
        f"({flag('resolution')} 1h), and raw data is saved as it is."
    )


def find_template(
    name: str, config: Config, ui: UI, answers: Answers | None = None
) -> tuple[str, Template]:
    """Look up a template, with "did you mean" for unknown names.

    Returns the name of the template used, which differs from ``name`` if the user
    chose another (recorded in ``answers``).
    """
    if name in config.templates:
        return name, config.templates[name]
    answers = Answers() if answers is None else answers

    names = list(config.templates)
    close = difflib.get_close_matches(name, names, n=1)
    if ui.interactive:
        if close and ui.confirm(
            f"Unknown template {name!r}. Did you mean {close[0]!r}?", fix=""
        ):
            answers.templates[name] = close[0]
            return close[0], config.templates[close[0]]
        if names:
            ui.warning(f"Unknown template {name!r}.")
            chosen = ui.choose("Which template?", [Option(n, n) for n in names], fix="")
            answers.templates[name] = chosen
            return chosen, config.templates[chosen]

    hint = f"Did you mean {close[0]!r}? " if close else ""
    hint += (
        f"Templates: {', '.join(names)}."
        if names
        else "No templates are configured; add some with `zabgrab config edit`."
    )
    raise ZabgrabError(f"Unknown template {name!r}.", hint=hint)


class _Resolver:
    """Resolves settings, recording their sources and logging overrides."""

    def __init__(
        self,
        cli: CliRequest,
        config: Config,
        templates: Sequence[tuple[str, Template]],
        ui: UI,
        defaults_source: DefaultsSource,
        answers: Answers,
    ) -> None:
        self.cli = cli
        self.config = config
        self.templates = templates
        self.ui = ui
        self.defaults_source = defaults_source
        self.answers = answers
        self.sources: dict[str, str] = {}

    def setting(self, key: str, *, compare: Callable[[Any], Any] = lambda v: v) -> Any:
        """The value of a setting (``None`` if nothing sets it)."""
        attribute = "from_" if key == "from" else key
        from_templates = [
            (name, getattr(template, attribute))
            for name, template in self.templates
            if getattr(template, attribute) is not None
        ]

        def same(value: Any) -> Any:
            return _hashable(compare(value))

        if key in self.cli.values:
            value = self.cli.values[key]
            overridden = [(n, v) for n, v in from_templates if same(v) != same(value)]
            if overridden:
                values = ", ".join(
                    f"{_show(v)} from template {n!r}" for n, v in overridden
                )
                self.ui.info(f"{flag(key)} {_show(value)} overrides {values}.")
            self.sources[key] = f"command line ({flag(key)})"
            return value

        if from_templates:
            groups: dict[Any, list[tuple[str, Any]]] = {}
            for name, value in from_templates:
                groups.setdefault(same(value), []).append((name, value))
            if len(groups) > 1:
                return self._conflict(key, list(groups.values()))
            (group,) = groups.values()
            self.sources[key] = _templates_label([name for name, _ in group])
            return group[0][1]

        value = getattr(self.config.defaults, attribute)
        if value is not None:
            source = self.defaults_source(key)
            self.sources[key] = (
                "built-in"
                if source in (None, "built-in")
                else f"[defaults] in {source}"
            )
        return value

    def _conflict(self, key: str, groups: list[list[tuple[str, Any]]]) -> Any:
        described = "; ".join(
            f"{_templates_label([n for n, _ in group])} sets {_show(group[0][1])}"
            for group in groups
        )
        self.ui.warning(f"The templates set different values for {key}: {described}.")
        chosen = self.ui.choose(
            f"Which {key} should be used?",
            [
                Option(
                    _show(group[0][1]), group, _templates_label([n for n, _ in group])
                )
                for group in groups
            ],
            fix=f"Pass {flag(key)} to choose.",
        )
        self.sources[key] = (
            f"{_templates_label([n for n, _ in chosen])} (chosen at a prompt)"
        )
        self.answers.settings[key] = config_value(chosen[0][1])
        return chosen[0][1]


def _templates_label(names: Sequence[str]) -> str:
    quoted = ", ".join(repr(name) for name in names)
    return f"template {quoted}" if len(names) == 1 else f"templates {quoted}"


def _hashable(value: Any) -> Any:
    if isinstance(value, Choice):
        return ("choice", tuple(value.choices), value.select)
    if isinstance(value, list):
        return tuple(value)
    return value


def _show(value: Any) -> str:
    if isinstance(value, Choice):
        return f"{' or '.join(map(str, value.choices))} ({value.select})"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if isinstance(value, Enum):
        return str(value.value)
    return repr(value) if isinstance(value, str) else str(value)


def _time(
    key: str,
    text: str | None,
    ui: UI,
    parse_time: TimeParser,
    tz: tzinfo | None,
    answers: Answers,
) -> tuple[str, datetime]:
    if text is not None:
        try:
            return text, parse_time(text, tz)
        except ValueError as exc:
            if not ui.interactive:
                raise ZabgrabError(
                    f"Invalid {flag(key)} time {text!r}.", hint=str(exc)
                ) from exc
            ui.error(str(exc))

    def parse(answer: str) -> tuple[str, datetime]:
        return answer.strip(), parse_time(answer, tz)

    answered = ui.prompt(
        f"{'Start' if key == 'from' else 'End'} time ({key})",
        fix=f"Pass {flag(key)}, or set {key} in the template or in [defaults], "
        "e.g. 'now' or '24 hours ago'.",
        parse=parse,
    )
    answers.settings[key] = answered[0]
    return answered


def _formats(
    value: Format | Choice[Format] | None, ui: UI, answers: Answers
) -> tuple[Format, ...]:
    if value is None:
        return ()
    if not isinstance(value, Choice):
        return (Format(value),)
    if value.select is Select.ALL:
        return tuple(value.choices)
    if value.select is Select.FIRST:
        return (value.choices[0],)
    chosen = tuple(
        ui.choose_many(
            "Which output formats?",
            [Option(str(f), f) for f in value.choices],
            fix="Pass --format.",
        )
    )
    answers.settings["format"] = config_value(chosen)
    return chosen


def _requests(
    cli: CliRequest,
    config: Config,
    templates: Sequence[tuple[str, Template]],
    ui: UI,
) -> tuple[tuple[Request, ...], bool]:
    """One request per template, plus one for command line items.

    Returns the requests and whether --server changed a template's server.
    """
    cli_server: str | Choice[str] | None = None
    if len(cli.servers) == 1:
        cli_server = cli.servers[0]
    elif cli.servers:
        cli_server = Choice[str](choices=list(cli.servers), select=Select.ALL)

    requests = []
    changed = False
    for name, template in templates:
        server = (
            template.server if template.server is not None else config.defaults.server
        )
        if cli_server is not None:
            within_choice = isinstance(template.server, Choice) and set(
                cli.servers
            ) <= set(template.server.choices)
            if template.server is not None and not within_choice:
                ui.info(
                    f"--server {', '.join(cli.servers)} overrides the server of "
                    f"template {name!r}."
                )
                changed = True
            server = cli_server
        if not template.items:
            ui.warning(f"Template {name!r} has no items.")
        requests.append(Request(name, server, tuple(template.items)))

    if cli.items or not templates:
        server = cli_server if cli_server is not None else config.defaults.server
        requests.append(Request(CUSTOM, server, tuple(cli.items)))

    return tuple(requests), changed
