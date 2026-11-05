"""``zabgrab migrate``, and how update, info, plot and export follow migrations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h5py
import pytest

from zabgrab.adapters.formats.hdf5 import read_fetches, write_fetch
from zabgrab.errors import EXIT_ERROR
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import FetchResult, Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.dataset import merge

from .cli_support import CONFIG, Servers, run, table_rows
from .fakes import RecordingUI
from .fakezabbix import T0, TOKEN, FakeHost, FakeItem, FakeZabbix, seeded
from .test_cli_fetch import TEMPLATES, ok

DEMO = "https://demo.example.org"
LAB = "https://lab.example.org"
THIRD = "https://third.example.org"
LATER = datetime.fromtimestamp(T0 + 5000, UTC)


def replacement(itemid: int = 90001, hostid: int = 50001) -> FakeZabbix:
    """A new server with demo1's 0.3um counter under a new ID, and no 0.5um one."""
    server = FakeZabbix(
        tokens={TOKEN},
        hosts=[
            FakeHost(hostid, "demo1", "DEMO 1"),
            FakeHost(hostid + 1, "lab9", "Lab 9"),
        ],
        items=[
            FakeItem(itemid, hostid, "Particles 0.3um", "particles[0.3]", 0, "#/m3"),
            FakeItem(itemid + 1, hostid + 1, "Particles 0.5um", "particles[0.5]", 0),
        ],
    )
    server.add_history(itemid, (T0 + 3600, 4.5), (T0 + 4000, 5.5))
    return server


@pytest.fixture
def zabbix(servers: Servers, user_config: Path) -> Servers:
    user_config.write_text(CONFIG + TEMPLATES)
    servers.by_url[DEMO] = seeded()
    servers.by_url[LAB] = replacement()
    return servers


@pytest.fixture(params=[".h5", ".mat"])
def data(request: pytest.FixtureRequest, zabbix: Servers, tmp_path: Path) -> Path:
    """Demo1's two particle counters (20001 and 20002), fetched from demo."""
    path = tmp_path / f"data{request.param}"
    ok(run("fetch", "-t", "particles", "-o", str(path)))
    return path


def items(path: Path, group: int = -1) -> list[tuple[str, int]]:
    """A group's items, as server URL and item ID."""
    return [(s.server.url, s.item.itemid) for s in read_fetches(path)[group].series]


def entries(path: Path, group: int = -1) -> dict[int, dict[str, Any]]:
    """A group's metadata item entries, by item ID."""
    metadata = read_fetches(path)[group].metadata
    return {entry["itemid"]: entry for entry in metadata["items"]}


class TestMoveServer:
    def test_moves_matched_items_and_retires_the_rest(self, data: Path) -> None:
        result = ok(run("migrate", str(data), "--server", "lab", "--yes"))
        assert table_rows(result.stdout) == [
            ["20001", "demo1: Particles 0.3um", "→ 90001 on 'lab' (matched)"],
            ["20002", "demo1: Particles 0.5um", "retire (not found on 'lab')"],
        ]
        assert "1 item will be retired: its data stays in the file" in result.stderr
        assert f"Changed {data}: moved 1 item to 'lab', retired 1." in result.stderr

        (group,) = read_fetches(data)
        moved, retired = group.series
        assert (moved.server.url, moved.server.name) == (LAB, "lab")
        assert (moved.item.itemid, moved.item.hostid) == (90001, 50001)
        assert moved.columns["value"] == [1.5, 2.5, 3.5]  # the data is untouched
        assert (retired.server.url, retired.item.itemid) == (DEMO, 20002)

        found = entries(data)
        (history,) = found[90001]["history"]
        assert history == {
            "server": DEMO,
            "server_name": "demo",
            "itemid": 20001,
            "hostid": 10001,
            "until": history["until"],
        }
        assert found[20002]["retired"] == {"at": history["until"]}
        metadata = group.metadata
        assert metadata["servers"] == [
            {"name": "demo", "url": DEMO},
            {"name": "lab", "url": LAB},
        ]
        assert metadata["query"]["requests"][0]["server"] == "lab"
        (log,) = metadata["migrations"]
        assert log["command"][1:4] == ["--no-color", "migrate", str(data)]
        assert log["servers"] == {
            "from": [{"name": "demo", "url": DEMO}],
            "to": {"name": "lab", "url": LAB},
        }
        assert log["moved"][0]["from"]["itemid"] == 20001
        assert log["moved"][0]["to"] == {
            "server": LAB,
            "server_name": "lab",
            "itemid": 90001,
            "hostid": 50001,
        }
        assert log["moved"][0]["how"] == "matched"
        assert log["retired"][0]["reason"] == "not found on 'lab'"

    def test_update_fetches_the_new_items_and_reads_as_one_series(
        self, data: Path, zabbix: Servers
    ) -> None:
        ok(run("migrate", str(data), "--server", "lab", "--yes"))
        zabbix.clock.time = LATER
        result = ok(run("update", str(data)))
        assert "Added 2 points for 1 item" in result.stderr
        assert items(data) == [(LAB, 90001)]
        # Histories and the log are carried into the new group.
        assert entries(data)[90001]["history"][0]["itemid"] == 20001
        assert len(read_fetches(data)[-1].metadata["migrations"]) == 1

        dataset = merge(read_fetches(data), RecordingUI())
        counter = next(s for s in dataset.series if s.item.itemid == 90001)
        assert counter.columns["value"] == [1.5, 2.5, 3.5, 4.5, 5.5]
        info = ok(run("info", str(data)))
        rows = table_rows(info.stdout)
        assert [row[:2] for row in rows] == [["lab", "90001"], ["demo", "20002"]]
        assert rows[1][6] == "raw, retired"

    def test_map_overrides_and_adds_to_matches(self, data: Path) -> None:
        result = ok(
            run("migrate", str(data), "-s", "lab", "--map", "20002=90002", "-y")
        )
        assert table_rows(result.stdout) == [
            ["20001", "demo1: Particles 0.3um", "→ 90001 on 'lab' (matched)"],
            ["20002", "demo1: Particles 0.5um", "→ 90002 on 'lab' (given)"],
        ]
        assert items(data) == [(LAB, 90001), (LAB, 90002)]
        assert "retired" not in result.stderr

    def test_map_file_and_no_match(self, data: Path, tmp_path: Path) -> None:
        mapping = tmp_path / "mapping.csv"
        mapping.write_text("old,new\n# the 0.5um counter moved hosts\n20002,90002\n")
        result = ok(
            run(
                "migrate",
                str(data),
                "-s",
                "lab",
                "--map-file",
                str(mapping),
                "--no-match",
                "-y",
            )
        )
        assert [row[2] for row in table_rows(result.stdout)] == [
            "→ 90002 on 'lab' (given)",
            "retire (not found on 'lab')",
        ]

    def test_items_with_the_same_id_are_unchanged(
        self, data: Path, zabbix: Servers
    ) -> None:
        zabbix.by_url[LAB] = replacement(itemid=20001, hostid=10001)
        result = ok(
            run("migrate", str(data), "-s", "lab", "--map", "20002=20002", "-y")
        )
        # The IDs are the same, but on another server: they still move.
        assert [row[2] for row in table_rows(result.stdout)] == [
            "→ 20001 on 'lab' (matched)",
            "→ 20002 on 'lab' (given)",
        ]
        again = ok(run("migrate", str(data), "-s", "lab", "--map", "20002=20002", "-y"))
        assert "2 items already have the same ID on 'lab'." in again.stderr
        assert f"Nothing to change in {data}." in again.stderr

    def test_several_migrations_keep_the_whole_history(
        self, data: Path, zabbix: Servers
    ) -> None:
        zabbix.by_url[THIRD] = replacement(itemid=70001, hostid=60001)
        zabbix.secrets.tokens[THIRD] = TOKEN
        ok(run("migrate", str(data), "-s", "lab", "-y"))
        ok(run("migrate", str(data), "-s", THIRD, "-y"))

        history = entries(data)[70001]["history"]
        assert [(h["server"], h["server_name"], h["itemid"]) for h in history] == [
            (DEMO, "demo", 20001),
            (LAB, "lab", 90001),
        ]
        log = read_fetches(data)[0].metadata["migrations"]
        assert [m["servers"]["to"]["url"] for m in log] == [LAB, THIRD]
        # The retired item stays where it was, and isn't retired again.
        assert log[1]["retired"] == []

        result = ok(run("info", str(data), "--migrations"))
        rows = table_rows(result.stdout)
        assert [row[1:] for row in rows] == [
            ["demo1: Particles 0.3um", "20001 on demo", "90001 on lab"],
            ["demo1: Particles 0.5um", "20002 on demo", "retired (not found on 'lab')"],
            ["demo1: Particles 0.3um", "90001 on lab", f"70001 on {THIRD}"],
        ]

    def test_dry_run_changes_nothing(self, data: Path) -> None:
        before = data.read_bytes()
        result = ok(run("migrate", str(data), "-s", "lab", "--dry-run"))
        assert len(table_rows(result.stdout)) == 2
        assert f"Dry run: {data} wasn't changed." in result.stderr
        assert data.read_bytes() == before

    @pytest.mark.usefixtures("interactive")
    def test_asks_first(self, data: Path) -> None:
        before = data.read_bytes()
        result = ok(run("migrate", str(data), "-s", "lab", input="n\n"))
        assert f"Rewrite {data} (1 group)?" in result.stderr
        assert f"{data} wasn't changed." in result.stderr
        assert data.read_bytes() == before
        ok(run("migrate", str(data), "-s", "lab", input="y\n"))
        assert items(data)[0] == (LAB, 90001)

    def test_needs_yes_when_it_cant_ask(self, data: Path) -> None:
        before = data.read_bytes()
        result = ok(run("migrate", str(data), "-s", "lab"))
        assert "pass --yes to change the file without being asked" in result.stderr
        assert data.read_bytes() == before

    def test_new_ids_must_exist(self, data: Path) -> None:
        result = run("migrate", str(data), "-s", "lab", "--map", "20002=12345")
        assert result.exit_code == EXIT_ERROR
        assert "'lab' has no item 12345." in result.stderr

    def test_two_items_cant_get_the_same_id(self, data: Path) -> None:
        result = run("migrate", str(data), "-s", "lab", "--map", "20002=90001")
        assert result.exit_code == EXIT_ERROR
        assert "Items 20001 and 20002 can't both become item 90001." in result.stderr


class TestSameServer:
    def test_map_changes_ids_on_the_same_server(
        self, data: Path, zabbix: Servers
    ) -> None:
        result = ok(run("migrate", str(data), "--map", "20002=20003", "-y"))
        assert table_rows(result.stdout) == [
            ["20002", "demo1: Particles 0.5um", "→ 20003 (given)"]
        ]
        assert f"Changed {data}: moved 1 item." in result.stderr
        assert items(data) == [(DEMO, 20001), (DEMO, 20003)]
        metadata = read_fetches(data)[0].metadata
        assert metadata["migrations"][0]["servers"] == {"from": [], "to": None}
        assert metadata["query"]["requests"][0]["server"] == "demo"

    def test_an_id_the_group_holds_is_refused(self, data: Path) -> None:
        result = run("migrate", str(data), "--map", "20002=20001")
        assert result.exit_code == EXIT_ERROR
        assert "Group 'fetch_0001' already holds item 20001" in result.stderr

    def test_ids_selected_by_the_template_follow(
        self, zabbix: Servers, tmp_path: Path
    ) -> None:
        path = tmp_path / "uptime.h5"
        ok(run("fetch", "-t", "uptime", "--to", "now", "-o", str(path)))
        result = ok(run("migrate", str(path), "--map", "20001=20003", "-y"))
        specs = read_fetches(path)[0].metadata["query"]["requests"][0]["items"]
        assert {"itemid": 20003} in specs
        assert (
            "Template 'uptime' in your config selects item 20001 by ID; change them "
            "to the new IDs"
        ) in result.stderr


class TestRetire:
    def test_retire_and_unretire_single_items(
        self, data: Path, zabbix: Servers
    ) -> None:
        zabbix.by_url[DEMO].calls.clear()
        result = ok(run("migrate", str(data), "--retire", "20002", "-y"))
        assert table_rows(result.stdout) == [
            ["20002", "demo1: Particles 0.5um", "retire (--retire)"]
        ]
        assert f"Changed {data}: retired 1." in result.stderr
        assert zabbix.by_url[DEMO].calls == []  # no server needed
        assert items(data) == [(DEMO, 20001), (DEMO, 20002)]  # nothing moved

        zabbix.by_url[DEMO].add_history(20001, (T0 + 3600, 4.5))
        zabbix.by_url[DEMO].add_history(20002, (T0 + 3600, 11.0))
        zabbix.clock.time = LATER
        ok(run("update", str(data)))
        assert items(data) == [(DEMO, 20001)]

        # The last group no longer lists it, so it's resumed from there.
        result = ok(run("migrate", str(data), "--unretire", "20002", "-y"))
        assert table_rows(result.stdout) == [
            ["20002", "demo1: Particles 0.5um", "bring back"]
        ]
        assert "retired" not in entries(data, 0)[20002]
        (resumed,) = read_fetches(data)[-1].metadata["resume"]
        assert resumed["itemid"] == 20002
        zabbix.by_url[DEMO].add_history(20002, (T0 + 5500, 12.0))
        zabbix.clock.time = datetime.fromtimestamp(T0 + 6000, UTC)
        ok(run("update", str(data)))
        assert items(data) == [(DEMO, 20001), (DEMO, 20002)]
        assert "resume" not in read_fetches(data)[-1].metadata
        rows = table_rows(ok(run("info", str(data), "--migrations")).stdout)
        assert [row[2:] for row in rows] == [
            ["20002 on demo", "retired (--retire)"],
            ["retired", "20002 on demo"],
        ]

    def test_items_brought_back_move_with_the_rest(self, data: Path) -> None:
        ok(run("migrate", str(data), "--retire", "20001", "-y"))
        result = ok(run("migrate", str(data), "-s", "lab", "--unretire", "20001", "-y"))
        assert [row[2] for row in table_rows(result.stdout)] == [
            "→ 90001 on 'lab' (matched)",
            "retire (not found on 'lab')",
            "bring back",
        ]
        assert "retired" not in entries(data)[90001]

    def test_unretire_before_any_update(self, data: Path) -> None:
        ok(run("migrate", str(data), "--retire", "20002", "-y"))
        ok(run("migrate", str(data), "--unretire", "20002", "-y"))
        assert "retired" not in entries(data)[20002]
        assert "resume" not in read_fetches(data)[-1].metadata

    def test_rematch_leaves_retired_items_out(
        self, data: Path, zabbix: Servers
    ) -> None:
        ok(run("migrate", str(data), "--retire", "20002", "-y"))
        zabbix.by_url[DEMO].add_history(20001, (T0 + 3600, 4.5))
        zabbix.clock.time = LATER
        result = ok(run("update", str(data), "--rematch"))
        assert "Leaving out 1 retired item (zabgrab migrate --unretire" in result.stderr
        assert items(data) == [(DEMO, 20001)]

    def test_export_keeps_records(self, data: Path, tmp_path: Path) -> None:
        ok(run("migrate", str(data), "-s", "lab", "-y"))
        out = tmp_path / "flat.h5"
        ok(run("export", str(data), "-o", str(out)))
        found = entries(out)
        assert found[90001]["history"][0]["itemid"] == 20001
        assert "retired" in found[20002]
        assert len(read_fetches(out)[0].metadata["migrations"]) == 1

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--retire", "20002", "--retire", "20002"], None),
            (["--retire", "99"], "--retire: the file has no item 99."),
            (
                ["--unretire", "20002"],
                "Item 20002 (demo1: Particles 0.5um) isn't retired.",
            ),
            (
                ["-s", "lab", "--map", "20002=90002", "--retire", "20002"],
                "Item 20002 can't be both mapped and retired.",
            ),
        ],
    )
    def test_checks(self, data: Path, args: list[str], message: str | None) -> None:
        result = run("migrate", str(data), *args, "-y")
        if message is None:
            assert result.exit_code == 0  # the same ID twice is one retirement
            result = run("migrate", str(data), *args, "-y")
            message = "Item 20002 (demo1: Particles 0.5um) is already retired."
        assert result.exit_code == EXIT_ERROR
        assert message in result.stderr


class TestUsage:
    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ([], "Nothing to do: give --server, --map, --map-file, --retire"),
            (["--no-match", "--retire", "1"], "--no-match only applies with --server."),
            (["--map", "1"], "'1' isn't OLD=NEW, two item IDs"),
            (["--map", "1=2", "--map", "1=3"], "Item 1 is given two new IDs."),
        ],
    )
    def test_usage_errors(self, data: Path, args: list[str], message: str) -> None:
        result = run("migrate", str(data), *args)
        assert result.exit_code == 2
        assert message in result.stderr

    def test_bad_map_file(self, data: Path, tmp_path: Path) -> None:
        mapping = tmp_path / "mapping.txt"
        mapping.write_text("20001 90001\n20002\n")
        result = run("migrate", str(data), "--map-file", str(mapping))
        assert result.exit_code == 2
        assert "Line 2 of" in result.stderr

    def test_the_same_mapping_twice_is_fine(self, data: Path) -> None:
        result = run(
            "migrate", str(data), "--map", "20002=20003", "--map", "20002=20003"
        )
        assert result.exit_code == 0

    def test_delimited_files_cant_be_migrated(self, tmp_path: Path) -> None:
        path = tmp_path / "data.csv"
        path.write_text("")
        result = run("migrate", str(path), "--retire", "1")
        assert result.exit_code == EXIT_ERROR
        assert "only .mat and .h5 files can be migrated" in result.stderr


def test_update_server_suggests_migrate(data: Path, zabbix: Servers) -> None:
    zabbix.clock.time = LATER
    result = run("update", str(data), "--server", "lab")
    assert "zabgrab migrate moves the file to their new IDs" in result.stderr


def test_info_without_migrations(data: Path) -> None:
    result = ok(run("info", str(data), "--migrations"))
    assert f"{data} hasn't been migrated." in result.stderr


def test_empty_file(zabbix: Servers, tmp_path: Path) -> None:
    path = tmp_path / "empty.h5"
    h5py.File(path, "w").close()
    result = run("migrate", str(path), "--retire", "1")
    assert result.exit_code == EXIT_ERROR
    assert "has no data to migrate" in result.stderr


def test_items_missing_from_their_host_are_retired(data: Path, zabbix: Servers) -> None:
    zabbix.by_url[LAB].hosts = [FakeHost(50001, "other", "Other")]
    result = ok(run("migrate", str(data), "-s", "lab", "--dry-run"))
    assert [row[2] for row in table_rows(result.stdout)] == [
        "retire (not found on 'lab')"
    ] * 2


def test_without_a_template(zabbix: Servers, tmp_path: Path) -> None:
    path = tmp_path / "custom.h5"
    ok(
        run(
            "fetch",
            "-s",
            "demo",
            "-i",
            "20001",
            "--from",
            "2 hours ago",
            "--to",
            "now",
            "-o",
            str(path),
        )
    )
    result = ok(run("migrate", str(path), "--map", "20001=20003", "-y"))
    assert "Template" not in result.stderr
    assert items(path) == [(DEMO, 20003)]


class TestTwoServers:
    """Files with items from two servers (as exported, or from before fetches used
    one server)."""

    @pytest.fixture
    def both(self, zabbix: Servers, tmp_path: Path) -> Path:
        path = tmp_path / "both.h5"
        series = []
        # Item 20001 on both, and one item only on each.
        for url, name, itemid in (
            (DEMO, "demo", 20001),
            (LAB, "lab", 20001),
            (DEMO, "demo", 20008),
            (LAB, "lab", 20009),
        ):
            item = Item(
                itemid,
                10001,
                "demo1",
                "DEMO 1",
                f"Counter {itemid} {name}",
                f"c[{itemid},{name}]",
                ValueType.FLOAT,
            )
            series.append(
                Series(
                    Server(name, ServerConfig(url=url)),
                    item,
                    Resolution.RAW,
                    {"time": [T0], "value": [1.0]},
                )
            )
        metadata = {
            "zabgrab": {"version": "1.1.1", "format_version": 1},
            "query": {"resolution": "raw", "requests": []},
            "servers": [{"name": "demo", "url": DEMO}, {"name": "lab", "url": LAB}],
            "items": [
                {
                    "server": s.server.url,
                    "itemid": s.item.itemid,
                    "host": "demo1",
                    "name": s.item.name,
                    "key": s.item.key,
                }
                for s in series
            ],
        }
        write_fetch(
            path,
            FetchResult(series=series),
            metadata,
            mat=False,
            t_from=T0,
            t_to=T0 + 1,
        )
        return path

    def test_an_id_on_both_is_ambiguous(self, both: Path) -> None:
        result = run("migrate", str(both), "--retire", "20001")
        assert result.exit_code == EXIT_ERROR
        assert "--retire: the file has item 20001 on several servers" in result.stderr

    def test_all_items_cant_move_to_one_server(self, both: Path) -> None:
        result = run("migrate", str(both), "-s", "lab")
        assert result.exit_code == EXIT_ERROR
        assert "The file's items come from several servers" in result.stderr

    def test_mapped_items_on_two_servers_need_one(self, both: Path) -> None:
        result = run("migrate", str(both), "--map", "20008=1", "--map", "20009=2")
        assert result.exit_code == EXIT_ERROR
        assert "The items given new IDs are on several servers" in result.stderr
