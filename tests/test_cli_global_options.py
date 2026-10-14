"""Global options can be given at any level of the command line."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import click
import pytest

from zabgrab.cli import cli

from .cli_support import Servers, run

GLOBAL = {
    "--verbose",
    "--quiet",
    "--debug",
    "--color",
    "--config",
    "--isolated",
    "--no-input",
}


def all_commands(
    command: click.Command, path: str = "zabgrab"
) -> Iterator[tuple[str, click.Command]]:
    yield path, command
    if isinstance(command, click.Group):
        for name, sub in command.commands.items():
            yield from all_commands(sub, f"{path} {name}")


@pytest.mark.parametrize(("path", "command"), list(all_commands(cli)))
def test_every_command_has_global_options(path: str, command: click.Command) -> None:
    names = {opt for param in command.params for opt in param.opts}
    assert names >= GLOBAL, f"{path} is missing {GLOBAL - names}"


CONNECTED = "Connected to demo (Zabbix 7.0.5)."


@pytest.mark.parametrize(
    "args",
    [
        ["-v", "hosts", "-s", "demo"],
        ["hosts", "-s", "demo", "-v"],
        ["hosts", "-v", "-s", "demo"],
    ],
)
def test_verbose_anywhere(servers: Servers, args: list[str]) -> None:
    result = run(*args)
    assert result.exit_code == 0, result.output
    assert CONNECTED in result.stderr


def test_levels_combine(servers: Servers) -> None:
    assert CONNECTED not in run("-v", "hosts", "-s", "demo", "-q").stderr
    assert CONNECTED in run("-v", "hosts", "-s", "demo", "-v", "-q").stderr


def test_quiet_after_command(servers: Servers) -> None:
    result = run("hosts", "-s", "demo", "nothing", "-q")
    assert "No hosts found." not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["-c", "{path}", "config", "show"],
        ["config", "-c", "{path}", "show"],
        ["config", "show", "-c", "{path}"],
    ],
)
def test_config_anywhere(tmp_path: Path, args: list[str]) -> None:
    path = tmp_path / "extra.toml"
    path.write_text("[defaults]\njobs = 3\n")
    result = run(*[arg.format(path=path) for arg in args])
    assert result.exit_code == 0, result.output
    assert "jobs = 3" in result.stdout


def test_isolated_after_command(servers: Servers) -> None:
    result = run("token", "list", "--isolated")
    assert "No servers are configured" in result.stderr
