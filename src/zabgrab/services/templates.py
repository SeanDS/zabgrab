"""Turning a fetch into a template (``--save-template``) or an equivalent command."""

from __future__ import annotations

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
    """Build the template that repeats a fetch, as a config table.

    It's self-contained, since templates can't build on each other: the template
    used contributes its items and settings, overridden by the command line and by
    answers given at prompts. ``[defaults]`` and built-in values are left out, so
    they still apply when the template is used. Values are kept as given
    (patterns, relative times).

    Parameters
    ----------
    cli : CliRequest
        What was given on the command line.
    query : Query
        The fetch, including the answers given at prompts.
    config : Config
        The config, for the template used.

    Returns
    -------
    dict
        The template's settings, as config values.
    """
    template = None if query.template is None else config.templates[query.template]
    table: dict[str, Any] = {}

    server = _server(query)
    if server is not None:
        table["server"] = server
    items = _items(query)
    if items:
        table["items"] = [
            spec.model_dump(mode="json", exclude_none=True) for spec in items
        ]

    for key in SETTINGS:
        value = _setting(key, cli, query.answers, template)
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
    key: str, cli: CliRequest, answers: Answers, template: Template | None
) -> Any:
    """Get a setting from the command line, else an answer, else the template."""
    if key in cli.values:
        return cli.values[key]
    if key in answers.settings:
        value = answers.settings[key]
        if key == "format" and isinstance(value, list):
            # Chosen at a prompt: a template takes one format, or all of several.
            return value[0] if len(value) == 1 else {"choices": value, "select": "all"}
        return value
    if template is None:
        return None
    return getattr(template, "from_" if key == "from" else key)


def _server(query: Query) -> Any:
    """Get the server chosen, given or from the template; not [defaults]' server."""
    if query.answers.server is not None:
        return query.answers.server
    if query.sources.get("server", "").startswith("[defaults]"):
        return None
    return config_value(query.server)


def _items(query: Query) -> list[ItemSpec]:
    """Get every request's items (or the ones chosen instead), each once."""
    specs: list[ItemSpec] = []
    for request in query.requests:
        for spec in query.answers.items.get(request.label, request.items):
            if spec not in specs:
                specs.append(spec)
    return specs


@dataclass
class Equivalent:
    """The command that repeats a fetch without its prompts.

    Attributes
    ----------
    args : list of str
        The command line arguments (after the program name).
    notes : list of str
        Answers the command can't include, as sentences.
    """

    args: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def command(self) -> str:
        """Get the command, quoted for a shell."""
        return shlex.join(["zabgrab", *self.args])


#: Command line option names for each value-taking option that answers can set.
_OPTIONS = {
    "template": ("-t", "--template"),
    "item": ("-i", "--item"),
    "server": ("-s", "--server"),
} | {key: (flag(key),) for key in SETTINGS}


def equivalent_command(args: Sequence[str], answers: Answers) -> Equivalent:
    """Build the command that repeats a fetch without its prompts.

    Answers become options, replacing any given for the same setting.

    Parameters
    ----------
    args : sequence of str
        The command line as given (after the program name).
    answers : Answers
        The answers given at prompts.

    Returns
    -------
    Equivalent
        The command, and notes on answers it can't include (items chosen from a
        template, several formats).
    """
    result = Equivalent(list(args))
    if answers.template is not None:
        result.args = _replace(result.args, _OPTIONS["template"], [answers.template])

    for key, value in answers.settings.items():
        values = value if isinstance(value, list) else [value]
        if key == "format" and len(values) > 1:
            result.notes.append(
                f"The formats chosen ({', '.join(values)}) can't be given on the "
                "command line; set format in a template or in [defaults]."
            )
            continue
        result.args = _replace(result.args, _OPTIONS[key], [str(v) for v in values])

    if answers.server is not None:
        result.args = _replace(result.args, _OPTIONS["server"], [answers.server])

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


def _replace(args: list[str], names: Sequence[str], values: list[str]) -> list[str]:
    """Remove an option from ``args``, then add it once per value.

    Handles ``--opt value``, ``--opt=value`` and ``-ovalue`` forms.
    """
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
