"""``zabgrab token``: managing API tokens stored in the system keyring."""

from __future__ import annotations

import asyncio
from typing import IO

import click

from ..errors import ZabgrabError
from ..model.server import Server
from ..services import tokens
from ..services.servers import resolve_server
from ..ui.render import tokens_table
from .app import AppContext, global_options, pass_app


@click.group("token")
@global_options
def token_group() -> None:
    """Manage API tokens stored in your system keyring.

    When connecting, zabgrab uses the token in a server's token_env environment
    variable if set, otherwise the token stored here.
    """


def _server(app: AppContext, ref: str | None) -> Server:
    config = app.config().config
    if ref is not None:
        return resolve_server(ref, config, app.ui, save=app.save_server)
    (server,) = app.select_servers([], None).servers
    return server


@token_group.command("list")
@click.option("--check", is_flag=True, help="Check each token against its server.")
@global_options
@pass_app
def list_command(app: AppContext, check: bool) -> None:
    """List servers and their tokens (tokens themselves are never shown)."""
    statuses = tokens.token_statuses(
        app.config().config, app.secrets, app.env.variables
    )
    if not statuses:
        app.ui.info(
            "No servers are configured and no tokens are stored. Add a token with "
            "`zabgrab token add`."
        )
        return

    results = None
    if check:

        async def check_all() -> list[tokens.CheckResult]:
            return [
                await tokens.check_token(
                    status.server,
                    factory=app.client_factory,
                    secrets=app.secrets,
                    env=app.env.variables,
                )
                for status in statuses
            ]

        results = asyncio.run(check_all())

    app.ui.print_data(tokens_table(statuses, results))


@token_group.command("add")
@click.argument("server", required=False, metavar="[NAME|URL]")
@click.argument("file", type=click.File("r"), required=False)
@click.option(
    "--no-check", is_flag=True, help="Store the token without checking it first."
)
@global_options
@pass_app
def add_command(
    app: AppContext, server: str | None, file: IO[str] | None, no_check: bool
) -> None:
    """Store an API token for a server.

    The token is read from FILE ('-' for standard input) if given, otherwise asked
    for. Avoid putting tokens on the command line itself.
    """
    if server == "-" and file is None:
        # `zabgrab token add -`: the only argument is the file.
        server, file = None, click.open_file("-")

    token = None
    if file is not None:
        token = file.readline().strip()
        if not token:
            raise ZabgrabError(f"No token found in {_file_name(file)}.")

    asyncio.run(
        tokens.add_token(
            _server(app, server),
            token,
            factory=app.client_factory,
            ui=app.ui,
            secrets=app.secrets,
            env=app.env.variables,
            check=not no_check,
        )
    )


def _file_name(file: IO[str]) -> str:
    name = getattr(file, "name", "-")
    return "standard input" if name in ("-", "<stdin>") else str(name)


@token_group.command("delete")
@click.argument("server", required=False, metavar="[NAME|URL]")
@click.option("-y", "--yes", is_flag=True, help="Don't ask for confirmation.")
@global_options
@pass_app
def delete_command(app: AppContext, server: str | None, yes: bool) -> None:
    """Delete a stored token (asks which, if not given)."""
    if server is None:
        statuses = tokens.token_statuses(
            app.config().config, app.secrets, app.env.variables
        )
        target = tokens.choose_stored(statuses, app.ui)
    else:
        target = _server(app, server)
    tokens.delete_token(target, ui=app.ui, secrets=app.secrets, yes=yes)


@token_group.command("reset")
@click.option("-y", "--yes", is_flag=True, help="Don't ask for confirmation.")
@global_options
@pass_app
def reset_command(app: AppContext, yes: bool) -> None:
    """Delete all stored tokens, leaving nothing of zabgrab's in your keyring."""
    tokens.reset_tokens(ui=app.ui, secrets=app.secrets, yes=yes)
