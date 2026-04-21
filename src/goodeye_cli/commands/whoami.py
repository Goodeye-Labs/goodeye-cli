"""`goodeye whoami` command."""

from __future__ import annotations

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired


def whoami(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show which user the current credentials identify."""
    console = Console()
    server = get_server()
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="No credentials found.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    with GoodeyeClient(server, api_key=api_key) as client:
        me = client.get_me()

    if json_output:
        typer.echo(me.model_dump_json())
        return

    console.print(f"Server:  {server}")
    console.print(f"User ID: {me.user_id}")
    console.print(f"Email:   {me.email}")
