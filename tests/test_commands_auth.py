"""Tests for the auth subcommand group (list-keys, create-key, revoke-key)."""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_auth_create_key_prints_secret(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/api-keys").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "key_01",
                "name": "ci",
                "key": "good_live_EXAMPLE_secret",
                "created_at": "2026-04-21T00:00:00Z",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "create-key", "--name", "ci"])
    assert result.exit_code == 0, result.output
    assert "good_live_EXAMPLE_secret" in result.output
    assert "key_01" in result.output


@respx.mock
def test_auth_list_keys_renders_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/api-keys").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "key_01", "name": "laptop", "created_at": "2026-04-21T00:00:00Z"},
                    {"id": "key_02", "name": "ci", "created_at": "2026-04-21T01:00:00Z"},
                ],
                "next_cursor": None,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "list-keys"])
    assert result.exit_code == 0, result.output
    assert "key_01" in result.output
    assert "key_02" in result.output
    assert "laptop" in result.output


@respx.mock
def test_auth_revoke_key(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/api-keys/key_01").mock(return_value=httpx.Response(204))

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "revoke-key", "key_01"])
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output


def test_auth_without_credentials_errors(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["auth", "list-keys"])
    assert result.exit_code != 0
