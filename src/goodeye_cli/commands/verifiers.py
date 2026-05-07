"""Typer commands for ``goodeye verifiers`` (deploy, list, run, revoke)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import (
    VERIFIER_REQUEST_TIMEOUT_SECONDS,
    get_api_key,
    get_request_timeout_seconds,
    get_server,
)
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)

app = typer.Typer(
    help=(
        "Manage verifiers: owner-scoped, versioned LLM judges that score one "
        "criterion ('does this output satisfy this rule?') against caller "
        "inputs. Workflows typically reference deployed verifiers by UUID "
        "(or UUID@version). The **`run`** command (and REST/MCP equivalents) "
        "also accepts the caller-owned name or **`system:<name>`** for seeded "
        "platform judges (`show`/`revoke` accept UUID or name only)."
        "\n\n"
        "All commands require auth (`goodeye login` or GOODEYE_API_KEY).\n"
        "\n"
        "Lifecycle: deploy -> show/list -> run (many times) -> revoke."
    ),
    no_args_is_help=True,
)


def _client(*, timeout_default: float | None = None) -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    timeout = (
        get_request_timeout_seconds(default=timeout_default)
        if timeout_default is not None
        else get_request_timeout_seconds()
    )
    return GoodeyeClient(get_server(), api_key=api_key, timeout=timeout)


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


def _read_config_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Verifier config file not found: {source}",
        )
    if not path.is_file():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Verifier config path is not a file: {source}",
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not decode verifier config as UTF-8: {source}",
        ) from exc
    except OSError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not read verifier config {source}: {exc}",
        ) from exc


@app.command("deploy")
def deploy(
    config_file: str = typer.Argument(
        ...,
        help=(
            "Path to a verifier config JSON file, or '-' to read JSON from stdin "
            "(preferred for generated agent output)."
        ),
    ),
) -> None:
    """Deploy a verifier or append a new version from a JSON file or stdin.

    The config file is a single JSON object with these fields:

    \b
      name                    Required. Lowercase letters/digits/hyphens, max 128.
                              Unique per owner among non-revoked verifiers.
      description             Required. 1 to 1024 chars.
      criterion               Required. Rubric prose the judge applies. Max 8000 chars.
      input_contract          Required. One of "text", "text_image", "image".
      input_fields            Required for text and text_image; must be empty for image.
                              List of input names the judge sees at run time.
      few_shot_examples       Optional list of labeled examples. Each entry has
                              inputs, media_url, passed, reasoning. Must satisfy
                              the contract (text-only, text+image, or image-only).
      model_settings          Optional {"model": ..., "reasoning_effort": ...}.
                              reasoning_effort: minimal | low | medium | high.
                              Only these two fields are honored.
      expected_version_token  Required when re-deploying an existing verifier.
                              Get the current token from `goodeye verifiers list`
                              or the previous deploy response. Omit for first deploy.

    On success prints `verifier_id`, the new `version`, and the new
    `version_token`. Persist the token: it's required for the next re-deploy.
    """
    payload = _json_object(_read_config_input(config_file), "config input")
    console = Console()
    with _client() as client:
        result = client.deploy_verifier(payload)
    console.print(
        f"[green]Deployed[/green] {result.name} v{result.version} "
        f"(verifier_id={result.verifier_id}, version_token={result.version_token})"
    )


@app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
    limit: int = typer.Option(25, "--limit", "-l", min=1, help="Max results per page."),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow cursors and combine all pages."),
) -> None:
    """List active (non-revoked) verifiers you own.

    Shows the current version and version token for each verifier. Use
    `goodeye verifiers show <id>` for the full deploy-time config of a
    specific version.
    """
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client() as client:
        items, final_cursor = fetch_pages(
            lambda page_cursor: client.list_verifiers(limit=limit, cursor=page_cursor),
            cursor=cursor,
            all_pages=all_pages,
        )
    if mode == "json":
        echo_json(items_envelope(items, next_cursor=final_cursor))
        return
    table = Table(title="Semantic verifiers")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Version", justify="right")
    table.add_column("Status")
    table.add_column("Role")
    table.add_column("Source wf")
    table.add_column("Description")
    for item in items:
        role = item.role or "—"
        src = item.source_workflow_id or "—"
        table.add_row(
            item.verifier_id,
            item.name,
            str(item.current_version),
            item.status,
            role,
            src,
            item.description,
        )
    if not items:
        console.print("[dim]No verifiers.[/dim]")
    else:
        console.print(table)
    if final_cursor:
        hint = next_page_hint(
            ("goodeye", "verifiers", "list"),
            next_cursor=final_cursor,
            limit=limit,
        )
        console.print(f"[dim]{hint}[/dim]")


@app.command("run")
def run(
    verifier_id: str = typer.Argument(
        ...,
        help=(
            "Verifier UUID, your caller-owned name for an active verifier, "
            "or system:<name> for a seeded platform judge."
        ),
    ),
    inputs_json: str = typer.Option(
        "{}",
        "--inputs-json",
        help=(
            "JSON object whose keys match the deployed `input_fields` exactly "
            "(no missing or extra). Use `{}` for image-only verifiers. "
            "All values must be non-empty strings."
        ),
    ),
    media_url: str | None = typer.Option(
        None,
        "--media-url",
        help=(
            "Public HTTPS image URL. Required for `text_image` and `image` "
            "contracts; rejected for `text`."
        ),
    ),
    version: int | None = typer.Option(
        None, "--version", "-v", help="Pin to a specific version; defaults to current."
    ),
    workflow_id: str | None = typer.Option(
        None,
        "--workflow-id",
        help=(
            "Optional workflow UUID stamped onto the run for provenance. "
            "Access-checked: a workflow you cannot see returns 404."
        ),
    ),
    workflow_version: int | None = typer.Option(
        None,
        "--workflow-version",
        help="Workflow version invoking this run; pair with --workflow-id.",
    ),
    workflow_ref: str | None = typer.Option(
        None,
        "--workflow-ref",
        help="Free-form workflow label (slug or name) for human-readable provenance.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Caller-supplied correlation ID stamped onto the run row.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Run a verifier and print pass/fail plus reasoning.

    Caller-shape errors (mismatched input keys, wrong media for the contract)
    return 400 with no run row written. Judge runtime errors persist a row
    with `status="error"`; the command exits 1 and prints the error code.

    Exits 0 on a successful judgment regardless of pass/fail; check the
    PASS/FAIL line or the JSON `passed` field to gate downstream actions.
    """
    inputs = _json_object(inputs_json, "--inputs-json")
    console = Console()
    with _client(timeout_default=VERIFIER_REQUEST_TIMEOUT_SECONDS) as client:
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
    verifier_id: str = typer.Argument(
        ...,
        help=(
            "Verifier UUID or your caller-owned name for an active verifier (not system:<name>)."
        ),
    ),
    version: int | None = typer.Option(
        None, "--version", "-v", help="Pin to a specific version; defaults to current."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Show one verifier version: criterion, contract, calibration.

    Returns the full deploy-time payload (criterion, input_contract,
    input_fields, few_shot_examples, judge_model_config) plus a `config_hash`
    for drift detection across versions. Owner-only: a non-owned or revoked
    verifier returns 404.
    """
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
    verifier_id: str = typer.Argument(
        ...,
        help=(
            "Verifier UUID or your caller-owned name for an active verifier (not system:<name>)."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Revoke a verifier you own.

    Sets the verifier to `revoked` and removes it from list/show/run
    (subsequent calls return 404). Existing run rows are retained for audit.
    Irreversible: replace by deploying a fresh verifier under a new name.
    """
    console = Console()
    if not confirm_destructive(f"Revoke verifier {verifier_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client() as client:
        result = client.revoke_verifier(verifier_id)
    console.print(f"[green]Revoked[/green] {result.name}")
