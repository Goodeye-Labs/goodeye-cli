"""Public CLI for Goodeye."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("goodeye")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
