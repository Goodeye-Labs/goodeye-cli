"""`goodeye billing ...` subcommand group.

Manage Pro subscription and one-time credit purchases: `plan upgrade` starts a
Stripe checkout to go Pro, `plan cancel` stops it from renewing at the end of
the current billing period, `portal` opens the Stripe billing portal to
update your payment method or view invoices, `buy-credits` buys a
one-time credit top-up (charging your card on file when one exists, or
opening a hosted checkout page otherwise), and `auto-topup show` / `set` /
`off` manage automatic credit top-ups that trigger whenever your balance
drops below a threshold you choose.
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
from goodeye_cli.errors import AuthRequired, NoPaymentMethod
from goodeye_cli.wire import (
    AutoTopUpResult,
    CheckoutResult,
    CreditPurchaseResult,
    PortalResult,
    SubscriptionCancelResult,
)

app = typer.Typer(help="Manage your Pro subscription and credits.", no_args_is_help=True)

plan_app = typer.Typer(help="Manage your Pro subscription plan.", no_args_is_help=True)
app.add_typer(plan_app, name="plan")

auto_topup_app = typer.Typer(help="Manage automatic credit top-ups.", no_args_is_help=True)
app.add_typer(auto_topup_app, name="auto-topup")


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


# ----- billing auto-topup -----


def _auto_topup_payload(result: AutoTopUpResult) -> dict[str, Any]:
    return {
        "enabled": result.enabled,
        "threshold_usd": result.threshold_usd,
        "amount_usd": result.amount_usd,
        "monthly_cap_usd": result.monthly_cap_usd,
        "monthly_spent_usd": result.monthly_spent_usd,
        "status": result.status,
        "last_failure_reason": result.last_failure_reason,
    }


def _print_auto_topup_terms(console: Console, result: AutoTopUpResult) -> None:
    """Print the resolved amount/threshold/cap/spend/status lines, if any are set.

    A caller who has never configured automatic top-ups has ``amount_usd``
    (and every other term) at None, so this prints a short "not configured"
    line instead of a block of empty-looking fields.
    """
    if result.amount_usd is None:
        console.print("No automatic top-up terms configured yet.")
        return
    console.print(f"Top-up amount: [bold]${result.amount_usd}[/bold]")
    if result.threshold_usd is not None:
        console.print(f"Triggers when balance drops below: ${result.threshold_usd}")
    if result.monthly_cap_usd is not None:
        console.print(f"Monthly cap: ${result.monthly_cap_usd}")
    if result.monthly_spent_usd is not None:
        console.print(f"Spent this month: ${result.monthly_spent_usd}")
    if result.status:
        console.print(f"Status: {result.status}")
    if result.last_failure_reason:
        console.print(f"[yellow]Last failure:[/yellow] {result.last_failure_reason}")


@auto_topup_app.command("show")
def auto_topup_show(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Show your automatic credit top-up configuration and this month's spend toward it.

    A caller who has never configured automatic top-ups sees a disabled
    default block, not an error.
    """
    console = Console()
    with _require_client() as client:
        result: AutoTopUpResult = client.get_auto_top_up()

    if json_output:
        typer.echo(_json.dumps(_auto_topup_payload(result)))
        return

    if result.enabled:
        console.print("[green]Automatic top-up is on.[/green]")
    else:
        console.print("Automatic top-up is off.")
    _print_auto_topup_terms(console, result)


@auto_topup_app.command("set")
def auto_topup_set(
    amount: int = typer.Option(
        ...,
        "--amount",
        help="Dollar amount to add each time your balance drops below the threshold.",
    ),
    threshold: int | None = typer.Option(
        None,
        "--threshold",
        help="Balance, in whole dollars, that triggers a top-up. Defaults to the top-up amount.",
    ),
    monthly_cap: int | None = typer.Option(
        None,
        "--monthly-cap",
        help=(
            "Maximum, in whole dollars, automatic top-ups can spend in a calendar "
            "month. Defaults to 4x the top-up amount."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Turn on automatic credit top-ups and set (or update) their terms.

    Requires a default payment method already on file: automatic top-ups
    charge that card off-session whenever your balance drops below the
    threshold. Prints the resolved terms, including any defaults the server
    filled in (threshold defaults to the top-up amount, monthly cap defaults
    to 4x the top-up amount). Re-running this also clears any previously
    failed automatic top-up state, acting as a retry.
    """
    console = Console()
    with _require_client() as client:
        try:
            result: AutoTopUpResult = client.configure_auto_top_up(
                amount_usd=amount, threshold_usd=threshold, monthly_cap_usd=monthly_cap
            )
        except NoPaymentMethod as exc:
            raise NoPaymentMethod(
                slug=exc.slug,
                message=exc.message,
                hint=(
                    "Automatic top-up charges a saved card off-session, and no "
                    "payment method is on file. Run `goodeye billing buy-credits` "
                    "once to save a card, or `goodeye billing portal` to add one."
                ),
                status_code=exc.status_code,
                extras=exc.extras,
            ) from exc

    if json_output:
        typer.echo(_json.dumps(_auto_topup_payload(result)))
        return

    console.print("[green]Automatic top-up is on.[/green]")
    _print_auto_topup_terms(console, result)


@auto_topup_app.command("off")
def auto_topup_off(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Turn off automatic credit top-ups.

    Leaves the previously configured amount, threshold, and monthly cap in
    place, so a later `set` with no flags is not needed to re-enable with the
    same terms.
    """
    console = Console()
    with _require_client() as client:
        result: AutoTopUpResult = client.disable_auto_top_up()

    if json_output:
        typer.echo(_json.dumps(_auto_topup_payload(result)))
        return

    console.print("[green]Automatic top-up disabled.[/green]")
    _print_auto_topup_terms(console, result)


# ----- deprecated `goodeye subscription ...` alias -----

# The `subscription` group shipped in v0.22.0 with `upgrade` / `cancel` /
# `portal`. Those commands were reorganized under `billing` (`billing plan
# upgrade`, `billing plan cancel`, `billing portal`). The old group is kept
# working here as a thin forwarding alias that announces the move on stderr, so
# a released command does not disappear from under existing scripts. Remove it
# in a later release.
subscription_app = typer.Typer(
    help="Deprecated: use `goodeye billing` instead.",
    no_args_is_help=True,
)


def _warn_subscription_deprecated(new_command: str) -> None:
    stderr = Console(stderr=True)
    stderr.print(
        f"[yellow]`goodeye subscription` is deprecated; use `{new_command}` instead.[/yellow]"
    )


@subscription_app.command("upgrade")
def subscription_upgrade(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Deprecated alias for `goodeye billing plan upgrade`."""
    _warn_subscription_deprecated("goodeye billing plan upgrade")
    upgrade(json_output=json_output)


@subscription_app.command("cancel")
def subscription_cancel(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Deprecated alias for `goodeye billing plan cancel`."""
    _warn_subscription_deprecated("goodeye billing plan cancel")
    cancel(json_output=json_output)


@subscription_app.command("portal")
def subscription_portal(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Deprecated alias for `goodeye billing portal`."""
    _warn_subscription_deprecated("goodeye billing portal")
    portal(json_output=json_output)


__all__ = [
    "app",
    "auto_topup_app",
    "auto_topup_off",
    "auto_topup_set",
    "auto_topup_show",
    "buy_credits",
    "cancel",
    "plan_app",
    "portal",
    "subscription_app",
    "subscription_cancel",
    "subscription_portal",
    "subscription_upgrade",
    "upgrade",
]
