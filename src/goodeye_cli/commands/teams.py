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
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
from goodeye_cli.output import echo_json, items_envelope, resolve_output_mode

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
    team: str = typer.Argument(..., help="Team UUID or handle."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a team you own. Releases the handle for reuse."""
    console = Console()
    if not confirm_destructive(f"Delete team {team}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _require_client() as client:
        client.delete_team(team)
    console.print(f"[green]Deleted[/green] team {team}")


@app.command("list")
def list_cmd(
    filter_: str = typer.Option(
        "all",
        "--filter",
        "-f",
        help="Scope filter: all, mine, or member.",
        case_sensitive=False,
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """List teams visible to you (owned + member of)."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _require_client() as client:
        result = client.list_teams(filter_=filter_.lower())

    if mode == "json":
        echo_json(result)
        return

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
    team: str = typer.Argument(..., help="Team UUID or handle."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """List members of a team (owner appears via a synthetic row)."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _require_client() as client:
        rows = client.list_team_members(team)

    if mode == "json":
        echo_json(items_envelope(rows))
        return

    table = Table(title=f"Members of {team}")
    table.add_column("User ID", no_wrap=True)
    table.add_column("Handle")
    table.add_column("Role")
    for row in rows:
        table.add_row(row.user_id, row.handle or "", row.role)
    console.print(table)


@app.command("add-member")
def add_member(
    team: str = typer.Argument(..., help="Team UUID or handle."),
    user: str = typer.Argument(..., help="User UUID, email, or handle."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Add a member to a team. Owner only. Returns an invitation the recipient must accept."""
    console = Console()
    with _require_client() as client:
        body = client.add_team_member(team, user)
    if json_output:
        from goodeye_cli.output import echo_json as _echo_json

        _echo_json(body)
        return
    if "invitation_id" in body:
        console.print("Invitation sent.")
        console.print(f"  id:         {body['invitation_id']}")
        console.print(f"  kind:       {body.get('kind', 'team_membership')}")
        console.print(f"  expires_at: {body.get('expires_at', '')}")
        console.print("")
        console.print(f"Recipient must run: goodeye invitations accept {body['invitation_id']}")
    else:
        console.print(f"[green]Added[/green] {user} to team {team}")


@app.command("remove-member")
def remove_member(
    team: str = typer.Argument(..., help="Team UUID or handle."),
    user: str = typer.Argument(..., help="User UUID, email, or handle."),
) -> None:
    """Remove a member from a team. Owner can remove anyone; members can self-leave."""
    console = Console()
    with _require_client() as client:
        client.remove_team_member(team, user)
    console.print(f"[green]Removed[/green] {user} from team {team}")


@app.command("transfer-ownership")
def transfer_ownership(
    team: str = typer.Argument(..., help="Team UUID or handle."),
    new_owner: str = typer.Argument(..., help="New owner UUID, email, or handle."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Transfer team ownership. Returns an invitation the recipient must accept."""
    console = Console()
    with _require_client() as client:
        body = client.transfer_team_ownership(team, new_owner)
    if json_output:
        from goodeye_cli.output import echo_json as _echo_json

        _echo_json(body)
        return
    if "invitation_id" in body:
        console.print("Ownership transfer invited.")
        console.print(f"  id:         {body['invitation_id']}")
        console.print(f"  expires_at: {body.get('expires_at', '')}")
        console.print("")
        console.print(f"Recipient must run: goodeye invitations accept {body['invitation_id']}")
    else:
        console.print("No-op: target is already the owner.")
