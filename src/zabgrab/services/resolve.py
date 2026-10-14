"""Turning command line options, templates and config into a :class:`Query`."""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping
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

    template: str | None = None
    items: tuple[ItemSpec, ...] = ()
    server: str | None = None
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

    Precedence: the command line, then the template, then ``[defaults]`` (including
    built-in values). Missing times are asked for. Overrides of template values are
    logged.
    """
    answers = Answers()
    template = (
        None
        if cli.template is None
        else find_template(cli.template, config, ui, answers)
    )
    resolver = _Resolver(cli, config, template, ui, defaults_source)

    timezone = resolver.setting("timezone")
    tz = None if timezone == "local" else ZoneInfo(timezone)

    def time_key(value: Any) -> Any:
        try:
            return parse_time(value, tz)
        except ValueError:
            return value

    from_text = resolver.setting("from", same=time_key)
    to_text = resolver.setting("to", same=time_key)
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
    server, server_custom = _server(
        cli, config, template, ui, resolver.sources, defaults_source
    )
    custom = (
        bool(cli.items)
        or template is None
        or bool(data_settings & set(cli.values))
        or server_custom
    )

    return Query(
        requests=_requests(cli, template, ui),
        template=None if template is None else template[0],
        server=server,
        custom=custom,
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
        setter = f"agg from {source}"
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
            answers.template = close[0]
            return close[0], config.templates[close[0]]
        if names:
            ui.warning(f"Unknown template {name!r}.")
            chosen = ui.choose("Which template?", [Option(n, n) for n in names], fix="")
            answers.template = chosen
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
        template: tuple[str, Template] | None,
        ui: UI,
        defaults_source: DefaultsSource,
    ) -> None:
        self.cli = cli
        self.config = config
        self.template = template
        self.ui = ui
        self.defaults_source = defaults_source
        self.sources: dict[str, str] = {}

    def setting(self, key: str, *, same: Callable[[Any], Any] = lambda v: v) -> Any:
        """The value of a setting (``None`` if nothing sets it).

        ``same`` maps values to something that compares equal when they mean the
        same (e.g. parsed times), to tell whether the command line overrides the
        template.
        """
        attribute = "from_" if key == "from" else key
        name, template = self.template or ("", None)
        from_template = None if template is None else getattr(template, attribute)

        if key in self.cli.values:
            value = self.cli.values[key]
            if from_template is not None and _hashable(same(from_template)) != (
                _hashable(same(value))
            ):
                self.ui.info(
                    f"{flag(key)} {_show(value)} overrides {_show(from_template)} "
                    f"from template {name!r}."
                )
            self.sources[key] = f"command line ({flag(key)})"
            return value

        if from_template is not None:
            self.sources[key] = f"template {name!r}"
            return from_template

        value = getattr(self.config.defaults, attribute)
        if value is not None:
            source = self.defaults_source(key)
            self.sources[key] = (
                "built-in"
                if source in (None, "built-in")
                else f"[defaults] in {source}"
            )
        return value


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


def _server(
    cli: CliRequest,
    config: Config,
    template: tuple[str, Template] | None,
    ui: UI,
    sources: dict[str, str],
    defaults_source: DefaultsSource,
) -> tuple[str | Choice[str] | None, bool]:
    """The server to fetch from: --server, else the template's, else [defaults]'.

    Returns it and whether --server changed the template's server (choosing one of
    the template's server choices doesn't).
    """
    configured = None if template is None else template[1].server
    if cli.server is None:
        if configured is None:
            if config.defaults.server is not None:
                sources["server"] = f"[defaults] in {defaults_source('server')}"
            return config.defaults.server, False
        assert template is not None
        sources["server"] = f"template {template[0]!r}"
        return configured, False
    sources["server"] = f"command line ({flag('server')})"
    if configured is None:
        return cli.server, False
    if isinstance(configured, Choice) and cli.server in configured.choices:
        return cli.server, False
    if configured != cli.server:
        assert template is not None
        ui.info(
            f"--server {cli.server} overrides the server of template {template[0]!r}."
        )
        return cli.server, True
    return cli.server, False


def _requests(
    cli: CliRequest, template: tuple[str, Template] | None, ui: UI
) -> tuple[Request, ...]:
    """The template's items, plus a request for any items on the command line."""
    requests = []
    if template is not None:
        name, settings = template
        if not settings.items:
            ui.warning(f"Template {name!r} has no items.")
        requests.append(Request(name, tuple(settings.items)))
    if cli.items or template is None:
        requests.append(Request(CUSTOM, tuple(cli.items)))
    return tuple(requests)
