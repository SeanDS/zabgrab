"""Output file names, metadata and the TSV/CSV writer."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from zabgrab import __version__
from zabgrab.adapters.formats.delimited import write_delimited
from zabgrab.errors import ZabgrabError
from zabgrab.model.choice import Choice
from zabgrab.model.config import Agg, Format, Resolution, ServerConfig, Template
from zabgrab.model.item import ItemSpec
from zabgrab.model.query import Query, Request
from zabgrab.model.series import FetchResult, Series, Skipped
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType
from zabgrab.services.metadata import build_metadata
from zabgrab.services.naming import format_for, output_name, unique_path

START = datetime(2026, 10, 13, 10, 15, tzinfo=UTC)
END = datetime(2026, 10, 14, 10, 15, tzinfo=UTC)
DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))
LAB = Server("lab", ServerConfig(url="https://lab.example.org"))


def name(**kwargs: Any) -> str:
    args: dict[str, Any] = {
        "hosts": ["demo1"],
        "templates": ["particles"],
        "custom": False,
        "start": START,
        "end": END,
        "servers": ["demo"],
        "resolution": "raw",
    }
    args.update(kwargs)
    pattern = args.pop("pattern", "{hosts}_{templates}_{from}--{to}")
    return output_name(pattern, **args)


class TestOutputName:
    def test_default_pattern(self) -> None:
        assert name() == "demo1_particles_20261013T101500Z--20261014T101500Z"

    def test_hosts_sorted_and_unique(self) -> None:
        assert name(hosts=["demo2", "demo1", "demo2"]).startswith("demo1+demo2_")

    def test_many_hosts_are_shortened(self) -> None:
        assert name(hosts=["a", "b", "c", "d", "e"]).startswith("a+b+3more_")

    def test_no_hosts(self) -> None:
        assert name(hosts=[]).startswith("nohosts_")

    def test_several_templates(self) -> None:
        assert "_particles+temps_" in name(templates=["particles", "temps"])

    def test_custom(self) -> None:
        assert "_particles+__custom_" in name(custom=True)
        assert "___custom_" in name(templates=[], custom=True)
        assert "___custom_" in name(templates=[])

    def test_times_in_utc(self) -> None:
        local = START.astimezone(ZoneInfo("Europe/London"))
        assert "20261013T101500Z" in name(start=local)

    def test_other_placeholders(self) -> None:
        assert name(pattern="{server}-{resolution}", servers=["demo", "lab"]) == (
            "demo+lab-raw"
        )

    def test_unsafe_characters(self) -> None:
        assert name(pattern="a b/c:{hosts}", hosts=["web 01"]) == "a-b-c-web-01"

    def test_long_names_shorten_hosts_first(self) -> None:
        hosts = [f"host-with-a-really-quite-long-name-{n}" for n in range(5)]
        result = name(hosts=hosts)
        assert len(result) <= 120
        assert result.startswith("host-with-a-really-quite-long-name-0+4more_")

    def test_long_names_are_truncated(self) -> None:
        assert len(name(pattern="x" * 200)) == 120

    @pytest.mark.parametrize("pattern", ["{nope}", "{0}", "{hosts"])
    def test_invalid_pattern(self, pattern: str) -> None:
        with pytest.raises(ZabgrabError, match="Invalid output_pattern") as exc_info:
            name(pattern=pattern)
        assert exc_info.value.hint is not None
        assert "{hosts}" in exc_info.value.hint


@pytest.mark.parametrize(
    ("file", "format"),
    [
        ("a.tsv", Format.TSV),
        ("a.TXT", Format.TSV),
        ("a.csv", Format.CSV),
        ("a.mat", Format.MAT),
        ("a.h5", Format.H5),
        ("a.hdf5", Format.H5),
        ("a.dat", None),
        ("a", None),
    ],
)
def test_format_for(file: str, format: Format | None) -> None:
    assert format_for(Path(file)) is format


def test_unique_path(tmp_path: Path) -> None:
    path = tmp_path / "data.tsv"
    assert unique_path(path) == path
    path.touch()
    assert unique_path(path) == tmp_path / "data-2.tsv"
    (tmp_path / "data-2.tsv").touch()
    assert unique_path(path) == tmp_path / "data-3.tsv"


def item(
    itemid: int, value_type: ValueType = ValueType.FLOAT, name: str = "Particles"
) -> Item:
    return Item(
        itemid=itemid,
        hostid=10001,
        host="demo1",
        host_name="DEMO 1",
        name=name,
        key="p",
        value_type=value_type,
        units="#",
    )


T = 1_760_000_000.0


def result() -> FetchResult:
    return FetchResult(
        series=[
            Series(
                DEMO,
                item(1),
                Resolution.RAW,
                {"time": [T, T + 60.5], "value": [1.5, 2.0]},
                ("particles",),
            ),
            Series(
                DEMO,
                item(2, ValueType.UNSIGNED, name="Count"),
                Resolution.RAW,
                {"time": [T], "value": [7]},
            ),
            Series(
                DEMO,
                item(3, ValueType.LOG, name="Syslog"),
                Resolution.RAW,
                {
                    "time": [T],
                    "value": ['tab\there, comma, "quote"\nnewline ✓'],
                    "log_time": [None],
                    "source": [""],
                    "severity": [0],
                    "event_id": [5],
                },
            ),
        ],
        skipped=[
            Skipped(DEMO, item(4, ValueType.BINARY), "binary items aren't supported")
        ],
        failed=[Skipped(DEMO, item(5), "Can't connect.")],
    )


def read(path: Path, delimiter: str) -> tuple[dict[str, Any], list[list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    comments = [line for line in lines if line.startswith("#")]
    assert comments[0].startswith("# zabgrab data.")
    metadata = json.loads("".join(line[2:] for line in comments[1:]))
    data = "".join(line for line in lines if not line.startswith("#"))
    return metadata, list(csv.reader(io.StringIO(data), delimiter=delimiter))


class TestDelimitedWriter:
    @pytest.mark.parametrize("delimiter", ["\t", ","])
    def test_layout(self, tmp_path: Path, delimiter: str) -> None:
        path = tmp_path / "out" / "data.txt"
        write_delimited(
            path,
            result(),
            {"answer": 42, "π": "pi"},
            delimiter=delimiter,
            tz=ZoneInfo("UTC"),
        )
        metadata, rows = read(path, delimiter)

        assert metadata == {"answer": 42, "π": "pi"}
        assert rows[0] == [
            "demo1: Particles time",
            "demo1: Particles value",
            "demo1: Count time",
            "demo1: Count value",
            "demo1: Syslog time",
            "demo1: Syslog value",
            "demo1: Syslog log_time",
            "demo1: Syslog source",
            "demo1: Syslog severity",
            "demo1: Syslog event_id",
        ]
        assert rows[1] == [
            "2025-10-09T08:53:20+00:00",
            "1.5",
            "2025-10-09T08:53:20+00:00",
            "7",
            "2025-10-09T08:53:20+00:00",
            'tab\there, comma, "quote"\nnewline ✓',
            "",
            "",
            "0",
            "5",
        ]
        # Shorter series are padded across all their columns (no shifting).
        assert rows[2] == ["2025-10-09T08:54:20.500000+00:00", "2.0"] + [""] * 8
        assert len(rows) == 3

    def test_times_in_timezone(self, tmp_path: Path) -> None:
        path = tmp_path / "data.tsv"
        write_delimited(
            path, result(), {}, delimiter="\t", tz=ZoneInfo("Europe/London")
        )
        assert read(path, "\t")[1][1][0] == "2025-10-09T09:53:20+01:00"

    def test_local_time(self, tmp_path: Path) -> None:
        path = tmp_path / "data.tsv"
        write_delimited(path, result(), {}, delimiter="\t", tz=None)
        expected = datetime.fromtimestamp(T, UTC).astimezone().isoformat()
        assert read(path, "\t")[1][1][0] == expected

    def test_labels_for_several_servers_and_duplicates(self, tmp_path: Path) -> None:
        data = FetchResult(
            series=[
                Series(DEMO, item(1), Resolution.RAW, {"time": [], "value": []}),
                Series(DEMO, item(2), Resolution.RAW, {"time": [], "value": []}),
                Series(LAB, item(1), Resolution.HOURLY, {"time": [], "avg": []}),
            ]
        )
        path = tmp_path / "data.csv"
        write_delimited(path, data, {}, delimiter=",", tz=None)
        assert read(path, ",")[1][0] == [
            "demo/demo1: Particles (1) time",
            "demo/demo1: Particles (1) value",
            "demo/demo1: Particles (2) time",
            "demo/demo1: Particles (2) value",
            "lab/demo1: Particles time",
            "lab/demo1: Particles avg",
        ]

    def test_unwritable(self, locked_dir: Path) -> None:
        with pytest.raises(ZabgrabError, match="Can't write"):
            write_delimited(
                locked_dir / "data.tsv", result(), {}, delimiter="\t", tz=None
            )

    def test_failure_leaves_no_temporary_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*args: object) -> None:
            raise OSError("disk full")

        directory = tmp_path / "out"
        monkeypatch.setattr("os.replace", fail)
        with pytest.raises(ZabgrabError, match="disk full"):
            write_delimited(
                directory / "data.tsv", result(), {}, delimiter="\t", tz=None
            )
        assert list(directory.iterdir()) == []


def test_metadata() -> None:
    query = Query(
        requests=(
            Request("particles", "demo", (ItemSpec(host="demo1", name="P*"),)),
            Request(
                "__custom", Choice[str](choices=["demo", "lab"]), (ItemSpec(itemid=5),)
            ),
        ),
        templates=("particles", "gone"),
        custom=True,
        start=START,
        end=END,
        from_text="24 hours ago",
        to_text="now",
        resolution=Resolution.RAW,
        agg=(Agg.AVG,),
        formats=(Format.TSV,),
        output_dir=".",
        output_pattern="x",
        timezone="UTC",
        tz=ZoneInfo("UTC"),
        jobs=2,
        max_items=50,
        mat_text_warn_entries=10,
        sources={"from": "template 'particles'"},
    )
    template = Template.model_validate(
        {"items": [{"name": "P*"}], "from": "24 hours ago"}
    )
    metadata = build_metadata(
        query,
        result(),
        templates={"particles": template},
        command=["zabgrab", "fetch", "-t", "particles"],
        created=END,
    )
    assert metadata["zabgrab"] == {"version": __version__, "format_version": 1}
    assert metadata["command"] == ["zabgrab", "fetch", "-t", "particles"]
    assert metadata["query"]["from"] == "24 hours ago"
    assert metadata["query"]["start"] == "2026-10-13T10:15:00+00:00"
    assert metadata["query"]["sources"] == {"from": "template 'particles'"}
    assert metadata["query"]["requests"][0] == {
        "label": "particles",
        "server": "demo",
        "items": [{"host": "demo1", "name": "P*"}],
    }
    assert metadata["query"]["requests"][1]["server"] == {
        "choices": ["demo", "lab"],
        "select": "prompt",
    }
    assert metadata["templates"] == {
        "particles": {"items": [{"name": "P*"}], "from": "24 hours ago"}
    }
    assert metadata["servers"] == [{"name": "demo", "url": "https://demo.example.org"}]
    assert metadata["items"][0] == {
        "server": "https://demo.example.org",
        "itemid": 1,
        "host": "demo1",
        "host_name": "DEMO 1",
        "name": "Particles",
        "key": "p",
        "units": "#",
        "value_type": "float",
        "requests": ["particles"],
        "columns": ["time", "value"],
        "points": 2,
    }
    assert metadata["skipped"][0]["reason"] == "binary items aren't supported"
    assert metadata["failed"][0]["reason"] == "Can't connect."
    json.dumps(metadata)
