"""`goodeye design` command."""

from __future__ import annotations

import json

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired


def design(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope."),
) -> None:
    """Print the Goodeye workflow-designer prompt pack to stdout.

    Pipe this into your AI coding assistant to kick off a skill + verifier
    design session.
    """
    console = Console(stderr=True)
    server = get_server()
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required to fetch the design prompt.",
            hint="Run `goodeye login`.",
        )
    with GoodeyeClient(server, api_key=api_key) as client:
        payload = client.get_design_prompt()

    if json_output:
        typer.echo(json.dumps(payload))
        return

    # The MCP tool returns a dict with a human-readable prompt under common keys.
    # Try the most likely keys, fall back to the whole payload.
    for key in ("prompt", "body", "content", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            typer.echo(value)
            return
    console.print("[yellow]Unrecognized design payload; emitting JSON.[/yellow]")
    typer.echo(json.dumps(payload, indent=2))
