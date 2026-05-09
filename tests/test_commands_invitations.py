"""Tests for the `goodeye invitations ...` subcommand group."""

from __future__ import annotations

import json

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths

SERVER = "https://example.test"
_INV_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_INV_ID2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_ITEM = {
    "id": _INV_ID,
    "kind": "team_membership",
    "target_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "target_label": "@acme",
    "proposed_by_handle": "alice",
    "proposed_to_handle": "bob",
    "created_at": "2026-01-01T00:00:00Z",
    "expires_at": "2026-02-01T00:00:00Z",
    "resolved_at": None,
    "resolution": None,
}


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


@respx.mock
def test_invitations_list_calls_correct_endpoint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    route = respx.get(f"{SERVER}/v1/invitations").mock(
        return_value=httpx.Response(200, json={"items": [_ITEM], "next_cursor": None})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert _INV_ID in result.output
    # Rich may truncate column content in narrow terminals; check short prefixes.
    assert "tea" in result.output  # "team_membership" prefix
    assert "ali" in result.output  # "alice" prefix from proposed_by_handle
    assert "20" in result.output  # datetime prefix (2026-...)


@respx.mock
def test_invitations_list_filter_and_state_params(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    route = respx.get(f"{SERVER}/v1/invitations").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["invitations", "list", "--filter", "sent", "--state", "all", "--limit", "25"]
    )
    assert result.exit_code == 0, result.output
    assert route.called
    params = dict(route.calls.last.request.url.params)
    assert params.get("filter") == "sent"
    assert params.get("state") == "all"
    assert params.get("limit") == "25"


@respx.mock
def test_invitations_list_json_output(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/invitations").mock(
        return_value=httpx.Response(200, json={"items": [_ITEM], "next_cursor": None})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "list", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["id"] == _INV_ID


@respx.mock
def test_invitations_list_next_cursor_hint(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/invitations").mock(
        return_value=httpx.Response(200, json={"items": [_ITEM], "next_cursor": "tok_abc"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "tok_abc" in result.output


@respx.mock
def test_invitations_accept_calls_correct_endpoint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    route = respx.post(f"{SERVER}/v1/invitations/{_INV_ID}/accept").mock(
        return_value=httpx.Response(200, json={"team_id": "t1", "user_id": "u1", "role": "member"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "accept", _INV_ID])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "Accepted" in result.output


@respx.mock
def test_invitations_accept_json_output(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    payload = {"team_id": "t1", "user_id": "u1", "role": "member"}
    respx.post(f"{SERVER}/v1/invitations/{_INV_ID}/accept").mock(
        return_value=httpx.Response(200, json=payload)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "accept", _INV_ID, "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["team_id"] == "t1"


@respx.mock
def test_invitations_decline_calls_correct_endpoint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    route = respx.post(f"{SERVER}/v1/invitations/{_INV_ID}/decline").mock(
        return_value=httpx.Response(200, json={"invitation_id": _INV_ID, "resolution": "declined"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "decline", _INV_ID])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "Declined" in result.output


@respx.mock
def test_invitations_cancel_calls_correct_endpoint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    route = respx.post(f"{SERVER}/v1/invitations/{_INV_ID}/cancel").mock(
        return_value=httpx.Response(200, json={"invitation_id": _INV_ID, "resolution": "cancelled"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "cancel", _INV_ID])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "Cancelled" in result.output


def test_invitations_list_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "list"])
    assert result.exit_code != 0


def test_invitations_accept_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["invitations", "accept", _INV_ID])
    assert result.exit_code != 0
