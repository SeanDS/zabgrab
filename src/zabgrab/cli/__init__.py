"""Command line interface."""

from __future__ import annotations

import click

from .. import PROGRAM, __version__
from .app import ZabgrabGroup, global_options
from .config import config_group
from .discovery import hosts_command, items_command
from .tokens import token_group


@click.group(
    cls=ZabgrabGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name=PROGRAM)
@global_options
def cli() -> None:
    """Retrieve data from Zabbix servers.

    The global options (-v, -q, --debug, --color, --config, ...) can be given
    before or after the command.
    """


cli.add_command(config_group)
cli.add_command(hosts_command)
cli.add_command(items_command)
cli.add_command(token_group)


def main() -> None:
    """Console script entry point."""
    cli(prog_name=PROGRAM)
