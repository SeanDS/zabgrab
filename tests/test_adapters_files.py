"""File permissions after safe (temporary file) writes."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from zabgrab.adapters.configfiles import add_server
from zabgrab.adapters.formats.delimited import write_delimited
from zabgrab.adapters.formats.hdf5 import write_fetch
from zabgrab.model.series import FetchResult

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Windows has no POSIX file permissions"
)

Writer = Callable[[Path], object]

WRITERS: dict[str, Writer] = {
    "data.tsv": lambda p: write_delimited(
        p, FetchResult(), {}, delimiter="\t", tz=None
    ),
    "data.h5": lambda p: write_fetch(p, FetchResult(), {}, mat=False, t_from=0, t_to=1),
    "data.mat": lambda p: write_fetch(p, FetchResult(), {}, mat=True, t_from=0, t_to=1),
    "config.toml": lambda p: add_server(
        p, f"s{len(p.read_text()) if p.exists() else 0}", "https://x"
    ),
}


@pytest.fixture
def umask() -> Iterator[None]:
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("name", list(WRITERS))
def test_new_files_follow_the_umask(tmp_path: Path, umask: None, name: str) -> None:
    path = tmp_path / name
    WRITERS[name](path)
    assert mode(path) == 0o644


@pytest.mark.parametrize("name", list(WRITERS))
def test_replaced_files_keep_their_permissions(
    tmp_path: Path, umask: None, name: str
) -> None:
    path = tmp_path / name
    WRITERS[name](path)
    path.chmod(0o640)
    WRITERS[name](path)
    assert mode(path) == 0o640
