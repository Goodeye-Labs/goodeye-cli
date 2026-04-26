"""`goodeye me ...` subcommand group: view and update the authenticated user's profile."""

from __future__ import annotations

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired

app = typer.Typer(help="View and update your profile.", no_args_is_help=True)


def _require_client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


@app.command("claim-handle")
def claim_handle(
    handle: str = typer.Argument(..., help="The handle you want to claim (3 to 40 chars)."),
) -> None:
    """Claim your handle: a publish identity shared across users and teams.

    Handles are lowercase alphanumeric with hyphens (3 to 40 characters),
    start and end with an alphanumeric, and are unique across the platform.
    """
    console = Console()
    with _require_client() as client:
        result = client.claim_handle(handle)
    console.print(f"[green]Claimed[/green] @{result.handle}")


@app.command("rename-handle")
def rename_handle(
    handle: str = typer.Argument(..., help="The new handle you want to use."),
) -> None:
    """Change a previously claimed handle to a new one.

    Subject to a cooldown and a yearly cap. Templates published under the
    old handle are reachable via redirects for a 90-day reservation
    window.
    """
    console = Console()
    with _require_client() as client:
        result = client.rename_handle(handle)
    if result.self_reclaim:
        console.print(f"[green]Reclaimed[/green] @{result.handle}")
    else:
        console.print(f"[green]Renamed[/green] handle to @{result.handle}")
    if result.claimed_at is not None:
        console.print(f"[dim]claimed_at: {result.claimed_at.isoformat()}[/dim]")
