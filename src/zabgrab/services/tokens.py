"""Managing stored API tokens."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..errors import AuthenticationFailed, Cancelled, ZabbixError, ZabgrabError
from ..model.config import AuthMethod, Config, ServerConfig
from ..model.server import Server
from .ports import UI, ClientFactory, Option, SecretStore
from .session import ask_for_token, find_token, non_empty


@dataclass(frozen=True)
class TokenStatus:
    """Whether a server has a token, and where from."""

    server: Server
    stored: bool
    """A token is stored in the keyring."""
    env_var: str | None
    env_set: bool


class CheckState(StrEnum):
    VALID = "valid"
    REJECTED = "rejected"
    MISSING = "missing"
    ERROR = "error"


@dataclass(frozen=True)
class CheckResult:
    state: CheckState
    detail: str = ""


def token_statuses(
    config: Config, secrets: SecretStore, env: Mapping[str, str]
) -> list[TokenStatus]:
    """Every configured server, then any other server with a stored token."""
    statuses = []
    for name, server_config in config.servers.items():
        variable = server_config.token_env
        statuses.append(
            TokenStatus(
                server=Server(name, server_config),
                stored=secrets.get_token(server_config.url) is not None,
                env_var=variable,
                env_set=bool(variable and env.get(variable)),
            )
        )

    configured = {server.url for server in config.servers.values()}
    for url in secrets.urls():
        if url not in configured:
            statuses.append(
                TokenStatus(Server(None, ServerConfig(url=url)), True, None, False)
            )
    return statuses


async def check_token(
    server: Server,
    *,
    factory: ClientFactory,
    secrets: SecretStore,
    env: Mapping[str, str],
) -> CheckResult:
    """Check the token zabgrab would use for ``server`` against the server."""
    lookup = find_token(server, secrets, env)
    if not lookup.token:
        return CheckResult(CheckState.MISSING)

    client = factory(server.config)
    try:
        await client.check_version()
        await client.login_token(lookup.token)
    except AuthenticationFailed:
        return CheckResult(CheckState.REJECTED, f"from {lookup.source}")
    except ZabbixError as err:
        return CheckResult(CheckState.ERROR, err.message)
    finally:
        await client.close()
    return CheckResult(CheckState.VALID, f"from {lookup.source}")


async def add_token(
    server: Server,
    token: str | None,
    *,
    factory: ClientFactory,
    ui: UI,
    secrets: SecretStore,
    env: Mapping[str, str],
    check: bool = True,
) -> None:
    """Store a token for ``server`` in the keyring, asking for it if ``token`` is
    ``None``. With ``check``, the server must accept the token first."""
    fix = (
        "Pipe the token in with --stdin, e.g. "
        f"`printenv MY_TOKEN | zabgrab token add {server.name or server.url} --stdin`."
    )
    if server.config.auth is AuthMethod.PASSWORD:
        ui.warning(
            f'{server.label} is configured with auth = "password"; the token will only '
            'be used if you change it to auth = "token".'
        )

    if token is None:
        ui.info(f"Create an API token at {server.token_page}")

    if check:
        client = factory(server.config)
        try:
            await client.check_version()
            if token is None:
                token = await ask_for_token(client, ui, fix=fix)
            else:
                try:
                    await client.login_token(token)
                except AuthenticationFailed as err:
                    err.hint = "Check the token, or pass --no-check to store it anyway."
                    raise
        finally:
            await client.close()
    elif token is None:
        token = ui.prompt("API token", fix=fix, parse=non_empty, secret=True)

    secrets.set_token(server.url, token)
    ui.success(f"Saved the token for {server.label} in your keyring.")

    variable = server.config.token_env
    if variable and env.get(variable):
        ui.warning(f"${variable} is set and takes precedence over the keyring.")


def choose_stored(statuses: list[TokenStatus], ui: UI) -> Server:
    """Ask which server's stored token to act on."""
    stored = [status.server for status in statuses if status.stored]
    if not stored:
        raise ZabgrabError("No tokens are stored in your keyring.")
    return ui.choose(
        "Which server's token?",
        [
            Option(server.label, server, server.url if server.name else None)
            for server in stored
        ],
        fix="Name the server: `zabgrab token delete NAME`.",
    )


def delete_token(server: Server, *, ui: UI, secrets: SecretStore, yes: bool) -> None:
    """Delete the stored token for ``server``, confirming first unless ``yes``."""
    if secrets.get_token(server.url) is None and server.url not in secrets.urls():
        raise ZabgrabError(f"No token is stored for {server.label}.")
    if not yes and not ui.confirm(
        f"Delete the stored token for {server.label}?",
        fix="Pass --yes to delete it without asking.",
        default=False,
    ):
        raise Cancelled()
    secrets.delete_token(server.url)
    ui.success(f"Deleted the token for {server.label}.")
