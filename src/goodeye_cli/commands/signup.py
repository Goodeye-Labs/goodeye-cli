"""`goodeye signup` command."""

from __future__ import annotations

import typer
from rich.console import Console

from goodeye_cli.auth_flows import magic_auth_flow
from goodeye_cli.config import get_server, save_credentials


def signup(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email address to sign up with. A code will be sent here.",
    ),
) -> None:
    """Create a Goodeye account.

    Uses the magic-auth flow: we email a one-time code, you paste it back, and
    we mint your initial API key.
    """
    console = Console()
    server = get_server()
    api_key = magic_auth_flow(server, email, intent="signup", console=console)
    path = save_credentials({"api_key": api_key, "server": server})
    console.print(f"[green]Account created.[/green] Credentials saved to {path}")
