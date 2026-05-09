"""`goodeye templates ...` subcommand group.

Templates are the public-sharing surface. A template is a snapshot of a
private workflow, addressable as ``@<handle>/<slug>`` or
``@<handle>/<slug>@v<N>``. Forks copy a template into the caller's
private workflow namespace.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired, SafetyVerificationFailed, SafetyVerificationUnavailable
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)
from goodeye_cli.wire import TemplateDetail

app = typer.Typer(
    help="Browse the public template catalog; publish or fork templates.",
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
        "all",
        "--filter",
        "-f",
        help="Scope filter: all or mine.",
        case_sensitive=False,
    ),
    search: str | None = typer.Option(None, "--search", "-s", help="Search query."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
    limit: int = typer.Option(25, "--limit", "-l", min=1, help="Max results per page."),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow cursors and combine all pages."),
) -> None:
    """List public templates."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=False) as client:
        items, final_cursor = fetch_pages(
            lambda page_cursor: client.list_templates(
                filter_=filter_.lower() if filter_ else None,
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

    table = Table(title=f"Templates ({filter_})")
    table.add_column("Handle/Slug", no_wrap=True)
    table.add_column("Latest", justify="right")
    table.add_column("Outcome")
    table.add_column("Safety")
    table.add_column("Published by")
    for item in items:
        status = item.safety_verification_status
        safety_cell = (
            "[green]verified[/green]"
            if status == "clean"
            else "[yellow]advisory[/yellow]"
            if status == "flagged"
            else "[dim]unverified[/dim]"
        )
        table.add_row(
            f"@{item.handle}/{item.slug}",
            f"v{item.latest_version}",
            item.outcome,
            safety_cell,
            item.publishing_handle,
        )
    if not items:
        console.print("[dim]No templates matched.[/dim]")
    else:
        console.print(table)
    if final_cursor:
        hint = next_page_hint(
            ("goodeye", "templates", "list"),
            next_cursor=final_cursor,
            limit=limit,
            options=(
                ("--filter", filter_.lower() if filter_ else None),
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
        help="all | mine",
        case_sensitive=False,
    ),
    limit: int = typer.Option(5, "--limit", "-l", min=1, max=10, help="Max results (1-10)."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """LLM-ranked search over templates (not lexical list filtering)."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        result = client.search_templates(
            query=query,
            filter_=filter_.lower(),
            limit=limit,
        )
    if mode == "json":
        echo_json(result)
        return

    table = Table(title="Template search")
    table.add_column("Rank", justify="right")
    table.add_column("Template")
    table.add_column("Match reason")
    for item in result.items:
        ident = (
            f"@{item.handle}/{item.slug}" if item.handle and item.slug else (item.name or item.id)
        )
        table.add_row(str(item.rank), ident, item.match_reason)
    if not result.items:
        console.print("[dim]No matches.[/dim]")
    else:
        console.print(table)


@app.command("get")
def get_cmd(
    identifier: str = typer.Argument(..., help="Template UUID, @handle/slug, or @handle/slug@vN."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned version."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write body to this path instead of stdout."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full template record as JSON instead of markdown."
    ),
) -> None:
    """Fetch a public template for the calling AI agent to execute.

    The body is a workflow: a markdown runbook the agent should follow on
    the user's behalf, not just display. Non-owner reads include an
    unverified-template safety banner. Prints the markdown to stdout
    (wrapped with agent-facing markers) by default.
    """
    console = Console(stderr=True)
    with _client(require_auth=False) as client:
        result, final_identifier = client.get_template_with_redirect(
            identifier, version=version, accept_markdown=not json_output
        )

    if final_identifier is not None and final_identifier != identifier:
        # Server told us this handle URL has been moved; surface it on stderr
        # so a user piping stdout into a file or another process still sees it.
        console.print(f"note: {identifier} redirected to {final_identifier}")

    if json_output:
        assert isinstance(result, TemplateDetail)
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


@app.command("publish")
def publish(
    workflow_ref: str = typer.Argument(
        ...,
        help="Workflow UUID or slug (same as the workflow ``name`` / front-matter ``name``).",
    ),
    release_notes: str | None = typer.Option(
        None, "--release-notes", "-n", help="Release notes for this version."
    ),
) -> None:
    """Publish a workflow as a new public template version.

    First publish creates the template (slug reused from the workflow);
    subsequent publishes append a monotonic version. Requires a claimed
    handle (run ``goodeye me claim-handle`` first).
    """
    console = Console()
    stderr = Console(stderr=True)
    try:
        with _client(require_auth=True) as client:
            result = client.publish_template_version(workflow_ref, release_notes=release_notes)
    except SafetyVerificationFailed as exc:
        stderr.print(f"[bold red]safety_verification_failed[/bold red]: {exc.message}")
        raise typer.Exit(code=2) from exc
    except SafetyVerificationUnavailable as exc:
        stderr.print(f"[bold red]safety_verification_unavailable[/bold red]: {exc.message}")
        raise typer.Exit(code=3) from exc
    console.print(
        f"[green]Published[/green] template {result.template_id} v{result.version} "
        f"as @{result.publishing_handle}"
    )
    sv = result.safety_verification
    if sv is not None:
        if sv.status == "clean":
            console.print("[dim]Safety:[/dim] [green]verified[/green]")
        elif sv.status == "flagged":
            console.print("[dim]Safety:[/dim] [yellow]advisory concerns flagged[/yellow]")
            if sv.advisory_reasoning:
                console.print(f"[dim]{sv.advisory_reasoning}[/dim]")


@app.command("unpublish")
def unpublish(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID, @handle/slug, or @handle/slug@vN (version arg overrides @v).",
    ),
    version: int = typer.Argument(..., help="Version to unpublish."),
) -> None:
    """Soft-unpublish a single template version.

    Existing forks pinned to this version continue to work. The catalog
    hides the template if no live version remains.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.unpublish_template_version(template_ref, version)
    console.print(f"[green]Unpublished[/green] template {result.template_id} v{result.version}")


@app.command("fork")
def fork(
    identifier: str = typer.Argument(..., help="Template UUID, @handle/slug, or @handle/slug@vN."),
    version: int | None = typer.Option(
        None, "--version", "-v", help="Pin to a specific template version."
    ),
    name: str | None = typer.Option(
        None, "--name", help="Override the fork's slug (default is the template slug)."
    ),
) -> None:
    """Fork a public template into a private workflow owned by the caller.

    Authentication is required. Returns the new workflow's id and lineage
    metadata; fetching the body and acting on it (if at all) is a
    separate ``workflows get`` call.
    """
    console = Console()
    stderr_console = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.fork_template(identifier, version=version, name=name)
    if result.redirected:
        requested = result.redirected_from_handle or identifier
        resolved = result.redirected_to_handle or "(see workflow_id)"
        stderr_console.print(f"note: {requested} redirected to {resolved}")
    if result.deprecation_warning:
        stderr_console.print(f"warning: {result.deprecation_warning}")
    console.print(
        f"[green]Forked[/green] workflow {result.workflow_id} "
        f"slug={result.slug} from {identifier} "
        f"at v{result.parent_template_version}"
    )
    if result.verifiers:
        console.print()
        console.print("[bold]Semantic verifiers pinned on this fork[/bold]")
        for ref in result.verifiers:
            role = ref.role or "—"
            src = ref.source_workflow_id or "—"
            console.print(
                f"  • [cyan]{ref.name}[/cyan] → {ref.verifier_id}  "
                f"[dim](role={role}, source_workflow_id={src})[/dim]"
            )


@app.command("delete")
def delete_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID or @handle/slug.",
    ),
    reason: str | None = typer.Option(
        None, "--reason", help="Optional reason recorded in the audit log."
    ),
) -> None:
    """Soft-delete a template you own.

    Existing forks pinned to any version keep working. The catalog hides
    deleted templates. Pair with ``goodeye templates undelete`` to
    restore.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.delete_template(template_ref, reason=reason)
    suffix = " (idempotent)" if result.idempotent else ""
    console.print(f"[green]Deleted[/green] template {result.template_id}.{suffix}")


@app.command("undelete")
def undelete_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID or @handle/slug.",
    ),
) -> None:
    """Restore a previously deleted template you own."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.undelete_template(template_ref)
    suffix = " (idempotent)" if result.idempotent else ""
    console.print(f"[green]Undeleted[/green] template {result.template_id}.{suffix}")


@app.command("deprecate-version")
def deprecate_version_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID, @handle/slug, or @handle/slug@vN (version arg overrides @v).",
    ),
    version: int = typer.Argument(..., help="Version to deprecate."),
    message: str = typer.Option(
        ...,
        "--message",
        "-m",
        help="Required deprecation message shown to users who fork this version.",
    ),
) -> None:
    """Flag a single template version as deprecated.

    The message is shown to anyone who forks this version. The version
    stays reachable so existing pins continue to work.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.deprecate_template_version(template_ref, version, message=message)
    console.print(
        f"[green]Deprecated[/green] template {result.template_id} v{result.version}: "
        f"{result.deprecation_message}"
    )


@app.command("transfer-ownership")
def transfer_ownership_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID or @handle/slug.",
    ),
    new_owner: str = typer.Argument(..., help="New owner UUID, email, or handle."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Transfer a template to another Goodeye user.

    Returns an invitation the recipient must accept.
    """
    console = Console()
    with _client(require_auth=True) as client:
        body = client.transfer_template_ownership(template_ref, new_owner)
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
    "app",
    "delete_cmd",
    "deprecate_version_cmd",
    "fork",
    "get_cmd",
    "list_cmd",
    "publish",
    "transfer_ownership_cmd",
    "undelete_cmd",
    "unpublish",
]
