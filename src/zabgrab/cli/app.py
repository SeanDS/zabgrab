"""Shared CLI state and central error handling.

Attributes
----------
pass_app : callable
    Decorator passing the :class:`AppContext` to a command.
GLOBAL_OPTIONS : list
    The options every command accepts (see :func:`global_options`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, TypeVar

import click
from click.core import ParameterSource

from ..adapters.configfiles import (
    BUILTIN_SOURCE,
    ConfigLayer,
    Environment,
    LoadedConfig,
    add_server,
    discover_layers,
    load_config,
    set_server_url,
    user_config_path,
)
from ..adapters.secrets import KeyringSecrets
from ..adapters.system import ClickEditor, SystemClock
from ..adapters.zabbix import HttpTransport, ZabbixAPI
from ..errors import EXIT_ERROR, EXIT_INTERRUPTED, ZabgrabError
from ..model.choice import Choice
from ..model.config import ServerConfig
from ..model.server import Server
from ..services import servers, session
from ..services.config import load_with_repair
from ..services.ports import ClientFactory, Clock, Editor, SecretStore, ZabbixClient
from ..ui.console import RichUI, is_interactive

F = TypeVar("F", bound=Callable[..., Any])


def http_client(config: ServerConfig) -> ZabbixClient:
    """Create a client that talks to a real server over HTTP(S).

    Parameters
    ----------
    config : ServerConfig
        The server's URL and connection settings.

    Returns
    -------
    ZabbixClient
        The client, not yet logged in.
    """
    return ZabbixAPI(
        HttpTransport(config.url, verify_ssl=config.verify_ssl, timeout=config.timeout)
    )


@dataclass
class GlobalOptions:
    """Options accepted by every command (see :func:`global_options`).

    Attributes
    ----------
    verbosity : int
        The count of ``-v`` minus the count of ``-q``.
    debug : bool
        ``--debug``.
    color : bool or None
        ``--color`` or ``--no-color``, if given.
    config_path : Path or None
        ``--config``.
    isolated : bool
        ``--isolated``.
    no_input : bool
        ``--no-input``.
    """

    verbosity: int = 0
    debug: bool = False
    color: bool | None = None
    config_path: Path | None = None
    isolated: bool = False
    no_input: bool = False


class AppContext:
    """State shared by all commands: the click context object.

    Global options update :attr:`options` while the command line is parsed, at
    whichever level they appear; everything else is built on first use. Tests
    replace the adapters (secrets, client factory, clock, ...) with fakes.

    Attributes
    ----------
    options : GlobalOptions
        The global options.
    env : Environment
        The environment variables and platform paths.
    editor : Editor
        Opens config files for editing.
    secrets : SecretStore
        Stores API tokens.
    client_factory : ClientFactory
        Creates Zabbix clients.
    clock : Clock
        The current time.
    """

    def __init__(self) -> None:
        self.options = GlobalOptions()
        self.env = Environment.current()
        self.editor: Editor = ClickEditor()
        self.secrets: SecretStore = KeyringSecrets()
        self.client_factory: ClientFactory = http_client
        self.clock: Clock = SystemClock()
        self._loaded: LoadedConfig | None = None

    @cached_property
    def ui(self) -> RichUI:
        """The terminal UI, configured by the global options."""
        return RichUI(
            interactive=is_interactive(
                self.env.variables, no_input=self.options.no_input
            ),
            verbosity=self.options.verbosity,
            color=self.options.color,
            debug=self.options.debug,
        )

    def layers(self) -> list[ConfigLayer]:
        """Find the config layers, including those without a file.

        Returns
        -------
        list of ConfigLayer
            The layers, lowest priority first.
        """
        return discover_layers(
            self.env,
            explicit=self.options.config_path,
            isolated=self.options.isolated,
        )

    def config(self) -> LoadedConfig:
        """Get the merged config, loaded on first use.

        Interactive sessions are offered the chance to fix invalid files.

        Returns
        -------
        LoadedConfig
            The config and the layers it came from.

        Raises
        ------
        ZabgrabError
            If a config file is invalid and isn't fixed.
        """
        if self._loaded is None:
            self._loaded = load_with_repair(
                lambda: load_config(self.layers()), self.ui, self.editor
            )
        return self._loaded

    def select_server(self, configured: str | Choice[str] | None) -> Server:
        """Choose the server to use.

        See :func:`zabgrab.services.servers.select_server`. Custom servers entered
        at a prompt can be saved to the user config.

        Parameters
        ----------
        configured : str or Choice of str or None
            The configured server, choice of servers, or ``None`` to ask.

        Returns
        -------
        Server
            The server.
        """
        return servers.select_server(
            configured, self.config().config, self.ui, save=self.save_server
        )

    def select_servers(
        self, cli: Sequence[str], configured: str | Choice[str] | None
    ) -> list[Server]:
        """Choose the servers to search.

        See :func:`zabgrab.services.servers.select_servers`.

        Parameters
        ----------
        cli : sequence of str
            The ``--server`` options given.
        configured : str or Choice of str or None
            The configured server, used if there are none.

        Returns
        -------
        list of Server
            The servers.
        """
        return servers.select_servers(
            cli, configured, self.config().config, self.ui, save=self.save_server
        )

    def save_server(self, name: str, url: str) -> None:
        """Add a server to the user config file.

        Parameters
        ----------
        name : str
            The server's name.
        url : str
            Its URL.
        """
        add_server(user_config_path(self.env), name, url)
        # Reload on next use, so the new server is known (e.g. to update its URL).
        self._loaded = None

    def update_server_url(self, name: str, url: str) -> None:
        """Change a configured server's URL in the config file that defines it.

        Parameters
        ----------
        name : str
            The server's name.
        url : str
            Its new URL.
        """
        source = self.config().source_of("servers", name)
        if source is None or source == BUILTIN_SOURCE:  # pragma: no cover
            raise ZabgrabError(f"Server {name!r} isn't defined in a config file.")
        set_server_url(Path(source), name, url)

    async def connect(self, server: Server) -> ZabbixClient:
        """Connect and log in to a server.

        Parameters
        ----------
        server : Server
            The server.

        Returns
        -------
        ZabbixClient
            The logged-in client, to be closed after use.

        Raises
        ------
        ZabgrabError
            If the server can't be reached or logging in fails.
        """
        return await session.connect(
            server,
            factory=self.client_factory,
            ui=self.ui,
            secrets=self.secrets,
            env=self.env.variables,
            update_url=self.update_server_url,
        )


pass_app = click.make_pass_decorator(AppContext, ensure=True)


def _set(
    apply: Callable[[GlobalOptions, Any], None],
) -> Callable[[click.Context, click.Parameter, Any], None]:
    """Make an option callback applying the option's value to the shared options.

    The context object is created on the first (root) command's context even when
    the option isn't given, so that every level shares it, including the error
    handler, which looks for it on the root context.

    Parameters
    ----------
    apply : callable
        Applies the value to the options.

    Returns
    -------
    callable
        The callback.
    """

    def callback(ctx: click.Context, param: click.Parameter, value: Any) -> None:
        """Apply the option's value, if it was given."""
        options = ctx.ensure_object(AppContext).options
        if ctx.get_parameter_source(param.name or "") is not ParameterSource.DEFAULT:
            apply(options, value)

    return callback


def _add_verbosity(options: GlobalOptions, count: int) -> None:
    """Apply ``-v``."""
    options.verbosity += count


def _subtract_verbosity(options: GlobalOptions, count: int) -> None:
    """Apply ``-q``."""
    options.verbosity -= count


GLOBAL_OPTIONS = [
    click.option(
        "-v",
        "--verbose",
        count=True,
        expose_value=False,
        callback=_set(_add_verbosity),
        help="Show more output (repeat for even more).",
    ),
    click.option(
        "-q",
        "--quiet",
        count=True,
        expose_value=False,
        callback=_set(_subtract_verbosity),
        help="Show less output (repeat for less).",
    ),
    click.option(
        "--debug",
        is_flag=True,
        expose_value=False,
        callback=_set(lambda options, value: setattr(options, "debug", value)),
        help="Show debug messages and full tracebacks for errors.",
    ),
    click.option(
        "--color/--no-color",
        default=None,
        expose_value=False,
        callback=_set(lambda options, value: setattr(options, "color", value)),
        help="Force coloured output on or off. Default: on for terminals, unless "
        "$NO_COLOR is set.",
    ),
    click.option(
        "-c",
        "--config",
        type=click.Path(dir_okay=False, path_type=Path),
        envvar="ZABGRAB_CONFIG",
        show_envvar=True,
        expose_value=False,
        callback=_set(lambda options, value: setattr(options, "config_path", value)),
        help="Extra config file, applied on top of all others.",
    ),
    click.option(
        "--isolated",
        is_flag=True,
        expose_value=False,
        callback=_set(lambda options, value: setattr(options, "isolated", value)),
        help="Ignore the system, user and project config files.",
    ),
    click.option(
        "--no-input",
        is_flag=True,
        expose_value=False,
        callback=_set(lambda options, value: setattr(options, "no_input", value)),
        help="Never prompt; fail with instructions instead. Also enabled by "
        "$ZABGRAB_NO_INPUT or $CI.",
    ),
]


def global_options(function: F) -> F:
    """Add the global options to a command or group.

    Every command gets them, so they can be given at any level, e.g. both
    ``zabgrab -v hosts`` and ``zabgrab hosts -v``.

    Parameters
    ----------
    function : callable
        The command or group's function.

    Returns
    -------
    callable
        The function, with the options added.
    """
    for option in reversed(GLOBAL_OPTIONS):
        function = option(function)
    return function


class ZabgrabGroup(click.Group):
    """Command group that turns exceptions into user-friendly messages."""

    def invoke(self, ctx: click.Context) -> Any:
        """Run the command, reporting errors and exiting with their exit codes.

        Parameters
        ----------
        ctx : click.Context
            The group's context.

        Returns
        -------
        Any
            What the command returns.
        """
        try:
            return super().invoke(ctx)
        except (click.exceptions.Exit, click.ClickException, click.Abort):
            raise
        except ZabgrabError as err:
            ui = _ui(ctx)
            if not err.reported:
                if ui.debug_mode:
                    ui.print_exception()
                ui.error(err.message, details=err.details, hint=err.hint)
            ctx.exit(err.exit_code)
        except KeyboardInterrupt:
            _ui(ctx).error("Interrupted.")
            ctx.exit(EXIT_INTERRUPTED)
        except Exception as err:
            ui = _ui(ctx)
            if ui.debug_mode:
                ui.print_exception()
            ui.error(
                f"Unexpected error: {type(err).__name__}: {err}",
                hint=None
                if ui.debug_mode
                else "Run with --debug for the full traceback, and please report "
                "this as a bug.",
            )
            ctx.exit(EXIT_ERROR)


def _ui(ctx: click.Context) -> RichUI:
    """Get the UI to report errors with, even if the command never made one."""
    app = ctx.find_object(AppContext)
    return app.ui if app is not None else RichUI(interactive=False)
