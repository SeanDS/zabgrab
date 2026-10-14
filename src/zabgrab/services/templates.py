"""Turning a fetch into a template (``--save-template``) or an equivalent command."""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..errors import ZabgrabError
from ..model.answers import Answers, config_value
from ..model.config import Config, Resolution, Template
from ..model.item import ItemSpec
from ..model.itemspec import format_item_spec
from ..model.query import CUSTOM, Query
from .resolve import SETTINGS, CliRequest, flag


def template_table(cli: CliRequest, query: Query, config: Config) -> dict[str, Any]:
    """The template that repeats this fetch, as a config table.

    It's self-contained, since templates can't build on each other: the templates
    used contribute their items and settings, overridden by the command line and by
    answers given at prompts. ``[defaults]`` and built-in values are left out, so
    they still apply when the template is used.

    Raises
    ------
    ZabgrabError
        If the fetch used different servers for different templates, which one
        template can't express.
    """
    templates = [config.templates[name] for name in query.templates]
    table: dict[str, Any] = {}

    server = _server(query, config)
    if server is not None:
        table["server"] = server
    items = _items(query)
    if items:
        table["items"] = [
            spec.model_dump(mode="json", exclude_none=True) for spec in items
        ]

    for key in SETTINGS:
        value = _setting(key, cli, query.answers, templates)
        if value is not None:
            table[key] = config_value(value)
    if query.resolution is Resolution.RAW:
        table.pop("agg", None)  # only applies to hourly trends

    try:
        Template.model_validate(table)
    except ValidationError as exc:  # pragma: no cover - built from valid values
        raise ZabgrabError(f"Can't save the template: {exc}") from exc
    return table


def _setting(
    key: str, cli: CliRequest, answers: Answers, templates: Sequence[Template]
) -> Any:
    if key in cli.values:
        return cli.values[key]
    if key in answers.settings:
        value = answers.settings[key]
        if key == "format" and isinstance(value, list):
            # Chosen at a prompt: a template takes one format, or all of several.
            return value[0] if len(value) == 1 else {"choices": value, "select": "all"}
        return value
    attribute = "from_" if key == "from" else key
    for template in templates:
        value = getattr(template, attribute)
        if value is not None:
            return value
    return None


def _server(query: Query, config: Config) -> Any:
    """The server all requests used, or ``None`` if it's [defaults]' server."""
    servers: dict[str, Any] = {}
    for request in query.requests:
        answered = query.answers.servers.get(request.label)
        if answered is not None:
            server: Any = (
                answered[0]
                if len(answered) == 1
                else {"choices": list(answered), "select": "all"}
            )
        elif request.server is None:
            server = None
        else:
            server = config_value(request.server)
        servers[request.label] = server

    effective = {
        label: config_value(config.defaults.server) if server is None else server
        for label, server in servers.items()
    }
    if len({json.dumps(server) for server in effective.values()}) > 1:
        used = "; ".join(
            f"{_describe(label)}: {_show(server)}"
            for label, server in effective.items()
        )
        raise ZabgrabError(
            "Can't save one template for different servers.",
            details=[used],
            hint="Pass --server to fetch them all from the same server(s), or save "
            "a template for each.",
        )
    if all(server is None for server in servers.values()):
        return None
    return next(iter(effective.values()))


def _items(query: Query) -> list[ItemSpec]:
    specs: list[ItemSpec] = []
    for request in query.requests:
        for spec in query.answers.items.get(request.label, request.items):
            if spec not in specs:
                specs.append(spec)
    return specs


def _describe(label: str) -> str:
    return "the command line" if label == CUSTOM else f"template {label!r}"


def _show(server: Any) -> str:
    if server is None:
        return "none"
    if isinstance(server, dict):
        return " or ".join(server["choices"]) + f" ({server['select']})"
    return str(server)


@dataclass
class Equivalent:
    """The command that repeats a fetch without its prompts."""

    args: list[str]
    notes: list[str] = field(default_factory=list)
    """Answers the command can't include."""

    @property
    def command(self) -> str:
        return shlex.join(["zabgrab", *self.args])


#: Command line option names for each value-taking option that answers can set.
_OPTIONS = {
    "template": ("-t", "--template"),
    "item": ("-i", "--item"),
    "server": ("-s", "--server"),
} | {key: (flag(key),) for key in SETTINGS}


def equivalent_command(args: Sequence[str], answers: Answers) -> Equivalent:
    """The command line ``args`` (after the program name) with the answers given at
    prompts added, so it can be run again without them."""
    result = Equivalent(_rename_templates(list(args), answers.templates))

    for key, value in answers.settings.items():
        values = value if isinstance(value, list) else [value]
        if key == "format" and len(values) > 1:
            result.notes.append(
                f"The formats chosen ({', '.join(values)}) can't be given on the "
                "command line; set format in a template or in [defaults]."
            )
            continue
        result.args = _replace(result.args, _OPTIONS[key], [str(v) for v in values])

    servers = set(answers.servers.values())
    if len(servers) == 1:
        (chosen,) = servers
        result.args = _replace(result.args, _OPTIONS["server"], list(chosen))
    elif servers:
        result.notes.append(
            "Different servers were chosen for different templates, which --server "
            "can't express."
        )

    for label, specs in answers.items.items():
        if label == CUSTOM:
            result.args = _replace(
                result.args, _OPTIONS["item"], [format_item_spec(s) for s in specs]
            )
        else:
            result.notes.append(
                f"The items chosen from template {label!r} can't be given on the "
                "command line."
            )

    if answers.all_items and not {"-y", "--yes"} & set(result.args):
        result.args.append("--yes")
    return result


def _rename_templates(args: list[str], renamed: dict[str, str]) -> list[str]:
    if not renamed:
        return args
    result = []
    expecting = False
    for arg in args:
        if expecting:
            arg = renamed.get(arg, arg)
            expecting = False
        elif arg in _OPTIONS["template"]:
            expecting = True
        elif arg.startswith("--template="):
            name = arg.removeprefix("--template=")
            arg = f"--template={renamed.get(name, name)}"
        elif arg.startswith("-t") and len(arg) > 2:
            arg = f"-t{renamed.get(arg[2:], arg[2:])}"
        result.append(arg)
    return result


def _replace(args: list[str], names: Sequence[str], values: list[str]) -> list[str]:
    """``args`` without the options ``names``, then with one per value."""
    result = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in names:
            skip = True
            continue
        if any(
            arg.startswith(f"{name}=")
            if name.startswith("--")
            else arg.startswith(name)
            for name in names
        ):
            continue
        result.append(arg)
    for value in values:
        result += [names[-1], value]
    return result
