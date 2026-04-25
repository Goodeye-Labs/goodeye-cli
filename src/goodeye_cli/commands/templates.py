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

app = typer.Typer(help="Browse, publish, and fork templates.", no_args_is_help=True)


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
    """Download a template. Prints the template's markdown to stdout."""
    console = Console(stderr=True)
    with _client(require_auth=False) as client:
        result = client.get_template(identifier, version=version, accept_markdown=not json_output)

    if json_output:
        assert isinstance(result, TemplateDetail)
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
    """Fork a template into your private workflow namespace.

    Authenticated callers get a real workflow. Anonymous callers (no auth)
    get a 24h ephemeral workflow that is promoted to a real one on signup.
    """
    console = Console()
    # Anonymous fork allowed: pass-through with an unauthenticated client.
    client_req_auth = get_api_key() is not None
    with _client(require_auth=False) as client:
        result = client.fork_template(identifier, version=version, name=name)
    tag = "(ephemeral)" if result.is_ephemeral else "(yours)"
    console.print(
        f"[green]Forked[/green] {tag} workflow {result.workflow_id} "
        f"slug={result.slug} from {identifier} "
        f"at v{result.parent_template_version}"
    )
    if result.is_ephemeral and not client_req_auth:
        console.print(
            "[yellow]Note:[/yellow] the fork is ephemeral (24h TTL). "
            "Run `goodeye signup` or `goodeye login` from the same client "
            "and it will be promoted to a real workflow you own."
        )


__all__ = [
    "app",
    "fork",
    "get_cmd",
    "list_cmd",
    "publish",
    "unpublish",
]
