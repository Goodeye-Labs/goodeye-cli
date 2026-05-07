"""Helpers for checking whether a newer Goodeye CLI is available on PyPI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Literal

import httpx
from packaging.version import InvalidVersion, Version

from goodeye_cli import __version__
from goodeye_cli.config import ConfigPaths, _load_json, _write_json_0600, get_config_paths
from goodeye_cli.errors import GoodeyeError

PYPI_JSON_URL = "https://pypi.org/pypi/goodeye/json"
UPDATE_CHECK_INTERVAL_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    update_available: bool
    pypi_url: str = PYPI_JSON_URL


def _update_error(message: str) -> GoodeyeError:
    return GoodeyeError(slug="update_check_failed", message=message)


def should_suppress_background_notice(args: Sequence[str], env: Mapping[str, str]) -> bool:
    """Return whether a CLI invocation should skip best-effort update notices."""
    if env.get("CI"):
        return True
    if not args:
        return True
    if "--json" in args:
        return True
    if "--help" in args or "-h" in args:
        return True

    first_arg = args[0]
    return first_arg in {"--version", "help", "update"}


def format_update_notice(result: UpdateCheckResult) -> str:
    """Format the concise stderr notice for an available update."""
    return f"goodeye {result.latest_version} is available; run: goodeye update"


def _parse_version(value: str, *, label: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion as exc:
        raise _update_error(f"Invalid {label} version from PyPI update check: {value}") from exc


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def fetch_latest_pypi_version(
    timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Fetch and validate the latest Goodeye CLI version advertised by PyPI."""
    try:
        with httpx.Client(timeout=timeout, transport=transport) as http:
            response = http.get(PYPI_JSON_URL)
    except httpx.HTTPError as exc:
        raise _update_error("Unable to reach PyPI while checking for Goodeye CLI updates.") from exc

    if response.is_error:
        raise _update_error(f"PyPI returned HTTP {response.status_code} during update check.")

    try:
        payload = response.json()
    except (ValueError, httpx.DecodingError) as exc:
        raise _update_error("PyPI returned invalid JSON during update check.") from exc

    info = payload.get("info") if isinstance(payload, dict) else None
    latest_version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(latest_version, str) or not latest_version.strip():
        raise _update_error("PyPI response did not include info.version for Goodeye.")

    _parse_version(latest_version, label="latest")
    return latest_version


def is_update_available(current: str, latest: str) -> bool:
    """Return whether ``latest`` is a newer installable version than ``current``."""
    current_version = _parse_version(current, label="current")
    latest_version = _parse_version(latest, label="latest")

    if latest_version <= current_version:
        return False
    return not (latest_version.is_prerelease and not current_version.is_prerelease)


def load_update_cache(paths: ConfigPaths | None = None) -> dict[str, Any] | None:
    """Load the update-check cache if present and valid JSON."""
    p = paths or get_config_paths()
    return _load_json(p.update_check_file)


def save_update_cache(
    latest_version: str | None,
    paths: ConfigPaths | None = None,
    checked_at: datetime | None = None,
) -> Path:
    """Persist the latest PyPI version (or a null failure marker) to the config dir."""
    p = paths or get_config_paths()
    timestamp = _normalize_datetime(checked_at or datetime.now(UTC))
    payload: dict[str, Any] = {
        "checked_at": timestamp.isoformat(),
        "latest_version": latest_version,
        "pypi_url": PYPI_JSON_URL,
    }
    _write_json_0600(p.update_check_file, payload)
    return p.update_check_file


def check_for_update(
    current_version: str = __version__,
    paths: ConfigPaths | None = None,
    timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> UpdateCheckResult:
    """Perform an explicit update check, surfacing network, parse, and cache failures."""
    latest_version = fetch_latest_pypi_version(timeout=timeout, transport=transport)
    save_update_cache(latest_version, paths=paths)
    return UpdateCheckResult(
        current_version=current_version,
        latest_version=latest_version,
        update_available=is_update_available(current_version, latest_version),
    )


def _cache_is_fresh(cache: dict[str, Any], *, now: datetime) -> bool:
    checked_at_raw = cache.get("checked_at")
    if not isinstance(checked_at_raw, str):
        return False
    try:
        checked_at = _normalize_datetime(datetime.fromisoformat(checked_at_raw))
    except ValueError:
        return False
    return (now - checked_at).total_seconds() < UPDATE_CHECK_INTERVAL_SECONDS


def check_for_update_background(
    current_version: str = __version__,
    paths: ConfigPaths | None = None,
    now: datetime | None = None,
    timeout: float = 1.5,
    transport: httpx.BaseTransport | None = None,
) -> UpdateCheckResult | None:
    """Best-effort update check for non-blocking callers.

    Background checks intentionally swallow network, cache, and parse failures.
    A fresh failure marker in the cache also short-circuits to ``None`` so
    repeat invocations during a PyPI outage do not each pay the network timeout.
    """
    try:
        checked_at = _normalize_datetime(now or datetime.now(UTC))
        cache = load_update_cache(paths)
        if cache is not None and _cache_is_fresh(cache, now=checked_at):
            cached_version = cache.get("latest_version")
            if isinstance(cached_version, str):
                return UpdateCheckResult(
                    current_version=current_version,
                    latest_version=cached_version,
                    update_available=is_update_available(current_version, cached_version),
                )
            return None

        try:
            latest_version = fetch_latest_pypi_version(timeout=timeout, transport=transport)
        except GoodeyeError:
            save_update_cache(None, paths=paths, checked_at=checked_at)
            return None

        save_update_cache(latest_version, paths=paths, checked_at=checked_at)
        return UpdateCheckResult(
            current_version=current_version,
            latest_version=latest_version,
            update_available=is_update_available(current_version, latest_version),
        )
    except Exception:
        return None


InstallMethod = Literal["uv_tool", "pipx", "pip", "unsupported"]

_DIST_NAME = "goodeye"


def manual_update_commands_text() -> str:
    """README-style commands for manual upgrades when auto-detect fails."""
    return (
        "Try one of:\n"
        "  uv tool upgrade goodeye\n"
        "  pipx upgrade goodeye\n"
        f"  {sys.executable} -m pip install --upgrade goodeye"
    )


def _load_direct_url_payload(dist: metadata.Distribution) -> dict[str, Any] | None:
    try:
        raw = dist.read_text("direct_url.json")
    except (OSError, UnicodeError, ValueError):
        return None
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _direct_url_requires_manual_install(payload: dict[str, Any]) -> bool:
    """Return True when PEP 610 metadata indicates editable, local, or VCS installs."""
    url = payload.get("url")
    if isinstance(url, str):
        lowered = url.lower()
        if lowered.startswith("file:"):
            return True
        if any(
            lowered.startswith(prefix)
            for prefix in ("git+", "hg+", "svn+", "bzr+", "ssh://", "git@")
        ):
            return True
        if "+" in url and "://" in url:
            return True

    dir_info = payload.get("dir_info")
    return isinstance(dir_info, dict) and dir_info.get("editable") is True


def _direct_url_is_confident_pypi_wheel(payload: dict[str, Any]) -> bool:
    url = payload.get("url")
    if not isinstance(url, str):
        return False
    return "files.pythonhosted.org" in url or url.startswith("https://pypi.org/")


def _prefix_is_uv_tool_goodeye(prefix: Path) -> bool:
    posix = prefix.resolve().as_posix().lower()
    return "uv/tools/goodeye" in posix or "uv/tool/goodeye" in posix


def _prefix_is_pipx_goodeye(prefix: Path, environ: Mapping[str, str]) -> bool:
    posix = prefix.resolve().as_posix().lower()
    if "/pipx/venvs/goodeye" in posix:
        return True
    pipx_home = environ.get("PIPX_HOME")
    if not pipx_home:
        return False
    try:
        expected = Path(pipx_home).expanduser().resolve() / "venvs" / "goodeye"
    except OSError:
        return False
    return prefix.resolve() == expected


def sysconfig_get_scripts_dir(prefix: Path) -> Path:
    """Return the directory where console scripts are installed for ``prefix``."""
    import sysconfig

    scheme = sysconfig.get_preferred_scheme("prefix")
    vars_map = {"base": str(prefix), "platbase": str(prefix)}
    return Path(sysconfig.get_path("scripts", scheme, vars=vars_map))


def _pip_layout_eligible(
    exe: str,
    prefix: Path,
    direct: dict[str, Any] | None,
) -> bool:
    try:
        bin_dir = Path(exe).resolve().parent
        prefix_bin = (prefix / "bin").resolve()
        scripts_dir = Path(sysconfig_get_scripts_dir(prefix)).resolve()
    except OSError:
        return False

    if bin_dir not in {prefix_bin, scripts_dir}:
        return False

    return direct is None or _direct_url_is_confident_pypi_wheel(direct)


def detect_install_method(
    *,
    sys_executable: str | None = None,
    sys_prefix: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    distribution: metadata.Distribution | None = None,
) -> InstallMethod:
    """Best-effort detection of how ``goodeye`` was installed.

    When the layout is ambiguous or not a standard PyPI install, returns
    ``unsupported`` so callers can print manual upgrade instructions.
    """
    exe = sys_executable or sys.executable
    prefix = Path(sys_prefix or sys.prefix).resolve()
    env: Mapping[str, str] = environ if environ is not None else os.environ

    try:
        dist = distribution or metadata.distribution(_DIST_NAME)
    except metadata.PackageNotFoundError:
        return "unsupported"

    direct = _load_direct_url_payload(dist)
    if direct is not None and _direct_url_requires_manual_install(direct):
        return "unsupported"

    if _prefix_is_uv_tool_goodeye(prefix):
        return "uv_tool"
    if _prefix_is_pipx_goodeye(prefix, env):
        return "pipx"

    if not _pip_layout_eligible(exe, prefix, direct):
        return "unsupported"

    return "pip"


def _default_run_cmd(argv: Sequence[str]) -> CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        text=True,
    )


def _invoke_update_runner(
    runner: Callable[[list[str]], CompletedProcess[str]],
    argv: list[str],
) -> CompletedProcess[str]:
    try:
        return runner(argv)
    except OSError as exc:
        joined = " ".join(argv)
        raise GoodeyeError(
            slug="update_command_os_error",
            message=f"Could not execute update command: {joined}\n{type(exc).__name__}: {exc}",
            hint=manual_update_commands_text(),
        ) from exc


def _tool_missing_error(tool: str, argv: list[str]) -> GoodeyeError:
    joined = " ".join(argv)
    return GoodeyeError(
        slug="update_tool_missing",
        message=f"{tool} is not available on PATH; cannot run: {joined}",
        hint=manual_update_commands_text(),
    )


def _subprocess_failed_error(argv: list[str], proc: CompletedProcess[str]) -> GoodeyeError:
    joined = " ".join(argv)
    return GoodeyeError(
        slug="update_command_failed",
        message=(
            f"Update command failed (exit {proc.returncode}): {joined}\n"
            "See the upgrade tool's output above for the underlying error."
        ),
        hint=manual_update_commands_text(),
    )


def run_update(
    method: InstallMethod,
    *,
    run_cmd: Callable[[list[str]], CompletedProcess[str]] | None = None,
    which: Callable[[str], str | None] | None = None,
    python_executable: str | None = None,
) -> None:
    """Run the updater for a detected install method.

    ``run_cmd`` defaults to ``subprocess.run`` with captured output. Tests may
    inject a stub that returns a ``CompletedProcess`` without executing a shell.
    """
    if method == "unsupported":
        raise GoodeyeError(
            slug="update_unsupported_install",
            message="Unsupported install method for automatic updates.",
            hint=manual_update_commands_text(),
        )

    runner = run_cmd or _default_run_cmd
    which_fn = which or shutil.which
    py = python_executable or sys.executable

    if method == "uv_tool":
        uv = which_fn("uv")
        if not uv:
            raise _tool_missing_error("uv", ["uv", "tool", "upgrade", _DIST_NAME])
        argv = [uv, "tool", "upgrade", _DIST_NAME]
        proc = _invoke_update_runner(runner, argv)
        if proc.returncode != 0:
            raise _subprocess_failed_error(argv, proc)
        return

    if method == "pipx":
        pipx = which_fn("pipx")
        if not pipx:
            raise _tool_missing_error("pipx", ["pipx", "upgrade", _DIST_NAME])
        argv = [pipx, "upgrade", _DIST_NAME]
        proc = _invoke_update_runner(runner, argv)
        if proc.returncode != 0:
            raise _subprocess_failed_error(argv, proc)
        return

    argv = [py, "-m", "pip", "install", "--upgrade", _DIST_NAME]
    proc = _invoke_update_runner(runner, argv)
    if proc.returncode != 0:
        raise _subprocess_failed_error(argv, proc)
