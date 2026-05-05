"""Typer commands for ``goodeye verifiers`` (deploy, list, run, revoke)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.prompts import confirm_destructive
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
    workflow_id: str | None = typer.Option(
        None,
        "--workflow-id",
        help="Stamp this workflow UUID onto the run for provenance.",
    ),
    workflow_version: int | None = typer.Option(
        None,
        "--workflow-version",
        help="Stamp the workflow version invoking this run.",
    ),
    workflow_ref: str | None = typer.Option(
        None,
        "--workflow-ref",
        help="Free-form workflow reference (e.g. slug or human label) for provenance.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Caller-supplied run correlation ID stamped onto the verifier run row.",
    ),
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
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_ref=workflow_ref,
            run_id=run_id,
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


@app.command("show")
def show(
    verifier_id: str = typer.Argument(..., help="Verifier UUID."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned verifier version."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Show one verifier version: criterion, input contract, calibration."""
    console = Console()
    with _client() as client:
        result = client.get_verifier(verifier_id, version=version)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(f"[bold]{result.name}[/bold] (v{result.version})  status={result.status}")
    console.print(f"verifier_id: {result.verifier_id}")
    console.print(f"description: {result.description}")
    console.print(f"input_contract: {result.input_contract}")
    if result.input_fields:
        console.print(f"input_fields: {', '.join(result.input_fields)}")
    console.print(f"config_hash: {result.config_hash}")
    console.print()
    console.print("[bold]criterion[/bold]")
    console.print(result.criterion)


@app.command("revoke")
def revoke(
    verifier_id: str = typer.Argument(..., help="Verifier UUID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Revoke a semantic verifier."""
    console = Console()
    if not confirm_destructive(f"Revoke verifier {verifier_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client() as client:
        result = client.revoke_verifier(verifier_id)
    console.print(f"[green]Revoked[/green] {result.name}")
