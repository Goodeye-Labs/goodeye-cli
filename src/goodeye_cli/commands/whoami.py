"""`goodeye whoami` command."""

from __future__ import annotations

import json

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import DEFAULT_SERVER, get_api_key, get_server
from goodeye_cli.errors import AuthRequired


def whoami(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show which user the current credentials identify.

    By default, prints just the authenticated email. When the active server
    differs from the built-in default (``GOODEYE_SERVER`` set, or a
    credentials file pointing elsewhere), the server URL is included too so
    you can't accidentally run destructive commands against the wrong
    environment.
    """
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

    non_default_server = server.rstrip("/") != DEFAULT_SERVER.rstrip("/")

    if json_output:
        payload: dict[str, str] = {"email": me.email}
        if non_default_server:
            payload["server"] = server
        typer.echo(json.dumps(payload))
        return

    if non_default_server:
        console.print(f"Server: {server}")
    console.print(f"Email:  {me.email}")
