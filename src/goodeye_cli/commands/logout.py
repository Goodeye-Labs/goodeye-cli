"""`goodeye logout` command."""

from __future__ import annotations

from rich.console import Console

from goodeye_cli.config import delete_credentials


def logout() -> None:
    """Sign out on this machine by removing saved credentials.

    Your API key stays valid on the server. To actually disable it, run
    ``goodeye auth revoke-key <id>`` before logging out (or sign back in
    later to manage existing keys).
    """
    console = Console()
    removed = delete_credentials()
    if removed:
        console.print("[green]Local credentials removed.[/green]")
    else:
        console.print("[yellow]No local credentials to remove.[/yellow]")
    console.print(
        "Note: your API key is still valid on the server. Sign in again and run "
        "`goodeye auth list-keys` to see it, or `goodeye auth revoke-key <id>` to revoke it."
    )
