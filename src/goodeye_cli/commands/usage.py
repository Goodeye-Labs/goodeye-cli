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

    The unpaid balance line is only shown when the value is positive.
    """
    usage = _coerce_usage(body)
    period_start = usage.period_start.strftime("%m/%d/%Y")
    period_end = usage.period_end.strftime("%m/%d/%Y")
    lines = [
        f"Tier: {usage.tier}",
        f"Period: {period_start} to {period_end}",
        f"Used:      ${usage.used_usd}",
        f"Remaining: ${usage.remaining_usd} of ${usage.granted_usd}",
    ]
    if float(usage.unpaid_balance_usd) > 0:
        lines.append(f"Unpaid:    ${usage.unpaid_balance_usd} (will be deducted from next grant)")
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
            "period_start": result.period_start.isoformat(),
            "period_end": result.period_end.isoformat(),
            "granted_usd": result.granted_usd,
            "used_usd": result.used_usd,
            "remaining_usd": result.remaining_usd,
            "unpaid_balance_usd": result.unpaid_balance_usd,
        }
        typer.echo(_json.dumps(payload))
        return

    console.print(format_usage_summary(result))


__all__ = ["format_usage_summary", "usage"]
