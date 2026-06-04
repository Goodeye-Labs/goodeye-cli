"""`goodeye workflows sync ...` subcommand group.

This layer configures where the caller's registry workflows are mirrored
locally and pulls workflow bodies down to those directories. It exposes the
``target`` subcommands (add, list, remove) that read and write the local sync
config, plus ``pull`` which writes registry workflows to disk and records what
it wrote in the local index.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli import sync
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_config_paths, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.output import echo_json, items_envelope, resolve_output_mode

app = typer.Typer(
    help="Sync workflows between the registry and local skill directories.",
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


target_app = typer.Typer(
    help="Configure local sync target directories.",
    no_args_is_help=True,
)
app.add_typer(target_app, name="target")


def _coerce_scope(raw: str) -> sync.SyncScope:
    """Validate a user-supplied scope, case-insensitively."""
    candidate = raw.strip().lower()
    if candidate not in sync.SYNC_SCOPES:
        valid = ", ".join(sorted(sync.SYNC_SCOPES))
        raise ValidationFailed(
            slug="validation_error",
            message=f"Unknown scope {raw!r}.",
            hint=f"Valid scopes: {valid}.",
        )
    # candidate is one of the literal scope strings, validated above.
    return candidate  # type: ignore[return-value]


@target_app.command("add")
def target_add(
    path: str | None = typer.Argument(
        None,
        help="Directory to mirror workflows into. Omit when using --preset.",
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help="Named target directory (claude, agents, or cursor). Use instead of a path.",
    ),
    scope: str = typer.Option(
        "owned",
        "--scope",
        help="Which workflows to mirror here: owned, all, or selected.",
    ),
    only: list[str] = typer.Option(
        [],
        "--only",
        help=("Slug or glob to include (repeatable). Only valid with --scope selected."),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the added target as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print the added target as a table."),
) -> None:
    """Add a local sync target.

    Provide either a directory path or a --preset, not both. This is a local
    configuration step: it does not contact the registry.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    coerced_scope = _coerce_scope(scope)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    target = sync.add_target(
        config,
        path=path,
        preset=preset,
        scope=coerced_scope,
        only=list(only),
    )
    sync.save_sync_config(config, paths)

    if mode == "json":
        echo_json(target)
        return
    console = Console()
    console.print(f"[green]Added[/green] sync target {target.path} " f"(scope={target.scope})")


@target_app.command("list")
def target_list(
    json_output: bool = typer.Option(False, "--json", help="Print targets as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print targets as a table."),
) -> None:
    """List the configured local sync targets."""
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    targets = sync.list_targets(config)

    if mode == "json":
        echo_json(items_envelope(targets))
        return

    console = Console()
    if not targets:
        console.print("[dim]No sync targets configured.[/dim]")
        return
    table = Table(title="Sync targets")
    table.add_column("Path", no_wrap=True)
    table.add_column("Scope")
    table.add_column("Selected")
    table.add_column("Link")
    for target in targets:
        table.add_row(
            target.path,
            target.scope,
            ", ".join(target.selected) if target.selected else "-",
            "yes" if target.link else "no",
        )
    console.print(table)


@target_app.command("remove")
def target_remove(
    path: str = typer.Argument(..., help="Directory of the sync target to remove."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print a one-line confirmation."),
) -> None:
    """Remove a configured local sync target by its directory."""
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    removed = sync.remove_target(config, path)
    sync.save_sync_config(config, paths)

    stored_path = sync.normalize_target_path(path)
    if mode == "json":
        echo_json({"path": stored_path, "removed": removed})
        return

    console = Console()
    if removed:
        console.print(f"[green]Removed[/green] sync target {stored_path}")
    else:
        console.print(f"[yellow]No sync target[/yellow] found for {stored_path}")


_SKIPPED_ACTIONS = frozenset({"skipped-modified", "skipped-conflict"})


@app.command("pull")
def pull(
    slugs: list[str] = typer.Argument(
        None,
        help="Workflow slugs to pull. Omit to pull everything in scope.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Operate on a single configured target directory instead of all of them.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite local edits with the registry copy.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """Pull registry workflows down to the configured local directories.

    Each in-scope workflow is written to <target>/<slug>/SKILL.md. A local file
    that has been edited since the last pull is preserved unless --force is
    given. Requires authentication.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    state = sync.load_sync_state(paths)

    with _client(require_auth=True) as client:
        result = sync.pull(
            client,
            config,
            state,
            slugs=list(slugs or []),
            target_path=target,
            force=force,
            paths=paths,
        )

    if mode == "json":
        echo_json(items_envelope(result.items))
        return

    console = Console()
    if not result.items:
        console.print("[dim]No workflows in scope to pull.[/dim]")
        return
    table = Table(title="Pulled workflows")
    table.add_column("Slug", no_wrap=True)
    table.add_column("Target", no_wrap=True)
    table.add_column("Action")
    for item in result.items:
        table.add_row(item.slug, item.target_path, item.action)
    console.print(table)

    if any(item.action in _SKIPPED_ACTIONS for item in result.items):
        console.print(
            "[yellow]Next:[/yellow] some workflows kept their local edits; "
            "re-run with --force to overwrite them with the registry copy."
        )


def _status_hints(items: list[sync.StatusItem]) -> list[str]:
    """Build neutral next-step hints that name only commands that exist now.

    ``behind-server`` workflows can be reconciled with the existing ``pull``
    command, so that hint is concrete. Local edits, conflicts, and untracked
    directories are reported as counts without pointing at a command, since the
    command that would reconcile them is not part of this release.
    """
    hints: list[str] = []
    behind = sum(1 for i in items if i.state == "behind-server")
    edited = sum(1 for i in items if i.state in {"modified-local", "conflict"})
    untracked = sum(1 for i in items if i.state == "untracked")
    if behind:
        hints.append(
            f"run `goodeye workflows sync pull` to update {behind} behind-server workflow(s)"
        )
    if edited:
        hints.append(f"{edited} workflow(s) have local edits")
    if untracked:
        hints.append(f"{untracked} local workflow(s) are not yet in the registry")
    return hints


@app.command("status")
def status(
    target: str | None = typer.Option(
        None,
        "--target",
        help="Report on a single configured target directory instead of all of them.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """Report drift between the registry and the local skill directories.

    For each in-scope workflow this compares what the registry reports against
    what was last mirrored locally and what is on disk, classifying each one as
    clean, edited locally, behind the registry, conflicted, deleted upstream, or
    a local directory the registry does not track yet. It reads only: nothing is
    fetched, written, or changed. Requires authentication.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    state = sync.load_sync_state(paths)

    with _client(require_auth=True) as client:
        result = sync.status(client, config, state, target_path=target)

    if mode == "json":
        echo_json(items_envelope(result.items))
        return

    console = Console()
    if not result.items:
        console.print("[dim]No workflows in scope.[/dim]")
        return
    table = Table(title="Sync status")
    table.add_column("Slug", no_wrap=True)
    table.add_column("Target", no_wrap=True)
    table.add_column("State")
    table.add_column("Next action")
    for item in result.items:
        table.add_row(item.slug, item.target_path, item.state, item.next_action)
    console.print(table)

    hints = _status_hints(result.items)
    if hints:
        console.print(f"[yellow]Next:[/yellow] {'; '.join(hints)}.")


def _push_hints(items: list[sync.PushItem]) -> list[str]:
    """Build neutral next-step hints for a push pass.

    A conflict points the caller at the existing ``pull`` and ``status``
    commands. An untracked local directory points at ``workflows publish``,
    the only path that creates a new registry workflow. Every command named
    here exists today.
    """
    hints: list[str] = []
    conflicts = sum(1 for i in items if i.action == "conflict")
    untracked = sum(1 for i in items if i.action == "untracked")
    if conflicts:
        hints.append(
            f"{conflicts} workflow(s) conflict; run `goodeye workflows sync pull` "
            "to merge then push again (check `goodeye workflows sync status` first)"
        )
    if untracked:
        hints.append(
            f"{untracked} local workflow(s) are not in the registry; create them "
            "with `goodeye workflows publish`"
        )
    return hints


@app.command("push")
def push(
    slugs: list[str] = typer.Argument(
        None,
        help="Workflow slugs to push. Omit to push every locally edited workflow in scope.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Operate on a single configured target directory instead of all of them.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """Push locally edited workflows back to the registry.

    Only workflows whose on-disk SKILL.md differs from the last sync are sent.
    Each upload is optimistic-locked: if the registry moved since the last sync,
    the workflow is reported as a conflict and left untouched, and you reconcile
    with `goodeye workflows sync pull` before pushing again. Renaming through
    push is not supported (the directory name is the workflow identity), and a
    workflow you hold only a view grant on is never uploaded. Requires
    authentication.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    state = sync.load_sync_state(paths)

    with _client(require_auth=True) as client:
        result = sync.push(
            client,
            config,
            state,
            slugs=list(slugs or []),
            target_path=target,
            paths=paths,
        )

    if mode == "json":
        echo_json(items_envelope(result.items))
        return

    console = Console()
    if not result.items:
        console.print("[dim]No locally edited workflows to push.[/dim]")
        return
    table = Table(title="Pushed workflows")
    table.add_column("Slug", no_wrap=True)
    table.add_column("Target", no_wrap=True)
    table.add_column("Action")
    table.add_column("Detail")
    for item in result.items:
        table.add_row(item.slug, item.target_path, item.action, item.detail or "")
    console.print(table)

    hints = _push_hints(result.items)
    if hints:
        console.print(f"[yellow]Next:[/yellow] {'; '.join(hints)}.")


__all__ = [
    "app",
    "pull",
    "push",
    "status",
    "target_add",
    "target_app",
    "target_list",
    "target_remove",
]
