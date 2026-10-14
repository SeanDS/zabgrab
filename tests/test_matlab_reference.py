"""Files written now match the reference files checked in MATLAB.

tests/matlab/reference/ holds sample files that passed tests/matlab/check_files.m
in MATLAB R2023b and R2026b. If a change to the file layout makes this test fail,
check the new files in MATLAB (see tests/matlab/generate.py) before updating the
references.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from zabgrab.adapters.formats.hdf5 import read_fetches

from .matlab.generate import generate

REFERENCE = Path(__file__).parent / "matlab" / "reference"
NAMES = ["basic.mat", "basic.h5", "trends.mat", "trends.h5"]


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("matlab")
    generate(folder, large=10)
    return folder


def layout(path: Path) -> dict[str, Any]:
    """Every object in a file: type, shape, dtype, attributes and data."""
    found: dict[str, Any] = {}
    with h5py.File(path) as file:
        refs = file.get("#refs#")

        def describe(name: str, obj: Any) -> None:
            attributes = {k: _plain(v) for k, v in obj.attrs.items()}
            if isinstance(obj, h5py.Group):
                found[name] = ("group", attributes)
            elif h5py.check_ref_dtype(obj.dtype):
                targets = [file[ref].name for ref in obj[()].reshape(-1)]
                found[name] = ("refs", obj.shape, attributes, targets)
            else:
                found[name] = (
                    "data",
                    obj.shape,
                    str(obj.dtype),
                    attributes,
                    _plain(obj[()]),
                )

        file.visititems(describe)
        found["/"] = {k: _plain(v) for k, v in file.attrs.items()}
        found["userblock"] = file.userblock_size
        assert refs is None or isinstance(refs, h5py.Group)
    return found


def _plain(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            return [_plain(v) for v in value.reshape(-1)]
        return _plain(value.tolist())
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, float) and value != value:
        return "nan"  # so that NaN compares equal
    if isinstance(value, bytes | np.bytes_):
        return bytes(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


@pytest.mark.parametrize("name", NAMES)
def test_layout_matches_reference(fresh: Path, name: str) -> None:
    assert layout(fresh / name) == layout(REFERENCE / name)


@pytest.mark.parametrize("name", [n for n in NAMES if n.endswith(".mat")])
def test_mat_header_matches_reference(fresh: Path, name: str) -> None:
    assert (fresh / name).read_bytes()[:128] == (REFERENCE / name).read_bytes()[:128]


@pytest.mark.parametrize("name", NAMES)
def test_reference_files_read_back(name: str) -> None:
    expected = json.loads((REFERENCE / "expected.json").read_text())["files"][name]
    groups = read_fetches(REFERENCE / name)
    assert [g.name for g in groups] == [g["name"] for g in expected]
    for group, wanted in zip(groups, expected, strict=True):
        assert len(group.series) == len(wanted["items"])


def test_expected_values_match(fresh: Path) -> None:
    assert json.loads((fresh / "expected.json").read_text()) == json.loads(
        (REFERENCE / "expected.json").read_text()
    ) | {"large_entries": 10}
