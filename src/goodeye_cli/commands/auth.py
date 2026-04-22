"""`goodeye auth ...` subcommand group."""

from __future__ import annotations

import shutil
import subprocess
import sys

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired

app = typer.Typer(help="Manage API keys.", no_args_is_help=True)


def _require_client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort copy using platform clipboard tools. Returns True on success."""
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        candidates.append(["pbcopy"])
    elif sys.platform.startswith("linux"):
        candidates.extend([["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]])
    elif sys.platform.startswith("win"):
        candidates.append(["clip"])
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False


@app.command("create-key")
def create_key(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable name for the key."),
    copy: bool = typer.Option(False, "--copy", help="Copy the key to the clipboard."),
) -> None:
    """Mint a new API key. The secret is shown exactly once."""
    console = Console()
    with _require_client() as client:
        created = client.create_api_key(name)

    console.print(f"[bold]ID:[/bold]   {created.id}")
    console.print(f"[bold]Name:[/bold] {created.name}")
    console.print(f"[bold]Key:[/bold]  [green]{created.key}[/green]")
    console.print("[dim]Save this key now; it cannot be retrieved again.[/dim]")

    if copy:
        if _copy_to_clipboard(created.key):
            console.print("[green]Copied to clipboard.[/green]")
        else:
            console.print(
                "[yellow]Could not find a clipboard utility. "
                "Install pbcopy/xclip/wl-copy as appropriate.[/yellow]"
            )


@app.command("list-keys")
def list_keys(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List your API keys (secrets are never returned)."""
    console = Console()
    with _require_client() as client:
        # Auto-follow cursor so we show everything the user owns.
        items = []
        cursor: str | None = None
        while True:
            page = client.list_api_keys(cursor=cursor)
            items.extend(page.items)
            cursor = page.next_cursor
            if not cursor:
                break

    if json_output:
        typer.echo("[" + ",".join(key.model_dump_json() for key in items) + "]")
        return

    table = Table(title="API keys")
    table.add_column("ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Created")
    for key in items:
        table.add_row(key.id, key.name, key.created_at.isoformat())
    if not items:
        console.print("[dim]No API keys.[/dim]")
    else:
        console.print(table)


@app.command("revoke-key")
def revoke_key(
    key_id: str = typer.Argument(
        ..., help="Key ID (ULID) to revoke; run `goodeye auth list-keys` to find it."
    ),
) -> None:
    """Revoke (soft-delete) an API key by ID."""
    console = Console()
    with _require_client() as client:
        client.revoke_api_key(key_id)
    console.print(f"[green]Revoked[/green] {key_id}")
