"""Command line interface."""

from __future__ import annotations

from pathlib import Path

import click

from .. import PROGRAM, __version__
from ..adapters.configfiles import Environment
from ..adapters.system import ClickEditor
from ..ui.console import RichUI, is_interactive
from .app import AppContext, ZabgrabGroup
from .config import config_group


@click.group(
    cls=ZabgrabGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "-v", "--verbose", count=True, help="Show more output (repeat for even more)."
)
@click.option("-q", "--quiet", count=True, help="Show less output (repeat for less).")
@click.option(
    "--debug",
    is_flag=True,
    help="Show debug messages and full tracebacks for errors.",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force coloured output on or off. Default: on for terminals, unless "
    "$NO_COLOR is set.",
)
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    envvar="ZABGRAB_CONFIG",
    show_envvar=True,
    help="Extra config file, applied on top of all others.",
)
@click.option(
    "--isolated",
    is_flag=True,
    help="Ignore the system, user and project config files.",
)
@click.option(
    "--no-input",
    is_flag=True,
    help="Never prompt; fail with instructions instead. Also enabled by "
    "$ZABGRAB_NO_INPUT or $CI.",
)
@click.version_option(__version__, "-V", "--version", prog_name=PROGRAM)
@click.pass_context
def cli(
    ctx: click.Context,
    verbose: int,
    quiet: int,
    debug: bool,
    color: bool | None,
    config_path: Path | None,
    isolated: bool,
    no_input: bool,
) -> None:
    """Retrieve data from Zabbix servers."""
    env = Environment.current()
    ui = RichUI(
        interactive=is_interactive(env.variables, no_input=no_input),
        verbosity=verbose - quiet,
        color=color,
        debug=debug,
    )
    ctx.obj = AppContext(
        ui=ui,
        env=env,
        editor=ClickEditor(),
        explicit_config=config_path,
        isolated=isolated,
    )


cli.add_command(config_group)


def main() -> None:
    """Console script entry point."""
    cli(prog_name=PROGRAM)
