"""Credential and client-config file handling.

Defines the on-disk layout under ``$XDG_CONFIG_HOME/goodeye`` (or
``~/.config/goodeye`` when XDG is unset) and the source-order rules used
to resolve the active API key and server URL.

Source order for the API key (highest wins):
    1. ``GOODEYE_API_KEY`` environment variable.
    2. ``credentials.json`` ``api_key`` field.
    3. None (unauthenticated).

Source order for the server URL (highest wins):
    1. ``GOODEYE_SERVER`` environment variable.
    2. ``credentials.json`` ``server`` field.
    3. ``DEFAULT_SERVER`` constant.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SERVER = "https://mcp.goodeyelabs.com"


@dataclass(frozen=True)
class ConfigPaths:
    """Resolved filesystem paths for CLI configuration files."""

    config_dir: Path
    credentials_file: Path
    config_file: Path


def get_config_paths(env: dict[str, str] | None = None) -> ConfigPaths:
    """Resolve config paths, honoring ``XDG_CONFIG_HOME``.

    Args:
        env: Environment mapping to consult. Defaults to ``os.environ``.
    """
    environ = env if env is not None else os.environ
    xdg = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    config_dir = base / "goodeye"
    return ConfigPaths(
        config_dir=config_dir,
        credentials_file=config_dir / "credentials.json",
        config_file=config_dir / "config.json",
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_json_0600(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    # Replace preserves the tmp file's mode on POSIX; re-chmod defensively.
    os.chmod(path, 0o600)


def load_credentials(paths: ConfigPaths | None = None) -> dict[str, Any] | None:
    """Load ``credentials.json`` if present.

    Returns ``None`` when the file does not exist or is unreadable.
    """
    p = paths or get_config_paths()
    return _load_json(p.credentials_file)


def save_credentials(payload: dict[str, Any], paths: ConfigPaths | None = None) -> Path:
    """Persist credentials to disk with mode 0600.

    Args:
        payload: Must contain at minimum ``api_key``. Recommended to also include
            ``server`` so the CLI remembers which instance the key targets.
        paths: Optional override for config paths (used by tests).

    Returns:
        The path written.
    """
    p = paths or get_config_paths()
    _write_json_0600(p.credentials_file, payload)
    return p.credentials_file


def delete_credentials(paths: ConfigPaths | None = None) -> bool:
    """Delete the credentials file if it exists. Returns True if a file was removed."""
    p = paths or get_config_paths()
    if p.credentials_file.exists():
        p.credentials_file.unlink()
        return True
    return False


def load_client_config(paths: ConfigPaths | None = None) -> dict[str, Any] | None:
    """Load cached ``/.well-known/goodeye-client-config`` payload."""
    p = paths or get_config_paths()
    return _load_json(p.config_file)


def save_client_config(payload: dict[str, Any], paths: ConfigPaths | None = None) -> Path:
    """Persist the fetched client-config payload."""
    p = paths or get_config_paths()
    _write_json_0600(p.config_file, payload)
    return p.config_file


def get_server(paths: ConfigPaths | None = None, env: dict[str, str] | None = None) -> str:
    """Resolve the server URL according to source-order rules."""
    environ = env if env is not None else os.environ
    if override := environ.get("GOODEYE_SERVER"):
        return override.rstrip("/")
    creds = load_credentials(paths)
    if creds and isinstance(creds.get("server"), str):
        return str(creds["server"]).rstrip("/")
    return DEFAULT_SERVER


def get_api_key(paths: ConfigPaths | None = None, env: dict[str, str] | None = None) -> str | None:
    """Resolve the active API key according to source-order rules."""
    environ = env if env is not None else os.environ
    if override := environ.get("GOODEYE_API_KEY"):
        return override
    creds = load_credentials(paths)
    if creds and isinstance(creds.get("api_key"), str):
        return str(creds["api_key"])
    return None
