"""`goodeye teams ...` subcommand group.

Covers the seven team tools shipped on the server: create, delete,
list, list-members, add-member, remove-member, transfer-ownership.
Handle immutability is enforced server-side; there is no rename path.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired

app = typer.Typer(help="Manage teams.", no_args_is_help=True)


def _require_client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


@app.command("create")
def create(
    handle: str = typer.Argument(..., help="Team handle (3 to 40 chars). Immutable."),
) -> None:
    """Create a team you own. Fails if your user handle is still provisional."""
    console = Console()
    with _require_client() as client:
        result = client.create_team(handle)
    console.print(f"[green]Created[/green] team @{result.handle} (id={result.team_id})")


@app.command("delete")
def delete(
    team_id: str = typer.Argument(..., help="Team ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a team you own. Releases the handle for reuse."""
    console = Console()
    if not yes:
        confirm = typer.confirm(f"Delete team {team_id}?", default=False)
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(code=1)
    with _require_client() as client:
        client.delete_team(team_id)
    console.print(f"[green]Deleted[/green] team {team_id}")


@app.command("list")
def list_cmd(
    filter_: str = typer.Option(
        "all",
        "--filter",
        "-f",
        help="Scope filter: all, mine, or member.",
        case_sensitive=False,
    ),
) -> None:
    """List teams visible to you (owned + member of)."""
    console = Console()
    with _require_client() as client:
        result = client.list_teams(filter_=filter_.lower())

    if not result.items:
        console.print("[dim]No teams matched.[/dim]")
        return

    table = Table(title=f"Teams ({filter_})")
    table.add_column("Team ID", no_wrap=True)
    table.add_column("Handle")
    table.add_column("Role")
    for item in result.items:
        table.add_row(item.team_id, f"@{item.handle}", item.role)
    console.print(table)


@app.command("members")
def members(
    team_id: str = typer.Argument(..., help="Team ID."),
) -> None:
    """List members of a team (owner appears via a synthetic row)."""
    console = Console()
    with _require_client() as client:
        rows = client.list_team_members(team_id)

    table = Table(title=f"Members of {team_id}")
    table.add_column("User ID", no_wrap=True)
    table.add_column("Email")
    table.add_column("Handle")
    table.add_column("Role")
    for row in rows:
        table.add_row(row.user_id, row.email, row.handle or "", row.role)
    console.print(table)


@app.command("add-member")
def add_member(
    team_id: str = typer.Argument(..., help="Team ID."),
    user: str = typer.Argument(..., help="User ID (UUID) or email."),
) -> None:
    """Add a member to a team. Owner only."""
    console = Console()
    with _require_client() as client:
        client.add_team_member(team_id, user)
    console.print(f"[green]Added[/green] {user} to team {team_id}")


@app.command("remove-member")
def remove_member(
    team_id: str = typer.Argument(..., help="Team ID."),
    user_id: str = typer.Argument(..., help="User ID (UUID) to remove."),
) -> None:
    """Remove a member from a team. Owner can remove anyone; members can self-leave."""
    console = Console()
    with _require_client() as client:
        client.remove_team_member(team_id, user_id)
    console.print(f"[green]Removed[/green] {user_id} from team {team_id}")


@app.command("transfer-ownership")
def transfer_ownership(
    team_id: str = typer.Argument(..., help="Team ID."),
    new_owner_user_id: str = typer.Argument(..., help="User ID of the new owner."),
) -> None:
    """Transfer team ownership. Old owner becomes a regular member."""
    console = Console()
    with _require_client() as client:
        client.transfer_team_ownership(team_id, new_owner_user_id)
    console.print(f"[green]Transferred[/green] team {team_id} ownership to {new_owner_user_id}")
