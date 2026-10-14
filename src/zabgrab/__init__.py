"""Zabbix data retrieval."""

from importlib.metadata import PackageNotFoundError, version

PROGRAM = "zabgrab"

try:
    __version__ = version(PROGRAM)
except PackageNotFoundError:  # pragma: no cover - only when running from a raw checkout
    __version__ = "0+unknown"

__all__ = ("PROGRAM", "__version__")
