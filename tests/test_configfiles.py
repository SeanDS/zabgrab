"""Config file discovery, loading and layering."""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from zabgrab.adapters import configfiles
from zabgrab.adapters.configfiles import (
    BUILTIN_SOURCE,
    Environment,
    LayerKind,
    add_server,
    discover_layers,
    find_project_config,
    load_config,
    user_config_path,
)
from zabgrab.errors import ConfigError
from zabgrab.model.config import BUILTIN_DEFAULTS, Config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestUserConfigPath:
    def test_defaults_to_dot_config(self, env: Environment) -> None:
        assert user_config_path(env) == env.home / ".config/zabgrab/config.toml"

    def test_respects_xdg_config_home(self, env: Environment, tmp_path: Path) -> None:
        env = replace(env, variables={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        assert user_config_path(env) == tmp_path / "xdg/zabgrab/config.toml"

    def test_ignores_relative_xdg_config_home(self, env: Environment) -> None:
        env = replace(env, variables={"XDG_CONFIG_HOME": "relative"})
        assert user_config_path(env) == env.home / ".config/zabgrab/config.toml"

    def test_windows_uses_appdata(self, env: Environment) -> None:
        env = replace(env, windows=True, variables={"APPDATA": "/appdata"})
        assert user_config_path(env) == Path("/appdata/zabgrab/config.toml")

    def test_windows_respects_xdg_config_home(
        self, env: Environment, tmp_path: Path
    ) -> None:
        env = replace(
            env,
            windows=True,
            variables={"APPDATA": "/appdata", "XDG_CONFIG_HOME": str(tmp_path)},
        )
        assert user_config_path(env) == tmp_path / "zabgrab/config.toml"


class TestSystemConfigPath:
    # The autouse fixture patches the module attribute; test the original.
    original = staticmethod(configfiles.system_config_path)

    def test_posix(self, env: Environment) -> None:
        assert self.original(env) == Path("/etc/zabgrab/config.toml")

    def test_windows(self, env: Environment) -> None:
        env = replace(env, windows=True, variables={"PROGRAMDATA": "/pd"})
        assert self.original(env) == Path("/pd/zabgrab/config.toml")


class TestProjectConfig:
    def test_found_in_cwd(self, env: Environment) -> None:
        path = write(env.cwd / "zabgrab.toml", "")
        assert find_project_config(env) == path

    def test_found_in_parent(self, env: Environment) -> None:
        path = write(env.cwd / "zabgrab.toml", "")
        nested = env.cwd / "a" / "b"
        nested.mkdir(parents=True)
        assert find_project_config(replace(env, cwd=nested)) == path

    def test_nearest_wins(self, env: Environment) -> None:
        write(env.cwd / "zabgrab.toml", "")
        nearer = write(env.cwd / "a" / "zabgrab.toml", "")
        assert find_project_config(replace(env, cwd=env.cwd / "a")) == nearer

    def test_search_stops_at_home(self, env: Environment) -> None:
        write(env.home / "zabgrab.toml", "")
        project = env.home / "project"
        project.mkdir()
        assert find_project_config(replace(env, cwd=project)) is None

    def test_not_found(self, env: Environment) -> None:
        assert find_project_config(env) is None


class TestDiscoverLayers:
    def test_order_and_existence(self, env: Environment) -> None:
        write(user_config_path(env), "")
        layers = discover_layers(env)
        assert [layer.kind for layer in layers] == [
            LayerKind.BUILTIN,
            LayerKind.SYSTEM,
            LayerKind.USER,
            LayerKind.PROJECT,
        ]
        assert [layer.active for layer in layers] == [True, False, True, False]

    def test_explicit_is_last_and_relative_to_cwd(self, env: Environment) -> None:
        write(env.cwd / "extra.toml", "")
        layer = discover_layers(env, explicit=Path("extra.toml"))[-1]
        assert layer.kind is LayerKind.EXPLICIT
        assert layer.path == env.cwd / "extra.toml"
        assert layer.exists

    def test_isolated_skips_files(self, env: Environment) -> None:
        write(user_config_path(env), "")
        write(env.cwd / "zabgrab.toml", "")
        layers = discover_layers(env, isolated=True, explicit=Path("x.toml"))
        skipped = {layer.kind for layer in layers if layer.skipped}
        assert skipped == {LayerKind.SYSTEM, LayerKind.USER, LayerKind.PROJECT}
        assert layers[0].active


class TestLoadConfig:
    def test_builtin_only(self, env: Environment) -> None:
        loaded = load_config(discover_layers(env))
        assert loaded.config.defaults.from_ == BUILTIN_DEFAULTS["from"]
        assert loaded.source_of("defaults", "from") == BUILTIN_SOURCE

    def test_precedence(self, env: Environment, tmp_path: Path) -> None:
        system = write(
            configfiles.system_config_path(env),
            '[defaults]\njobs = 1\nformat = "csv"\ntimezone = "UTC"\n',
        )
        user = write(user_config_path(env), '[defaults]\njobs = 2\nformat = "tsv"\n')
        project = write(env.cwd / "zabgrab.toml", "[defaults]\njobs = 3\n")
        explicit = write(tmp_path / "explicit.toml", "[defaults]\nmax_items = 9\n")

        loaded = load_config(discover_layers(env, explicit=explicit))
        defaults = loaded.config.defaults

        assert (defaults.jobs, defaults.format, defaults.timezone) == (3, "tsv", "UTC")
        assert defaults.max_items == 9
        assert defaults.output_dir == BUILTIN_DEFAULTS["output_dir"]
        assert loaded.source_of("defaults", "jobs") == str(project)
        assert loaded.source_of("defaults", "format") == str(user)
        assert loaded.source_of("defaults", "timezone") == str(system)
        assert loaded.source_of("defaults", "max_items") == str(explicit)

    def test_named_entries_are_replaced_whole(self, env: Environment) -> None:
        write(
            user_config_path(env),
            '[templates.t]\nfrom = "2 days ago"\nitems = [{ itemid = 1 }]\n'
            "[templates.other]\nitems = [{ itemid = 2 }]\n"
            '[servers.s]\nurl = "https://a"\nverify_ssl = false\n',
        )
        project = write(
            env.cwd / "zabgrab.toml",
            '[templates.t]\nitems = [{ itemid = 3 }]\n[servers.s]\nurl = "https://b"\n',
        )

        loaded = load_config(discover_layers(env))
        template = loaded.config.templates["t"]

        assert template.from_ is None  # not merged from the user's version
        assert [item.itemid for item in template.items] == [3]
        assert loaded.source_of("templates", "t") == str(project)
        assert "other" in loaded.config.templates
        assert loaded.config.servers["s"].verify_ssl is True

    def test_isolated_ignores_files(self, env: Environment) -> None:
        write(user_config_path(env), "[defaults]\njobs = 2\n")
        loaded = load_config(discover_layers(env, isolated=True))
        assert loaded.config.defaults.jobs == BUILTIN_DEFAULTS["jobs"]

    def test_toml_syntax_error(self, env: Environment) -> None:
        path = write(user_config_path(env), "[defaults]\njobs = \n")
        with pytest.raises(ConfigError) as exc_info:
            load_config(discover_layers(env))
        err = exc_info.value
        assert err.path == path
        assert err.message == f"Invalid TOML in config file {path}."
        assert "line 2" in err.details[0]
        assert err.hint is not None
        assert "zabgrab config edit --target user" in err.hint

    def test_schema_error_points_at_file(self, env: Environment) -> None:
        write(user_config_path(env), "[defaults]\njobs = 2\n")
        project = write(env.cwd / "zabgrab.toml", "[defaults]\njobs = 0\n")
        with pytest.raises(ConfigError) as exc_info:
            load_config(discover_layers(env))
        assert exc_info.value.path == project
        assert exc_info.value.details[0].startswith("defaults.jobs:")

    def test_missing_explicit_file(self, env: Environment) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            load_config(discover_layers(env, explicit=Path("nope.toml")))

    def test_unreadable_file(self, env: Environment) -> None:
        path = user_config_path(env)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(ConfigError, match="Can't read config file"):
            load_config(discover_layers(env))


class TestAddServer:
    def test_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "new" / "config.toml"
        add_server(path, "demo", "https://demo.example.org")
        loaded = Config.model_validate(tomllib.loads(path.read_text()))
        assert loaded.servers["demo"].url == "https://demo.example.org"

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        original = (
            "# My zabgrab config.\n\n"
            "[defaults]\njobs   =   2  # aligned on purpose\n\n"
            '[servers.old]\nurl = "https://old.example.org"\n'
        )
        path = write(tmp_path / "config.toml", original)
        add_server(path, "new", "https://new.example.org")
        text = path.read_text()
        assert text.startswith(original)
        assert '[servers.new]\nurl = "https://new.example.org"\n' in text
        assert set(tomllib.loads(text)["servers"]) == {"old", "new"}

    def test_existing_server(self, tmp_path: Path) -> None:
        path = write(tmp_path / "config.toml", '[servers.a]\nurl = "https://a"\n')
        with pytest.raises(ConfigError, match="already exists"):
            add_server(path, "a", "https://b")

    def test_servers_not_a_table(self, tmp_path: Path) -> None:
        path = write(tmp_path / "config.toml", "servers = 1\n")
        with pytest.raises(ConfigError, match="isn't a table"):
            add_server(path, "a", "https://b")

    def test_invalid_toml(self, tmp_path: Path) -> None:
        path = write(tmp_path / "config.toml", "a = [")
        with pytest.raises(ConfigError, match="Can't update"):
            add_server(path, "a", "https://b")

    def test_unwritable(self, tmp_path: Path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir(mode=0o500)
        try:
            with pytest.raises(ConfigError, match="Can't write"):
                add_server(locked / "config.toml", "a", "https://b")
        finally:
            locked.chmod(0o700)

    def test_failed_replace_removes_temporary_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*args: object) -> None:
            raise OSError("disk full")

        directory = tmp_path / "config"
        monkeypatch.setattr("os.replace", fail)
        with pytest.raises(ConfigError, match="Can't write"):
            add_server(directory / "config.toml", "a", "https://b")
        assert list(directory.iterdir()) == []
