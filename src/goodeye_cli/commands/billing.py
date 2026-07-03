"""`goodeye billing ...` subcommand group.

Manage Pro subscription and one-time credit purchases: `plan upgrade` starts a
Stripe checkout to go Pro, `plan cancel` stops it from renewing at the end of
the current billing period, `portal` opens the Stripe billing portal to
update your payment method or view invoices, and `buy-credits` buys a
one-time credit top-up (charging your card on file when one exists, or
opening a hosted checkout page otherwise).
"""

from __future__ import annotations

import contextlib
import json as _json
import uuid
import webbrowser
from typing import Any

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
from goodeye_cli.wire import (
    CheckoutResult,
    CreditPurchaseResult,
    PortalResult,
    SubscriptionCancelResult,
)

app = typer.Typer(help="Manage your Pro subscription and credits.", no_args_is_help=True)

plan_app = typer.Typer(help="Manage your Pro subscription plan.", no_args_is_help=True)
app.add_typer(plan_app, name="plan")


def _require_client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


def _open_url(url: str) -> None:
    """Best-effort browser open. Never fatal: the URL is already printed."""
    with contextlib.suppress(Exception):
        webbrowser.open(url)


@plan_app.command("upgrade")
def upgrade(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Start a Pro subscription and open a secure Stripe checkout page.

    Prints the checkout link and opens it in your default browser so you can
    complete payment there. Your account upgrades to Pro once checkout
    succeeds.
    """
    console = Console()
    with _require_client() as client:
        result: CheckoutResult = client.start_checkout()

    if json_output:
        typer.echo(_json.dumps({"checkout_url": result.checkout_url}))
    else:
        console.print(f"Checkout link: [bold]{result.checkout_url}[/bold]")
        console.print("Opening in your browser...")
    _open_url(result.checkout_url)


@plan_app.command("cancel")
def cancel(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Cancel your Pro subscription at the end of the current billing period.

    This does not revoke access immediately: your Pro access continues until
    the current paid period ends.
    """
    console = Console()
    with _require_client() as client:
        result: SubscriptionCancelResult = client.cancel_subscription()

    if json_output:
        payload: dict[str, Any] = {
            "status": result.status,
            "access_until": result.access_until.isoformat() if result.access_until else None,
        }
        typer.echo(_json.dumps(payload))
        return

    console.print(f"[green]Subscription set to cancel[/green] (status: {result.status})")
    if result.access_until is not None:
        access_until = result.access_until.strftime("%m/%d/%Y")
        console.print(f"Your Pro access continues until {access_until}, then it ends.")
    else:
        console.print("Your Pro access will end at the close of the current billing period.")


@app.command("portal")
def portal(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Open the Stripe billing portal to manage your subscription and payment method.

    Prints the portal link and opens it in your default browser. Use the
    portal to update your card, view invoices, or cancel your subscription.
    """
    console = Console()
    with _require_client() as client:
        result: PortalResult = client.open_billing_portal()

    if json_output:
        typer.echo(_json.dumps({"portal_url": result.portal_url}))
    else:
        console.print(f"Billing portal: [bold]{result.portal_url}[/bold]")
        console.print("Opening in your browser...")
    _open_url(result.portal_url)


@app.command("buy-credits")
def buy_credits(
    amount: int = typer.Option(
        ..., "--amount", help="Dollar amount of credits to buy (whole US dollars)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    idempotency_key: str | None = typer.Option(
        None,
        "--idempotency-key",
        help="Reuse a specific idempotency key instead of generating a fresh one.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Buy a one-time credit top-up.

    Charges the card on file and credits your balance immediately when a
    payment method is on file. Otherwise prints a secure hosted checkout link
    (and opens it in your default browser) so you can finish the purchase
    there. A fresh idempotency key is generated for every invocation unless
    you pass --idempotency-key, so a network retry of this command cannot
    charge your card twice.
    """
    console = Console()
    if not confirm_destructive(f"Charge ${amount} to your card on file?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)

    key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
    with _require_client() as client:
        result: CreditPurchaseResult = client.purchase_credits(amount, idempotency_key=key)

    if json_output:
        payload: dict[str, Any] = {"status": result.status}
        if result.status == "charged":
            payload["amount_usd"] = result.amount_usd
            payload["new_balance_usd"] = result.new_balance_usd
        else:
            payload["checkout_url"] = result.checkout_url
        typer.echo(_json.dumps(payload))
        return

    if result.status == "charged":
        console.print(f"[green]Charged[/green] ${result.amount_usd} to your card on file.")
        console.print(f"New balance: ${result.new_balance_usd}")
        return

    console.print(f"Checkout link: [bold]{result.checkout_url}[/bold]")
    console.print("Open it to finish the purchase.")
    console.print("Opening in your browser...")
    if result.checkout_url is not None:
        _open_url(result.checkout_url)


__all__ = ["app", "buy_credits", "cancel", "plan_app", "portal", "upgrade"]
