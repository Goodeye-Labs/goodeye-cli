"""`goodeye billing ...` subcommand group.

Covers the Stripe-hosted billing portal, where you update your payment
method, view invoices, or manage your Pro subscription. See
`goodeye subscription upgrade` and `goodeye subscription cancel` for starting
or ending a subscription directly from the CLI.
"""

from __future__ import annotations

import contextlib
import json as _json
import webbrowser

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
from goodeye_cli.wire import PortalResult

app = typer.Typer(help="Manage billing for your Pro subscription.", no_args_is_help=True)


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


__all__ = ["app", "portal"]
