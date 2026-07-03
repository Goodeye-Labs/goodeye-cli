"""Tests for the `goodeye billing ...` subcommand group.

Covers the `plan upgrade` / `plan cancel` subgroup, the direct `portal` and
`buy-credits` commands, the billing error slugs, and the fact that the old
`goodeye subscription ...` group no longer exists (mirrors the referrals
command test style).
"""

from __future__ import annotations

import json
import re
from unittest import mock

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
    InvalidAmount,
    NoActiveSubscription,
    NoPaymentMethod,
    error_from_body,
)

SERVER = "https://example.test"

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

_CHECKOUT_BODY = {"checkout_url": "https://checkout.stripe.com/c/pay/EXAMPLE"}
_CANCEL_BODY = {"status": "active", "access_until": "2026-08-01T00:00:00+00:00"}
_PORTAL_BODY = {"portal_url": "https://billing.stripe.com/p/session/EXAMPLE"}
_CHARGED_BODY = {"status": "charged", "amount_usd": 25, "new_balance_usd": "25.00"}
_CREDIT_CHECKOUT_BODY = {
    "status": "checkout_required",
    "checkout_url": "https://checkout.stripe.com/c/pay/CREDITS-EXAMPLE",
}

runner = CliRunner()


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


# ----- billing plan upgrade (checkout) -----


@respx.mock
def test_billing_plan_upgrade_prints_checkout_url_and_opens_browser(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/checkout").mock(
        return_value=httpx.Response(200, json=_CHECKOUT_BODY)
    )
    opened: list[str] = []
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", opened.append)
    result = runner.invoke(app, ["billing", "plan", "upgrade"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert _CHECKOUT_BODY["checkout_url"] in result.output
    assert opened == [_CHECKOUT_BODY["checkout_url"]]


@respx.mock
def test_billing_plan_upgrade_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/checkout").mock(
        return_value=httpx.Response(200, json=_CHECKOUT_BODY)
    )
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", lambda url: None)
    result = runner.invoke(app, ["billing", "plan", "upgrade", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"checkout_url": _CHECKOUT_BODY["checkout_url"]}


def test_billing_plan_upgrade_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    result = runner.invoke(app, ["billing", "plan", "upgrade"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_billing_plan_upgrade_surfaces_already_subscribed(
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
    result = runner.invoke(app, ["billing", "plan", "upgrade"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AlreadySubscribed)


@respx.mock
def test_billing_plan_upgrade_surfaces_billing_not_enabled(
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
    result = runner.invoke(app, ["billing", "plan", "upgrade"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BillingNotEnabled)


# ----- billing plan cancel -----


@respx.mock
def test_billing_plan_cancel_prints_status_and_access_until(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json=_CANCEL_BODY)
    )
    result = runner.invoke(app, ["billing", "plan", "cancel"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "active" in result.output
    assert "08/01/2026" in result.output
    # Phrasing should make clear access continues until then, not immediately.
    assert "continues" in result.output.lower() or "until" in result.output.lower()


@respx.mock
def test_billing_plan_cancel_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json=_CANCEL_BODY)
    )
    result = runner.invoke(app, ["billing", "plan", "cancel", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"status": "active", "access_until": "2026-08-01T00:00:00+00:00"}


@respx.mock
def test_billing_plan_cancel_null_access_until(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(200, json={"status": "canceled", "access_until": None})
    )
    result = runner.invoke(app, ["billing", "plan", "cancel"])
    assert result.exit_code == 0, result.output
    assert "canceled" in result.output


def test_billing_plan_cancel_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    result = runner.invoke(app, ["billing", "plan", "cancel"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_billing_plan_cancel_surfaces_no_active_subscription(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/subscription/cancel").mock(
        return_value=httpx.Response(
            409,
            json={"error": "no_active_subscription", "message": "no active subscription to cancel"},
        )
    )
    result = runner.invoke(app, ["billing", "plan", "cancel"])
    assert result.exit_code != 0
    assert isinstance(result.exception, NoActiveSubscription)


# ----- billing portal -----


@respx.mock
def test_billing_portal_prints_url_and_opens_browser(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/portal").mock(
        return_value=httpx.Response(200, json=_PORTAL_BODY)
    )
    opened: list[str] = []
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", opened.append)
    result = runner.invoke(app, ["billing", "portal"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert _PORTAL_BODY["portal_url"] in result.output
    assert opened == [_PORTAL_BODY["portal_url"]]


@respx.mock
def test_billing_portal_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/portal").mock(
        return_value=httpx.Response(200, json=_PORTAL_BODY)
    )
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", lambda url: None)
    result = runner.invoke(app, ["billing", "portal", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"portal_url": _PORTAL_BODY["portal_url"]}


def test_billing_portal_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    result = runner.invoke(app, ["billing", "portal"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_billing_portal_surfaces_billing_not_enabled(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/portal").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "billing_not_enabled",
                "message": "self-service subscription billing is not enabled",
            },
        )
    )
    result = runner.invoke(app, ["billing", "portal"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BillingNotEnabled)


# ----- billing buy-credits -----


@respx.mock
def test_buy_credits_charged_prints_balance(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CHARGED_BODY)
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "25" in result.output
    assert "25.00" in result.output


@respx.mock
def test_buy_credits_charged_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CHARGED_BODY)
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"status": "charged", "amount_usd": 25, "new_balance_usd": "25.00"}


@respx.mock
def test_buy_credits_checkout_required_prints_url_and_opens_browser(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CREDIT_CHECKOUT_BODY)
    )
    opened: list[str] = []
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", opened.append)
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "30"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert _CREDIT_CHECKOUT_BODY["checkout_url"] in result.output
    assert opened == [_CREDIT_CHECKOUT_BODY["checkout_url"]]


@respx.mock
def test_buy_credits_checkout_required_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CREDIT_CHECKOUT_BODY)
    )
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", lambda url: None)
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "30", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {
        "status": "checkout_required",
        "checkout_url": _CREDIT_CHECKOUT_BODY["checkout_url"],
    }


@respx.mock
def test_buy_credits_auto_approves_when_not_tty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """CliRunner stdin is not a TTY, so the confirmation auto-approves without --yes."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CHARGED_BODY)
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1


def test_buy_credits_human_decline_exits_zero_without_calling_server(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    with mock.patch("goodeye_cli.commands.billing.confirm_destructive", return_value=False):
        result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25"])
    assert result.exit_code == 0, result.output
    assert "Cancelled" in result.output


@respx.mock
def test_buy_credits_yes_flag_skips_confirm(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CHARGED_BODY)
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "Charged" in result.output


@respx.mock
def test_buy_credits_generates_fresh_idempotency_key_per_invocation(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CHARGED_BODY)
    )
    result_a = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--yes"])
    result_b = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--yes"])
    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output

    body_a = json.loads(route.calls[0].request.content.decode())
    body_b = json.loads(route.calls[1].request.content.decode())
    assert body_a["amount_usd"] == 25
    assert _UUID4_RE.match(body_a["idempotency_key"])
    assert _UUID4_RE.match(body_b["idempotency_key"])
    # Different invocations must never reuse the same idempotency key.
    assert body_a["idempotency_key"] != body_b["idempotency_key"]


@respx.mock
def test_buy_credits_idempotency_key_override_is_sent_verbatim(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(200, json=_CHARGED_BODY)
    )
    result = runner.invoke(
        app,
        [
            "billing",
            "buy-credits",
            "--amount",
            "25",
            "--yes",
            "--idempotency-key",
            "my-caller-supplied-key",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content.decode())
    assert body == {"amount_usd": 25, "idempotency_key": "my-caller-supplied-key"}


def test_buy_credits_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_buy_credits_surfaces_invalid_amount(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_amount",
                "message": "amount must be between $5.00 and $500.00 (got $1)",
            },
        )
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "1", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, InvalidAmount)


@respx.mock
def test_buy_credits_surfaces_billing_not_enabled(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "billing_not_enabled",
                "message": "self-service subscription billing is not enabled",
            },
        )
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BillingNotEnabled)


@respx.mock
def test_buy_credits_tolerates_no_payment_method(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The server reserves this slug for the automatic top-up path, but the CLI
    must still map it to a structured error rather than falling through to a
    generic server error, in case a future server release ever surfaces it here.
    """
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/credits/purchase").mock(
        return_value=httpx.Response(
            409,
            json={"error": "no_payment_method", "message": "no default payment method on file"},
        )
    )
    result = runner.invoke(app, ["billing", "buy-credits", "--amount", "25", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, NoPaymentMethod)


# ----- `goodeye subscription` no longer exists -----


def test_subscription_group_is_gone(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """The old `subscription` group name was hard-dropped in favor of `billing`."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    result = runner.invoke(app, ["subscription", "portal"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_subscription_upgrade_is_gone(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    result = runner.invoke(app, ["subscription", "upgrade"])
    assert result.exit_code != 0
    assert "No such command" in result.output


# ----- `goodeye downgrade` is not a command -----


def test_downgrade_is_not_a_registered_command() -> None:
    """`billing plan cancel` is the only cancel path; there is no top-level alias."""
    result = runner.invoke(app, ["downgrade"])
    assert result.exit_code != 0
    assert "No such command" in result.output


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


def test_error_slug_invalid_amount() -> None:
    err = error_from_body(
        400,
        {"error": "invalid_amount", "message": "amount must be between $5.00 and $500.00"},
    )
    assert isinstance(err, InvalidAmount)
    assert err.slug == "invalid_amount"


def test_error_slug_no_payment_method() -> None:
    err = error_from_body(
        409,
        {"error": "no_payment_method", "message": "no default payment method on file"},
    )
    assert isinstance(err, NoPaymentMethod)
    assert err.slug == "no_payment_method"


# ----- module-level guards -----


def test_billing_commands_no_em_dash() -> None:
    """The brand constraint forbids em dashes anywhere in source."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "billing.py"
    assert "—" not in src.read_text()


def test_billing_command_appears_in_help() -> None:
    """`billing` is registered as a top-level command group.

    The old `subscription` group name is gone as a command (verified
    separately by ``test_subscription_group_is_gone``); the word
    "subscription" may still appear in prose here (e.g. "Manage your Pro
    subscription"), so this test does not assert its absence.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "billing" in result.output


def test_billing_group_help_lists_plan_portal_and_buy_credits() -> None:
    result = runner.invoke(app, ["billing", "--help"])
    assert result.exit_code == 0
    assert "plan" in result.output
    assert "portal" in result.output
    assert "buy-credits" in result.output


def test_billing_plan_group_help_lists_upgrade_and_cancel() -> None:
    result = runner.invoke(app, ["billing", "plan", "--help"])
    assert result.exit_code == 0
    assert "upgrade" in result.output
    assert "cancel" in result.output


# Avoid "imported but unused" if pytest is reordered.
_ = pytest
