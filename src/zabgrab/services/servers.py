"""Choosing which Zabbix server(s) to use."""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..errors import InputRequired, ZabgrabError
from ..model.choice import Choice, Select
from ..model.config import Config, ServerConfig
from ..model.names import is_url, is_valid_name
from ..model.server import Server
from .ports import UI, Option

SaveServer = Callable[[str, str], None]
"""Saves a new server (name, URL) to the user's config."""

ADD_SERVER_SNIPPET = """\
[servers.myserver]
url = "https://zabbix.example.org\""""


@dataclass(frozen=True)
class ServerSelection:
    """The servers to use, in order."""

    servers: list[Server]
    failover: bool = False
    """Use only the first server that works, rather than all of them."""


def select_servers(
    cli: Sequence[str],
    configured: str | Choice[str] | None,
    config: Config,
    ui: UI,
    *,
    save: SaveServer | None = None,
) -> ServerSelection:
    """Resolve the servers to use.

    ``cli`` (repeated ``--server`` options) wins over ``configured`` (a template's
    or ``[defaults]``'s ``server``). When nothing is given, the user is asked.

    ``save``, if given, is offered for custom URLs entered at a prompt.
    """
    if cli:
        return ServerSelection(
            [resolve_server(ref, config, ui, save=save) for ref in cli]
        )

    if configured is None:
        return ServerSelection([_ask_for_server(config, ui, save=save)])

    if isinstance(configured, str):
        return ServerSelection([resolve_server(configured, config, ui, save=save)])

    servers = [resolve_server(ref, config, ui, save=save) for ref in configured.choices]
    if configured.select is Select.ALL:
        return ServerSelection(servers)
    if configured.select is Select.FIRST:
        return ServerSelection(servers, failover=True)

    options = [_option(server) for server in servers]
    options.append(Option[Server | None]("Custom…", None, "enter a URL"))
    chosen = ui.choose_many(
        "Which servers?",
        options,
        fix='Pass --server NAME (repeat for several), or set select = "all" or '
        '"first" for this server choice in your config.',
    )
    return ServerSelection(
        [
            _ask_for_url(config, ui, save=save) if choice is None else choice
            for choice in chosen
        ]
    )


def resolve_server(
    ref: str, config: Config, ui: UI, *, save: SaveServer | None = None
) -> Server:
    """Resolve a server name or URL.

    Unknown names get a "did you mean" suggestion (a question, if interactive).
    """
    if ref in config.servers:
        return Server(ref, config.servers[ref])
    if is_url(ref):
        return Server(None, ServerConfig(url=ref))

    names = list(config.servers)
    close = difflib.get_close_matches(ref, names, n=1)

    if ui.interactive:
        if close and ui.confirm(
            f"Unknown server {ref!r}. Did you mean {close[0]!r}?", fix=""
        ):
            return Server(close[0], config.servers[close[0]])
        ui.warning(f"Unknown server {ref!r}.")
        return _ask_for_server(config, ui, save=save)

    hint = f"Did you mean {close[0]!r}? " if close else ""
    hint += (
        f"Configured servers: {', '.join(names)}."
        if names
        else "No servers are configured. Add one to your config "
        f"(`zabgrab config edit`):\n{ADD_SERVER_SNIPPET}"
    )
    raise ZabgrabError(f"Unknown server {ref!r}.", hint=hint)


def _option(server: Server) -> Option[Server | None]:
    return Option(server.label, server, server.url if server.name else None)


def _ask_for_server(config: Config, ui: UI, *, save: SaveServer | None) -> Server:
    fix = (
        "Pass --server NAME or --server URL, or set `server` in the template or in "
        "[defaults]."
    )
    if not config.servers:
        fix += (
            " To add a server to your config (`zabgrab config edit`):\n"
            + ADD_SERVER_SNIPPET
        )
        if not ui.interactive:
            raise InputRequired("Which Zabbix server?", fix=fix)
        return _ask_for_url(config, ui, save=save)

    options = [_option(Server(name, server)) for name, server in config.servers.items()]
    options.append(Option[Server | None]("Custom…", None, "enter a URL"))
    choice = ui.choose("Which Zabbix server?", options, fix=fix)
    return _ask_for_url(config, ui, save=save) if choice is None else choice


def parse_url(text: str) -> str:
    """Parse a server URL typed by the user, adding ``https://`` if needed."""
    text = text.strip()
    if text and "://" not in text:
        text = f"https://{text}"
    if not is_url(text):
        raise ValueError(
            "Enter the Zabbix frontend URL, e.g. 'https://zabbix.example.org' or "
            "'zabbix.example.org:8080/zabbix'."
        )
    return text


def suggest_name(url: str, existing: Sequence[str]) -> str:
    """A server name derived from a URL's hostname, not clashing with ``existing``."""
    labels = (urlsplit(url).hostname or "").split(".")
    candidates = [label for label in labels if label not in ("zabbix", "www")]
    base = "".join(
        char if char.isalnum() or char in "_-" else "-"
        for char in (candidates or labels)[0]
    )
    if not is_valid_name(base):
        base = f"server-{base}" if base else "server"
    name, number = base, 2
    while name in existing:
        name, number = f"{base}-{number}", number + 1
    return name


def _ask_for_url(config: Config, ui: UI, *, save: SaveServer | None) -> Server:
    url = ui.prompt(
        "Zabbix server URL",
        fix="Pass --server URL.",
        parse=parse_url,
    )
    if save is None or not ui.confirm("Save this server to your config?", fix=""):
        return Server(None, ServerConfig(url=url))

    def parse_name(text: str) -> str:
        text = text.strip()
        if not is_valid_name(text):
            raise ValueError(
                "Names must start with a letter and contain only letters, digits, "
                "'_' and '-'."
            )
        if text in config.servers:
            raise ValueError(f"A server called {text!r} already exists.")
        return text

    name = ui.prompt(
        "Name for this server",
        fix="",
        parse=parse_name,
        default=suggest_name(url, list(config.servers)),
    )
    save(name, url)
    ui.success(f"Saved server {name!r}; use it with --server {name}.")
    return Server(name, ServerConfig(url=url))
