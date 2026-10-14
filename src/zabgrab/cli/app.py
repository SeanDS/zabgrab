"""Shared CLI state and central error handling."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from ..adapters.configfiles import (
    ConfigLayer,
    Environment,
    LoadedConfig,
    add_server,
    discover_layers,
    load_config,
    user_config_path,
)
from ..errors import EXIT_ERROR, EXIT_INTERRUPTED, ZabgrabError
from ..model.choice import Choice
from ..model.server import Server
from ..services import servers, session
from ..services.config import load_with_repair
from ..services.ports import ClientFactory, Clock, Editor, SecretStore, ZabbixClient
from ..ui.console import RichUI


@dataclass
class AppContext:
    """State shared by all commands, built from the global options."""

    ui: RichUI
    env: Environment
    editor: Editor
    secrets: SecretStore
    client_factory: ClientFactory
    clock: Clock
    explicit_config: Path | None = None
    isolated: bool = False
    _loaded: LoadedConfig | None = field(default=None, init=False, repr=False)

    def layers(self) -> list[ConfigLayer]:
        """The config layers, including those without a file."""
        return discover_layers(
            self.env, explicit=self.explicit_config, isolated=self.isolated
        )

    def config(self) -> LoadedConfig:
        """The merged config, loaded on first use.

        Interactive sessions are offered the chance to fix invalid files.
        """
        if self._loaded is None:
            self._loaded = load_with_repair(
                lambda: load_config(self.layers()), self.ui, self.editor
            )
        return self._loaded

    def select_servers(
        self, cli: Sequence[str], configured: str | Choice[str] | None
    ) -> servers.ServerSelection:
        """The servers to use (see :func:`zabgrab.services.servers.select_servers`).

        Custom servers entered at a prompt can be saved to the user config.
        """
        return servers.select_servers(
            cli, configured, self.config().config, self.ui, save=self.save_server
        )

    def save_server(self, name: str, url: str) -> None:
        """Add a server to the user config file."""
        add_server(user_config_path(self.env), name, url)

    async def connect(self, server: Server) -> ZabbixClient:
        """A logged-in client for ``server``."""
        return await session.connect(
            server,
            factory=self.client_factory,
            ui=self.ui,
            secrets=self.secrets,
            env=self.env.variables,
        )


pass_app = click.make_pass_decorator(AppContext)


class ZabgrabGroup(click.Group):
    """Command group that turns exceptions into user-friendly messages."""

    def invoke(self, ctx: click.Context) -> Any:
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
    app = ctx.find_object(AppContext)
    return app.ui if app is not None else RichUI(interactive=False)
