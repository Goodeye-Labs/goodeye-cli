"""Tests for goodeye_cli.config."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from goodeye_cli.config import (
    DEFAULT_SERVER,
    ConfigPaths,
    delete_credentials,
    get_api_key,
    get_config_paths,
    get_server,
    load_client_config,
    load_credentials,
    save_client_config,
    save_credentials,
)


def test_get_config_paths_uses_xdg(tmp_path: Path) -> None:
    paths = get_config_paths({"XDG_CONFIG_HOME": str(tmp_path)})
    assert paths.config_dir == tmp_path / "goodeye"
    assert paths.credentials_file == tmp_path / "goodeye" / "credentials.json"
    assert paths.config_file == tmp_path / "goodeye" / "config.json"


def test_get_config_paths_defaults_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = get_config_paths({})
    assert paths.config_dir == tmp_path / ".config" / "goodeye"


def test_save_credentials_writes_0600(tmp_config_paths: ConfigPaths) -> None:
    path = save_credentials(
        {"api_key": "good_live_EXAMPLE", "server": "https://x"}, tmp_config_paths
    )
    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    with path.open() as fh:
        data = json.load(fh)
    assert data == {"api_key": "good_live_EXAMPLE", "server": "https://x"}


def test_load_credentials_missing_returns_none(tmp_config_paths: ConfigPaths) -> None:
    assert load_credentials(tmp_config_paths) is None


def test_load_credentials_roundtrip(tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "k", "server": "s"}, tmp_config_paths)
    assert load_credentials(tmp_config_paths) == {"api_key": "k", "server": "s"}


def test_delete_credentials(tmp_config_paths: ConfigPaths) -> None:
    assert delete_credentials(tmp_config_paths) is False
    save_credentials({"api_key": "k"}, tmp_config_paths)
    assert delete_credentials(tmp_config_paths) is True
    assert not tmp_config_paths.credentials_file.exists()


def test_client_config_roundtrip(tmp_config_paths: ConfigPaths) -> None:
    payload = {
        "workos_client_id": "client_X",
        "workos_device_authorization_uri": "https://api.workos.com/user_management/authorize/device",
        "workos_token_uri": "https://api.workos.com/user_management/authenticate",
    }
    save_client_config(payload, tmp_config_paths)
    assert load_client_config(tmp_config_paths) == payload


def test_get_server_env_beats_credentials(
    tmp_config_paths: ConfigPaths,
) -> None:
    save_credentials({"api_key": "k", "server": "https://fromfile"}, tmp_config_paths)
    env = {"GOODEYE_SERVER": "https://fromenv"}
    assert get_server(tmp_config_paths, env) == "https://fromenv"
    assert get_server(tmp_config_paths, {}) == "https://fromfile"


def test_get_server_falls_back_to_default(tmp_config_paths: ConfigPaths) -> None:
    assert get_server(tmp_config_paths, {}) == DEFAULT_SERVER


def test_get_api_key_precedence(tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "from_file"}, tmp_config_paths)
    assert get_api_key(tmp_config_paths, {"GOODEYE_API_KEY": "from_env"}) == "from_env"
    assert get_api_key(tmp_config_paths, {}) == "from_file"


def test_get_api_key_none_when_no_source(tmp_config_paths: ConfigPaths) -> None:
    assert get_api_key(tmp_config_paths, {}) is None


def test_get_server_strips_trailing_slash(tmp_config_paths: ConfigPaths) -> None:
    assert get_server(tmp_config_paths, {"GOODEYE_SERVER": "https://x/"}) == "https://x"
