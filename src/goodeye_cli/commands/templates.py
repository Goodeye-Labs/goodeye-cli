"""`goodeye templates ...` subcommand group.

Templates are the public-sharing surface. A template is a snapshot of a
private workflow, addressable as ``@<handle>/<slug>`` or
``@<handle>/<slug>@v<N>``. Forks copy a template into the caller's
private workflow namespace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired
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
) -> None:
    """List public templates."""
    console = Console()
    items: list[Any] = []
    with _client(require_auth=False) as client:
        cursor: str | None = None
        while True:
            page = client.list_templates(
                filter_=filter_.lower() if filter_ else None,
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

    table = Table(title=f"Templates ({filter_})")
    table.add_column("Handle/Slug", no_wrap=True)
    table.add_column("Latest", justify="right")
    table.add_column("Outcome")
    table.add_column("Published by")
    for item in items:
        table.add_row(
            f"@{item.handle}/{item.slug}",
            f"v{item.latest_version}",
            item.outcome,
            item.publishing_handle,
        )
    if not items:
        console.print("[dim]No templates matched.[/dim]")
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
    workflow_id: str = typer.Argument(..., help="Workflow ID to publish."),
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
    with _client(require_auth=True) as client:
        result = client.publish_template_version(workflow_id, release_notes=release_notes)
    console.print(
        f"[green]Published[/green] template {result.template_id} v{result.version} "
        f"as @{result.publishing_handle}"
    )


@app.command("unpublish")
def unpublish(
    template_id: str = typer.Argument(..., help="Template ID."),
    version: int = typer.Argument(..., help="Version to unpublish."),
) -> None:
    """Soft-unpublish a single template version.

    Existing forks pinned to this version continue to work. The catalog
    hides the template if no live version remains.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.unpublish_template_version(template_id, version)
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


@app.command("delete")
def delete_cmd(
    template_id: str = typer.Argument(..., help="Template ID."),
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
        result = client.delete_template(template_id, reason=reason)
    suffix = " (idempotent)" if result.idempotent else ""
    console.print(f"[green]Deleted[/green] template {result.template_id}.{suffix}")


@app.command("undelete")
def undelete_cmd(
    template_id: str = typer.Argument(..., help="Template ID."),
) -> None:
    """Restore a previously deleted template you own."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.undelete_template(template_id)
    suffix = " (idempotent)" if result.idempotent else ""
    console.print(f"[green]Undeleted[/green] template {result.template_id}.{suffix}")


@app.command("deprecate-version")
def deprecate_version_cmd(
    template_id: str = typer.Argument(..., help="Template ID."),
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
        result = client.deprecate_template_version(template_id, version, message=message)
    console.print(
        f"[green]Deprecated[/green] template {result.template_id} v{result.version}: "
        f"{result.deprecation_message}"
    )


@app.command("transfer-ownership")
def transfer_ownership_cmd(
    template_id: str = typer.Argument(..., help="Template ID."),
    new_owner: str = typer.Argument(..., help="New owner UUID, email, or handle."),
) -> None:
    """Transfer a template to another Goodeye user. Owner only."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.transfer_template_ownership(template_id, new_owner)
    if not result.transferred:
        console.print(f"[dim]Ownership already belongs to[/dim] {result.owner_user_id}.")
        return
    console.print(
        f"[green]Transferred[/green] template {result.template_id} to {result.owner_user_id}."
    )


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
