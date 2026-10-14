"""Interactive repair of invalid config files."""

from __future__ import annotations

from pathlib import Path

import pytest

from zabgrab.adapters.configfiles import (
    Environment,
    LoadedConfig,
    discover_layers,
    load_config,
    user_config_path,
)
from zabgrab.errors import ConfigError, InputRequired
from zabgrab.services.config import load_with_repair

from .fakes import FakeEditor, RecordingUI

VALID = "[defaults]\njobs = 2\n"
INVALID = "[defaults]\njobs = 0\n"


@pytest.fixture
def config_path(env: Environment) -> Path:
    path = user_config_path(env)
    path.parent.mkdir(parents=True)
    path.write_text(INVALID)
    return path


def loader(env: Environment) -> LoadedConfig:
    return load_config(discover_layers(env))


def test_valid_config_needs_no_interaction(env: Environment) -> None:
    ui = RecordingUI()
    loaded = load_with_repair(lambda: loader(env), ui, FakeEditor())
    assert loaded.config.defaults.jobs == 6
    assert ui.questions == []


def test_interactive_repair(env: Environment, config_path: Path) -> None:
    ui = RecordingUI(answers=[True])
    editor = FakeEditor(contents=[VALID])

    loaded = load_with_repair(lambda: loader(env), ui, editor)

    assert loaded.config.defaults.jobs == 2
    assert editor.opened == [config_path]
    assert ui.levels("error") == [f"Invalid config file {config_path}."]


def test_repair_retries_until_valid(env: Environment, config_path: Path) -> None:
    ui = RecordingUI(answers=[True, True])
    editor = FakeEditor(contents=["still = 'bad'", VALID])

    load_with_repair(lambda: loader(env), ui, editor)

    assert editor.opened == [config_path, config_path]


def test_declining_marks_error_reported(env: Environment, config_path: Path) -> None:
    ui = RecordingUI(answers=[False])
    with pytest.raises(ConfigError) as exc_info:
        load_with_repair(lambda: loader(env), ui, FakeEditor())
    assert exc_info.value.reported


def test_non_interactive_raises_unreported(env: Environment, config_path: Path) -> None:
    ui = RecordingUI(interactive=False)
    with pytest.raises(ConfigError) as exc_info:
        load_with_repair(lambda: loader(env), ui, FakeEditor())
    assert not exc_info.value.reported
    assert ui.messages == []


def test_errors_without_a_file_are_not_repaired(env: Environment) -> None:
    ui = RecordingUI()
    with pytest.raises(ConfigError):
        load_with_repair(
            lambda: load_config(discover_layers(env, explicit=Path("missing.toml"))),
            ui,
            FakeEditor(),
        )
    assert ui.questions == []


def test_recording_ui_mirrors_non_interactive_behaviour() -> None:
    with pytest.raises(InputRequired) as exc_info:
        RecordingUI(interactive=False).confirm("Sure?", fix="Pass --yes.")
    assert exc_info.value.hint == "Pass --yes."
