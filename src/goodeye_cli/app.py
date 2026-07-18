"""Typer app root. Wires subcommands and a global error handler."""

from __future__ import annotations

import atexit
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from goodeye_cli import __version__, sync
from goodeye_cli import background as background_sync
from goodeye_cli import update as update_checks
from goodeye_cli.commands import auth as auth_cmds
from goodeye_cli.commands import billing as billing_cmds
from goodeye_cli.commands import design as design_cmd
from goodeye_cli.commands import image_generators as image_generators_cmds
from goodeye_cli.commands import images as images_cmds
from goodeye_cli.commands import invitations as invitations_cmds
from goodeye_cli.commands import login as login_cmd
from goodeye_cli.commands import logout as logout_cmd
from goodeye_cli.commands import me as me_cmds
from goodeye_cli.commands import referrals as referrals_cmds
from goodeye_cli.commands import register as register_cmd
from goodeye_cli.commands import skills as skills_cmds
from goodeye_cli.commands import teams as teams_cmds
from goodeye_cli.commands import templates as templates_cmds
from goodeye_cli.commands import update as update_cmd
from goodeye_cli.commands import usage as usage_cmd
from goodeye_cli.commands import verifiers as verifiers_cmds
from goodeye_cli.commands import whoami as whoami_cmd
from goodeye_cli.config import get_api_key, get_config_paths
from goodeye_cli.errors import GoodeyeError

app = typer.Typer(
    name="goodeye",
    help=(
        "Goodeye CLI: a private home for the skills your AI follows and the "
        "verifiers its work must pass. Keep them in sync across every machine "
        "and agent you run, and share them only with who you choose."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _deprecated_workflows_notice(ctx: typer.Context) -> None:
    """Warn that `goodeye workflows` is a deprecated alias for `goodeye skills`.

    Registered as the callback for the hidden `workflows` alias typer app, so
    it runs once per invocation before the forwarded subcommand executes.
    """
    _ = ctx
    typer.echo(
        "'goodeye workflows' is deprecated and will be removed on 2026-10-01; "
        "use 'goodeye skills'.",
        err=True,
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
app.add_typer(
    skills_cmds.app,
    name="skills",
    help="Find, run, save, share, and sync your private skills.",
)
# Deprecated alias for the skills command group: kept working (and hidden
# from top-level help) so scripts using `goodeye workflows ...` keep running
# while a stderr notice points them at `goodeye skills` ahead of the removal
# date. Remove once the cutoff passes.
app.add_typer(
    skills_cmds.app,
    name="workflows",
    hidden=True,
    callback=_deprecated_workflows_notice,
)
app.add_typer(templates_cmds.app, name="templates", help="Browse, publish, and fork templates.")
app.add_typer(teams_cmds.app, name="teams", help="Manage teams.")
app.add_typer(
    verifiers_cmds.app,
    name="verifiers",
    help="Deploy and run verifiers: hosted, versioned checks everyone runs the same way.",
)
app.add_typer(
    image_generators_cmds.app,
    name="image-generators",
    help="Deploy and run image generators.",
)
app.add_typer(images_cmds.app, name="images", help="Upload and manage hosted images.")
app.add_typer(invitations_cmds.app, name="invitations", help="Manage invitations.")
app.add_typer(referrals_cmds.app, name="referrals", help="View and redeem referral codes.")
app.add_typer(
    billing_cmds.app,
    name="billing",
    help="Manage your Pro subscription and credits.",
)
# Deprecated alias for the reorganized subscription commands: kept working (and
# hidden from top-level help) so scripts using the v0.22.0 `goodeye
# subscription upgrade / cancel / portal` keep running while a stderr notice
# points them at `goodeye billing`. Remove in a later release.
app.add_typer(
    billing_cmds.subscription_app,
    name="subscription",
    hidden=True,
    help="Deprecated: use `goodeye billing` instead.",
)


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


def _maybe_register_auto_pull_tail() -> None:
    """Register the automatic-pull tail when the cheap synchronous gate passes.

    Reuses the best-effort ethos of the update notice: a broken gate must never
    break the user's command, so the whole body is wrapped in a swallow. The
    network work runs only later, in the ``atexit`` tail, so even an eligible
    invocation pays nothing here beyond a couple of small local JSON reads.
    """
    try:
        args = _get_background_notice_args()
        paths = get_config_paths()
        config = sync.load_sync_config(paths)
        state = sync.load_sync_state(paths)
        if background_sync.should_run_auto_pull(
            args,
            os.environ,
            config,
            state,
            authenticated=get_api_key(paths) is not None,
            now=datetime.now(UTC),
        ):
            atexit.register(background_sync.run_auto_pull_tail)
    except Exception:
        # A broken gate must never break the user's command.
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
    _maybe_register_auto_pull_tail()


def main() -> None:
    """Console-script entrypoint with a structured-error-friendly wrapper."""
    console = Console(stderr=True)
    try:
        app()
    except GoodeyeError as exc:
        console.print(f"[bold red]{exc.slug}[/bold red]: {exc.message}")
        if exc.hint:
            console.print(f"[dim]hint: {exc.hint}[/dim]")
        # Under GOODEYE_DEBUG, surface the underlying cause (e.g. the exact
        # transport failure behind a network_error: DNS vs timeout vs cert) so
        # the humane message does not hide what a debugger needs.
        if os.environ.get("GOODEYE_DEBUG") and exc.__cause__ is not None:
            cause = exc.__cause__
            console.print(f"[dim]debug: {type(cause).__name__}: {cause}[/dim]")
        sys.exit(exc.exit_code)
    except Exception as exc:
        # Backstop so no command ever dumps a raw traceback on a first run.
        # Expected transport failures are already translated to NetworkError
        # (a GoodeyeError) above; this catches anything genuinely unexpected.
        # SystemExit and KeyboardInterrupt are BaseException, so normal exits,
        # `--help`, and Ctrl-C pass straight through.
        if os.environ.get("GOODEYE_DEBUG"):
            raise
        console.print(f"[bold red]unexpected error[/bold red]: {type(exc).__name__}: {exc}")
        console.print("[dim]hint: re-run with GOODEYE_DEBUG=1 to see the full traceback[/dim]")
        sys.exit(1)
