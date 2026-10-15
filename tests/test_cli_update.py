"""``zabgrab update`` end to end."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import h5py
import pytest

from zabgrab.adapters.formats.hdf5 import read_fetches, read_metadata, write_fetch
from zabgrab.errors import EXIT_ERROR, EXIT_PARTIAL
from zabgrab.model.series import FetchResult

from .cli_support import CONFIG, NOW, Servers, run, table_rows
from .fakezabbix import T0, FakeItem, FakeZabbix, seeded
from .test_cli_fetch import TEMPLATES, TIMES, ok

DEMO = "https://demo.example.org"
LATER = datetime.fromtimestamp(T0 + 5000, UTC)


@pytest.fixture
def zabbix(servers: Servers, user_config: Path) -> FakeZabbix:
    user_config.write_text(CONFIG + TEMPLATES)
    servers.by_url[DEMO] = server = seeded()
    return server


@pytest.fixture(params=[".mat", ".h5"])
def data(request: pytest.FixtureRequest, zabbix: FakeZabbix, tmp_path: Path) -> Path:
    """A file with one fetch of the particles template, ending at NOW."""
    path = tmp_path / f"data{request.param}"
    ok(run("fetch", "-t", "particles", "-o", str(path)))
    return path


def later(servers: Servers) -> None:
    servers.clock.time = LATER


def test_appends_new_data(data: Path, zabbix: FakeZabbix, servers: Servers) -> None:
    zabbix.add_history(20001, (T0 + 3600, 4.5), (T0 + 4000, 5.5))
    later(servers)
    result = ok(run("update", str(data)))
    assert f"Added 2 points for 2 items to {data} (group fetch_0002)." in result.stderr

    first, second = read_fetches(data)
    assert second.t_from == pytest.approx(first.t_to + 1e-6)
    assert second.t_to == LATER.timestamp()
    assert [s.points for s in second.series] == [2, 0]
    assert second.series[0].requests == ("particles",)
    assert second.metadata["update_of"] == "fetch_0001"
    assert second.metadata["query"]["templates"] == ["particles"]
    assert second.metadata["query"]["sources"]["from"] == "end of group 'fetch_0001'"
    assert second.metadata["templates"] == first.metadata["templates"]


def test_warns_beyond_history_retention(
    data: Path, zabbix: FakeZabbix, servers: Servers
) -> None:
    zabbix.items[0].history = "10m"
    later(servers)
    result = ok(run("update", str(data)))
    assert "Particles 0.3um) keeps raw values for 10m" in result.stderr


def test_up_to_date(data: Path) -> None:
    result = ok(run("update", str(data)))
    assert "is already up to date" in result.stderr
    assert [name for name, _ in read_metadata(data)] == ["fetch_0001"]


def test_no_new_data(data: Path, servers: Servers) -> None:
    later(servers)
    result = ok(run("update", str(data)))
    assert "No new data since the last fetch" in result.stderr
    assert [name for name, _ in read_metadata(data)] == ["fetch_0001"]


def test_to(data: Path, zabbix: FakeZabbix, servers: Servers) -> None:
    zabbix.add_history(20001, (T0 + 3600, 4.5), (T0 + 4000, 5.5))
    later(servers)
    ok(run("update", str(data), "--to", "2025-10-09T09:05:00Z"))
    assert read_fetches(data)[1].series[0].columns["value"] == [4.5]


def test_dry_run(data: Path, servers: Servers) -> None:
    later(servers)
    result = ok(run("update", str(data), "--dry-run"))
    assert [row[0] for row in table_rows(result.stdout)] == ["20001", "20002"]
    assert [name for name, _ in read_metadata(data)] == ["fetch_0001"]


def test_rematch_picks_up_new_items(
    data: Path, zabbix: FakeZabbix, servers: Servers
) -> None:
    zabbix.items.append(FakeItem(20050, 10001, "Particles 1.0um", "particles[1.0]", 0))
    zabbix.add_history(20050, (T0 + 3600, 1.0))
    zabbix.add_history(20001, (T0 + 3600, 4.5), (T0 + 5500, 6.5))
    later(servers)
    ok(run("update", str(data)))
    assert [s.item.itemid for s in read_fetches(data)[1].series] == [20001, 20002]
    later(servers)
    servers.clock.time = datetime.fromtimestamp(T0 + 6000, UTC)
    ok(run("update", str(data), "--rematch"))
    assert [s.item.itemid for s in read_fetches(data)[2].series] == [
        20001,
        20002,
        20050,
    ]


def test_deleted_item(data: Path, zabbix: FakeZabbix, servers: Servers) -> None:
    zabbix.items = [item for item in zabbix.items if item.itemid != 20002]
    later(servers)
    result = ok(run("update", str(data)))
    assert "Item 20002 no longer exists on 'demo'." in result.stderr


def test_server_removed_from_config(
    data: Path, servers: Servers, user_config: Path
) -> None:
    user_config.write_text(CONFIG.replace("[servers.demo]", "[servers.renamed]"))
    servers.secrets.tokens[DEMO] = "valid-token"
    later(servers)
    result = ok(run("update", str(data), "--dry-run"))
    assert f"Server 'demo' isn't in your config any more; using {DEMO} instead." in (
        result.stderr
    )


def test_server_override(data: Path, servers: Servers) -> None:
    later(servers)
    ok(run("update", str(data), "--server", "lab", "--dry-run"))
    assert "https://lab.example.org" in servers.by_url


def test_partial_failure(data: Path, zabbix: FakeZabbix, servers: Servers) -> None:
    zabbix.add_history(20001, (T0 + 3600, 4.5))
    zabbix.failing_items.add(20002)
    later(servers)
    assert run("update", str(data)).exit_code == EXIT_PARTIAL


def test_everything_failed(data: Path, zabbix: FakeZabbix, servers: Servers) -> None:
    zabbix.failing_items |= {20001, 20002}
    later(servers)
    result = run("update", str(data))
    assert result.exit_code == EXIT_ERROR
    assert "Nothing was fetched." in result.stderr


def test_no_items_left(data: Path, zabbix: FakeZabbix, servers: Servers) -> None:
    zabbix.items = []
    later(servers)
    result = run("update", str(data))
    assert result.exit_code == EXIT_ERROR
    assert "No items to update." in result.stderr


def test_trends_continue_from_the_last_complete_hour(
    zabbix: FakeZabbix, servers: Servers, tmp_path: Path
) -> None:
    path = tmp_path / "trends.h5"
    ok(
        run(
            "fetch",
            "-s",
            "demo",
            "-i",
            "20001",
            "--resolution",
            "1h",
            *TIMES,
            "-o",
            str(path),
        )
    )
    (first,) = read_fetches(path)
    # NOW is 08:58:20 UTC, so the 08:00 hour (T0) wasn't complete yet.
    assert first.t_to == T0 - 1
    later(servers)
    ok(run("update", str(path)))
    # The update asks for that hour again, along with the next.
    assert read_fetches(path)[1].series[0].columns["time"] == [T0, T0 + 3600]


def test_large_text(
    zabbix: FakeZabbix, servers: Servers, user_config: Path, tmp_path: Path
) -> None:
    path = tmp_path / "logs.mat"
    ok(run("fetch", "-t", "logs", "-o", str(path)))
    user_config.write_text(
        CONFIG + TEMPLATES + "[defaults]\nmat_text_warn_entries = 2\n"
    )
    zabbix.add_log(
        20005, T0 + 3600, "new", timestamp=0, source="", severity=0, logeventid=0
    )
    later(servers)
    result = ok(run("update", str(path)))
    assert "text values, more than mat_text_warn_entries" in result.stderr


class TestErrors:
    def test_delimited_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.csv"
        path.write_text("x")
        result = run("update", str(path))
        assert result.exit_code == EXIT_ERROR
        assert "only .mat and .h5 files can be updated" in result.stderr

    def test_empty_file(self, zabbix: FakeZabbix, tmp_path: Path) -> None:
        path = tmp_path / "empty.h5"
        h5py.File(path, "w").close()
        result = run("update", str(path))
        assert result.exit_code == EXIT_ERROR
        assert "has no data to update" in result.stderr

    def test_missing_metadata(self, data: Path) -> None:
        with h5py.File(data, "a") as file:
            group = file["fetch_0001"]
            del group["meta"]
            # Readable (see test_formats_hdf5), but without the query or items.
            meta = '{"zabgrab": {"format_version": 1}}'
            group.create_dataset("meta", data=meta, dtype=h5py.string_dtype())
        result = run("update", str(data))
        assert result.exit_code == EXIT_ERROR
        assert "doesn't have the metadata needed to update it" in result.stderr

    def test_newer_file_format(self, data: Path) -> None:
        (group,) = read_fetches(data)
        newer = {**group.metadata, "zabgrab": {"format_version": 99}}
        with h5py.File(data, "a") as file:
            del file["fetch_0001"]["meta"]
            file["fetch_0001"].create_dataset(
                "meta", data=json.dumps(newer), dtype=h5py.string_dtype()
            )
        for command in (["update"], ["plot"], ["export", "-o", "out.h5"]):
            result = run(*command, str(data))
            assert result.exit_code == EXIT_ERROR
            assert "was written by a newer version of zabgrab" in result.stderr
            assert "Fix: Upgrade zabgrab: pip install --upgrade zabgrab" in (
                result.stderr
            )

    def test_invalid_to(self, data: Path) -> None:
        result = run("update", str(data), "--to", "garbage")
        assert result.exit_code == 2
        assert "Invalid value for --to" in result.stderr


def test_now_is_the_test_clock() -> None:
    assert NOW.timestamp() == T0 + 3500


def test_unreachable_server(data: Path, servers: Servers) -> None:
    servers.down.add(DEMO)
    later(servers)
    result = run("update", str(data))
    assert result.exit_code == EXIT_ERROR
    assert "Can't connect" in result.stderr


def test_rematch_with_several_servers(
    zabbix: FakeZabbix, servers: Servers, tmp_path: Path
) -> None:
    # Fetches used to be able to use several servers; such groups can still be
    # updated, but not looked up again.
    single, both = tmp_path / "single.h5", tmp_path / "both.h5"
    ok(run("fetch", "-s", "demo", "-i", "20004", *TIMES, "-o", str(single)))
    (group,) = read_fetches(single)
    metadata = group.metadata
    lab = "https://lab.example.org"
    metadata["items"].append({**metadata["items"][0], "server": lab})
    metadata["servers"].append({"name": "lab", "url": lab})
    write_fetch(
        both,
        FetchResult(series=group.series),
        metadata,
        mat=False,
        t_from=group.t_from,
        t_to=group.t_to,
    )
    later(servers)
    result = run("update", str(both), "--rematch", "--dry-run")
    assert result.exit_code == EXIT_ERROR
    assert "was fetched from several servers" in result.stderr
    result = ok(run("update", str(both), "--dry-run"))
    assert [row[0] for row in table_rows(result.stdout)] == ["demo", "lab"]
    result = run("update", str(both), "--server", "demo", "--dry-run")
    assert result.exit_code == EXIT_ERROR
    assert "can't be updated from one other server" in result.stderr


def test_server_chosen_when_fetched(
    zabbix: FakeZabbix,
    servers: Servers,
    user_config: Path,
    tmp_path: Path,
    interactive: None,
) -> None:
    user_config.write_text(
        CONFIG
        + '[templates.either]\nserver = { choices = ["lab", "demo"] }\n'
        + "items = [{ itemid = 20001 }]\n",
        encoding="utf-8",
    )
    path = tmp_path / "chosen.h5"
    ok(run("fetch", "-t", "either", *TIMES, "-o", str(path), input="2\n"))
    later(servers)
    for rematch in ([], ["--rematch"]):
        result = ok(run("update", str(path), *rematch, "--dry-run"))
        assert "Which Zabbix server?" not in result.stderr
        assert [row[0] for row in table_rows(result.stdout)] == ["20001"]

    # A group that found no items has no server to go back to: ask.
    (group,) = read_fetches(path)
    empty = tmp_path / "empty.h5"
    write_fetch(
        empty,
        FetchResult(),
        group.metadata | {"items": [], "servers": []},
        mat=False,
        t_from=group.t_from,
        t_to=group.t_to,
    )
    result = ok(run("update", str(empty), "--rematch", "--dry-run", input="2\n"))
    assert "Which Zabbix server?" in result.stderr
    assert [row[0] for row in table_rows(result.stdout)] == ["20001"]


def test_rematch_with_server_override(data: Path, servers: Servers) -> None:
    later(servers)
    ok(run("update", str(data), "--rematch", "--server", "lab", "--dry-run"))
    assert "https://lab.example.org" in servers.by_url


def test_server_given_by_url(
    zabbix: FakeZabbix, servers: Servers, tmp_path: Path
) -> None:
    other = "https://other.example.org"
    servers.secrets.tokens[other] = "valid-token"
    path = tmp_path / "other.h5"
    ok(run("fetch", "-s", other, "-i", "20004", *TIMES, "-o", str(path)))
    later(servers)
    result = ok(run("update", str(path), "--dry-run"))
    assert "isn't in your config" not in result.stderr
