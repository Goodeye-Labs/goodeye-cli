"""`goodeye login` command."""

from __future__ import annotations

import platform

import typer
from rich.console import Console

from goodeye_cli.auth_flows import device_code_login
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.referrals import _maybe_redeem_referral
from goodeye_cli.config import get_server, save_client_config, save_credentials


def run_interactive_login(server: str, console: Console, referral_code: str | None) -> None:
    """Run the browser/device-code sign-in flow and save local credentials.

    Shared by ``goodeye login`` and ``goodeye register``: the hosted sign-in
    page creates the account for new users and signs in returning users, so both
    commands drive the same interactive path. Redeems ``referral_code`` once
    credentials are saved, if one was supplied.
    """
    with GoodeyeClient(server) as client:
        client_config = client.get_client_config()
    save_client_config(client_config.model_dump())
    hostname = platform.node() or "unknown"
    api_key = device_code_login(
        server,
        workos_client_id=client_config.workos_client_id,
        workos_device_authorization_uri=client_config.workos_device_authorization_uri,
        workos_token_uri=client_config.workos_token_uri,
        hostname=hostname,
        console=console,
    )

    path = save_credentials({"api_key": api_key, "server": server})
    console.print(f"[green]Signed in.[/green] Credentials saved to {path}")
    _maybe_redeem_referral(server, api_key, referral_code, console)


def login(
    email: str | None = typer.Option(
        None,
        "--email",
        "-e",
        help=(
            "Start non-interactive email-code login. "
            "Use `goodeye login-verify --email <email> --code <code>` to finish."
        ),
    ),
    referral_code: str | None = typer.Option(
        None,
        "--referral-code",
        help="Referral code to claim a bonus after signing in.",
    ),
) -> None:
    """Sign in to Goodeye on this machine.

    With no options, runs the interactive browser/device-code login for humans.
    With ``--email``, starts a non-interactive email-code login for agents and
    automation, then exits so the code can be supplied in a separate command.
    """
    console = Console()
    server = get_server()

    if email:
        with GoodeyeClient(server) as client:
            client.login(email)
        console.print("Check your email for next steps.")
        console.print(
            f"[dim]Non-interactive login started. Finish with: "
            f"goodeye login-verify --email {email} --code <code>"
            f" (add --referral-code <code> to claim a referral bonus)[/dim]"
        )
        return

    run_interactive_login(server, console, referral_code)


def login_verify(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email address used to start non-interactive login.",
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
        help="Referral code to claim a bonus after signing in.",
    ),
) -> None:
    """Complete non-interactive email-code login and save local credentials."""
    console = Console()
    server = get_server()
    with GoodeyeClient(server) as client:
        result = client.login_verify(email, code)
    path = save_credentials({"api_key": result.api_key, "server": server})
    console.print(f"[green]Signed in.[/green] Credentials saved to {path}")
    _maybe_redeem_referral(server, result.api_key, referral_code, console)
