"""`goodeye workflows sync ...` subcommand group.

This layer configures where the caller's registry workflows are mirrored
locally. It exposes the ``target`` subcommands (add, list, remove) that
read and write the local sync config. Pulling and pushing workflow bodies
is handled by later layers and is not wired up here.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli import sync
from goodeye_cli.config import get_config_paths
from goodeye_cli.errors import ValidationFailed
from goodeye_cli.output import echo_json, items_envelope, resolve_output_mode

app = typer.Typer(
    help="Sync workflows between the registry and local skill directories.",
    no_args_is_help=True,
)

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


__all__ = ["app", "target_add", "target_app", "target_list", "target_remove"]
