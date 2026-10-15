"""Managing stored API tokens (``zabgrab token``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..errors import AuthenticationFailed, Cancelled, ZabbixError, ZabgrabError
from ..model.config import AuthMethod, Config, ServerConfig
from ..model.server import Server
from .ports import UI, ClientFactory, Option, SecretStore
from .session import (
    UpdateServerUrl,
    ask_for_token,
    find_token,
    non_empty,
    open_client,
)


@dataclass(frozen=True)
class TokenStatus:
    """Whether a server has a token, and where from.

    Attributes
    ----------
    server : Server
        The server.
    stored : bool
        Whether a token is stored in the keyring.
    env_var : str or None
        The server's ``token_env`` variable, if it has one.
    env_set : bool
        Whether that variable is set.
    """

    server: Server
    stored: bool
    env_var: str | None
    env_set: bool


class CheckState(StrEnum):
    """The outcome of checking a token against its server.

    Attributes
    ----------
    VALID
        The server accepted it.
    REJECTED
        The server rejected it.
    MISSING
        There's no token for the server.
    ERROR
        The server couldn't be asked (e.g. it's unreachable).
    """

    VALID = "valid"
    REJECTED = "rejected"
    MISSING = "missing"
    ERROR = "error"


@dataclass(frozen=True)
class CheckResult:
    """The result of checking a token.

    Attributes
    ----------
    state : CheckState
        The outcome.
    detail : str
        Where the token came from, or the error.
    """

    state: CheckState
    detail: str = ""


def token_statuses(
    config: Config, secrets: SecretStore, env: Mapping[str, str]
) -> list[TokenStatus]:
    """List where each server's token comes from.

    Parameters
    ----------
    config : Config
        The configured servers.
    secrets : SecretStore
        The stored tokens.
    env : mapping of str to str
        Environment variables.

    Returns
    -------
    list of TokenStatus
        Every configured server, then any other server with a stored token.
    """
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
    """Check the token zabgrab would use for a server against the server.

    Parameters
    ----------
    server : Server
        The server.
    factory : ClientFactory
        Creates the client.
    secrets : SecretStore
        The stored tokens.
    env : mapping of str to str
        Environment variables (for ``token_env``).

    Returns
    -------
    CheckResult
        Whether the token is valid, rejected, missing or couldn't be checked.
    """
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
    update_url: UpdateServerUrl | None = None,
) -> None:
    """Store a token for a server in the keyring.

    Parameters
    ----------
    server : Server
        The server.
    token : str or None
        The token, or ``None`` to ask for it.
    factory : ClientFactory
        Creates the client, to check the token.
    ui : UI
        Asks for the token, and reports the outcome.
    secrets : SecretStore
        Where to store it.
    env : mapping of str to str
        Environment variables (to warn if ``token_env`` overrides the keyring).
    check : bool, optional
        Make sure the server accepts the token first. If the server redirects, the
        token is stored for the URL it redirects to (see
        :func:`zabgrab.services.session.open_client`).
    update_url : callable, optional
        Offered to update a configured server's URL after a redirect.

    Raises
    ------
    AuthenticationFailed
        If checking and the server rejects the token.
    InputRequired
        If the token is needed and the session isn't interactive.
    """
    fix = (
        "Pass the token on standard input with '-', e.g. "
        f"`printenv MY_TOKEN | zabgrab token add {server.name or server.url} -`, or "
        "give the path of a file containing it."
    )
    if server.config.auth is AuthMethod.PASSWORD:
        ui.warning(
            f'{server.display} is configured with auth = "password"; the token will '
            'only be used if you change it to auth = "token".'
        )

    if check:
        # Connect first: the server may redirect, changing the token page's URL.
        client, server, _ = await open_client(
            server, factory=factory, ui=ui, update_url=update_url
        )
        try:
            if token is None:
                ui.info(f"Create an API token at {server.token_page}")
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
        ui.info(f"Create an API token at {server.token_page}")
        token = ui.prompt("API token", fix=fix, parse=non_empty, secret=True)

    secrets.set_token(server.url, token)
    ui.success(f"Saved the token for {server.display} in your keyring.")

    variable = server.config.token_env
    if variable and env.get(variable):
        ui.warning(f"${variable} is set and takes precedence over the keyring.")


def choose_stored(statuses: list[TokenStatus], ui: UI) -> Server:
    """Ask which server's stored token to act on.

    Raises
    ------
    ZabgrabError
        If no tokens are stored.
    """
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
    """Delete the stored token for a server, confirming first unless ``yes``.

    Raises
    ------
    ZabgrabError
        If no token is stored for it.
    Cancelled
        If the user says no.
    """
    if secrets.get_token(server.url) is None:
        raise ZabgrabError(f"No token is stored for {server.display}.")
    if not yes and not ui.confirm(
        f"Delete the stored token for {server.display}?",
        fix="Pass --yes to delete it without asking.",
        default=False,
    ):
        raise Cancelled()
    secrets.delete_token(server.url)
    ui.success(f"Deleted the token for {server.display}.")


def reset_tokens(*, ui: UI, secrets: SecretStore, yes: bool) -> None:
    """Delete every stored token, confirming first unless ``yes``.

    Raises
    ------
    Cancelled
        If the user says no.
    """
    count = len(secrets.urls())
    if count and not yes:
        noun = "token" if count == 1 else f"all {count} tokens"
        if not ui.confirm(
            f"Delete {noun} stored by zabgrab in your keyring?",
            fix="Pass --yes to delete them without asking.",
            default=False,
        ):
            raise Cancelled()
    # Clear the entry even if it looked empty, in case it was unreadable.
    secrets.delete_all()
    if count:
        ui.success(
            f"Deleted {count} token{'' if count == 1 else 's'}; zabgrab has nothing "
            "left in your keyring."
        )
    else:
        ui.info("No tokens were stored; zabgrab has nothing in your keyring.")
