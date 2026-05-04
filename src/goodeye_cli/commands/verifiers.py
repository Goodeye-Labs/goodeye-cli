"""Typer commands for ``goodeye verifiers`` (deploy, list, run, revoke)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed

app = typer.Typer(
    help="Manage native semantic verifiers.",
    no_args_is_help=True,
)


def _client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"{label} must be valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed(
            slug="validation_error",
            message=f"{label} must be a JSON object.",
        )
    return parsed


@app.command("deploy")
def deploy(
    config_file: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Verifier config JSON file.",
    ),
) -> None:
    """Deploy or version a verifier from a JSON config file."""
    payload = _json_object(config_file.read_text(encoding="utf-8"), "config file")
    console = Console()
    with _client() as client:
        result = client.deploy_verifier(payload)
    console.print(
        f"[green]Deployed[/green] {result.name} v{result.version} "
        f"(verifier_id={result.verifier_id}, version_token={result.version_token})"
    )


@app.command("list")
def list_cmd(json_output: bool = typer.Option(False, "--json", help="Print JSON.")) -> None:
    """List owned semantic verifiers."""
    console = Console()
    with _client() as client:
        result = client.list_verifiers()
    if json_output:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in result.items], indent=2))
        return
    table = Table(title="Semantic verifiers")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Version", justify="right")
    table.add_column("Status")
    table.add_column("Description")
    for item in result.items:
        table.add_row(
            item.verifier_id,
            item.name,
            str(item.current_version),
            item.status,
            item.description,
        )
    if not result.items:
        console.print("[dim]No verifiers.[/dim]")
    else:
        console.print(table)


@app.command("run")
def run(
    verifier_id: str = typer.Argument(..., help="Verifier UUID."),
    inputs_json: str = typer.Option("{}", "--inputs-json", help="JSON object of text inputs."),
    media_url: str | None = typer.Option(None, "--media-url", help="Image URL for image modes."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned verifier version."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Run a semantic verifier and print pass/fail plus reasoning."""
    inputs = _json_object(inputs_json, "--inputs-json")
    console = Console()
    with _client() as client:
        result = client.run_verifier(
            verifier_id,
            inputs={key: str(value) for key, value in inputs.items()},
            media_url=media_url,
            version=version,
        )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    if result.status == "error":
        console.print(f"[bold red]ERROR[/bold red] {result.error_code}: {result.error_message}")
        raise typer.Exit(code=1)
    passed = result.passed is True
    label = "PASS" if passed else "FAIL"
    color = "green" if passed else "red"
    console.print(f"[bold {color}]{label}[/bold {color}] verifier_run_id={result.verifier_run_id}")
    if result.reasoning:
        console.print(result.reasoning)


@app.command("revoke")
def revoke(
    verifier_id: str = typer.Argument(..., help="Verifier UUID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Revoke a semantic verifier."""
    console = Console()
    if not yes and not typer.confirm(f"Revoke verifier {verifier_id}?", default=False):
        console.print("Cancelled.")
        raise typer.Exit(code=1)
    with _client() as client:
        result = client.revoke_verifier(verifier_id)
    console.print(f"[green]Revoked[/green] {result.name}")
