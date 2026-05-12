"""`goodeye workflows ...` subcommand group."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)
from goodeye_cli.wire import SafetyCheckResult, WorkflowDetail

_WORKFLOW_VERIFIER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")

app = typer.Typer(
    help="Browse and manage the caller's private workflows.",
    no_args_is_help=True,
)


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
        help=(
            "Scope filter: mine (workflows you own), shared-with-me "
            "(workflows shared with you via grants), or all (mine + shared)."
        ),
        case_sensitive=False,
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    search: str | None = typer.Option(None, "--search", "-s", help="Search query."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
    limit: int = typer.Option(25, "--limit", "-l", min=1, help="Max results per page."),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow cursors and combine all pages."),
) -> None:
    """List workflows you own."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        items, final_cursor = fetch_pages(
            lambda page_cursor: client.list_workflows(
                filter_=filter_.lower() if filter_ else None,
                tag=tag,
                search=search,
                limit=limit,
                cursor=page_cursor,
            ),
            cursor=cursor,
            all_pages=all_pages,
        )

    if mode == "json":
        echo_json(items_envelope(items, next_cursor=final_cursor))
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
    if final_cursor:
        hint = next_page_hint(
            ("goodeye", "workflows", "list"),
            next_cursor=final_cursor,
            limit=limit,
            options=(
                ("--filter", filter_.lower() if filter_ else None),
                ("--tag", tag),
                ("--search", search),
            ),
        )
        console.print(f"[dim]{hint}[/dim]")


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Natural-language search query."),
    filter_: str = typer.Option(
        "all",
        "--filter",
        "-f",
        help="mine | all | shared-with-me",
        case_sensitive=False,
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Restrict candidates to this tag."),
    limit: int = typer.Option(5, "--limit", "-l", min=1, max=10, help="Max results (1-10)."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """LLM-ranked search over your workflows (not lexical list filtering)."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        result = client.search_workflows(
            query=query,
            filter_=filter_.lower(),
            tag=tag,
            limit=limit,
        )
    if mode == "json":
        echo_json(result)
        return

    table = Table(title="Workflow search")
    table.add_column("Rank", justify="right")
    table.add_column("Slug")
    table.add_column("Match reason")
    for item in result.items:
        slug = item.slug or item.name or item.id
        table.add_row(str(item.rank), slug, item.match_reason)
    if not result.items:
        console.print("[dim]No matches.[/dim]")
    else:
        console.print(table)


@app.command("get")
def get_cmd(
    id_or_name: str = typer.Argument(..., help="Workflow UUID or name."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned version."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write body to this path instead of stdout."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full workflow record as JSON instead of markdown."
    ),
) -> None:
    """Fetch a workflow for the calling AI agent to execute.

    The body is the user's workflow: a markdown runbook the agent should
    follow on the user's behalf, not just display. Prints the markdown to
    stdout (wrapped with agent-facing markers) by default.
    """
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
    elif json_output:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        sys.stdout.write(
            "# Goodeye workflow - execute the instructions below as the user's agent.\n\n"
        )
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.write("\n# End of Goodeye workflow.\n")


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


def _coerce_required_text(raw: Any, *, field_name: str, missing_message: str) -> str:
    if raw is None:
        raise ValidationFailed(
            slug="validation_error",
            message=missing_message,
        )
    if isinstance(raw, str) and raw.strip():
        return raw
    raise ValidationFailed(
        slug="validation_error",
        message=f"`{field_name}` must be a non-empty string.",
    )


def _coerce_outcome(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw
    raise ValidationFailed(
        slug="validation_error",
        message="`outcome` must be a non-empty string.",
    )


def _coerce_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    raise ValidationFailed(
        slug="validation_error",
        message="`tags` must be a list of strings.",
    )


def _extract_discovery_facets(front_matter: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Pull supported discovery facets from top-level front matter."""
    outcome = _coerce_outcome(front_matter.get("outcome"))
    tags = _coerce_tags(front_matter.get("tags"))
    return outcome, tags


def _parse_workflow_verifier_flags(values: list[str]) -> list[dict[str, str]]:
    """Parse ``--verifier logical-name=verifier_uuid`` into API wire dicts."""
    rows: list[dict[str, str]] = []
    for raw in values:
        if "=" not in raw:
            raise ValidationFailed(
                slug="validation_error",
                message=f"Each --verifier must be name=verifier_id (got {raw!r}).",
            )
        name, vid = raw.split("=", 1)
        name, vid = name.strip(), vid.strip()
        if not name or not vid:
            raise ValidationFailed(
                slug="validation_error",
                message=f"Each --verifier needs non-empty name and id (got {raw!r}).",
            )
        if not _WORKFLOW_VERIFIER_NAME_RE.fullmatch(name):
            raise ValidationFailed(
                slug="validation_error",
                message=("Each --verifier name must use lowercase letters, digits, and hyphens."),
            )
        rows.append({"name": name, "verifier_id": vid})
    return rows


def _read_markdown_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Markdown file not found: {source}",
        )
    if not path.is_file():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Markdown path is not a file: {source}",
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not decode markdown file as UTF-8: {source}",
        ) from exc
    except OSError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not read markdown file: {source}",
        ) from exc


@app.command("publish")
def publish(
    file: str = typer.Argument(
        ...,
        help=(
            "Markdown workflow file to upload, or '-' to read markdown from stdin "
            "(preferred for generated agent output)."
        ),
    ),
    name_override: str | None = typer.Option(
        None, "--name", help="Override the `name` from front-matter."
    ),
    description_override: str | None = typer.Option(
        None, "--description", help="Override the `description` from front-matter."
    ),
    outcome_override: str | None = typer.Option(
        None,
        "--outcome",
        help="Override the `outcome` from front-matter. Required for workflow discovery.",
    ),
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help=(
                "Workflow discovery tag. Repeat to set multiple tags; overrides front-matter tags."
            ),
        ),
    ] = None,
    expected_version_token: str | None = typer.Option(
        None,
        "--expected-version-token",
        help="Required token when updating an existing workflow. Omit only for creates.",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Optional provenance marker: 'manual' or 'teach'. Defaults to NULL.",
    ),
    verifier: Annotated[
        list[str] | None,
        typer.Option(
            "--verifier",
            help=(
                "Lowercase-kebab name=verifier UUID binding (repeatable). "
                "Forwarded in save payload."
            ),
        ),
    ] = None,
    clear_verifiers: bool = typer.Option(
        False,
        "--clear-verifiers",
        help="Send an explicit empty verifier binding list, removing existing bindings.",
    ),
) -> None:
    """Upload a workflow from a markdown file, or from stdin when FILE is `-`.

    Metadata can be passed as flags, read from YAML front-matter, or both.
    When both are present, command-line flags win:

    \b
    goodeye workflows publish - \\
      --name incident-postmortem \\
      --description "Draft a postmortem from an incident transcript." \\
      --outcome "Reduce mean-time-to-postmortem from days to hours." \\
      --tag sre --tag postmortem

    Front-matter follows the Goodeye workflow body convention:

    \b
    ---
    name: incident-postmortem
    description: Draft a postmortem from an incident transcript. Use when ...
    outcome: Reduce mean-time-to-postmortem from days to hours.
    # Optional discovery facet:
    # tags: [sre, postmortem]
    ---

    ``name``, ``description``, and ``outcome`` are required, either as
    flags or front-matter. Tags are optional.

    Workflows are always private to the caller. To share a workflow as a
    public template, run ``goodeye templates publish <workflow-uuid-or-name>`` as a
    separate, explicit step.

    Inline deterministic checks may live in the body as fenced code blocks.
    Deploy LLM-judge checks separately as verifiers and reference them by
    verifier_id or verifier_id@version.
    """
    console = Console()
    markdown = _read_markdown_input(file)
    front_matter, _stripped_body = _parse_front_matter(markdown)
    # Server stores the full markdown (including front-matter) so workflow
    # bodies round-trip through `goodeye workflows get`.
    body = markdown

    effective_name = _coerce_required_text(
        name_override
        if name_override is not None
        else front_matter.get("name") or front_matter.get("slug"),
        field_name="name",
        missing_message="Missing `name`. Add `name:` to the front-matter or pass --name.",
    )

    description = _coerce_required_text(
        description_override
        if description_override is not None
        else front_matter.get("description"),
        field_name="description",
        missing_message=(
            "Missing `description`. Add `description:` to the front-matter or pass --description."
        ),
    )

    outcome, tags = _extract_discovery_facets(front_matter)
    if outcome_override is not None:
        outcome = _coerce_outcome(outcome_override)
    if outcome is None:
        raise ValidationFailed(
            slug="validation_error",
            message="Missing `outcome`. Add `outcome:` to the front-matter or pass --outcome.",
        )
    if tag:
        tags = list(tag)
    if clear_verifiers and verifier:
        raise ValidationFailed(
            slug="validation_error",
            message="Use either --clear-verifiers or --verifier, not both.",
        )
    verifiers = _parse_workflow_verifier_flags(list(verifier or []))
    verifier_payload: list[dict[str, str]] | None = [] if clear_verifiers else verifiers or None

    with _client(require_auth=True) as client:
        result = client.save_workflow(
            name=effective_name,
            description=description,
            body=body,
            outcome=outcome,
            tags=tags,
            expected_version_token=expected_version_token,
            source=source,
            verifiers=verifier_payload,
        )

    console.print(
        f"[green]Saved[/green] {result.name} v{result.version} "
        f"(workflow_id={result.workflow_id}, version_token={result.version_token})"
    )


@app.command("lineage")
def lineage(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
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
    if result.parent_template_deleted_at is not None:
        reason = result.parent_template_delete_reason or "no reason given"
        console.print(
            f"[yellow]Parent template deleted[/yellow] "
            f"at {result.parent_template_deleted_at} ({reason})."
        )
    if result.parent_version_deprecated_at is not None:
        message = result.parent_version_deprecation_message or "no message"
        console.print(
            f"[yellow]Pinned version deprecated[/yellow] "
            f"at {result.parent_version_deprecated_at}: {message}."
        )


@app.command("teach")
def teach(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name to teach"),
) -> None:
    """Fetch the teach SKILL pack for an existing workflow.

    The command returns the pack content; the agent (or you, working from
    a script) follows the pack to run the teach session and persist the
    result via `goodeye workflows publish - --name <name>
    --description <description> --outcome <outcome> --source teach
    --expected-version-token <captured at stage 2>`.
    """
    stderr = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.teach_workflow(workflow_id)
    stderr.print(f"[bold]workflow_id:[/bold] {result.workflow_id}")
    # Write skill_md raw to stdout so markdown link syntax (`[text](url)`) is not
    # parsed as Rich markup, line wrapping is left to the caller, and piping into
    # an agent or file produces a clean SKILL.md.
    sys.stdout.write(result.skill_md)
    if not result.skill_md.endswith("\n"):
        sys.stdout.write("\n")


@app.command("optimize")
def optimize(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name to optimize"),
    max_iterations: int | None = typer.Option(
        None,
        "--max-iterations",
        help=(
            "Optimization loop budget. Defaults to 20 when omitted. "
            "Accepted range 1 to 1000; the upper bound is a safety cap, "
            "not a recommended setting (most runs converge well before that)."
        ),
        min=1,
        max=1000,
    ),
) -> None:
    """Fetch the optimize SKILL pack for an existing workflow.

    The command returns the pack content; the agent (or you, working from
    a script) follows the pack to run the optimization loop, then persists
    the final version via:

    \b
        goodeye workflows publish - --name <name> --description <description>
            --outcome <outcome> --source optimization
            --expected-version-token <captured at stage 1>

    only after explicit user approval. The loop never auto-saves.
    """
    stderr = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.optimize_workflow(workflow_id, max_iterations=max_iterations)
    stderr.print(f"[bold]workflow_id:[/bold] {result.workflow_id}")
    stderr.print(f"[bold]max_iterations:[/bold] {result.max_iterations}")
    # Stream skill_md raw to stdout so markdown link syntax (`[text](url)`) is
    # not parsed as Rich markup. Reference files are appended as labeled
    # sub-sections so a piped agent sees the full pack in order, matching the
    # rendering used by `goodeye design`.
    sys.stdout.write(result.skill_md.rstrip() + "\n")
    if result.references:
        sys.stdout.write("\n---\n\n# Reference files\n")
        for path in sorted(result.references):
            body = result.references[path]
            sys.stdout.write(f"\n## {path}\n\n{body.rstrip()}\n")


@app.command("delete")
def delete(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a workflow you own."""
    console = Console()
    if not confirm_destructive(f"Delete workflow {workflow_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.delete_workflow(workflow_id)
    if result.deleted:
        console.print(f"[green]Deleted[/green] {result.name}")
    else:
        console.print(f"[yellow]Not deleted[/yellow] {result.name}")


@app.command("grant")
def grant(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
    grantee: str = typer.Argument(..., help="User or team UUID, email, or handle."),
    role: str = typer.Argument(..., help="Role to grant: view, edit, or admin."),
) -> None:
    """Grant workflow access to a named user or team."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.grant_workflow(workflow_id, grantee, role.lower())
    console.print(f"[green]Granted[/green] {result.role} on {result.workflow_id} to {grantee}")


@app.command("revoke-grant")
def revoke_grant(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
    grantee: str = typer.Argument(..., help="User or team UUID, email, or handle."),
) -> None:
    """Revoke a direct workflow grant."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.revoke_workflow_grant(workflow_id, grantee)
    if result.revoked:
        console.print(f"[green]Revoked[/green] grant for {grantee}")
    else:
        console.print(f"[yellow]No direct grant found[/yellow] for {grantee}")


@app.command("grants")
def grants(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
    json_output: bool = typer.Option(False, "--json", help="Print grants as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print grants as a table."),
) -> None:
    """List workflow grants."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        result = client.list_workflow_grants(workflow_id)
    if mode == "json":
        echo_json(result)
        return
    table = Table(title=f"Workflow grants ({workflow_id})")
    table.add_column("Grantee")
    table.add_column("Type")
    table.add_column("Role")
    table.add_column("Via team")
    for grant_row in result.items:
        table.add_row(
            grant_row.grantee_identifier,
            grant_row.grantee_type,
            grant_row.role,
            "yes" if grant_row.is_via_team else "no",
        )
    if not result.items:
        console.print("[dim]No grants.[/dim]")
    else:
        console.print(table)


@app.command("leave")
def leave(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove your direct grant on a shared workflow."""
    console = Console()
    if not confirm_destructive(f"Leave shared workflow {workflow_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.leave_shared_workflow(workflow_id)
    console.print(
        f"[green]Left[/green] {result.workflow_id}; "
        f"removed {result.removed_direct_grants} direct grant(s)."
    )


def _split_version_suffix(value: str) -> tuple[str, int | None]:
    """Strip an optional trailing ``@N`` or ``@vN`` version suffix.

    Returns ``(identifier_without_suffix, version)``. ``@`` characters
    inside the identifier (e.g. ``@handle/slug``) are preserved because
    only the final segment after the last ``@`` is examined. A trailing
    ``@<garbage>`` (non-numeric) is treated as user error: the caller
    clearly meant to pin a version, so ``ValidationFailed`` is raised
    instead of silently passing the unparsed string through to the
    server (which would 404 with no useful signal).
    """
    if "@" not in value:
        return value, None
    head, _, tail = value.rpartition("@")
    if not head or not tail:
        return value, None
    candidate = tail[1:] if tail.startswith("v") else tail
    if not candidate.isdigit():
        raise ValidationFailed(
            slug="validation_error",
            message=(
                f"Version suffix '{tail}' is not a valid version number. "
                "Use @N or @vN (e.g. my-workflow@3 or my-workflow@v3)."
            ),
        )
    parsed = int(candidate)
    if parsed < 1:
        raise ValidationFailed(
            slug="validation_error",
            message=(
                f"Version suffix '{tail}' is not a valid version number. "
                "Use @N or @vN (e.g. my-workflow@3 or my-workflow@v3)."
            ),
        )
    return head, parsed


def _render_safety_check(result: SafetyCheckResult, console: Console) -> None:
    """Pretty-print a safety-check result, color-coded by status."""
    color = {
        "clean": "green",
        "flagged": "yellow",
        "blocked": "red",
        "error": "red",
    }.get(result.status, "white")
    console.print(
        f"[bold {color}]{result.status.upper()}[/bold {color}] "
        f"{result.resource_type} {result.resource_id} v{result.resource_version}"
    )
    for label, run in (("block", result.block), ("advisory", result.advisory)):
        verdict_color = {
            "pass": "green",
            "fail": "red",
            "error": "red",
        }.get(run.verdict, "white")
        verifier_id_text = run.verifier_id or "unknown"
        version_text = f" v{run.verifier_version}" if run.verifier_version is not None else ""
        run_id_text = f", verifier_run_id={run.verifier_run_id}" if run.verifier_run_id else ""
        console.print(
            f"  [{verdict_color}]{run.verdict}[/{verdict_color}] "
            f"{label} (verifier_id={verifier_id_text}{version_text}{run_id_text})"
        )
        if run.reasoning:
            # `reasoning` is server-controlled LLM-generated text. Escape so
            # tags like `[bold]` or `[/red]` are not interpreted as Rich markup.
            console.print(f"    {rich_escape(run.reasoning)}")


@app.command("check-safety")
def check_safety(
    workflow_id: str = typer.Argument(
        ...,
        help=(
            "Workflow UUID or slug, optionally pinned with ``@N`` or ``@vN`` "
            "(e.g. my-workflow@3). The ``--version`` flag overrides any suffix."
        ),
    ),
    version: int | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Pin to a specific workflow version; overrides any ``@N`` suffix.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Run the safety verifiers on a workflow you can see.

    Auth required. Each call costs two metered verifier runs (one block,
    one advisory). The verdicts are not persisted onto the workflow row;
    re-run to re-check. Returns ``status=clean`` when both verifiers pass,
    ``flagged`` when only the advisory fails, ``blocked`` when the block
    verifier fails, and ``error`` when the block verifier errors.
    """
    console = Console()
    parsed_id, parsed_version = _split_version_suffix(workflow_id)
    effective_version = version if version is not None else parsed_version
    with _client(require_auth=True) as client:
        result = client.check_workflow_safety(parsed_id, version=effective_version)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    _render_safety_check(result, console)


@app.command("transfer-ownership")
def transfer_ownership(
    workflow_id: str = typer.Argument(..., help="Workflow UUID or name."),
    new_owner: str = typer.Argument(..., help="New owner UUID, email, or handle."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Transfer a workflow to another user. Returns an invitation the recipient must accept."""
    console = Console()
    with _client(require_auth=True) as client:
        body = client.transfer_workflow_ownership(workflow_id, new_owner)
    if json_output:
        echo_json(body)
        return
    if "invitation_id" in body:
        console.print("Ownership transfer invited.")
        console.print(f"  id:         {body['invitation_id']}")
        console.print(f"  expires_at: {body.get('expires_at', '')}")
        console.print("")
        console.print(f"Recipient must run: goodeye invitations accept {body['invitation_id']}")
    else:
        console.print("No-op: target is already the owner.")


__all__ = [
    "_parse_front_matter",
    "app",
    "check_safety",
    "delete",
    "get_cmd",
    "grant",
    "grants",
    "leave",
    "lineage",
    "list_cmd",
    "optimize",
    "publish",
    "revoke_grant",
    "teach",
    "transfer_ownership",
]
