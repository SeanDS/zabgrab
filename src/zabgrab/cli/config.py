"""``zabgrab config`` commands."""

from __future__ import annotations

from pathlib import Path

import click

from ..adapters.configfiles import (
    Environment,
    LayerKind,
    project_config_target,
    read_config_file,
    system_config_path,
    user_config_path,
)
from ..errors import ConfigError, ZabgrabError
from ..ui.render import (
    config_to_toml,
    config_with_sources,
    highlighted_toml,
    layers_table,
)
from .app import AppContext, global_options, pass_app

STARTER_CONFIG = """\
# zabgrab configuration (TOML).
#
# Run `zabgrab config paths` to see which config files are read, and in which order.
#
# [servers.example]
# url = "https://zabbix.example.org"
#
# [templates.example]
# server = "example"
# items = [{ host = "myhost", name = "CPU *" }]
# from = "24 hours ago"
"""

EDIT_TARGETS = {
    "user": LayerKind.USER,
    "project": LayerKind.PROJECT,
    "system": LayerKind.SYSTEM,
}


@click.group("config")
@global_options
def config_group() -> None:
    """Show and edit the configuration."""


@config_group.command("paths")
@global_options
@pass_app
def paths_command(app: AppContext) -> None:
    """List the config files zabgrab reads, and which exist."""
    app.ui.print_data(layers_table(app.layers()))


@config_group.command("show")
@click.option(
    "--sources",
    is_flag=True,
    help="Prefix each line with the file its value came from.",
)
@global_options
@pass_app
def show_command(app: AppContext, sources: bool) -> None:
    """Print the effective configuration (all files merged) as TOML."""
    loaded = app.config()
    if sources:
        app.ui.print_data(config_with_sources(loaded))
    else:
        app.ui.print_data(highlighted_toml(config_to_toml(loaded)))


@config_group.command("edit")
@click.option(
    "--target",
    type=click.Choice(list(EDIT_TARGETS)),
    default="user",
    show_default=True,
    help="Which config file to edit. 'project' is the nearest zabgrab.toml, or a new "
    "one in the current directory.",
)
@global_options
@pass_app
def edit_command(app: AppContext, target: str) -> None:
    """Open a config file in your editor, creating it if needed."""
    kind = EDIT_TARGETS[target]
    path = edit_target_path(app.env, kind)

    if not app.ui.interactive:
        raise ZabgrabError(
            "`zabgrab config edit` needs an interactive terminal.",
            hint=f"Edit {path} directly.",
        )

    _create_if_missing(path)

    while True:
        app.editor.edit(path)
        try:
            read_config_file(path, kind=kind)
        except ConfigError as err:
            app.ui.error(err.message, details=err.details)
            if app.ui.confirm("Edit it again?", fix=f"Edit {path}."):
                continue
            err.reported = True
            raise
        break

    app.ui.success(f"Config file {path} is valid.")


def edit_target_path(env: Environment, kind: LayerKind) -> Path:
    """Find the config file to edit for a layer.

    Parameters
    ----------
    env : Environment
        The environment, for the platform's config paths.
    kind : LayerKind
        The layer (user, system or project).

    Returns
    -------
    Path
        The file, which may not exist yet.
    """
    if kind is LayerKind.USER:
        return user_config_path(env)
    if kind is LayerKind.SYSTEM:
        return system_config_path(env)
    return project_config_target(env)


def _create_if_missing(path: Path) -> None:
    """Create a config file with the starter config, if it doesn't exist."""
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STARTER_CONFIG, encoding="utf-8")
    except PermissionError as exc:
        raise ZabgrabError(
            f"Can't create {path}: permission denied.",
            hint="The system config usually needs administrator rights to edit.",
        ) from exc
