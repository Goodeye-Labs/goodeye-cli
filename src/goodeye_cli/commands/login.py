"""`goodeye login` command."""

from __future__ import annotations

import platform

import typer
from rich.console import Console

from goodeye_cli.auth_flows import device_code_login
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_server, save_client_config, save_credentials


def login(
    email: str | None = typer.Option(
        None,
        "--email",
        "-e",
        help=(
            "Start non-interactive email-code login. "
            "Use `goodeye login-verify --email <email> --code <code>` to finish."
        ),
    ),
) -> None:
    """Sign in to Goodeye on this machine.

    With no options, runs the interactive browser/device-code login for humans.
    With ``--email``, starts a non-interactive email-code login for agents and
    automation, then exits so the code can be supplied in a separate command.
    """
    console = Console()
    server = get_server()

    if email:
        with GoodeyeClient(server) as client:
            client.login(email)
        console.print("Check your email for next steps.")
        console.print(
            f"[dim]Non-interactive login started. Finish with: "
            f"goodeye login-verify --email {email} --code <code>[/dim]"
        )
        return

    with GoodeyeClient(server) as client:
        client_config = client.get_client_config()
    save_client_config(client_config.model_dump())
    hostname = platform.node() or "unknown"
    api_key = device_code_login(
        server,
        workos_client_id=client_config.workos_client_id,
        workos_device_authorization_uri=client_config.workos_device_authorization_uri,
        workos_token_uri=client_config.workos_token_uri,
        hostname=hostname,
        console=console,
    )

    path = save_credentials({"api_key": api_key, "server": server})
    console.print(f"[green]Signed in.[/green] Credentials saved to {path}")


def login_verify(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email address used to start non-interactive login.",
    ),
    code: str = typer.Option(
        ...,
        "--code",
        "-c",
        help="One-time code sent to your email.",
    ),
) -> None:
    """Complete non-interactive email-code login and save local credentials."""
    console = Console()
    server = get_server()
    with GoodeyeClient(server) as client:
        result = client.login_verify(email, code)
    path = save_credentials({"api_key": result.api_key, "server": server})
    console.print(f"[green]Signed in.[/green] Credentials saved to {path}")
