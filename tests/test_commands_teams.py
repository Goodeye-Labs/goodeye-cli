"""Tests for the `goodeye teams ...` subcommand group.

Covers happy-path create/list/delete and two error surfaces
(``handle_not_claimed`` on create, missing credentials) to confirm the
CLI forwards the server's structured errors.
"""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths

SERVER = "https://example.test"
_TEAM_ID = "11111111-1111-1111-1111-111111111111"
_USER_ID = "22222222-2222-2222-2222-222222222222"


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


@respx.mock
def test_teams_create_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.post(f"{SERVER}/v1/teams").mock(
        return_value=httpx.Response(201, json={"team_id": _TEAM_ID, "handle": "acme"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["teams", "create", "acme"])
    assert result.exit_code == 0, result.output
    assert "acme" in result.output


@respx.mock
def test_teams_create_handle_not_claimed(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.post(f"{SERVER}/v1/teams").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "handle_not_claimed",
                "message": "claim a handle before performing this action",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["teams", "create", "acme"])
    assert result.exit_code != 0


@respx.mock
def test_teams_list_prints_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/teams").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "team_id": _TEAM_ID,
                        "handle": "acme",
                        "owner_user_id": _USER_ID,
                        "role": "owner",
                    }
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["teams", "list"])
    assert result.exit_code == 0, result.output
    assert "acme" in result.output


@respx.mock
def test_teams_delete_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.delete(f"{SERVER}/v1/teams/{_TEAM_ID}").mock(
        return_value=httpx.Response(200, json={"team_id": _TEAM_ID, "deleted": True})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["teams", "delete", _TEAM_ID, "--yes"])
    assert result.exit_code == 0, result.output


def test_teams_create_without_credentials_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["teams", "create", "acme"])
    assert result.exit_code != 0


@respx.mock
def test_teams_members_renders_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """Server returns `{items: [...]}`; the CLI must unwrap before validating rows."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/teams/{_TEAM_ID}/members").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": _USER_ID,
                        "email": "owner@example.com",
                        "handle": "ownerhandle",
                        "role": "owner",
                    },
                    {
                        "user_id": "33333333-3333-3333-3333-333333333333",
                        "email": "member@example.com",
                        "handle": None,
                        "role": "member",
                    },
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["teams", "members", _TEAM_ID])
    assert result.exit_code == 0, result.output
    assert "ownerhandle" in result.output
    assert "owner" in result.output
    assert "member" in result.output
