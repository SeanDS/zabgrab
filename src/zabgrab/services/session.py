"""Connecting and logging in to a Zabbix server."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import AuthenticationFailed, ZabgrabError
from ..model.config import AuthMethod
from ..model.server import Server
from .ports import UI, ClientFactory, Option, SecretStore, ZabbixClient

_TOKEN = "token"
_PASSWORD = "password"


@dataclass(frozen=True)
class TokenLookup:
    """Where a server's token comes from."""

    token: str | None
    source: str
    """``$VARIABLE`` or ``your keyring``."""


def find_token(
    server: Server, secrets: SecretStore, env: Mapping[str, str]
) -> TokenLookup:
    """The token to use for ``server``: its ``token_env`` variable if set, otherwise
    the keyring."""
    variable = server.config.token_env
    if variable and env.get(variable):
        return TokenLookup(env[variable], f"${variable}")
    return TokenLookup(secrets.get_token(server.url), "your keyring")


def token_fix(server: Server) -> str:
    """How to give zabgrab a token for ``server`` without being prompted."""
    name = server.name or "myserver"
    variable = (
        server.config.token_env or f"ZABGRAB_{name.upper().replace('-', '_')}_TOKEN"
    )
    return (
        f"Create an API token at {server.token_page}, then store it with `zabgrab "
        f'token add {server.name or server.url}`, or set token_env = "{variable}" '
        f"in [servers.{name}] in your config and export {variable}."
    )


async def ask_for_token(client: ZabbixClient, ui: UI, *, fix: str = "") -> str:
    """Ask for a token until the server accepts one, and return it."""
    while True:
        token = ui.prompt("API token", fix=fix, parse=non_empty, secret=True)
        try:
            await client.login_token(token)
            return token
        except AuthenticationFailed:
            ui.error("That token was rejected; check it and try again.")


def non_empty(text: str) -> str:
    if not text.strip():
        raise ValueError("Please enter a value.")
    return text.strip()


async def connect(
    server: Server,
    *,
    factory: ClientFactory,
    ui: UI,
    secrets: SecretStore,
    env: Mapping[str, str],
) -> ZabbixClient:
    """Return a logged-in client for ``server``.

    Tokens come from the server's ``token_env`` variable, then the keyring. If there
    is none (or it's rejected), interactive users can paste a token or log in with a
    password; otherwise an error explains how to provide one.
    """
    if not server.config.verify_ssl:
        ui.warning(f"SSL certificate verification is disabled for {server.label}.")

    client = factory(server.config)
    try:
        version = await client.check_version()
        ui.debug(f"Connected to {server.label} (Zabbix {version}).")
        if server.config.auth is AuthMethod.PASSWORD:
            await _password_login(client, server, ui)
        else:
            await _token_login(client, server, ui, secrets, env)
    except BaseException:
        await client.close()
        raise
    return client


async def _token_login(
    client: ZabbixClient,
    server: Server,
    ui: UI,
    secrets: SecretStore,
    env: Mapping[str, str],
) -> None:
    variable = server.config.token_env
    lookup = find_token(server, secrets, env)
    token, source = lookup.token, lookup.source
    if variable and not token:
        ui.warning(f"${variable} is not set.")

    if token:
        try:
            await client.login_token(token)
        except AuthenticationFailed as err:
            if not ui.interactive:
                err.hint = f"The API token from {source} was rejected. " + token_fix(
                    server
                )
                raise
            ui.error(f"The API token from {source} was rejected for {server.label}.")
        else:
            ui.debug(f"Logged in to {server.label} with the API token from {source}.")
            return

    method = ui.choose(
        f"How do you want to log in to {server.label}?",
        [
            Option("Paste an API token", _TOKEN, f"create one at {server.token_page}"),
            Option("Username and password", _PASSWORD),
        ],
        fix=token_fix(server),
    )
    if method == _PASSWORD:
        await _password_login(client, server, ui)
        return

    token = await ask_for_token(client, ui)
    if ui.confirm("Save the token in your system keyring for next time?", fix=""):
        try:
            secrets.set_token(server.url, token)
        except ZabgrabError as err:
            ui.warning(f"{err.message} {err.hint or ''}".strip())
        else:
            ui.success("Token saved.")


async def _password_login(client: ZabbixClient, server: Server, ui: UI) -> None:
    fix = (
        "Password login needs an interactive terminal. Use an API token instead: "
        + token_fix(server)
    )
    username = server.config.username
    while True:
        if server.config.username is None:
            username = ui.prompt("Username", fix=fix, parse=non_empty, default=username)
        password = ui.prompt(
            f"Password for {username}" if username else "Password",
            fix=fix,
            parse=non_empty,
            secret=True,
        )
        assert username is not None
        try:
            await client.login_password(username, password)
            return
        except AuthenticationFailed:
            ui.error("Incorrect username or password; try again.")
