"""Output file names.

Attributes
----------
MAX_LENGTH : int
    The longest automatic name (without extension).
MAX_HOSTS : int
    The most host names in an automatic name before the rest are counted.
PLACEHOLDERS : tuple of str
    The placeholders ``output_pattern`` can use.
EXTENSIONS : dict of Format to str
    The file extension for each format.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..errors import ZabgrabError
from ..model.config import Format
from ..model.query import CUSTOM

MAX_LENGTH = 120
MAX_HOSTS = 3
PLACEHOLDERS = ("hosts", "templates", "from", "to", "server", "resolution")

EXTENSIONS = {
    Format.TSV: ".tsv",
    Format.CSV: ".csv",
    Format.MAT: ".mat",
    Format.H5: ".h5",
}
_FORMATS_BY_SUFFIX = {
    ".tsv": Format.TSV,
    ".txt": Format.TSV,
    ".csv": Format.CSV,
    ".mat": Format.MAT,
    ".h5": Format.H5,
    ".hdf5": Format.H5,
}


def format_for(path: Path) -> Format | None:
    """Get the output format implied by a file name's extension, if any."""
    return _FORMATS_BY_SUFFIX.get(path.suffix.lower())


def output_name(
    pattern: str,
    *,
    hosts: Sequence[str],
    templates: Sequence[str],
    custom: bool,
    start: datetime,
    end: datetime,
    servers: Sequence[str],
    resolution: str,
) -> str:
    """Build a file name (without extension) from ``output_pattern``.

    Unsafe characters become ``-``, and the name is kept to :data:`MAX_LENGTH`
    characters, showing fewer hosts if needed.

    Parameters
    ----------
    pattern : str
        The pattern. Placeholders: ``{hosts}`` (matched hosts, sorted, joined with
        ``+``; more than three are shortened), ``{templates}`` (the template's
        name, plus ``__custom`` if the command line added or changed data),
        ``{from}``/``{to}`` (UTC, e.g. ``20261014T101500Z``), ``{server}`` and
        ``{resolution}``.
    hosts : sequence of str
        The hosts of the fetched items.
    templates : sequence of str
        The template used, if any.
    custom : bool
        Whether the command line added or changed data.
    start, end : datetime
        The fetched time range.
    servers : sequence of str
        Short names of the servers.
    resolution : str
        ``raw`` or ``1h``.

    Returns
    -------
    str
        The name.

    Raises
    ------
    ZabgrabError
        If the pattern is invalid (e.g. an unknown placeholder).
    """
    names = list(templates) + ([CUSTOM] if custom or not templates else [])
    unique_hosts = sorted(set(hosts)) or ["nohosts"]

    def render(max_hosts: int) -> str:
        shown = unique_hosts
        if len(shown) > max_hosts:
            hidden = len(shown) - max(max_hosts - 1, 1)
            shown = [*shown[: max(max_hosts - 1, 1)], f"{hidden}more"]
        values = {
            "hosts": "+".join(shown),
            "templates": "+".join(names),
            "from": _stamp(start),
            "to": _stamp(end),
            "server": "+".join(servers),
            "resolution": resolution,
        }
        try:
            name = pattern.format_map(values)
        # Patterns are user config, so they can name unknown placeholders or be
        # malformed.
        except (KeyError, IndexError, ValueError) as exc:
            raise ZabgrabError(
                f"Invalid output_pattern {pattern!r}: {exc}",
                hint="Use placeholders from: "
                + ", ".join("{" + p + "}" for p in PLACEHOLDERS)
                + ".",
            ) from None
        return re.sub(r"[^A-Za-z0-9._+-]", "-", name)

    for max_hosts in (MAX_HOSTS, 2, 1):
        name = render(max_hosts)
        if len(name) <= MAX_LENGTH:
            return name
    return name[:MAX_LENGTH]


def unique_path(path: Path) -> Path:
    """Get ``path``, or ``path`` with ``-2``, ``-3``, ... added if it exists."""
    candidate, number = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        number += 1
    return candidate


def _stamp(time: datetime) -> str:
    """Format a time for file names, e.g. ``20261014T101500Z``."""
    return time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
