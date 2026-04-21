"""`goodeye logout` command."""

from __future__ import annotations

from rich.console import Console

from goodeye_cli.config import delete_credentials


def logout() -> None:
    """Delete local credentials.

    This does **not** revoke the key server-side. Run ``goodeye auth list-keys``
    (while still authenticated) or use the REST API to revoke keys you no longer
    want to be usable. If you already deleted the local credentials, sign in
    again to manage existing keys.
    """
    console = Console()
    removed = delete_credentials()
    if removed:
        console.print("[green]Local credentials removed.[/green]")
    else:
        console.print("[yellow]No local credentials to remove.[/yellow]")
    console.print(
        "Note: your API key is still valid on the server. Run "
        "`goodeye auth list-keys` to see it, or `goodeye auth revoke-key <id>` to revoke it."
    )
