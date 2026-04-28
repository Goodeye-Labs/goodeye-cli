"""Public CLI for Goodeye."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_FALLBACK_UNKNOWN = "0.0.0+unknown"


def _version_from_source_tree() -> str | None:
    """Read ``project.version`` from ``pyproject.toml`` at the repo root.

    Used when importlib metadata is missing (some tooling runs the package from
    a checkout without registering the distribution).
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    v = project.get("version")
    return str(v) if isinstance(v, str) else None


try:
    __version__ = version("goodeye")
except PackageNotFoundError:
    __version__ = _version_from_source_tree() or _FALLBACK_UNKNOWN
