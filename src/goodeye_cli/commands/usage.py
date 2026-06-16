"""`goodeye usage` command: show monthly Goodeye credit usage."""

from __future__ import annotations

import json as _json
from typing import Any

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
from goodeye_cli.wire import UsageResponse


def _coerce_usage(body: UsageResponse | dict[str, Any]) -> UsageResponse:
    """Accept either a typed ``UsageResponse`` or a plain dict (e.g. from tests)."""
    if isinstance(body, UsageResponse):
        return body
    return UsageResponse.model_validate(body)


def format_usage_summary(body: UsageResponse | dict[str, Any]) -> str:
    """Render the usage payload as a human-readable multi-line string.

    The purchased-credits, referral-credits, and unpaid-balance lines only
    render when their values are positive.
    """
    usage = _coerce_usage(body)
    refill_at = usage.monthly_refill_at.strftime("%m/%d/%Y")
    has_purchased = float(usage.purchased_remaining_usd) > 0
    has_referral = float(usage.referral_remaining_usd) > 0
    lines = [
        f"Tier: {usage.tier}",
        f"Available: ${usage.available_usd}",
    ]
    if has_purchased or has_referral:
        lines.append(
            f"  ${usage.monthly_remaining_usd} monthly "
            f"(refills to ${usage.monthly_refill_usd} on {refill_at})"
        )
        if has_purchased:
            lines.append(f"  ${usage.purchased_remaining_usd} one-off credits")
        if has_referral:
            lines.append(f"  ${usage.referral_remaining_usd} referral credits")
    else:
        lines.append(f"  refills to ${usage.monthly_refill_usd} on {refill_at}")
    if float(usage.unpaid_balance_usd) > 0:
        lines.append(f"Unpaid: ${usage.unpaid_balance_usd} (deducted from next grant)")
    return "\n".join(lines)


def usage(
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """Show your monthly Goodeye credit usage.

    Prints your current tier, the active billing period, how much credit
    you have used and how much remains, plus any unpaid balance that will
    be deducted from your next monthly grant.
    """
    console = Console()
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    with GoodeyeClient(get_server(), api_key=api_key) as client:
        result = client.get_usage()

    if json_output:
        payload = {
            "tier": result.tier,
            "available_usd": result.available_usd,
            "monthly_remaining_usd": result.monthly_remaining_usd,
            "monthly_refill_usd": result.monthly_refill_usd,
            "monthly_refill_at": result.monthly_refill_at.isoformat(),
            "purchased_remaining_usd": result.purchased_remaining_usd,
            "referral_remaining_usd": result.referral_remaining_usd,
            "unpaid_balance_usd": result.unpaid_balance_usd,
        }
        typer.echo(_json.dumps(payload))
        return

    console.print(format_usage_summary(result))


__all__ = ["format_usage_summary", "usage"]
