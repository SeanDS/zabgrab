"""Choosing which Zabbix server(s) to use.

Attributes
----------
SaveServer : type
    Saves a new server (name, URL) to the user's config.
ADD_SERVER_SNIPPET : str
    Config showing how to add a server, for hints.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from ..errors import InputRequired, ZabgrabError
from ..model.choice import Choice
from ..model.config import Config, ServerConfig
from ..model.names import is_url, is_valid_name
from ..model.server import Server
from .ports import UI, Option

SaveServer = Callable[[str, str], None]

ADD_SERVER_SNIPPET = """\
[servers.myserver]
url = "https://zabbix.example.org\""""


def select_server(
    configured: str | Choice[str] | None,
    config: Config,
    ui: UI,
    *,
    save: SaveServer | None = None,
) -> Server:
    """Resolve the one server to fetch from.

    Parameters
    ----------
    configured : str or Choice of str or None
        A server name or URL, a choice to ask about, or ``None`` to ask which of the
        configured servers to use.
    config : Config
        The configuration.
    ui : UI
        For questions and warnings.
    save : SaveServer, optional
        If given, offered for custom URLs entered at a prompt.

    Returns
    -------
    Server
        The server.

    Raises
    ------
    ZabgrabError
        If a server name is unknown and the user can't be asked.
    InputRequired
        If a question is needed but the session isn't interactive.
    """
    if configured is None:
        return _ask_for_server(config, ui, save=save)
    if isinstance(configured, str):
        return resolve_server(configured, config, ui, save=save)

    options = [
        _option(resolve_server(ref, config, ui, save=save))
        for ref in configured.choices
    ]
    options.append(Option[Server | None]("Custom…", None, "enter a URL"))
    chosen = ui.choose("Which Zabbix server?", options, fix="Pass --server NAME.")
    return _ask_for_url(config, ui, save=save) if chosen is None else chosen


def select_servers(
    cli: Sequence[str],
    configured: str | Choice[str] | None,
    config: Config,
    ui: UI,
    *,
    save: SaveServer | None = None,
) -> list[Server]:
    """Resolve the servers to search (``hosts``, ``items``).

    Parameters
    ----------
    cli : sequence of str
        The ``--server`` names or URLs given; each is used if there are any.
    configured : str or Choice of str or None
        Otherwise, the one server to use (see :func:`select_server`).
    config : Config
        The configuration.
    ui : UI
        For questions and warnings.
    save : SaveServer, optional
        If given, offered for custom URLs entered at a prompt.

    Returns
    -------
    list of Server
        The servers.
    """
    if cli:
        return [resolve_server(ref, config, ui, save=save) for ref in cli]
    return [select_server(configured, config, ui, save=save)]


def resolve_server(
    ref: str, config: Config, ui: UI, *, save: SaveServer | None = None
) -> Server:
    """Resolve a server name or URL.

    Unknown names get a "did you mean" suggestion: a question if the session is
    interactive (falling back to asking which server to use), else a hint.

    Parameters
    ----------
    ref : str
        A configured server's name, or a URL.
    config : Config
        The configuration.
    ui : UI
        For questions and warnings.
    save : SaveServer, optional
        If given, offered for custom URLs entered at a prompt.

    Returns
    -------
    Server
        The server.

    Raises
    ------
    ZabgrabError
        If the name is unknown and the session isn't interactive.
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
    """Offer a server, showing its URL next to its name."""
    return Option(server.label, server, server.url if server.name else None)


def _ask_for_server(config: Config, ui: UI, *, save: SaveServer | None) -> Server:
    """Ask which configured server to use, or for a URL if there are none."""
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
    """Parse a server URL typed by the user, adding ``https://`` if needed.

    Parameters
    ----------
    text : str
        What the user typed.

    Returns
    -------
    str
        The URL.

    Raises
    ------
    ValueError
        If it isn't a URL.
    """
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
    """Suggest a name for a server from its URL's hostname.

    The first hostname label that isn't ``zabbix`` or ``www`` is used, made into a
    valid name and numbered if needed to avoid clashes.

    Parameters
    ----------
    url : str
        The server's URL.
    existing : sequence of str
        Names already taken.

    Returns
    -------
    str
        The name.

    Examples
    --------
    >>> suggest_name("https://zabbix.demo.example.org", ["demo"])
    'demo-2'
    """
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
    """Ask for a server URL, offering to save it to the config if ``save`` is given."""
    url = ui.prompt(
        "Zabbix server URL",
        fix="Pass --server URL.",
        parse=parse_url,
    )
    if save is None or not ui.confirm("Save this server to your config?", fix=""):
        return Server(None, ServerConfig(url=url))

    def parse_name(text: str) -> str:
        """Check a new server name is valid and free."""
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
    ui.success(f"Saved server {name!r}; use it with `--server {name}`.")
    return Server(name, ServerConfig(url=url))
