"""``zabgrab hosts`` and ``zabgrab items``: finding what's on a server."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

import click

from ..model.item import ItemSpec
from ..model.itemspec import EXAMPLE, parse_item_spec
from ..model.server import Server
from ..services.discover import find_items, on_servers
from ..services.ports import ZabbixClient
from ..ui.render import hosts_table, items_table
from .app import AppContext, global_options, pass_app

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_LIMIT = 500


if TYPE_CHECKING:
    _ParamType = click.ParamType[ItemSpec]
else:
    _ParamType = click.ParamType


class ItemSpecType(_ParamType):
    """An ``--item`` value."""

    name = "item"

    def convert(
        self, value: Any, param: click.Parameter | None, ctx: click.Context | None
    ) -> ItemSpec:
        try:
            return parse_item_spec(value)
        except ValueError as exc:
            self.fail(str(exc), param, ctx)


def server_option(function: F) -> F:
    return click.option(
        "-s",
        "--server",
        "servers",
        multiple=True,
        metavar="NAME|URL",
        help="Server to query (repeat for several). Default: [defaults] server in "
        "your config, otherwise you're asked.",
    )(function)


def limit_option(function: F) -> F:
    return click.option(
        "--limit",
        type=click.IntRange(min=1),
        default=DEFAULT_LIMIT,
        show_default=True,
        help="Maximum number of results per server.",
    )(function)


def _query(
    app: AppContext,
    servers: Sequence[str],
    action: Callable[[ZabbixClient], Any],
) -> list[tuple[Server, Any]]:
    selection = app.select_servers(servers, app.config().config.defaults.server)
    return asyncio.run(on_servers(selection, app.connect, action, app.ui))


def _report_limit(
    app: AppContext, results: Sequence[tuple[Server, Sequence[Any]]], limit: int
) -> None:
    for server, rows in results:
        if len(rows) >= limit:
            app.ui.warning(
                f"Showing only the first {limit} results from {server.display}; narrow "
                "the search or raise --limit."
            )


@click.command("hosts")
@click.argument("pattern", required=False)
@click.option("-g", "--group", help="Only hosts in host groups matching this pattern.")
@server_option
@limit_option
@global_options
@pass_app
def hosts_command(
    app: AppContext,
    pattern: str | None,
    group: str | None,
    servers: tuple[str, ...],
    limit: int,
) -> None:
    """List hosts, optionally only those whose name matches PATTERN.

    Patterns are case-insensitive, match anywhere in the name, and may contain '*'
    wildcards, like the filters in the Zabbix web UI.
    """

    async def action(client: ZabbixClient) -> Any:
        return await client.hosts(pattern=pattern, group=group, limit=limit)

    results = _query(app, servers, action)
    rows = [(server, host) for server, hosts in results for host in hosts]
    if not rows:
        app.ui.info("No hosts found.")
        return
    app.ui.print_data(hosts_table(rows))
    _report_limit(app, results, limit)


@click.command("items")
@click.argument("pattern", required=False)
@click.option("-H", "--host", help="Only items on hosts matching this pattern.")
@click.option("-k", "--key", help="Only items whose key matches this pattern.")
@click.option("--tag", help="Only items with this tag ('name' or 'name:value').")
@click.option(
    "-i",
    "--item",
    "items",
    type=ItemSpecType(),
    multiple=True,
    help=f"Item spec to include (repeat for several); {EXAMPLE}.",
)
@server_option
@limit_option
@global_options
@pass_app
def items_command(
    app: AppContext,
    pattern: str | None,
    host: str | None,
    key: str | None,
    tag: str | None,
    items: tuple[ItemSpec, ...],
    servers: tuple[str, ...],
    limit: int,
) -> None:
    """List items, optionally only those whose name matches PATTERN.

    Patterns are case-insensitive, match anywhere in the text, and may contain '*'
    wildcards, like the filters in the Zabbix web UI. All of PATTERN, --host, --key
    and --tag must match; each --item adds further items.
    """
    specs = list(items)
    if pattern or host or key or tag:
        specs.insert(0, ItemSpec(name=pattern, host=host, key=key, tag=tag))

    async def action(client: ZabbixClient) -> Any:
        return await find_items(client, specs, app.ui, limit=limit)

    results = _query(app, servers, action)
    rows = [(server, item) for server, found in results for item in found]
    if not rows:
        app.ui.info("No items found.")
        return
    app.ui.print_data(items_table(rows, now=app.clock.now()))
    _report_limit(app, results, limit)
