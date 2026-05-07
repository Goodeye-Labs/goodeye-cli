"""Tests for the auth subcommand group (list-keys, create-key, revoke-key)."""

from __future__ import annotations

import json

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
    result = runner.invoke(app, ["auth", "list-keys", "--table"])
    assert result.exit_code == 0, result.output
    assert "key_01" in result.output
    assert "key_02" in result.output
    assert "laptop" in result.output


@respx.mock
def test_auth_list_keys_defaults_to_one_compact_json_page(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/api-keys").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "key_01", "name": "laptop", "created_at": "2026-04-21T00:00:00Z"}
                ],
                "next_cursor": "more",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "list-keys"])

    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert result.output == (
        '{"items":[{"id":"key_01","name":"laptop",'
        '"created_at":"2026-04-21T00:00:00Z","last_used_at":null}],'
        '"next_cursor":"more"}\n'
    )
    assert dict(route.calls.last.request.url.params) == {"limit": "25"}


@respx.mock
def test_auth_list_keys_all_follows_cursor(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/api-keys").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "key_01", "name": "laptop", "created_at": "2026-04-21T00:00:00Z"}
                    ],
                    "next_cursor": "next",
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "key_02", "name": "ci", "created_at": "2026-04-21T01:00:00Z"}
                    ],
                    "next_cursor": None,
                },
            ),
        ]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "list-keys", "--all", "--cursor", "start"])

    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    data = json.loads(result.output)
    assert [item["id"] for item in data["items"]] == ["key_01", "key_02"]
    assert data["next_cursor"] is None
    assert dict(route.calls[0].request.url.params) == {"limit": "25", "cursor": "start"}
    assert dict(route.calls[1].request.url.params) == {"limit": "25", "cursor": "next"}


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
