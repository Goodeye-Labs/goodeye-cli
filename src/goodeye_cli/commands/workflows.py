"""`goodeye workflows ...` subcommand group."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.wire import WorkflowDetail

app = typer.Typer(help="Browse and manage workflows.", no_args_is_help=True)


def _client(*, require_auth: bool) -> GoodeyeClient:
    api_key = get_api_key()
    if require_auth and not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


@app.command("list")
def list_cmd(
    filter_: str = typer.Option(
        "mine",
        "--filter",
        "-f",
        help="Scope filter: mine or all (workflows are always private; these are equivalent).",
        case_sensitive=False,
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    search: str | None = typer.Option(None, "--search", "-s", help="Search query."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
) -> None:
    """List workflows you own."""
    console = Console()
    items: list[Any] = []
    with _client(require_auth=True) as client:
        cursor: str | None = None
        while True:
            page = client.list_workflows(
                filter_=filter_.lower() if filter_ else None,
                tag=tag,
                search=search,
                cursor=cursor,
            )
            items.extend(page.items)
            cursor = page.next_cursor
            if not cursor:
                break

    if json_output:
        typer.echo("[" + ",".join(i.model_dump_json() for i in items) + "]")
        return

    table = Table(title=f"Workflows ({filter_})")
    table.add_column("ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Version", justify="right")
    table.add_column("Fork of", no_wrap=True)
    table.add_column("Description")
    for item in items:
        fork_cell = ""
        if item.parent_template_id:
            fork_cell = f"tpl {item.parent_template_id[:8]}...@v{item.parent_template_version}"
        table.add_row(item.id, item.name, str(item.current_version), fork_cell, item.description)
    if not items:
        console.print("[dim]No workflows matched.[/dim]")
    else:
        console.print(table)


@app.command("get")
def get_cmd(
    id_or_name: str = typer.Argument(..., help="Workflow ID (ULID) or name."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned version."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write body to this path instead of stdout."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full workflow record as JSON instead of markdown."
    ),
) -> None:
    """Download a workflow. Prints the workflow's markdown to stdout."""
    console = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.get_workflow(id_or_name, version=version, accept_markdown=not json_output)

    if json_output:
        assert isinstance(result, WorkflowDetail)
        text = result.model_dump_json(indent=2)
    else:
        assert isinstance(result, str)
        text = result

    if output is not None:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output}")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


def _parse_front_matter(source: str) -> tuple[dict[str, Any], str]:
    """Extract a YAML front-matter block from a markdown source.

    Front-matter is recognised when the file begins with ``---`` on its own line
    and a matching terminator ``---`` appears later. Everything between is parsed
    as YAML. Everything after the terminator is the body.

    Returns ``({}, source)`` if no front-matter is present.
    """
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return {}, source
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            yaml_text = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            if body.startswith("\n"):
                body = body[1:]
            parsed = yaml.safe_load(yaml_text) or {}
            if not isinstance(parsed, dict):
                raise ValidationFailed(
                    slug="validation_error",
                    message="YAML front-matter must be a mapping.",
                )
            return parsed, body
    return {}, source


def _coerce_legacy_manifest(front_matter: dict[str, Any]) -> dict[str, Any]:
    raw = front_matter.get("manifest")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationFailed(
            slug="validation_error",
            message="`manifest` in front-matter must be a mapping.",
        )
    return dict(raw)


def _coerce_outcome(front_matter: dict[str, Any], legacy: dict[str, Any]) -> str | None:
    raw = front_matter.get("outcome", legacy.get("outcome"))
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw
    raise ValidationFailed(
        slug="validation_error",
        message="`outcome` in front-matter must be a non-empty string.",
    )


def _coerce_tags(front_matter: dict[str, Any], legacy: dict[str, Any]) -> list[str]:
    raw = front_matter.get("tags", legacy.get("tags"))
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    raise ValidationFailed(
        slug="validation_error",
        message="`tags` in front-matter must be a list of strings.",
    )


def _extract_discovery_facets(
    front_matter: dict[str, Any], *, console: Console
) -> tuple[str | None, list[str]]:
    """Pull outcome+tags from the front-matter, promoting legacy manifest keys."""
    legacy = _coerce_legacy_manifest(front_matter)
    outcome = _coerce_outcome(front_matter, legacy)
    tags = _coerce_tags(front_matter, legacy)
    if legacy:
        dropped = sorted(set(legacy) - {"outcome", "tags"})
        if dropped:
            console.print(
                "[yellow]Warning:[/yellow] front-matter `manifest:` block is "
                "deprecated. Promoted `outcome` / `tags` to the top level; "
                f"dropped: {', '.join(dropped)}. The server no longer stores "
                "these fields; move verifier scripts and cURLs into the body."
            )
    return outcome, tags


@app.command("publish")
def publish(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Markdown file to upload."),
    name_override: str | None = typer.Option(
        None, "--name", help="Override the `name` from front-matter."
    ),
) -> None:
    """Upload a workflow from a markdown file with YAML front-matter.

    The front-matter follows the Claude Code skills convention:

    \b
    ---
    name: incident-postmortem
    description: Draft a postmortem from an incident transcript. Use when ...
    # Optional discovery facets:
    # tags: [sre, postmortem]
    # outcome: Reduce mean-time-to-postmortem from days to hours.
    ---

    Only ``name`` and ``description`` are required. Everything else is optional.

    Workflows are always private to the caller. To share a workflow as a
    public template, run ``goodeye templates publish <workflow-id>`` as a
    separate, explicit step.

    Verifier scripts and Truesight cURLs belong in the body as fenced code
    blocks; the registry treats the body as opaque markdown.
    """
    console = Console()
    source = file.read_text(encoding="utf-8")
    front_matter, _stripped_body = _parse_front_matter(source)
    # Server stores the full markdown (including front-matter) so the workflow
    # round-trips as a drop-in Claude Code SKILL.md.
    body = source

    effective_name: str | None = (
        name_override or front_matter.get("name") or front_matter.get("slug")
    )
    if not isinstance(effective_name, str) or not effective_name:
        raise ValidationFailed(
            slug="validation_error",
            message="Missing `name`. Add `name:` to the front-matter or pass --name.",
        )

    description = front_matter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValidationFailed(
            slug="validation_error",
            message="Missing `description`. Add `description:` to the front-matter.",
        )

    outcome, tags = _extract_discovery_facets(front_matter, console=console)

    with _client(require_auth=True) as client:
        result = client.save_workflow(
            name=effective_name,
            description=description,
            body=body,
            outcome=outcome,
            tags=tags,
        )

    console.print(
        f"[green]Saved[/green] {result.name} v{result.version} "
        f"(workflow_id={result.workflow_id})"
    )


@app.command("lineage")
def lineage(
    workflow_id: str = typer.Argument(..., help="Workflow ID or name."),
    json_output: bool = typer.Option(False, "--json", help="Print lineage as JSON."),
) -> None:
    """Show a workflow's fork lineage."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.lookup_workflow_lineage(workflow_id)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    if result.parent_template_id is None:
        console.print("[dim]Not a fork (no parent template).[/dim]")
        return
    console.print(
        f"Forked from template {result.parent_template_id} "
        f"pinned to v{result.parent_template_version}; "
        f"upstream latest: v{result.upstream_latest_version}; "
        f"upstream unpublished at pinned version: {result.is_upstream_unpublished}."
    )


@app.command("delete")
def delete(
    workflow_id: str = typer.Argument(..., help="Workflow ID or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a workflow you own."""
    console = Console()
    if not yes:
        confirm = typer.confirm(f"Delete workflow {workflow_id}?", default=False)
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(code=1)
    with _client(require_auth=True) as client:
        result = client.delete_workflow(workflow_id)
    if result.deleted:
        console.print(f"[green]Deleted[/green] {result.name}")
    else:
        console.print(f"[yellow]Not deleted[/yellow] {result.name}")


__all__ = [
    "_parse_front_matter",
    "app",
    "delete",
    "get_cmd",
    "lineage",
    "list_cmd",
    "publish",
]
