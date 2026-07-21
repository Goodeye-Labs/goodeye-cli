"""`goodeye update` command."""

from __future__ import annotations

import typer

from goodeye_cli import __version__
from goodeye_cli.errors import GoodeyeError
from goodeye_cli.update import (
    blocked_upgrade_hint,
    check_for_update,
    detect_install_method,
    manual_update_commands_text,
    read_installed_version,
    run_update,
)


def update(
    check: bool = typer.Option(
        False,
        "--check",
        help="Check PyPI for a newer version without installing.",
    ),
) -> None:
    """Upgrade the CLI to the latest release."""
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
        f"Updating goodeye from {result.current_version} "
        f"to {result.latest_version} via {method}...",
        err=True,
    )
    run_update(method)

    # The upgrade tool exits 0 even when it changes nothing (a version pin, or a
    # no-op). Confirm the real outcome by re-reading the installed version rather
    # than assuming the intended target landed.
    installed = read_installed_version()
    before = result.current_version

    if installed is None:
        typer.echo("The upgrade ran, but goodeye could not confirm the installed version.")
        typer.echo("Run `goodeye --version` to check which version is active.")
        return

    if installed == before:
        typer.echo(
            f"No upgrade happened: goodeye is still {before} "
            f"({result.latest_version} is available)."
        )
        typer.echo(blocked_upgrade_hint(method))
        return

    typer.echo(f"Updated goodeye from {before} to {installed}.")
    typer.echo("Run `goodeye --version` to verify the new version is active.")
