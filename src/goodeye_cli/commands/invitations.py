"""`goodeye invitations ...` subcommand group.

Covers listing invitations (received/sent), accepting, declining, and
cancelling them. Invitations are created by propose operations such as
`goodeye teams add-member` or the various transfer-ownership commands.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
from goodeye_cli.output import echo_json, next_page_hint, resolve_output_mode

app = typer.Typer(help="Manage invitations.", no_args_is_help=True)


def _require_client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


@app.command("list")
def list_cmd(
    filter_: str = typer.Option(
        "received",
        "--filter",
        "-f",
        help="Scope filter: received, sent, or all.",
        case_sensitive=False,
    ),
    state: str = typer.Option(
        "pending",
        "--state",
        help="State filter: pending or all.",
        case_sensitive=False,
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-l",
        min=1,
        help="Max results per page. Omit to use the server default.",
    ),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """List invitations you have received or sent."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _require_client() as client:
        result = client.list_invitations(
            filter_=filter_.lower(),
            state=state.lower(),
            limit=limit,
            cursor=cursor,
        )

    if mode == "json":
        echo_json(result)
        return

    if not result.items:
        console.print("[dim]No invitations matched.[/dim]")
    else:
        table = Table(title=f"Invitations ({filter_} / {state})")
        table.add_column("ID", no_wrap=True)
        table.add_column("Kind")
        table.add_column("Target")
        table.add_column("From")
        table.add_column("To")
        table.add_column("Expires at")
        table.add_column("Resolution")
        for item in result.items:
            table.add_row(
                item.id,
                item.kind,
                item.target_label,
                item.proposed_by_handle,
                item.proposed_to_handle,
                str(item.expires_at),
                item.resolution or "",
            )
        console.print(table)

    if result.next_cursor:
        hint = next_page_hint(
            ("goodeye", "invitations", "list"),
            next_cursor=result.next_cursor,
            limit=limit if limit is not None else 50,
            options=(
                ("--filter", filter_.lower()),
                ("--state", state.lower()),
            ),
        )
        console.print(f"[dim]{hint}[/dim]")


@app.command("accept")
def accept(
    invitation_id: str = typer.Argument(..., help="Invitation UUID."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Accept a pending invitation."""
    console = Console()
    with _require_client() as client:
        result = client.accept_invitation(invitation_id)
    if json_output:
        echo_json(result)
        return
    console.print(f"[green]Accepted[/green] invitation {invitation_id}.")


@app.command("decline")
def decline(
    invitation_id: str = typer.Argument(..., help="Invitation UUID."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Decline a pending invitation."""
    console = Console()
    with _require_client() as client:
        result = client.decline_invitation(invitation_id)
    if json_output:
        echo_json(result)
        return
    console.print(f"[yellow]Declined[/yellow] invitation {invitation_id}.")


@app.command("cancel")
def cancel(
    invitation_id: str = typer.Argument(..., help="Invitation UUID."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Cancel an invitation you sent."""
    console = Console()
    with _require_client() as client:
        result = client.cancel_invitation(invitation_id)
    if json_output:
        echo_json(result)
        return
    console.print(f"[yellow]Cancelled[/yellow] invitation {invitation_id}.")


__all__ = ["accept", "app", "cancel", "decline", "list_cmd"]
