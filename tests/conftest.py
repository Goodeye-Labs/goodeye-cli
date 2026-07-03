"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from goodeye_cli.config import ConfigPaths


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never open a real browser during tests.

    Command groups such as billing open a checkout or portal URL through
    webbrowser.open. Neutralizing it suite-wide means a test can never pop a
    browser tab, even if a per-test stub targets the wrong module path. Tests
    that assert on the opened URL patch their command module's opener directly.
    """
    monkeypatch.setattr("webbrowser.open", lambda *args, **kwargs: True)


@pytest.fixture
def tmp_config_paths(tmp_path: Path) -> ConfigPaths:
    """A ConfigPaths rooted at a temp directory, used in place of ~/.config/goodeye."""
    config_dir = tmp_path / "goodeye"
    return ConfigPaths(
        config_dir=config_dir,
        credentials_file=config_dir / "credentials.json",
        config_file=config_dir / "config.json",
        update_check_file=config_dir / "update-check.json",
        sync_file=config_dir / "sync.json",
        sync_state_file=config_dir / "sync-state.json",
        sync_lock_file=config_dir / "sync.lock",
    )


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Scrub CLI env vars so tests don't pick up the developer's real settings."""
    for var in ("GOODEYE_API_KEY", "GOODEYE_SERVER", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    yield
