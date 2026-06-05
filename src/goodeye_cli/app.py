"""Typer app root. Wires subcommands and a global error handler."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

import typer
from rich.console import Console

from goodeye_cli import __version__
from goodeye_cli import update as update_checks
from goodeye_cli.commands import auth as auth_cmds
from goodeye_cli.commands import design as design_cmd
from goodeye_cli.commands import image_generators as image_generators_cmds
from goodeye_cli.commands import invitations as invitations_cmds
from goodeye_cli.commands import login as login_cmd
from goodeye_cli.commands import logout as logout_cmd
from goodeye_cli.commands import me as me_cmds
from goodeye_cli.commands import register as register_cmd
from goodeye_cli.commands import teams as teams_cmds
from goodeye_cli.commands import templates as templates_cmds
from goodeye_cli.commands import update as update_cmd
from goodeye_cli.commands import usage as usage_cmd
from goodeye_cli.commands import verifiers as verifiers_cmds
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
app.command("update")(update_cmd.update)
app.command("upgrade", hidden=True)(update_cmd.update)
app.command("whoami")(whoami_cmd.whoami)
app.command("usage")(usage_cmd.usage)
app.command("design")(design_cmd.design)

# Command groups.
app.add_typer(auth_cmds.app, name="auth", help="Manage API keys.")
app.add_typer(me_cmds.app, name="me", help="View and update your profile.")
app.add_typer(workflows_cmds.app, name="workflows", help="Browse and manage workflows.")
app.add_typer(templates_cmds.app, name="templates", help="Browse, publish, and fork templates.")
app.add_typer(teams_cmds.app, name="teams", help="Manage teams.")
app.add_typer(verifiers_cmds.app, name="verifiers", help="Deploy and run verifiers.")
app.add_typer(
    image_generators_cmds.app,
    name="image-generators",
    help="Deploy and run image generators.",
)
app.add_typer(invitations_cmds.app, name="invitations", help="Manage invitations.")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"goodeye {__version__}")
        raise typer.Exit()


def _get_background_notice_args() -> Sequence[str]:
    """Return real CLI args when running through the console entrypoint.

    Typer's CliRunner does not rewrite ``sys.argv``; tests patch this function
    when they need to exercise invocation-specific suppression.
    """
    executable = Path(sys.argv[0]).name
    if not (executable.startswith("goodeye") or executable == "__main__.py"):
        return ()
    return sys.argv[1:]


def _maybe_emit_background_update_notice() -> None:
    try:
        args = _get_background_notice_args()
        if update_checks.should_suppress_background_notice(args, os.environ):
            return

        result = update_checks.check_for_update_background()
        if result is None or not result.update_available:
            return

        typer.echo(update_checks.format_update_notice(result), err=True)
    except Exception:
        # Swallow everything: a broken update check must never break the user's command.
        return


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
    _ = version
    _maybe_emit_background_update_notice()


def main() -> None:
    """Console-script entrypoint with a structured-error-friendly wrapper."""
    console = Console(stderr=True)
    try:
        app()
    except GoodeyeError as exc:
        console.print(f"[bold red]{exc.slug}[/bold red]: {exc.message}")
        if exc.hint:
            console.print(f"[dim]hint: {exc.hint}[/dim]")
        sys.exit(exc.exit_code)
