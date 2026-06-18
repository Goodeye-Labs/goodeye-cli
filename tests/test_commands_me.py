"""Tests for the `goodeye me ...` subcommand group."""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import (
    Conflict,
    RateLimited,
    ValidationFailed,
    error_from_body,
)

SERVER = "https://example.test"


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


@respx.mock
def test_claim_handle_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.patch(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(
            200, json={"handle": "alice", "claimed_at": "2026-04-24T21:45:00+00:00"}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "claim-handle", "alice"])
    assert result.exit_code == 0, result.output
    assert "alice" in result.output


@respx.mock
def test_claim_handle_reserved_errors_out(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.patch(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(
            409,
            json={"error": "handle_reserved", "message": "handle is reserved"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "claim-handle", "anthropic"])
    assert result.exit_code != 0


def test_claim_handle_without_credentials_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["me", "claim-handle", "alice"])
    assert result.exit_code != 0


@respx.mock
def test_rename_handle_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.post(f"{SERVER}/v1/me/rename-handle").mock(
        return_value=httpx.Response(
            200,
            json={
                "handle": "alice-renamed",
                "claimed_at": "2026-04-25T12:00:00+00:00",
                "renamed": True,
                "self_reclaim": False,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "rename-handle", "alice-renamed"])
    assert result.exit_code == 0, result.output
    assert "alice-renamed" in result.output
    assert "Renamed" in result.output


@respx.mock
def test_rename_handle_self_reclaim(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.post(f"{SERVER}/v1/me/rename-handle").mock(
        return_value=httpx.Response(
            200,
            json={
                "handle": "alice",
                "claimed_at": "2026-04-25T12:00:00+00:00",
                "renamed": True,
                "self_reclaim": True,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "rename-handle", "alice"])
    assert result.exit_code == 0, result.output
    assert "Reclaimed" in result.output
    assert "alice" in result.output


@respx.mock
def test_rename_handle_too_soon_errors(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.post(f"{SERVER}/v1/me/rename-handle").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": "handle_rename_too_soon",
                "message": "rename cooldown not yet elapsed",
                "hint": "wait 30 days between renames",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "rename-handle", "another"])
    assert result.exit_code != 0


def test_rename_handle_without_credentials_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["me", "rename-handle", "alice"])
    assert result.exit_code != 0


# ----- handle error slug mapping -----


def test_error_slug_handle_invalid() -> None:
    err = error_from_body(
        400,
        {"error": "handle_invalid", "message": "handle does not satisfy the allowed format"},
    )
    assert isinstance(err, ValidationFailed)
    assert err.slug == "handle_invalid"


def test_error_slug_handle_reserved() -> None:
    err = error_from_body(
        409,
        {"error": "handle_reserved", "message": "handle is reserved and cannot be claimed"},
    )
    assert isinstance(err, Conflict)
    assert err.slug == "handle_reserved"


def test_error_slug_handle_taken() -> None:
    err = error_from_body(
        409,
        {"error": "handle_taken", "message": "handle is already taken"},
    )
    assert isinstance(err, Conflict)
    assert err.slug == "handle_taken"


def test_error_slug_handle_not_claimed() -> None:
    err = error_from_body(
        409,
        {"error": "handle_not_claimed", "message": "claim a handle before performing this action"},
    )
    assert isinstance(err, Conflict)
    assert err.slug == "handle_not_claimed"


def test_error_slug_handle_rename_too_soon() -> None:
    err = error_from_body(
        429,
        {
            "error": "handle_rename_too_soon",
            "message": "handles can be renamed at most once every 90 days",
        },
    )
    assert isinstance(err, RateLimited)
    assert err.slug == "handle_rename_too_soon"


def test_error_slug_handle_rename_limit_exceeded() -> None:
    err = error_from_body(
        429,
        {
            "error": "handle_rename_limit_exceeded",
            "message": "handle rename limit reached for the current calendar year (max 3)",
        },
    )
    assert isinstance(err, RateLimited)
    assert err.slug == "handle_rename_limit_exceeded"
