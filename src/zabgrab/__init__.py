"""Zabbix data retrieval.

Attributes
----------
PROGRAM : str
    The program's name, used for the command, config directories and keyring.
__version__ : str
    The installed version (from the package metadata, set by setuptools_scm).
"""

from importlib.metadata import PackageNotFoundError, version

PROGRAM = "zabgrab"

try:
    __version__ = version(PROGRAM)
except PackageNotFoundError:  # pragma: no cover - only when running from a raw checkout
    __version__ = "0+unknown"

__all__ = ("PROGRAM", "__version__")
