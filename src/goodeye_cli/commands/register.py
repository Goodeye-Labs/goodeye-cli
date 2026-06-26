"""`goodeye register` and `goodeye register-verify` commands."""

from __future__ import annotations

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.login import run_interactive_login
from goodeye_cli.commands.referrals import _maybe_redeem_referral
from goodeye_cli.config import get_server, save_credentials


def register(
    email: str | None = typer.Option(
        None,
        "--email",
        "-e",
        help=(
            "Start non-interactive account registration. "
            "Use `goodeye register-verify --email <email> --code <code>` to finish."
        ),
    ),
    referral_code: str | None = typer.Option(
        None,
        "--referral-code",
        help="Referral code to claim a bonus after registering.",
    ),
) -> None:
    """Create a Goodeye account on this machine.

    With no options, runs the interactive browser/device-code flow for humans:
    the hosted sign-in page creates the account for new users (and signs in
    returning users), so registering and signing in share one path. With
    ``--email``, starts a non-interactive email-code registration for agents and
    automation, then exits so the emailed code can be supplied in a separate
    command.
    """
    console = Console()
    server = get_server()

    if email is None:
        run_interactive_login(server, console, referral_code)
        return

    with GoodeyeClient(server) as client:
        client.register(email)
    console.print("Check your email for next steps.")
    console.print(
        f"[dim]Non-interactive registration started. Finish with: "
        f"goodeye register-verify --email {email} --code <code>"
        f" (add --referral-code <code> to claim a referral bonus)[/dim]"
    )


def register_verify(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email address used to start registration.",
    ),
    code: str = typer.Option(
        ...,
        "--code",
        "-c",
        help="One-time code sent to your email.",
    ),
    referral_code: str | None = typer.Option(
        None,
        "--referral-code",
        help="Referral code to claim a bonus after registering.",
    ),
) -> None:
    """Finish a non-interactive sign-up by submitting the emailed code (for AI agents
    and automation, no browser)."""
    console = Console()
    server = get_server()
    with GoodeyeClient(server) as client:
        result = client.register_verify(email, code)
    path = save_credentials({"api_key": result.api_key, "server": server})
    console.print(f"[green]Account registered.[/green] Credentials saved to {path}")
    _maybe_redeem_referral(server, result.api_key, referral_code, console)
