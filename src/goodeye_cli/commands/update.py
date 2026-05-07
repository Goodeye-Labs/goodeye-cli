"""`goodeye update` command."""

from __future__ import annotations

import typer

from goodeye_cli import __version__
from goodeye_cli.errors import GoodeyeError
from goodeye_cli.update import (
    check_for_update,
    detect_install_method,
    manual_update_commands_text,
    run_update,
)


def update(
    check: bool = typer.Option(
        False,
        "--check",
        help="Check PyPI for a newer version without installing.",
    ),
) -> None:
    """Update the Goodeye CLI to the latest PyPI release when possible."""
    try:
        result = check_for_update(current_version=__version__, timeout=5.0)
    except GoodeyeError as exc:
        if check:
            typer.secho(exc.message, err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        raise

    if check:
        typer.echo(f"Current version: {result.current_version}")
        typer.echo(f"Latest PyPI version: {result.latest_version}")
        typer.echo(f"Update available: {'yes' if result.update_available else 'no'}")
        return

    if not result.update_available:
        typer.echo(f"goodeye {result.current_version} is up to date.")
        return

    method = detect_install_method()
    if method == "unsupported":
        raise GoodeyeError(
            slug="update_unsupported_install",
            message=(
                "This Goodeye install cannot be updated automatically. "
                "Use one of the manual commands below."
            ),
            hint=manual_update_commands_text(),
        )

    typer.echo(
        f"Upgrading goodeye from {result.current_version} "
        f"to {result.latest_version} via {method}...",
        err=True,
    )
    run_update(method)
    typer.echo(
        f"Updated goodeye from {result.current_version} to {result.latest_version}."
    )
    typer.echo("Run `goodeye --version` to verify the new version is active.")
