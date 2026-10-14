"""``zabgrab token`` end to end, against fake servers and an in-memory keyring."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from zabgrab.cli import cli
from zabgrab.errors import EXIT_ERROR, EXIT_INTERRUPTED

from .cli_support import CONFIG, Servers, run, table_rows
from .fakezabbix import TOKEN, seeded

DEMO = "https://demo.example.org"
LAB = "https://lab.example.org"
OTHER = "https://other.example.org"

hidden_input = pytest.mark.filterwarnings("ignore::getpass.GetPassWarning")


class TestList:
    def test_statuses(self, servers: Servers) -> None:
        servers.secrets.tokens[DEMO] = TOKEN
        servers.secrets.tokens[OTHER] = TOKEN
        result = CliRunner(env={"COLUMNS": "200", "LAB_TOKEN": "x"}).invoke(
            cli, ["--no-color", "token", "list"]
        )
        assert result.exit_code == 0, result.output
        assert table_rows(result.stdout) == [
            ["demo", DEMO, "stored", "$DEMO_TOKEN (not set)"],
            ["lab", LAB, "-", "$LAB_TOKEN (set)"],
            ["", OTHER, "stored", ""],
        ]
        assert TOKEN not in result.output

    def test_check(self, servers: Servers, user_config: Path) -> None:
        user_config.write_text(
            CONFIG + '\n[servers.none]\nurl = "https://none.example.org"\n'
            '[servers.down]\nurl = "https://down.example.org"\n'
        )
        servers.secrets.tokens[DEMO] = TOKEN
        servers.secrets.tokens["https://down.example.org"] = TOKEN
        servers.by_url["https://down.example.org"] = down = seeded()
        down.errors["apiinfo.version"] = {
            "code": -1,
            "message": "Down for maintenance.",
        }
        result = CliRunner(env={"COLUMNS": "250", "LAB_TOKEN": "nope"}).invoke(
            cli, ["--no-color", "token", "list", "--check"]
        )
        assert result.exit_code == 0, result.output
        checks = [row[-1] for row in table_rows(result.stdout)]
        assert checks == [
            "valid from your keyring",
            "rejected from $LAB_TOKEN",
            "missing",
            "error Zabbix API error in apiinfo.version: Down for maintenance",
        ]

    def test_nothing_to_list(self, servers: Servers) -> None:
        result = run("--isolated", "token", "list")
        assert result.exit_code == 0
        assert result.stdout == ""
        assert "No servers are configured and no tokens are stored" in result.stderr


class TestAdd:
    def test_from_stdin(self, servers: Servers) -> None:
        result = run("token", "add", "demo", "-", input=TOKEN + "\n", tokens=False)
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {DEMO: TOKEN}
        assert "Saved the token for 'demo' in your keyring." in result.stderr
        assert "Create an API token" not in result.stderr

    def test_from_stdin_literal_url(self, servers: Servers) -> None:
        result = run("token", "add", OTHER, "-", input=TOKEN, tokens=False)
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {OTHER: TOKEN}

    def test_empty_stdin(self, servers: Servers) -> None:
        result = run("token", "add", "demo", "-", input="\n", tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert "No token found in standard input." in result.stderr

    def test_rejected(self, servers: Servers) -> None:
        result = run("token", "add", "demo", "-", input="nope", tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert "Session terminated" in result.stderr
        assert "--no-check" in result.stderr
        assert servers.secrets.tokens == {}

    def test_no_check(self, servers: Servers) -> None:
        result = run(
            "token", "add", "demo", "-", "--no-check", input="nope", tokens=False
        )
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {DEMO: "nope"}
        assert servers.by_url == {}

    def test_non_interactive_without_stdin(self, servers: Servers) -> None:
        result = run("token", "add", "demo", tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert "Pass the token on standard input with '-'" in result.stderr
        assert "zabgrab token add demo -`" in result.stderr

    def test_non_interactive_no_check_without_stdin(self, servers: Servers) -> None:
        result = run("token", "add", "demo", "--no-check", tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert "Pass the token on standard input with '-'" in result.stderr

    @hidden_input
    def test_interactive(self, servers: Servers, interactive: None) -> None:
        result = run("token", "add", input="2\nnope\n" + TOKEN + "\n", tokens=False)
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {LAB: TOKEN}
        assert f"{LAB}/zabbix.php?action=user.token.list" in result.stderr
        assert "That token was rejected" in result.stderr

    @hidden_input
    def test_interactive_no_check(self, servers: Servers, interactive: None) -> None:
        result = run("token", "add", "demo", "--no-check", input="abc\n", tokens=False)
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {DEMO: "abc"}

    def test_environment_variable_takes_precedence(self, servers: Servers) -> None:
        result = run("token", "add", "demo", "-", input=TOKEN)
        assert result.exit_code == 0, result.output
        assert "$DEMO_TOKEN is set and takes precedence" in result.stderr

    def test_password_server_warning(self, servers: Servers, user_config: Path) -> None:
        user_config.write_text(
            '[servers.pw]\nurl = "https://pw.example.org"\nauth = "password"\n'
        )
        result = run("token", "add", "pw", "-", input=TOKEN, tokens=False)
        assert result.exit_code == 0, result.output
        assert 'configured with auth = "password"' in result.stderr

    def test_unknown_server(self, servers: Servers) -> None:
        result = run("token", "add", "dmeo", "-", input=TOKEN, tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert "Unknown server 'dmeo'" in result.stderr

    def test_dash_alone_reads_stdin(self, servers: Servers, interactive: None) -> None:
        result = run("token", "add", "-", input=TOKEN + "\n", tokens=False)
        # The server is still asked for; the token isn't.
        assert "Which Zabbix server?" in result.stderr
        assert "API token" not in result.stderr

    def test_from_file(self, servers: Servers, tmp_path: Path) -> None:
        path = tmp_path / "token.txt"
        path.write_text(TOKEN + "\nignored\n")
        result = run("token", "add", "demo", str(path), tokens=False)
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {DEMO: TOKEN}

    def test_empty_file(self, servers: Servers, tmp_path: Path) -> None:
        path = tmp_path / "token.txt"
        path.write_text("")
        result = run("token", "add", "demo", str(path), tokens=False)
        assert result.exit_code == EXIT_ERROR
        assert f"No token found in {path}." in result.stderr

    def test_missing_file(self, servers: Servers, tmp_path: Path) -> None:
        result = run("token", "add", "demo", str(tmp_path / "nope"), tokens=False)
        assert result.exit_code == 2
        assert "No such file" in result.stderr


class TestReset:
    def test_with_yes(self, servers: Servers) -> None:
        servers.secrets.tokens |= {DEMO: TOKEN, OTHER: TOKEN}
        result = run("token", "reset", "--yes")
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {}
        assert "Deleted 2 tokens; zabgrab has nothing left" in result.stderr

    def test_single(self, servers: Servers, interactive: None) -> None:
        servers.secrets.tokens[DEMO] = TOKEN
        result = run("token", "reset", input="y\n")
        assert result.exit_code == 0, result.output
        assert "Delete token stored by zabgrab" in result.stderr
        assert "Deleted 1 token;" in result.stderr

    def test_interactive_decline(self, servers: Servers, interactive: None) -> None:
        servers.secrets.tokens |= {DEMO: TOKEN, LAB: TOKEN}
        result = run("token", "reset", input="n\n")
        assert result.exit_code == EXIT_INTERRUPTED
        assert "Delete all 2 tokens" in result.stderr
        assert len(servers.secrets.tokens) == 2

    def test_non_interactive_needs_yes(self, servers: Servers) -> None:
        servers.secrets.tokens[DEMO] = TOKEN
        result = run("token", "reset")
        assert result.exit_code == EXIT_ERROR
        assert "Pass --yes" in result.stderr

    def test_nothing_stored(self, servers: Servers) -> None:
        result = run("token", "reset")
        assert result.exit_code == 0
        assert "No tokens were stored" in result.stderr


class TestDelete:
    def test_with_yes(self, servers: Servers) -> None:
        servers.secrets.tokens |= {DEMO: TOKEN, LAB: TOKEN}
        result = run("token", "delete", "demo", "--yes")
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {LAB: TOKEN}
        assert "Deleted the token for 'demo'." in result.stderr

    def test_literal_url(self, servers: Servers) -> None:
        servers.secrets.tokens[OTHER] = TOKEN
        assert run("token", "delete", OTHER, "-y").exit_code == 0
        assert servers.secrets.tokens == {}

    def test_nothing_stored(self, servers: Servers) -> None:
        result = run("token", "delete", "demo", "--yes")
        assert result.exit_code == EXIT_ERROR
        assert "No token is stored for 'demo'." in result.stderr

    def test_non_interactive_needs_yes(self, servers: Servers) -> None:
        servers.secrets.tokens[DEMO] = TOKEN
        result = run("token", "delete", "demo")
        assert result.exit_code == EXIT_ERROR
        assert "Pass --yes" in result.stderr
        assert servers.secrets.tokens == {DEMO: TOKEN}

    def test_interactive_confirm(self, servers: Servers, interactive: None) -> None:
        servers.secrets.tokens[DEMO] = TOKEN
        result = run("token", "delete", "demo", input="y\n")
        assert result.exit_code == 0, result.output
        assert servers.secrets.tokens == {}

    def test_interactive_decline(self, servers: Servers, interactive: None) -> None:
        servers.secrets.tokens[DEMO] = TOKEN
        result = run("token", "delete", "demo", input="n\n")
        assert result.exit_code == EXIT_INTERRUPTED
        assert "Cancelled." in result.stderr
        assert servers.secrets.tokens == {DEMO: TOKEN}

    def test_choose(self, servers: Servers, interactive: None) -> None:
        servers.secrets.tokens |= {LAB: TOKEN, OTHER: TOKEN}
        result = run("token", "delete", input="2\ny\n")
        assert result.exit_code == 0, result.output
        assert "1. lab" in result.stderr
        assert f"2. {OTHER}" in result.stderr
        assert servers.secrets.tokens == {LAB: TOKEN}

    def test_choose_non_interactive(self, servers: Servers) -> None:
        servers.secrets.tokens[LAB] = TOKEN
        result = run("token", "delete")
        assert result.exit_code == EXIT_ERROR
        assert "zabgrab token delete NAME" in result.stderr

    def test_choose_with_nothing_stored(self, servers: Servers) -> None:
        result = run("token", "delete")
        assert result.exit_code == EXIT_ERROR
        assert "No tokens are stored in your keyring." in result.stderr
