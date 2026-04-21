"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from goodeye_cli.config import ConfigPaths


@pytest.fixture
def tmp_config_paths(tmp_path: Path) -> ConfigPaths:
    """A ConfigPaths rooted at a temp directory, used in place of ~/.config/goodeye."""
    config_dir = tmp_path / "goodeye"
    return ConfigPaths(
        config_dir=config_dir,
        credentials_file=config_dir / "credentials.json",
        config_file=config_dir / "config.json",
    )


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Scrub CLI env vars so tests don't pick up the developer's real settings."""
    for var in ("GOODEYE_API_KEY", "GOODEYE_SERVER", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    yield
