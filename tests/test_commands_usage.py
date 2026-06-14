"""Tests for the `goodeye usage` command and 402/403 error rendering."""

from __future__ import annotations

import httpx
import pytest
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.commands.usage import format_usage_summary
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import (
    AccountSuspended,
    AnonymousDailyCapReached,
    BudgetExhausted,
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


# ----- format_usage_summary unit tests -----


def _body(**overrides) -> dict:
    base = {
        "tier": "hobby",
        "available_usd": "4.17",
        "monthly_remaining_usd": "4.17",
        "monthly_refill_usd": "5.00",
        "monthly_refill_at": "2026-05-14T00:00:00+00:00",
        "purchased_remaining_usd": "0.00",
        "unpaid_balance_usd": "0.00",
    }
    base.update(overrides)
    return base


def test_format_usage_summary_human() -> None:
    out = format_usage_summary(_body())
    assert "hobby" in out
    assert "Available: $4.17" in out
    assert "refills to $5.00 on 05/14/2026" in out
    # Unpaid line suppressed when zero.
    assert "Unpaid" not in out
    # No one-off credits line when zero.
    assert "one-off" not in out


def test_format_usage_summary_with_purchased() -> None:
    out = format_usage_summary(
        _body(
            available_usd="54.17",
            monthly_remaining_usd="4.17",
            purchased_remaining_usd="50.00",
        )
    )
    assert "Available: $54.17" in out
    assert "$4.17 monthly (refills to $5.00 on 05/14/2026)" in out
    assert "$50.00 one-off credits" in out


def test_format_usage_summary_with_unpaid() -> None:
    out = format_usage_summary(
        _body(
            tier="pro",
            available_usd="0.00",
            monthly_remaining_usd="0.00",
            monthly_refill_usd="20.00",
            unpaid_balance_usd="1.50",
        )
    )
    assert "pro" in out
    assert "$1.50" in out
    assert "Unpaid" in out


def test_format_usage_summary_no_em_dash() -> None:
    out = format_usage_summary(_body())
    assert "—" not in out


# ----- CLI command tests -----


@respx.mock
def test_usage_command_human(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me/usage").mock(return_value=httpx.Response(200, json=_body()))
    runner = CliRunner()
    result = runner.invoke(app, ["usage"])
    assert result.exit_code == 0, result.output
    assert "hobby" in result.output
    assert "Available: $4.17" in result.output
    assert "refills to $5.00" in result.output


@respx.mock
def test_usage_command_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me/usage").mock(
        return_value=httpx.Response(
            200,
            json=_body(
                tier="pro",
                available_usd="15.00",
                monthly_remaining_usd="15.00",
                monthly_refill_usd="20.00",
            ),
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["usage", "--json"])
    assert result.exit_code == 0, result.output
    import json

    data = json.loads(result.output.strip().splitlines()[-1])
    assert data["tier"] == "pro"
    assert data["available_usd"] == "15.00"
    assert data["monthly_refill_usd"] == "20.00"


def test_usage_command_without_credentials_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["usage"])
    assert result.exit_code != 0


# ----- 402 / 403 error mapping -----


def test_error_from_body_maps_account_suspended() -> None:
    err = error_from_body(
        403,
        {
            "error": "account_suspended",
            "message": "Your Goodeye account has been suspended.",
        },
    )
    assert isinstance(err, AccountSuspended)
    assert err.status_code == 403
    assert err.slug == "account_suspended"
    assert "suspended" in err.message


def test_error_from_body_maps_budget_exhausted() -> None:
    err = error_from_body(
        402,
        {
            "error": "budget_exhausted",
            "message": "Your monthly Goodeye credits are exhausted.",
            "extras": {"period_end": "2026-05-14T00:00:00+00:00"},
        },
    )
    assert isinstance(err, BudgetExhausted)
    assert err.status_code == 402
    assert err.slug == "budget_exhausted"
    assert "exhausted" in err.message


def test_error_from_body_maps_anonymous_daily_cap() -> None:
    err = error_from_body(
        402,
        {
            "error": "anonymous_daily_cap",
            "message": "Goodeye's anonymous usage limit for today has been reached.",
            "resets_at": "2026-06-15T00:00:00+00:00",
        },
    )
    assert isinstance(err, AnonymousDailyCapReached)
    assert err.status_code == 402
    assert err.slug == "anonymous_daily_cap"
    assert "anonymous" in err.message.lower()
    assert err.extras["resets_at"] == "2026-06-15T00:00:00+00:00"


@respx.mock
def test_usage_command_renders_account_suspended(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me/usage").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": "account_suspended",
                "message": "Your Goodeye account has been suspended.",
            },
        )
    )
    runner = CliRunner()
    # CliRunner does not invoke the GoodeyeError handler in main(); it lets the
    # exception propagate. Confirm the typed exception is raised so the
    # console-script wrapper renders it.
    result = runner.invoke(app, ["usage"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AccountSuspended)
    assert "suspended" in result.exception.message


@respx.mock
def test_usage_command_renders_budget_exhausted(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me/usage").mock(
        return_value=httpx.Response(
            402,
            json={
                "error": "budget_exhausted",
                "message": "Your monthly Goodeye credits are exhausted.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["usage"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BudgetExhausted)
    assert "exhausted" in result.exception.message


# Sanity: the exception classes are wired into the slug map.
def test_slug_classes_distinct_from_server_error() -> None:
    from goodeye_cli.errors import ServerError

    suspended = error_from_body(403, {"error": "account_suspended", "message": "x"})
    exhausted = error_from_body(402, {"error": "budget_exhausted", "message": "x"})
    assert not isinstance(suspended, ServerError)
    assert not isinstance(exhausted, ServerError)


# ----- module-level guards -----


def test_usage_command_module_has_no_em_dash() -> None:
    """The brand constraint forbids em dashes anywhere in source."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "usage.py"
    assert "—" not in src.read_text()


def test_usage_command_help_lists_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "usage" in result.output


# Avoid "imported but unused" if pytest is reordered.
_ = pytest
