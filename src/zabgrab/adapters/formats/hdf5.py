"""HDF5 (``.h5``) and MATLAB v7.3 (``.mat``) files.

Both use the same layout::

    /fetch_0001                 one group per fetch (one MATLAB variable)
        meta                    JSON metadata
        t_from, t_to            the fetched range, POSIX seconds (UTC)
        items/
            item_12345/         (item_12345_2, ... if two servers share an ID)
                itemid, hostid, value_type
                host, host_name, name, key, units, server, server_name
                time, value, ...    the series' columns (N x 1)
    /fetch_0002 ...

``.h5`` files are plain HDF5: text is UTF-8, and vectors are 1-D. ``.mat`` files add
what MATLAB's ``load`` needs: the 512-byte v7.3 header, ``MATLAB_class`` attributes,
text as UTF-16 ``char`` arrays, text columns as cell arrays (in ``/#refs#``, as
MATLAB itself writes them), and vectors stored so that MATLAB sees N x 1.

Attributes
----------
GROUP_PATTERN : re.Pattern
    Automatic group names (``fetch_NNNN``).
IDENTIFIER : re.Pattern
    Valid group names (MATLAB variable names).
USERBLOCK : int
    The size of the MATLAB header before the HDF5 data.
REFS : str
    The group MATLAB keeps cell array contents in.
LARGE_TEXT_ACCEPTED : str
    The file attribute recording that the user accepted a lot of text in a
    ``.mat`` file, so updates don't ask again.
ITEM_FIELDS : tuple of str
    The scalar fields of an item group (everything else is a column).
INTEGER_COLUMNS : frozenset of str
    Columns stored as integers (besides unsigned values).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from ...errors import ZabgrabError
from ...model.config import Resolution, ServerConfig
from ...model.series import FetchResult, Series
from ...model.server import Server
from ...model.zabbix import Item, ValueType
from ..files import replacing

GROUP_PATTERN = re.compile(r"fetch_(\d+)")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,62}")
USERBLOCK = 512
REFS = "#refs#"
LARGE_TEXT_ACCEPTED = "zabgrab_large_text_accepted"

ITEM_FIELDS = (
    "itemid",
    "hostid",
    "value_type",
    "host",
    "host_name",
    "name",
    "key",
    "units",
    "server",
    "server_name",
)
INTEGER_COLUMNS = frozenset({"count", "severity", "event_id"})


@dataclass
class FetchGroup:
    """One fetch read back from a file.

    Attributes
    ----------
    name : str
        The group's name.
    metadata : dict
        The fetch's metadata (see :mod:`zabgrab.services.metadata`).
    t_from, t_to : float
        The fetched time range, in POSIX seconds.
    series : list of Series
        The data.
    """

    name: str
    metadata: dict[str, Any]
    t_from: float
    t_to: float
    series: list[Series]


def is_valid_group_name(name: str) -> bool:
    """Check whether ``name`` can be a group (MATLAB variable) name."""
    return IDENTIFIER.fullmatch(name) is not None


def next_group_name(existing: Sequence[str]) -> str:
    """Get the next unused ``fetch_NNNN`` name after ``existing`` groups."""
    numbers = [int(m[1]) for name in existing if (m := GROUP_PATTERN.fullmatch(name))]
    return f"fetch_{max(numbers, default=0) + 1:04d}"


def group_names(path: Path) -> list[str]:
    """Get the names of the fetch groups in a file, in order.

    Raises
    ------
    ZabgrabError
        If the file doesn't exist or isn't HDF5.
    """
    with _open(path, "r") as file:
        return [name for name in file if not name.startswith("#")]


def write_fetch(
    path: Path,
    result: FetchResult,
    metadata: Mapping[str, Any],
    *,
    mat: bool,
    t_from: float,
    t_to: float,
    append: bool = False,
    group: str | None = None,
    in_place: bool = False,
    large_text_accepted: bool = False,
    created: datetime | None = None,
) -> str:
    """Write a fetch as a new group, creating the file or appending to it.

    Writing goes via a temporary file that then replaces ``path`` (so a crash can't
    corrupt it), unless appending ``in_place``.

    Parameters
    ----------
    path : Path
        The file.
    result : FetchResult
        The data.
    metadata : mapping
        The fetch's metadata, stored as JSON.
    mat : bool
        Write a MATLAB v7.3 file rather than plain HDF5.
    t_from, t_to : float
        The fetched time range, in POSIX seconds.
    append : bool, optional
        Add to the file if it exists (else it's replaced).
    group : str, optional
        The group's name (default: the next ``fetch_NNNN``).
    in_place : bool, optional
        Append to the file directly, without the safety copy.
    large_text_accepted : bool, optional
        Record that the user accepted a lot of text in a ``.mat`` file.
    created : datetime, optional
        The creation time for a new ``.mat`` file's header (default: now).

    Returns
    -------
    str
        The group's name.

    Raises
    ------
    ZabgrabError
        If the group name is invalid or taken, the file isn't a zabgrab MATLAB file
        when appending to it as one, or it can't be written.
    """
    exists = append and path.exists()
    if exists:
        existing = group_names(path)
        if mat and not is_mat(path):
            raise ZabgrabError(f"{path} isn't a MATLAB file written by zabgrab.")
    else:
        existing = []
    name = group or next_group_name(existing)
    if not is_valid_group_name(name):
        raise ZabgrabError(
            f"Invalid group name {name!r}.",
            hint="Group names must start with a letter and contain only letters, "
            "digits and '_' (at most 63 characters).",
        )
    if name in existing:
        raise ZabgrabError(f"{path} already has a group called {name!r}.")

    codec: _Codec = _MatCodec() if mat else _PlainCodec()
    with _writing(
        path, copy=exists and not in_place, direct=exists and in_place
    ) as target:
        mode = "a" if exists else "w"
        # New files track creation order, so groups read back in the order written.
        options: dict[str, Any] = {} if exists else {"track_order": True}
        if mat and not exists:
            options["userblock_size"] = USERBLOCK
        with h5py.File(target, mode, libver="earliest", **options) as file:
            _write_group(file, codec, name, result, metadata, t_from, t_to)
            if large_text_accepted:
                file.attrs[LARGE_TEXT_ACCEPTED] = np.uint8(1)
        if mat and not exists:
            _write_mat_header(target, created or datetime.now(UTC))
    return name


def read_fetches(path: Path) -> list[FetchGroup]:
    """Read every fetch group in a file written by zabgrab.

    Parameters
    ----------
    path : Path
        The ``.h5`` or ``.mat`` file.

    Returns
    -------
    list of FetchGroup
        The groups, in the order they were written.

    Raises
    ------
    ZabgrabError
        If the file doesn't exist, isn't HDF5, or has groups zabgrab didn't write.
    """
    with _open(path, "r") as file:
        groups = []
        for name in file:
            if name.startswith("#"):
                continue
            group = file[name]
            if not isinstance(group, h5py.Group) or "meta" not in group:
                raise ZabgrabError(
                    f"{path} has a group {name!r} not written by zabgrab."
                )
            groups.append(_read_group(file, name, group))
        return groups


def is_mat(path: Path) -> bool:
    """Check whether a file starts with a MATLAB v7.3 header."""
    with path.open("rb") as file:
        return file.read(10) == b"MATLAB 7.3"


def large_text_accepted(path: Path) -> bool:
    """Check whether a lot of text in ``path`` was accepted before (see write_fetch)."""
    with _open(path, "r") as file:
        return bool(file.attrs.get(LARGE_TEXT_ACCEPTED, 0))


def text_entries(path: Path) -> int:
    """Count the strings stored as cell array entries in a ``.mat`` file."""
    with _open(path, "r") as file:
        refs = file.get(REFS)
        return len(refs) if isinstance(refs, h5py.Group) else 0


# Writing.


@contextmanager
def _writing(path: Path, *, copy: bool, direct: bool) -> Iterator[Path]:
    """Get the file to write to: ``path`` itself, or a temporary replacement.

    Parameters
    ----------
    path : Path
        The file being written.
    copy : bool
        Start a temporary file as a copy of ``path`` (to append to it safely).
    direct : bool
        Write to ``path`` itself (appending ``--in-place``).

    Yields
    ------
    Path
        The file to write.

    Raises
    ------
    ZabgrabError
        If the file can't be written.
    """
    if direct:
        yield path
        return
    try:
        with replacing(path, copy=copy) as temporary:
            yield temporary
    except OSError as exc:
        raise ZabgrabError(f"Can't write {path}: {exc.strerror or exc}") from exc


@contextmanager
def _open(path: Path, mode: str) -> Iterator[h5py.File]:
    """Open an HDF5 file, explaining missing or non-HDF5 files."""
    try:
        file = h5py.File(path, mode)
    except FileNotFoundError:
        raise ZabgrabError(f"{path} doesn't exist.") from None
    except OSError as exc:
        raise ZabgrabError(
            f"{path} isn't an HDF5 or MATLAB v7.3 file ({exc})."
        ) from None
    with file:
        yield file


def _write_group(
    file: h5py.File,
    codec: _Codec,
    name: str,
    result: FetchResult,
    metadata: Mapping[str, Any],
    t_from: float,
    t_to: float,
) -> None:
    """Write a fetch group: its metadata, time range and an item group per series."""
    group = codec.struct(file, name)
    codec.text(group, "meta", json.dumps(metadata, ensure_ascii=False))
    codec.scalar(group, "t_from", t_from)
    codec.scalar(group, "t_to", t_to)
    items = codec.struct(group, "items")
    used: set[str] = set()
    for series in result.series:
        # Items on different servers can share an ID; readers use the stored
        # server and ID, so the suffix only has to make the name unique.
        item_name = base = f"item_{series.item.itemid}"
        number = 2
        while item_name in used:
            item_name, number = f"{base}_{number}", number + 1
        used.add(item_name)
        _write_series(codec, codec.struct(items, item_name), series)
        codec.finish_struct(items[item_name])
    codec.finish_struct(items)
    codec.finish_struct(group)


def _write_series(codec: _Codec, group: h5py.Group, series: Series) -> None:
    """Write an item's details and columns into its group."""
    item = series.item
    codec.scalar(group, "itemid", item.itemid)
    codec.scalar(group, "hostid", item.hostid)
    codec.scalar(group, "value_type", int(item.value_type))
    for field, text in (
        ("host", item.host),
        ("host_name", item.host_name),
        ("name", item.name),
        ("key", item.key),
        ("units", item.units),
        ("server", series.server.url),
        ("server_name", series.server.name or ""),
    ):
        codec.text(group, field, text)
    for column, values in series.columns.items():
        codec.column(group, column, values, _kind(item, column))


def _kind(item: Item, column: str) -> str:
    """Get how a column is stored: ``text``, ``uint64``, ``int64`` or ``double``."""
    if column == "value" and not item.value_type.numeric:
        return "text"
    if column == "source":
        return "text"
    if column == "value" and item.value_type is ValueType.UNSIGNED:
        return "uint64"
    if column in INTEGER_COLUMNS:
        return "int64"
    return "double"


def _numbers(values: Sequence[Any], kind: str) -> np.ndarray:
    """Convert a numeric column to an array (missing doubles as NaN)."""
    if kind == "double":
        return np.array([np.nan if v is None else v for v in values], dtype=np.float64)
    return np.array(values, dtype=np.uint64 if kind == "uint64" else np.int64)


def _write_mat_header(path: Path, time: datetime) -> None:
    """Write the 128-byte MATLAB v7.3 header into the file's user block."""
    created = time.strftime("%a %b %d %H:%M:%S %Y")
    text = (
        f"MATLAB 7.3 MAT-file, Platform: zabgrab, Created on: {created} "
        "HDF5 schema 1.00 ."
    ).ljust(116)
    # Subsystem data offset (unused), version 0x0200, and the endian indicator.
    header = text.encode("ascii") + b"\x00" * 8 + b"\x00\x02" + b"IM"
    with path.open("r+b") as file:
        file.write(header.ljust(USERBLOCK, b"\x00"))


class _Codec:
    """How values are stored (plain HDF5, or MATLAB-compatible)."""

    def struct(self, parent: h5py.Group, name: str) -> h5py.Group:
        """Create a group (a MATLAB struct), keeping its members in order."""
        return parent.create_group(name, track_order=True)

    def finish_struct(self, group: h5py.Group) -> None:
        """Finish a group once all its members are written."""

    def text(self, parent: h5py.Group, name: str, value: str) -> None:
        """Write a string."""
        raise NotImplementedError  # pragma: no cover

    def scalar(self, parent: h5py.Group, name: str, value: float) -> None:
        """Write a number."""
        raise NotImplementedError  # pragma: no cover

    def column(
        self, parent: h5py.Group, name: str, values: Sequence[Any], kind: str
    ) -> None:
        """Write a column of values stored as ``kind`` (see _kind)."""
        raise NotImplementedError  # pragma: no cover


class _PlainCodec(_Codec):
    """Plain HDF5: UTF-8 strings, 1-D compressed columns."""

    def text(self, parent: h5py.Group, name: str, value: str) -> None:
        """Write a UTF-8 string."""
        parent.create_dataset(name, data=value, dtype=h5py.string_dtype())

    def scalar(self, parent: h5py.Group, name: str, value: float) -> None:
        """Write a number, as an integer if it is one."""
        dtype = np.int64 if isinstance(value, int) else np.float64
        parent.create_dataset(name, data=np.array(value, dtype=dtype))

    def column(
        self, parent: h5py.Group, name: str, values: Sequence[Any], kind: str
    ) -> None:
        """Write a 1-D column (compressed unless empty)."""
        if kind == "text":
            data: np.ndarray = np.array(list(values), dtype=object)
            dtype: Any = h5py.string_dtype()
        else:
            data = _numbers(values, kind)
            dtype = data.dtype
        options = {"compression": "gzip", "chunks": True} if len(values) else {}
        parent.create_dataset(
            name, data=data, dtype=dtype, shape=(len(values),), **options
        )


class _MatCodec(_Codec):
    """Stores values the way MATLAB's ``load`` expects them in v7.3 files."""

    def struct(self, parent: h5py.Group, name: str) -> h5py.Group:
        """Create a group marked as a MATLAB struct."""
        group = super().struct(parent, name)
        _matlab_class(group, "struct")
        return group

    def finish_struct(self, group: h5py.Group) -> None:
        """List the struct's fields, in order, as MATLAB expects."""
        fields = np.empty(len(group), dtype=object)
        for index, field in enumerate(group):
            fields[index] = np.array(list(field), dtype="S1")
        group.attrs.create(
            "MATLAB_fields", fields, dtype=h5py.vlen_dtype(np.dtype("S1"))
        )

    def text(self, parent: h5py.Group, name: str, value: str) -> None:
        """Write a string as a MATLAB ``char`` array."""
        self._char(parent, name, value)

    @staticmethod
    def _char(parent: h5py.Group, name: str, value: str) -> h5py.Dataset:
        """Write a string as UTF-16 code units, a MATLAB ``char`` column."""
        if not value:
            return _empty(parent, name, "char", (0, 0))
        codes = np.frombuffer(value.encode("utf-16-le"), dtype="<u2")
        dataset = parent.create_dataset(name, data=codes.reshape(-1, 1))
        _matlab_class(dataset, "char")
        dataset.attrs.create("MATLAB_int_decode", np.int32(2))
        return dataset

    def scalar(self, parent: h5py.Group, name: str, value: float) -> None:
        """Write a number as a 1 x 1 MATLAB ``double``."""
        dataset = parent.create_dataset(
            name, data=np.array([[value]], dtype=np.float64)
        )
        _matlab_class(dataset, "double")

    def column(
        self, parent: h5py.Group, name: str, values: Sequence[Any], kind: str
    ) -> None:
        """Write a column that MATLAB loads as N x 1 (text as a cell array).

        HDF5 stores arrays row-major and MATLAB column-major, so a 1 x N dataset
        is what MATLAB sees as N x 1.
        """
        matlab = "cell" if kind == "text" else kind
        if not values:
            _empty(parent, name, matlab, (0, 1))
            return
        if kind == "text":
            refs = self._refs(parent.file)
            data: np.ndarray = np.empty((1, len(values)), dtype=h5py.ref_dtype)
            number = len(refs)
            for index, value in enumerate(values):
                while f"r{number}" in refs:  # pragma: no cover - only after edits
                    number += 1
                data[0, index] = self._char(refs, f"r{number}", str(value)).ref
                number += 1
            dataset = parent.create_dataset(name, data=data, dtype=h5py.ref_dtype)
        else:
            data = _numbers(values, kind).reshape(1, -1)
            dataset = parent.create_dataset(
                name, data=data, compression="gzip", chunks=True
            )
        _matlab_class(dataset, matlab)

    @staticmethod
    def _refs(file: h5py.File) -> h5py.Group:
        """Get the ``#refs#`` group holding cell array contents, creating it."""
        if REFS not in file:
            file.create_group(REFS)
        refs = file[REFS]
        assert isinstance(refs, h5py.Group)
        return refs


def _matlab_class(obj: h5py.HLObject, name: str) -> None:
    """Mark a dataset or group with its MATLAB class."""
    obj.attrs.create("MATLAB_class", np.bytes_(name))


def _empty(
    parent: h5py.Group, name: str, matlab: str, dims: tuple[int, int]
) -> h5py.Dataset:
    """Write an empty MATLAB array, which MATLAB stores as its dimensions."""
    dataset = parent.create_dataset(name, data=np.array(dims, dtype=np.uint64))
    _matlab_class(dataset, matlab)
    dataset.attrs.create("MATLAB_empty", np.uint8(1))
    return dataset


# Reading.


def _read_group(file: h5py.File, name: str, group: h5py.Group) -> FetchGroup:
    """Read a fetch group back into series."""
    metadata = json.loads(_read(file, group["meta"]))
    resolution = Resolution(metadata.get("query", {}).get("resolution", "raw"))
    requests = {
        (entry["server"], entry["itemid"]): tuple(entry.get("requests", ()))
        for entry in metadata.get("items", [])
    }
    series = []
    for item_group in group["items"].values():
        fields = {
            field: _read(file, item_group[field], scalar=True) for field in ITEM_FIELDS
        }
        item = Item(
            itemid=int(fields["itemid"]),
            hostid=int(fields["hostid"]),
            host=fields["host"],
            host_name=fields["host_name"],
            name=fields["name"],
            key=fields["key"],
            value_type=ValueType(int(fields["value_type"])),
            units=fields["units"],
        )
        server = Server(
            fields["server_name"] or None, ServerConfig(url=fields["server"])
        )
        columns = {
            column: _column(column, _read(file, item_group[column]))
            for column in item_group
            if column not in ITEM_FIELDS
        }
        series.append(
            Series(
                server,
                item,
                resolution,
                columns,
                requests.get((server.url, item.itemid), ()),
            )
        )
    return FetchGroup(
        name,
        metadata,
        float(_read(file, group["t_from"], scalar=True)),
        float(_read(file, group["t_to"], scalar=True)),
        series,
    )


def _column(name: str, values: Any) -> list[Any]:
    """Convert a column read from a file back to its values."""
    column: list[Any] = list(values)
    if name == "log_time":
        return [None if v != v else int(v) for v in column]  # NaN means missing
    return column


def _read(file: h5py.File, dataset: Any, *, scalar: bool = False) -> Any:
    """Read a dataset written by either codec (strings, cells, numbers)."""
    matlab = dataset.attrs.get("MATLAB_class")
    if dataset.attrs.get("MATLAB_empty"):
        return "" if matlab == b"char" else []
    if matlab == b"char":
        return dataset[()].astype("<u2").tobytes().decode("utf-16-le")
    if matlab == b"cell":
        return [_read(file, file[ref]) for ref in dataset[()].reshape(-1)]
    if h5py.check_string_dtype(dataset.dtype):
        data = dataset.asstr()[()]
        return data if isinstance(data, str) else list(data)
    data = dataset[()]
    if scalar:
        return data.reshape(-1)[0].item()
    return data.reshape(-1).tolist()
