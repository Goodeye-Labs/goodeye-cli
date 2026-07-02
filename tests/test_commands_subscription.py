"""Tests for the `goodeye subscription ...` subcommand group and the
top-level `goodeye downgrade` alias.

Covers starting a Pro checkout, canceling a subscription, the alias, and the
three new billing error slugs (mirrors the referrals command test style).
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
    AlreadySubscribed,
    AuthRequired,
    BillingNotEnabled,
    NoActiveSubscription,
    error_from_body,
)

SERVER = "https://example.test"

_CHECKOUT_BODY = {"checkout_url": "https://checkout.stripe.com/c/pay/EXAMPLE"}
_CANCEL_BODY = {"status": "active", "access_until": "2026-08-01T00:00:00+00:00"}


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


# ----- subscription upgrade (checkout) -----


@respx.mock
def test_subscription_upgrade_prints_checkout_url_and_opens_browser(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/checkout").mock(
        return_value=httpx.Response(200, json=_CHECKOUT_BODY)
    )
    opened: list[str] = []
    monkeypatch.setattr(
        "goodeye_cli.commands.subscription._open_url", lambda url: opened.append(url)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "upgrade"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert _CHECKOUT_BODY["checkout_url"] in result.output
    assert opened == [_CHECKOUT_BODY["checkout_url"]]


@respx.mock
def test_subscription_upgrade_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/checkout").mock(
        return_value=httpx.Response(200, json=_CHECKOUT_BODY)
    )
    monkeypatch.setattr("goodeye_cli.commands.subscription._open_url", lambda url: None)
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "upgrade", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"checkout_url": _CHECKOUT_BODY["checkout_url"]}


def test_subscription_upgrade_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "upgrade"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_subscription_upgrade_surfaces_already_subscribed(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/checkout").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "already_subscribed",
                "message": "you already have an active subscription",
                "hint": "Manage your existing subscription from the billing portal.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "upgrade"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AlreadySubscribed)


@respx.mock
def test_subscription_upgrade_surfaces_billing_not_enabled(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/checkout").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "billing_not_enabled",
                "message": "self-service subscription billing is not enabled",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "upgrade"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BillingNotEnabled)


# ----- subscription cancel -----


@respx.mock
def test_subscription_cancel_prints_status_and_access_until(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json=_CANCEL_BODY)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "cancel"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "active" in result.output
    assert "08/01/2026" in result.output
    # Phrasing should make clear access continues until then, not immediately.
    assert "continues" in result.output.lower() or "until" in result.output.lower()


@respx.mock
def test_subscription_cancel_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json=_CANCEL_BODY)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "cancel", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"status": "active", "access_until": "2026-08-01T00:00:00+00:00"}


@respx.mock
def test_subscription_cancel_null_access_until(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json={"status": "canceled", "access_until": None})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "cancel"])
    assert result.exit_code == 0, result.output
    assert "canceled" in result.output


def test_subscription_cancel_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "cancel"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_subscription_cancel_surfaces_no_active_subscription(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(
            409,
            json={"error": "no_active_subscription", "message": "no active subscription to cancel"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscription", "cancel"])
    assert result.exit_code != 0
    assert isinstance(result.exception, NoActiveSubscription)


# ----- `goodeye downgrade` alias -----


@respx.mock
def test_downgrade_alias_dispatches_to_subscription_cancel(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json=_CANCEL_BODY)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["downgrade"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "active" in result.output


def test_downgrade_alias_hidden_from_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "downgrade" not in result.output


# ----- error slug mapping -----


def test_error_slug_billing_not_enabled() -> None:
    err = error_from_body(
        400,
        {
            "error": "billing_not_enabled",
            "message": "self-service subscription billing is not enabled",
        },
    )
    assert isinstance(err, BillingNotEnabled)
    assert err.slug == "billing_not_enabled"


def test_error_slug_already_subscribed() -> None:
    err = error_from_body(
        409,
        {"error": "already_subscribed", "message": "you already have an active subscription"},
    )
    assert isinstance(err, AlreadySubscribed)
    assert err.slug == "already_subscribed"


def test_error_slug_no_active_subscription() -> None:
    err = error_from_body(
        409,
        {"error": "no_active_subscription", "message": "no active subscription to cancel"},
    )
    assert isinstance(err, NoActiveSubscription)
    assert err.slug == "no_active_subscription"


# ----- module-level guards -----


def test_subscription_commands_no_em_dash() -> None:
    """The brand constraint forbids em dashes anywhere in source."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "goodeye_cli"
        / "commands"
        / "subscription.py"
    )
    assert "—" not in src.read_text()


def test_subscription_command_appears_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "subscription" in result.output


# Avoid "imported but unused" if pytest is reordered.
_ = pytest
