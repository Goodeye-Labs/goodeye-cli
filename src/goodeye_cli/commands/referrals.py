"""`goodeye referral ...` subcommand group.

Covers viewing your referral code and redeeming a referral from another user.
"""

from __future__ import annotations

import json as _json

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired

app = typer.Typer(help="View and redeem referral codes.", no_args_is_help=True)


def _require_client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


@app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Show your referral code and how many people have used it.

    Prints your personal referral code, instructions for sharing it, how many
    people have redeemed it, how many have qualified for credits, and how much
    you have earned so far.
    """
    console = Console()
    with _require_client() as client:
        result = client.get_referral_status()

    if json_output:
        payload = {
            "code": result.code,
            "instructions": result.instructions,
            "redeemed_count": result.redeemed_count,
            "qualified_count": result.qualified_count,
            "credits_earned_usd": result.credits_earned_usd,
            "slots_remaining": result.slots_remaining,
        }
        typer.echo(_json.dumps(payload))
        return

    console.print(f"Your referral code: [bold]{result.code}[/bold]")
    console.print(f"Instructions: {result.instructions}")
    console.print(f"Redeemed: {result.redeemed_count}")
    console.print(f"Qualified: {result.qualified_count}")
    console.print(f"Credits earned: ${result.credits_earned_usd}")
    console.print(f"Slots remaining: {result.slots_remaining}")


@app.command("redeem")
def redeem(
    code: str = typer.Argument(..., help="Referral code to redeem."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Redeem a referral code from another Goodeye user.

    Applies the referral to your account. Credits are granted once your account
    meets the qualification criteria. The referrer also receives credits when
    you qualify.
    """
    console = Console()
    with _require_client() as client:
        result = client.redeem_referral_code(code)

    if json_output:
        payload = {
            "status": result.status,
            "credits_granted_usd": result.credits_granted_usd,
            "expires_at": result.expires_at.isoformat(),
            "referrer_handle": result.referrer_handle,
        }
        typer.echo(_json.dumps(payload))
        return

    console.print(f"[green]Referral redeemed[/green] (status: {result.status})")
    console.print(f"Credits granted: ${result.credits_granted_usd}")
    console.print(f"Referred by: @{result.referrer_handle}")
    console.print(f"Expires at: {result.expires_at.isoformat()}")


__all__ = ["app", "redeem", "status"]
