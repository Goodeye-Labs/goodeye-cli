"""`goodeye usage` command: show monthly Goodeye credit usage."""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def format_usage_summary(body: dict[str, Any]) -> str:
    """Render the usage payload as a human-readable multi-line string.

    The unpaid balance line is only shown when the value is positive.
    """
    period_start = _parse_date(body["period_start"]).strftime("%m/%d/%Y")
    period_end = _parse_date(body["period_end"]).strftime("%m/%d/%Y")
    used = body["used_usd"]
    remaining = body["remaining_usd"]
    granted = body["granted_usd"]
    unpaid = body["unpaid_balance_usd"]
    lines = [
        f"Tier: {body['tier']}",
        f"Period: {period_start} to {period_end}",
        f"Used:      ${used}",
        f"Remaining: ${remaining} of ${granted}",
    ]
    if float(unpaid) > 0:
        lines.append(f"Unpaid:    ${unpaid} (will be deducted from next grant)")
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

    payload: dict[str, Any] = {
        "tier": result.tier,
        "period_start": result.period_start.isoformat(),
        "period_end": result.period_end.isoformat(),
        "granted_usd": result.granted_usd,
        "used_usd": result.used_usd,
        "remaining_usd": result.remaining_usd,
        "unpaid_balance_usd": result.unpaid_balance_usd,
    }

    if json_output:
        typer.echo(_json.dumps(payload))
        return

    console.print(format_usage_summary(payload))
