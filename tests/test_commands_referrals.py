"""Tests for the `goodeye referral ...` subcommand group.

Covers happy-path status and redeem, --json output, all four error slugs,
and missing credentials.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import (
    AuthRequired,
    Conflict,
    NotFound,
    error_from_body,
)

SERVER = "https://example.test"

_STATUS_BODY = {
    "code": "GOODEYE-ABC123",
    "instructions": "Share this code with a friend to earn credits.",
    "redeemed_count": 2,
    "qualified_count": 1,
    "credits_earned_usd": "5.00",
    "slots_remaining": 8,
}

_REDEEM_BODY = {
    "status": "pending",
    "credits_granted_usd": "5.00",
    "expires_at": "2026-09-15T00:00:00+00:00",
    "referrer_handle": "alice",
}


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


# ----- referral status -----


@respx.mock
def test_referral_status_human(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.get(f"{SERVER}/v1/referrals/me").mock(return_value=httpx.Response(200, json=_STATUS_BODY))
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "status"])
    assert result.exit_code == 0, result.output
    assert "GOODEYE-ABC123" in result.output
    assert "Share this code" in result.output
    assert "Redeemed: 2" in result.output
    assert "Qualified: 1" in result.output
    assert "$5.00" in result.output
    assert "Slots remaining: 8" in result.output


@respx.mock
def test_referral_status_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.get(f"{SERVER}/v1/referrals/me").mock(return_value=httpx.Response(200, json=_STATUS_BODY))
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["code"] == "GOODEYE-ABC123"
    assert data["redeemed_count"] == 2
    assert data["qualified_count"] == 1
    assert data["credits_earned_usd"] == "5.00"
    assert data["slots_remaining"] == 8
    assert "instructions" in data


def test_referral_status_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "status"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


# ----- referral redeem -----


@respx.mock
def test_referral_redeem_human(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(200, json=_REDEEM_BODY)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "GOODEYE-ABC123"])
    assert result.exit_code == 0, result.output
    assert "redeemed" in result.output.lower()
    assert "pending" in result.output
    assert "$5.00" in result.output
    assert "@alice" in result.output


@respx.mock
def test_referral_redeem_human_blank_referrer_handle(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When the server returns an empty referrer_handle, do not print a bare `@`."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    body = {**_REDEEM_BODY, "referrer_handle": ""}
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(return_value=httpx.Response(200, json=body))
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "GOODEYE-ABC123"])
    assert result.exit_code == 0, result.output
    assert "redeemed" in result.output.lower()
    assert "$5.00" in result.output
    assert "Referred by:" not in result.output
    assert "@" not in result.output


@respx.mock
def test_referral_redeem_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(200, json=_REDEEM_BODY)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "GOODEYE-ABC123", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["status"] == "pending"
    assert data["credits_granted_usd"] == "5.00"
    assert data["referrer_handle"] == "alice"
    assert "expires_at" in data


def test_referral_redeem_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "GOODEYE-ABC123"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


# ----- error slug mapping -----


def test_error_slug_referral_code_not_found() -> None:
    err = error_from_body(
        404,
        {"error": "referral_code_not_found", "message": "Referral code not found."},
    )
    assert isinstance(err, NotFound)
    assert err.slug == "referral_code_not_found"
    assert "not found" in err.message.lower()


def test_error_slug_self_referral() -> None:
    err = error_from_body(
        409,
        {"error": "self_referral", "message": "You cannot redeem your own referral code."},
    )
    assert isinstance(err, Conflict)
    assert err.slug == "self_referral"
    assert "own" in err.message.lower()


def test_error_slug_already_referred() -> None:
    err = error_from_body(
        409,
        {"error": "already_referred", "message": "You have already used a referral code."},
    )
    assert isinstance(err, Conflict)
    assert err.slug == "already_referred"
    assert "already" in err.message.lower()


def test_error_slug_referral_not_eligible() -> None:
    err = error_from_body(
        409,
        {"error": "referral_not_eligible", "message": "This referral code is no longer eligible."},
    )
    assert isinstance(err, Conflict)
    assert err.slug == "referral_not_eligible"
    assert "eligible" in err.message.lower()


@respx.mock
def test_referral_redeem_surfaces_self_referral_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            409,
            json={"error": "self_referral", "message": "You cannot redeem your own referral code."},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "MYCODE"])
    assert result.exit_code != 0
    assert isinstance(result.exception, Conflict)
    assert result.exception.slug == "self_referral"


@respx.mock
def test_referral_redeem_surfaces_already_referred_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            409,
            json={"error": "already_referred", "message": "You have already used a referral code."},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "SOMEREF"])
    assert result.exit_code != 0
    assert isinstance(result.exception, Conflict)
    assert result.exception.slug == "already_referred"


@respx.mock
def test_referral_redeem_surfaces_code_not_found_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.get(f"{SERVER}/v1/referrals/me").mock(
        return_value=httpx.Response(
            404,
            json={"error": "referral_code_not_found", "message": "Referral code not found."},
        )
    )
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            404,
            json={"error": "referral_code_not_found", "message": "Referral code not found."},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "BADCODE"])
    assert result.exit_code != 0
    assert isinstance(result.exception, NotFound)
    assert result.exception.slug == "referral_code_not_found"


@respx.mock
def test_referral_redeem_surfaces_not_eligible_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "referral_not_eligible",
                "message": "This referral code is no longer eligible.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["referral", "redeem", "EXPIREDCODE"])
    assert result.exit_code != 0
    assert isinstance(result.exception, Conflict)
    assert result.exception.slug == "referral_not_eligible"


# ----- module-level guards -----


def test_referral_commands_no_em_dash() -> None:
    """The brand constraint forbids em dashes anywhere in source."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "referrals.py"
    )
    assert "—" not in src.read_text()


def test_referral_command_appears_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "referral" in result.output


# Avoid "imported but unused" if pytest is reordered.
_ = pytest
