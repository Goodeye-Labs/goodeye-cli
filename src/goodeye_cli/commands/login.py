"""`goodeye login` command."""

from __future__ import annotations

import platform

import typer
from rich.console import Console

from goodeye_cli.auth_flows import device_code_login, magic_auth_flow
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_server, save_client_config, save_credentials


def login(
    email: str | None = typer.Option(
        None,
        "--email",
        "-e",
        help="Use the magic-auth flow with this email. Omit for the device-code flow.",
    ),
) -> None:
    """Authenticate this machine with Goodeye.

    With no ``--email``, opens your browser to complete a WorkOS device-code
    approval. With ``--email``, sends a one-time code to that address and
    prompts you to paste it back.

    On success, writes ``~/.config/goodeye/credentials.json`` (mode 0600) with
    the newly minted API key and the server URL.
    """
    console = Console()
    server = get_server()

    if email:
        api_key = magic_auth_flow(server, email, intent="login", console=console)
    else:
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
