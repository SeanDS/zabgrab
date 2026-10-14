"""Sample files read correctly in MATLAB itself.

Runs tests/matlab/check_files.m in every MATLAB found: the executables listed in
``ZABGRAB_MATLAB`` (separated like ``PATH``), else ``matlab`` on ``PATH`` and, on
macOS, ``/Applications/MATLAB_*.app``. Skipped when there is no MATLAB, as in CI;
tests/test_matlab_reference.py still checks the layout against files verified here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .matlab.generate import generate

CHECK_FILES = Path(__file__).parent / "matlab" / "check_files.m"


def find_matlab() -> list[Path]:
    """MATLAB executables to test with, without duplicates."""
    if listed := os.environ.get("ZABGRAB_MATLAB"):
        candidates = [Path(p) for p in listed.split(os.pathsep) if p]
    else:
        on_path = shutil.which("matlab")
        candidates = [Path(on_path)] if on_path else []
        candidates += sorted(Path("/Applications").glob("MATLAB_*.app/bin/matlab"))
    found: dict[Path, Path] = {}
    for candidate in candidates:
        if candidate.is_file():
            found.setdefault(candidate.resolve(), candidate)
    return list(found.values())


def _release(executable: Path) -> str:
    # /Applications/MATLAB_R2023b.app/bin/matlab -> R2023b
    app = executable.resolve().parent.parent.name
    return app.removeprefix("MATLAB_").removesuffix(".app") or str(executable)


MATLAB = find_matlab()


@pytest.mark.skipif(not MATLAB, reason="MATLAB not found (set ZABGRAB_MATLAB)")
@pytest.mark.parametrize("matlab", MATLAB, ids=[_release(m) for m in MATLAB])
def test_check_files(matlab: Path, tmp_path: Path) -> None:
    generate(tmp_path, large=10_000)
    folder = str(tmp_path).replace("'", "''")
    result = subprocess.run(
        [str(matlab), "-batch", f"check_files('{folder}')"],
        cwd=CHECK_FILES.parent,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert " 0 failed." in output, output
