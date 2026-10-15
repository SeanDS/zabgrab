"""HDF5 and MATLAB v7.3 files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from zabgrab.adapters.formats.hdf5 import (
    LARGE_TEXT_ACCEPTED,
    is_mat,
    is_valid_group_name,
    large_text_accepted,
    next_group_name,
    read_fetches,
    read_metadata,
    text_entries,
    write_fetch,
)
from zabgrab.errors import ZabgrabError
from zabgrab.model.config import Resolution, ServerConfig
from zabgrab.model.series import FORMAT_VERSION, FetchResult, Series
from zabgrab.model.server import Server
from zabgrab.model.zabbix import Item, ValueType

DEMO = Server("demo", ServerConfig(url="https://demo.example.org"))
OTHER = Server(None, ServerConfig(url="https://other.example.org"))


def item(itemid: int, value_type: ValueType, name: str = "Thing") -> Item:
    return Item(itemid, 10001, "demo1", "DEMO 1", name, "key[a,b]", value_type, "#")


def result() -> FetchResult:
    return FetchResult(
        series=[
            Series(
                DEMO,
                item(1, ValueType.FLOAT, "Particles ✓"),
                Resolution.RAW,
                {"time": [1.0, 2.5], "value": [0.5, 1.5]},
                ("particles",),
            ),
            Series(
                DEMO,
                item(2, ValueType.UNSIGNED),
                Resolution.RAW,
                {"time": [1.0], "value": [2**63 + 5]},
            ),
            Series(
                DEMO,
                item(3, ValueType.LOG),
                Resolution.RAW,
                {
                    "time": [1.0, 2.0],
                    "value": ["a\tb\nc", "𝄞 outside the BMP"],
                    "log_time": [5, None],
                    "source": ["", "kern"],
                    "severity": [1, 2],
                    "event_id": [3, 4],
                },
            ),
            Series(
                DEMO, item(4, ValueType.TEXT), Resolution.RAW, {"time": [], "value": []}
            ),
            Series(
                OTHER,
                item(1, ValueType.CHAR),
                Resolution.RAW,
                {"time": [9.0], "value": [""]},
            ),
        ]
    )


METADATA: dict[str, Any] = {
    "zabgrab": {"version": "1.0.0", "format_version": FORMAT_VERSION},
    "query": {"resolution": "raw"},
    "items": [{"server": DEMO.url, "itemid": 1, "requests": ["particles"]}],
}


def write(path: Path, **kwargs: Any) -> str:
    kwargs.setdefault("mat", path.suffix == ".mat")
    kwargs.setdefault("t_from", 0.0)
    kwargs.setdefault("t_to", 10.0)
    return write_fetch(path, kwargs.pop("result", result()), METADATA, **kwargs)


@pytest.fixture(params=[".h5", ".mat"])
def path(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    return tmp_path / f"data{request.param}"


class TestRoundTrip:
    def test_values_and_types(self, path: Path) -> None:
        assert write(path) == "fetch_0001"
        (group,) = read_fetches(path)
        assert group.name == "fetch_0001"
        assert (group.t_from, group.t_to) == (0.0, 10.0)
        assert group.metadata == METADATA
        written = result().series
        assert [s.columns for s in group.series] == [s.columns for s in written]
        assert [s.item for s in group.series] == [s.item for s in written]
        assert [s.server for s in group.series] == [s.server for s in written]
        assert group.series[0].requests == ("particles",)
        assert group.series[1].requests == ()
        assert all(s.resolution is Resolution.RAW for s in group.series)
        assert isinstance(group.series[1].columns["value"][0], int)
        assert isinstance(group.series[2].columns["severity"][0], int)

    def test_trends(self, path: Path) -> None:
        trends = FetchResult(
            series=[
                Series(
                    DEMO,
                    item(1, ValueType.FLOAT),
                    Resolution.HOURLY,
                    {"time": [0.0, 3600.0], "avg": [1.0, 2.0], "count": [60, 59]},
                )
            ]
        )
        write_fetch(
            path,
            trends,
            {**METADATA, "query": {"resolution": "1h"}},
            mat=path.suffix == ".mat",
            t_from=0,
            t_to=7199,
        )
        (series,) = read_fetches(path)[0].series
        assert series.resolution is Resolution.HOURLY
        assert series.columns == {
            "time": [0.0, 3600.0],
            "avg": [1.0, 2.0],
            "count": [60, 59],
        }

    def test_append(self, path: Path) -> None:
        write(path)
        assert write(path, append=True, t_from=10.0, t_to=20.0) == "fetch_0002"
        assert write(path, append=True, group="extra") == "extra"
        assert [name for name, _ in read_metadata(path)] == [
            "fetch_0001",
            "fetch_0002",
            "extra",
        ]
        assert write(path, append=True) == "fetch_0003"
        groups = read_fetches(path)
        assert [g.t_from for g in groups] == [0.0, 10.0, 0.0, 0.0]
        assert all(len(g.series) == 5 for g in groups)

    def test_append_in_place(self, path: Path) -> None:
        write(path)
        write(path, append=True, in_place=True)
        assert [name for name, _ in read_metadata(path)] == ["fetch_0001", "fetch_0002"]

    def test_append_creates_missing_file(self, path: Path) -> None:
        assert write(path, append=True) == "fetch_0001"

    def test_overwrite(self, path: Path) -> None:
        write(path)
        write(path, append=False)
        assert [name for name, _ in read_metadata(path)] == ["fetch_0001"]

    def test_items_with_the_same_id_on_two_servers(self, path: Path) -> None:
        write(path)
        with h5py.File(path) as file:
            assert list(file["fetch_0001/items"]) == [
                "item_1",
                "item_2",
                "item_3",
                "item_4",
                "item_1_2",
            ]


class TestMatLayout:
    @pytest.fixture
    def mat(self, tmp_path: Path) -> Path:
        path = tmp_path / "data.mat"
        write(path)
        write(path, append=True)
        return path

    def test_header(self, mat: Path) -> None:
        header = mat.read_bytes()[:512]
        assert header.startswith(
            b"MATLAB 7.3 MAT-file, Platform: zabgrab, Created on: "
        )
        assert header[:116].rstrip().endswith(b"HDF5 schema 1.00 .")
        assert header[116:124] == b"\x00" * 8
        assert header[124:128] == b"\x00\x02IM"
        assert header[128:] == b"\x00" * 384
        assert is_mat(mat)
        with h5py.File(mat) as file:
            assert file.userblock_size == 512

    def test_oldest_superblock_for_compatibility(self, mat: Path) -> None:
        data = mat.read_bytes()
        assert data[512:520] == b"\x89HDF\r\n\x1a\n"
        assert data[520] == 0  # superblock version 0

    def test_structs(self, mat: Path) -> None:
        with h5py.File(mat) as file:
            group = file["fetch_0002"]
            assert group.attrs["MATLAB_class"] == b"struct"
            fields = [
                "".join(c.decode() for c in f) for f in group.attrs["MATLAB_fields"]
            ]
            assert fields == ["meta", "t_from", "t_to", "items"]
            item = group["items/item_3"]
            assert item.attrs["MATLAB_class"] == b"struct"
            assert len(item.attrs["MATLAB_fields"]) == len(item)

    def test_text(self, mat: Path) -> None:
        with h5py.File(mat) as file:
            name = file["fetch_0001/items/item_1/name"]
            assert name.attrs["MATLAB_class"] == b"char"
            assert name.attrs["MATLAB_int_decode"] == 2
            assert name.dtype == np.dtype("<u2")
            assert name.shape == (len("Particles ✓"), 1)
            empty = file["fetch_0001/items/item_1_2/server_name"]
            assert empty.attrs["MATLAB_empty"] == 1
            assert list(empty[()]) == [0, 0]
            meta = (
                file["fetch_0001/meta"][()].astype("<u2").tobytes().decode("utf-16-le")
            )
            assert json.loads(meta) == METADATA

    def test_numbers(self, mat: Path) -> None:
        with h5py.File(mat) as file:
            item = file["fetch_0001/items"]
            time = item["item_1/time"]
            assert (time.attrs["MATLAB_class"], time.shape, time.dtype) == (
                b"double",
                (1, 2),
                np.float64,
            )
            assert item["item_2/value"].attrs["MATLAB_class"] == b"uint64"
            assert item["item_3/severity"].attrs["MATLAB_class"] == b"int64"
            assert item["item_1/itemid"].shape == (1, 1)
            empty = item["item_4/time"]
            assert empty.attrs["MATLAB_empty"] == 1
            assert empty.attrs["MATLAB_class"] == b"double"

    def test_cells(self, mat: Path) -> None:
        with h5py.File(mat) as file:
            value = file["fetch_0002/items/item_3/value"]
            assert value.attrs["MATLAB_class"] == b"cell"
            assert value.shape == (1, 2)
            entry = file[value[0, 1]]
            assert entry.name.startswith("/#refs#/")
            assert entry.attrs["MATLAB_class"] == b"char"
            text = entry[()].astype("<u2").tobytes().decode("utf-16-le")
            assert text == "𝄞 outside the BMP"
            assert entry.shape == (len(text.encode("utf-16-le")) // 2, 1)
            assert (
                file["fetch_0001/items/item_4/value"].attrs["MATLAB_class"] == b"cell"
            )
        # Two log columns (value, source) and one char value, in each of two groups.
        assert text_entries(mat) == 2 * (2 + 2 + 1)


class TestPlainLayout:
    def test_no_matlab_details(self, tmp_path: Path) -> None:
        path = tmp_path / "data.h5"
        write(path)
        assert not is_mat(path)
        with h5py.File(path) as file:
            assert file.userblock_size == 0
            names: list[str] = []
            file.visititems(lambda name, obj: names.append(name) if obj.attrs else None)
            assert names == []
            value = file["fetch_0001/items/item_3/value"]
            assert h5py.check_string_dtype(value.dtype).encoding == "utf-8"
            assert value.shape == (2,)
            assert file["fetch_0001/items/item_1/itemid"].shape == ()
            assert file["fetch_0001/items/item_2/value"].dtype == np.uint64
        assert text_entries(path) == 0


class TestGroups:
    @pytest.mark.parametrize(
        ("existing", "expected"),
        [
            ([], "fetch_0001"),
            (["fetch_0001", "other"], "fetch_0002"),
            (["fetch_0009"], "fetch_0010"),
        ],
    )
    def test_next_group_name(self, existing: list[str], expected: str) -> None:
        assert next_group_name(existing) == expected

    @pytest.mark.parametrize(
        ("name", "valid"),
        [
            ("fetch_0001", True),
            ("a", True),
            ("_a", False),
            ("1a", False),
            ("a-b", False),
            ("a" * 64, False),
        ],
    )
    def test_valid_names(self, name: str, valid: bool) -> None:
        assert is_valid_group_name(name) is valid

    def test_invalid_custom_group(self, path: Path) -> None:
        with pytest.raises(ZabgrabError, match="Invalid group name"):
            write(path, group="no good")

    def test_duplicate_group(self, path: Path) -> None:
        write(path, group="mine")
        with pytest.raises(ZabgrabError, match="already has a group called 'mine'"):
            write(path, append=True, group="mine")


class TestErrors:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ZabgrabError, match="doesn't exist"):
            read_fetches(tmp_path / "nope.h5")

    def test_not_hdf5(self, tmp_path: Path) -> None:
        path = tmp_path / "data.h5"
        path.write_text("hello")
        with pytest.raises(ZabgrabError, match=r"isn't an HDF5 or MATLAB v7\.3 file"):
            read_fetches(path)

    def test_foreign_group(self, tmp_path: Path) -> None:
        path = tmp_path / "data.h5"
        with h5py.File(path, "w") as file:
            file.create_group("something")
        with pytest.raises(ZabgrabError, match="not written by zabgrab"):
            read_fetches(path)

    def test_appending_mat_to_plain_hdf5(self, tmp_path: Path) -> None:
        path = tmp_path / "data.mat"
        write(path, mat=False)
        with pytest.raises(
            ZabgrabError, match="isn't a MATLAB file written by zabgrab"
        ):
            write(path, append=True)

    def test_failed_write_keeps_the_original(
        self, path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write(path)
        original = path.read_bytes()

        def fail(*args: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", fail)
        with pytest.raises(ZabgrabError, match="disk full"):
            write(path, append=True)
        assert path.read_bytes() == original
        assert list(path.parent.glob("*.tmp")) == []

    def test_unwritable_directory(self, locked_dir: Path) -> None:
        with pytest.raises(ZabgrabError, match="Can't write"):
            write(locked_dir / "data.h5")


def test_large_text_flag(path: Path) -> None:
    write(path)
    assert not large_text_accepted(path)
    write(path, append=True, large_text_accepted=True)
    assert large_text_accepted(path)
    with h5py.File(path) as file:
        assert file.attrs[LARGE_TEXT_ACCEPTED] == 1


def set_meta(path: Path, meta: str) -> None:
    """Replace the first group's metadata, as plain HDF5 text."""
    with h5py.File(path, "a") as file:
        group = file["fetch_0001"]
        del group["meta"]
        group.create_dataset("meta", data=meta, dtype=h5py.string_dtype())


class TestFormatVersion:
    def test_written_and_read_back(self, path: Path) -> None:
        write(path)
        ((name, metadata),) = read_metadata(path)
        assert name == "fetch_0001"
        assert metadata["zabgrab"]["format_version"] == FORMAT_VERSION == 1

    def test_newer_versions_are_refused(self, path: Path) -> None:
        write(path)
        newer = {**METADATA, "zabgrab": {"format_version": FORMAT_VERSION + 1}}
        set_meta(path, json.dumps(newer))
        for read in (read_fetches, read_metadata):
            with pytest.raises(ZabgrabError) as exc_info:
                read(path)
            assert exc_info.value.message == (
                f"Group 'fetch_0001' in {path} was written by a newer version of "
                f"zabgrab (file format {FORMAT_VERSION + 1}; this version reads up "
                f"to {FORMAT_VERSION})."
            )
            assert (
                exc_info.value.hint == "Upgrade zabgrab: pip install --upgrade zabgrab"
            )

    def test_appending_to_newer_versions_is_refused(self, path: Path) -> None:
        write(path)
        newer = {**METADATA, "zabgrab": {"format_version": FORMAT_VERSION + 1}}
        set_meta(path, json.dumps(newer))
        original = path.read_bytes()
        with pytest.raises(ZabgrabError, match="written by a newer version"):
            write(path, append=True)
        assert path.read_bytes() == original

    @pytest.mark.parametrize(
        "meta",
        [
            "{}",
            "[]",
            "not json",
            '{"zabgrab": "1.0.0"}',
            '{"zabgrab": {"version": "1.0.0"}}',
            '{"zabgrab": {"format_version": "1"}}',
            '{"zabgrab": {"format_version": true}}',
            '{"zabgrab": {"format_version": 0}}',
        ],
    )
    def test_unknown_versions_are_refused(self, path: Path, meta: str) -> None:
        write(path)
        set_meta(path, meta)
        with pytest.raises(ZabgrabError, match="doesn't record its file format"):
            read_fetches(path)
