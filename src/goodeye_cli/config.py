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

Source order for request timeout seconds (highest wins):
    1. ``GOODEYE_TIMEOUT_SECONDS`` environment variable.
    2. Command-specific default.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from goodeye_cli.errors import ValidationFailed

DEFAULT_SERVER = "https://api.goodeye.dev"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
VERIFIER_REQUEST_TIMEOUT_SECONDS = 300.0

# One-shot migrator for stale ``server`` pins. Older installs of the CLI shipped
# with ``api.goodeyelabs.com`` as the default and wrote it into
# ``credentials.json`` at login time. The canonical host is now
# ``api.goodeye.dev``; the legacy host still resolves during the REST migration
# grace period but is deprecated. We rewrite the pin in place, idempotently,
# whenever we see it, so users move off the legacy host without re-running
# ``goodeye auth login``. Only the exact historical default migrates; any
# custom ``server`` value (staging, a fork, ``localhost``) is left alone.
# Delete this block once the legacy REST host is retired.
_LEGACY_API_HOSTS: frozenset[str] = frozenset({"https://api.goodeyelabs.com"})


@dataclass(frozen=True)
class ConfigPaths:
    """Resolved filesystem paths for CLI configuration files."""

    config_dir: Path
    credentials_file: Path
    config_file: Path
    update_check_file: Path
    sync_file: Path
    sync_state_file: Path
    sync_lock_file: Path


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
        update_check_file=config_dir / "update-check.json",
        sync_file=config_dir / "sync.json",
        sync_state_file=config_dir / "sync-state.json",
        sync_lock_file=config_dir / "sync.lock",
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
    """Atomically write ``payload`` as JSON to ``path`` with mode 0600.

    The canonical writer for every file under the config dir, not only the
    credentials file: update-check cache, sync config, and sync state route
    through it too. Those payloads are not secret, but 0600 is a harmless floor
    for them and the load-bearing guarantee for the ones that are (credentials,
    cached client config), so a single atomic-write path keeps the behavior
    uniform. Comments below reason about "the secret" because that is the
    strictest call site; they describe the mechanism, not a claim that every
    payload is sensitive.
    """
    # Create the config dir at 0700 for new installs, and best-effort tighten an
    # existing dir an older CLI created at the umask default. The 0600 file below
    # is the load-bearing guarantee; the 0700 dir is defense in depth, so a chmod
    # failure (e.g. a dir owned by another user) must not break the invocation.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    # mkstemp creates the file atomically with mode 0600 and a unique name,
    # closing the window a post-hoc chmod leaves open (the secret is never on
    # disk at a broader mode) and avoiding a fixed temp path that concurrent
    # writers would collide on. os.replace then atomically renames it into place,
    # preserving the 0600 mode, so no chmod is needed afterward.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _migrate_legacy_server_pin(creds: dict[str, Any], credentials_file: Path) -> dict[str, Any]:
    """Rewrite a stale ``server`` pin to the canonical host, in place.

    No-op unless the pin matches an exact historical default (case-insensitive,
    trailing slash stripped). On rewrite, persists the file with the same 0600
    permissions ``save_credentials`` uses and announces the change on stderr.
    Idempotent: the next read sees the new value and short-circuits.
    """
    raw = creds.get("server")
    if not isinstance(raw, str):
        return creds
    normalized = raw.rstrip("/").lower()
    if normalized not in _LEGACY_API_HOSTS:
        return creds
    migrated = dict(creds)
    migrated["server"] = DEFAULT_SERVER
    try:
        _write_json_0600(credentials_file, migrated)
    except OSError:
        # File became unwritable between read and write (e.g., read-only mount).
        # Don't fail the CLI invocation over a best-effort migration; the
        # in-memory dict still carries the new value for this run.
        return migrated
    sys.stderr.write(f"goodeye: migrated CLI server pin {normalized} -> {DEFAULT_SERVER}\n")
    return migrated


def load_credentials(paths: ConfigPaths | None = None) -> dict[str, Any] | None:
    """Load ``credentials.json`` if present.

    Returns ``None`` when the file does not exist or is unreadable.
    """
    p = paths or get_config_paths()
    creds = _load_json(p.credentials_file)
    if creds is None:
        return None
    return _migrate_legacy_server_pin(creds, p.credentials_file)


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


def get_request_timeout_seconds(
    env: dict[str, str] | None = None,
    *,
    default: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> float:
    """Resolve the HTTP request timeout in seconds."""
    environ = env if env is not None else os.environ
    raw = environ.get("GOODEYE_TIMEOUT_SECONDS")
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message="GOODEYE_TIMEOUT_SECONDS must be a positive number.",
        ) from exc
    if value <= 0:
        raise ValidationFailed(
            slug="validation_error",
            message="GOODEYE_TIMEOUT_SECONDS must be a positive number.",
        )
    return value


def get_api_key(paths: ConfigPaths | None = None, env: dict[str, str] | None = None) -> str | None:
    """Resolve the active API key according to source-order rules."""
    environ = env if env is not None else os.environ
    if override := environ.get("GOODEYE_API_KEY"):
        return override
    creds = load_credentials(paths)
    if creds and isinstance(creds.get("api_key"), str):
        return str(creds["api_key"])
    return None
