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
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.commands.workflows import (
    _print_authoring_notes,
    _render_safety_check,
    _split_version_suffix,
)
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired, SafetyVerificationFailed, SafetyVerificationUnavailable
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)
from goodeye_cli.wire import DesignChecks, TemplateDetail

app = typer.Typer(
    help="Browse the public template catalog; publish or fork templates.",
    no_args_is_help=True,
)

# The three authoring checks, in display order, mapped to their field name.
_AUTHORING_CHECKS: tuple[tuple[str, str], ...] = (
    ("outcome", "Outcome"),
    ("runnable", "Runnable"),
    ("well_formed", "Well-formed"),
)


def _check_state_markup(state: str) -> str:
    """Rich markup for one authoring check state, distinct per state."""
    if state == "met":
        return "[green]met[/green]"
    if state == "not_met":
        return "[yellow]not met[/yellow]"
    return "[dim]not checked[/dim]"


def _render_design_checks(console: Console, checks: DesignChecks) -> None:
    """Print the three authoring check states on one line."""
    parts = [
        f"{label} {_check_state_markup(getattr(checks, key))}" for key, label in _AUTHORING_CHECKS
    ]
    console.print("[dim]Checks:[/dim] " + "  ".join(parts))


def _design_checks_cell(checks: DesignChecks) -> str:
    """Compact triplet for the list table: a colored O/R/W per check state."""
    color = {"met": "green", "not_met": "yellow", "not_checked": "dim"}
    cells = [
        f"[{color.get(getattr(checks, key), 'dim')}]{label[0]}[/]"
        for key, label in _AUTHORING_CHECKS
    ]
    return " ".join(cells)


def _client(*, require_auth: bool) -> GoodeyeClient:
    api_key = get_api_key()
    if require_auth and not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


def _client_for_safety_check(*, anonymous: bool) -> GoodeyeClient:
    """Build a client for `templates check-safety`.

    Anonymous calls never attach stored credentials, even if configured.
    Authenticated calls attach an API key when one is on file but do not
    require one (the server treats the template route as optional-auth).
    """
    if anonymous:
        return GoodeyeClient(get_server(), api_key=None)
    return GoodeyeClient(get_server(), api_key=get_api_key())


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
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help=(
            "Also list your own archived templates (restore with `templates unarchive`). "
            "A template still appears only while at least one of its versions is published."
        ),
    ),
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
                include_archived=include_archived,
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
    table.add_column("Checks (ORW)", no_wrap=True)
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
            _design_checks_cell(item.design_checks),
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
    """Find a public template by describing what you need, not just its name."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    # Anonymous reads, like `templates list` and `get`: the server route is
    # public and bills any LLM ranking against the per-IP anonymous grant.
    # `--filter mine` for an anonymous caller returns no candidates server-side
    # (no spend), matching `templates list --filter mine`.
    with _client(require_auth=False) as client:
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


@app.command("get-file")
def get_file_cmd(
    identifier: str = typer.Argument(..., help="Template UUID, @handle/slug, or @handle/slug@vN."),
    remote_path: str = typer.Argument(
        ..., help="Path of the file within the template (e.g. demo/preview.png)."
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Local path to write the file's raw bytes to.",
    ),
    sha256: str | None = typer.Option(
        None,
        "--sha256",
        help=(
            "Expected content address of the file. When set, the file is "
            "resolved by path and confirmed to match; a stale or mismatched "
            "address returns not found."
        ),
    ),
) -> None:
    """Save one file from a template to your local disk.

    Use this to pull a template's demo asset (or any attached file) verbatim,
    for example a demo preview image. The bytes are written to the path given
    by --output; nothing is printed to stdout. Pass --sha256 to content-address
    the fetch so a republished or removed file no longer resolves at a stale
    address.
    """
    console = Console(stderr=True)
    with _client(require_auth=False) as client:
        data = client.get_template_file_raw(identifier, remote_path, sha256=sha256)
    if output.parent != Path():
        output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    console.print(f"[green]Wrote[/green] {output} ({len(data)} bytes)")


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
    _render_design_checks(console, result.design_checks)
    failed = [c for c in result.design_checks.criteria if not c.passed]
    if failed:
        guidance = result.design_checks.guidance or "Run `goodeye workflows audit` to improve."
        console.print(f"[dim]{guidance}[/dim]")
    if result.verifier_exposure_notice:
        console.print(f"[yellow]Note:[/yellow] {result.verifier_exposure_notice}")
    _print_authoring_notes(result.authoring_notes)


@app.command("unpublish")
def unpublish(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID, @handle/slug, or @handle/slug@vN (version arg overrides @v).",
    ),
    version: int = typer.Argument(..., help="Version to unpublish."),
) -> None:
    """Hide one published version from the public catalog (distinct from deprecate and delete).

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
    """Copy a public template into your own private workflow to customize it.

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
            role = ref.role or "-"
            src = ref.source_workflow_id or "-"
            console.print(
                f"  * [cyan]{ref.name}[/cyan] -> {ref.verifier_id}  "
                f"[dim](role={role}, source_workflow_id={src})[/dim]"
            )


@app.command("archive")
def archive_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID or @handle/slug.",
    ),
    reason: str | None = typer.Option(
        None, "--reason", help="Optional reason recorded with the archive action."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Archive a template you own.

    Archiving hides the template from public listing but keeps all versions
    and fork lineage intact. Existing forks continue to work. Use
    `templates unarchive` to restore. Use `templates delete` only when you
    want permanent, irreversible removal.
    """
    console = Console()
    if not confirm_destructive(f"Archive template {template_ref}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.archive_template(template_ref, archive_reason=reason)
    console.print(f"[green]Archived[/green] template {result.template_id}")


@app.command("unarchive")
def unarchive_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID or @handle/slug.",
    ),
) -> None:
    """Restore an archived template you own.

    The inverse of `templates archive`. The template becomes visible again
    in the public catalog.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.unarchive_template(template_ref)
    console.print(f"[green]Unarchived[/green] template {result.template_id}")


@app.command("delete")
def delete_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID or @handle/slug.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Permanently and immediately delete a template you own.

    WARNING: This is permanent. The template, all its versions, all attached
    files, and all version verification records are removed from the live
    system at once. There is NO recovery path.

    Use `templates archive` if you want a reversible alternative. Archived
    templates can be restored at any time with `templates unarchive`.

    Serving gate: if the template is not archived AND has at least one
    published version, deletion is refused (409). Unpublish the relevant
    version(s) or archive the template first.

    Encrypted backups age out within the platform's standard retention window
    (up to three months), so the content is not instantly erased from all
    systems everywhere, but it is no longer accessible through any product
    surface after this call.

    Fork lineage: forks keep their own content; their parent pointer is
    severed. `workflows lineage` will report the source as permanently deleted.
    """
    console = Console()
    if not confirm_destructive(
        f"Permanently delete template {template_ref}? This cannot be undone.",
        yes=yes,
    ):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.delete_template(template_ref)
    if result.deleted:
        console.print(f"[green]Permanently deleted[/green] template {result.template_id}")
    else:
        console.print(f"[yellow]Not deleted[/yellow] template {result.template_id}")


@app.command("delete-version")
def delete_version_cmd(
    template_ref: str = typer.Argument(
        ...,
        help="Template UUID, @handle/slug, or @handle/slug@vN (version arg overrides @v).",
    ),
    version: int = typer.Argument(..., help="Version to permanently delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Permanently and immediately delete a single template version.

    WARNING: This is permanent. The version row, all its attached files, and
    all version verification records are removed from the live system at once.
    There is NO recovery path.

    Use `templates archive` if you want a reversible alternative for the
    whole template.

    Serving gate: the version must be unpublished first (use
    `templates unpublish`). A still-published version cannot be permanently
    deleted because it is currently served to readers.

    Encrypted backups age out within the platform's standard retention window
    (up to three months), so the content is not instantly erased from all
    systems everywhere, but it is no longer accessible through any product
    surface after this call.

    Version numbers remain monotonic with a gap where the deleted version was.
    Surviving versions are not renumbered.
    """
    console = Console()
    if not confirm_destructive(
        f"Permanently delete template {template_ref} version {version}? This cannot be undone.",
        yes=yes,
    ):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.delete_template_version(template_ref, version)
    if result.deleted:
        console.print(
            f"[green]Permanently deleted[/green] template {result.template_id} v{result.version}"
        )
    else:
        console.print(
            f"[yellow]Not deleted[/yellow] template {result.template_id} v{result.version}"
        )


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
    """Warn users a version is outdated while keeping it published.

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


@app.command("lineage")
def lineage_cmd(
    workflow_ref: str = typer.Argument(
        ...,
        help="Forked workflow's UUID or name. This is a workflow identifier, not a template one.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print lineage as JSON."),
) -> None:
    """Show a forked workflow's lineage relative to the template it was forked from.

    Pass the forked workflow's id or name (not a template id or @handle/slug). The
    output reports the parent template, the pinned version, and whether that parent
    was later archived, permanently deleted, or had the pinned version deprecated.
    This is the same view as `goodeye workflows lineage <workflow_ref>`.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.lookup_workflow_lineage(workflow_ref)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    # A permanently-deleted parent severs the FK, so the response carries
    # parent_template_id=None while still pinning the version the fork was
    # taken from. A workflow that was never a fork has neither. Only the
    # latter is "not a fork"; gating on parent_template_id alone would
    # mislabel a deleted-parent fork and try to print "template None".
    if result.parent_template_id is None and result.parent_template_version is None:
        console.print("[dim]Not a fork (no parent template).[/dim]")
        return
    if result.parent_template_id is None:
        console.print(
            f"Forked from a template (now permanently deleted) "
            f"pinned to v{result.parent_template_version}."
        )
        console.print(
            "[red]Parent template permanently deleted[/red] "
            "(content no longer accessible through any product surface)."
        )
        return
    console.print(
        f"Forked from template {result.parent_template_id} "
        f"pinned to v{result.parent_template_version}; "
        f"upstream latest: v{result.upstream_latest_version}."
    )
    source_status = result.parent_source_status or "unknown"
    if result.parent_permanently_deleted:
        console.print(
            "[red]Parent template permanently deleted[/red] "
            "(content no longer accessible through any product surface)."
        )
    elif source_status == "archived":
        archived_at = result.parent_template_archived_at or "unknown time"
        console.print(f"[yellow]Parent template archived[/yellow] at {archived_at}.")
    if result.parent_version_deprecated_at is not None:
        message = result.parent_version_deprecation_message or "no message"
        console.print(
            f"[yellow]Pinned version deprecated[/yellow] "
            f"at {result.parent_version_deprecated_at}: {message}."
        )


@app.command("check-safety")
def check_safety(
    template_id: str = typer.Argument(
        ...,
        help=(
            "Template UUID, @handle/slug, or any of those pinned with "
            "``@N`` or ``@vN`` (e.g. @alice/my-tpl@3). The ``--version`` "
            "flag overrides any suffix."
        ),
    ),
    version: int | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Pin to a specific template version; overrides any ``@N`` suffix.",
    ),
    anonymous: bool = typer.Option(
        False,
        "--anonymous",
        help=(
            "Do not send credentials (anonymous public preview; spend is "
            "billed against a small per-IP credit budget)."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Run the safety verifiers on a public template version.

    Each call costs two metered verifier runs (one block, one advisory).
    Auth is optional: pass ``--anonymous`` to run without sending an API
    key. The verdicts are not persisted onto the template row; re-run to
    re-check. Returns ``status=clean`` when both verifiers pass,
    ``flagged`` when only the advisory fails, ``blocked`` when the block
    verifier fails, and ``error`` when the block verifier errors.

    A field longer than the safety check's input limit is truncated for this
    scan and reported via ``truncated`` / ``truncated_fields``; the
    authoritative verdict remains the status recorded at publish time.
    """
    console = Console()
    parsed_id, parsed_version = _split_version_suffix(template_id)
    effective_version = version if version is not None else parsed_version
    with _client_for_safety_check(anonymous=anonymous) as client:
        result = client.check_template_safety(
            parsed_id, version=effective_version, anonymous=anonymous
        )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    _render_safety_check(result, console)


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
    "archive_cmd",
    "check_safety",
    "delete_cmd",
    "delete_version_cmd",
    "deprecate_version_cmd",
    "fork",
    "get_cmd",
    "get_file_cmd",
    "lineage_cmd",
    "list_cmd",
    "publish",
    "transfer_ownership_cmd",
    "unarchive_cmd",
    "unpublish",
]
