"""`goodeye subscription ...` subcommand group.

Manage your Pro subscription: `upgrade` starts a Stripe checkout to go Pro,
`cancel` stops it from renewing at the end of the current billing period, and
`portal` opens the Stripe billing portal to update your payment method or view
invoices.
"""

from __future__ import annotations

import contextlib
import json as _json
import webbrowser
from typing import Any

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
from goodeye_cli.wire import CheckoutResult, PortalResult, SubscriptionCancelResult

app = typer.Typer(help="Manage your Pro subscription.", no_args_is_help=True)


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


@app.command("upgrade")
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


@app.command("cancel")
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


__all__ = ["app", "cancel", "portal", "upgrade"]
