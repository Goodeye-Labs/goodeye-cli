"""Typer app root. Wires subcommands and a global error handler."""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from goodeye_cli import __version__
from goodeye_cli.commands import auth as auth_cmds
from goodeye_cli.commands import design as design_cmd
from goodeye_cli.commands import login as login_cmd
from goodeye_cli.commands import logout as logout_cmd
from goodeye_cli.commands import me as me_cmds
from goodeye_cli.commands import register as register_cmd
from goodeye_cli.commands import teams as teams_cmds
from goodeye_cli.commands import templates as templates_cmds
from goodeye_cli.commands import whoami as whoami_cmd
from goodeye_cli.commands import workflows as workflows_cmds
from goodeye_cli.errors import GoodeyeError

app = typer.Typer(
    name="goodeye",
    help="Goodeye CLI - manage AI workflows from the terminal.",
    no_args_is_help=True,
    add_completion=False,
)

# Top-level commands.
app.command("login")(login_cmd.login)
app.command("login-verify")(login_cmd.login_verify)
app.command("register")(register_cmd.register)
app.command("register-verify")(register_cmd.register_verify)
app.command("logout")(logout_cmd.logout)
app.command("whoami")(whoami_cmd.whoami)
app.command("design")(design_cmd.design)

# Command groups.
app.add_typer(auth_cmds.app, name="auth", help="Manage API keys.")
app.add_typer(me_cmds.app, name="me", help="View and update your profile.")
app.add_typer(workflows_cmds.app, name="workflows", help="Browse and manage workflows.")
app.add_typer(templates_cmds.app, name="templates", help="Browse, publish, and fork templates.")
app.add_typer(teams_cmds.app, name="teams", help="Manage teams.")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"goodeye {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the CLI version and exit.",
    ),
) -> None:
    """Global options processed before any subcommand."""
    # Body intentionally empty; the callback fires only to register the option.
    _ = version


def main() -> None:
    """Console-script entrypoint with a structured-error-friendly wrapper."""
    console = Console(stderr=True)
    try:
        app()
    except GoodeyeError as exc:
        console.print(f"[bold red]{exc.slug}[/bold red]: {exc.message}")
        if exc.hint:
            console.print(f"[dim]hint: {exc.hint}[/dim]")
        sys.exit(1)
