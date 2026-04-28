"""`goodeye register` and `goodeye register-verify` commands."""

from __future__ import annotations

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_server, save_credentials


def register(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help=(
            "Start non-interactive account registration. "
            "Use `goodeye register-verify --email <email> --code <code>` to finish."
        ),
    ),
) -> None:
    """Start non-interactive Goodeye account registration.

    This command sends the email-code request and exits without prompting so an
    AI agent or automation can run the verify command after the user supplies
    the emailed code.
    """
    console = Console()
    server = get_server()
    with GoodeyeClient(server) as client:
        client.register(email)
    console.print("Check your email for next steps.")
    console.print(
        f"[dim]Non-interactive registration started. Finish with: "
        f"goodeye register-verify --email {email} --code <code>[/dim]"
    )


def register_verify(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email address used to start registration.",
    ),
    code: str = typer.Option(
        ...,
        "--code",
        "-c",
        help="One-time code sent to your email.",
    ),
) -> None:
    """Complete non-interactive registration and save local credentials."""
    console = Console()
    server = get_server()
    with GoodeyeClient(server) as client:
        result = client.register_verify(email, code)
    path = save_credentials({"api_key": result.api_key, "server": server})
    console.print(f"[green]Account registered.[/green] Credentials saved to {path}")
