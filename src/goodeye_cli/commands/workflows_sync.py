"""`goodeye skills sync ...` subcommand group.

This layer configures where the caller's hosted skills are mirrored
locally and pulls skill bodies down to those directories. It exposes the
``target`` subcommands (add, list, remove) that read and write the local sync
config, plus ``pull`` which writes hosted skills to disk and records what
it wrote in the local index.
"""

from __future__ import annotations

import contextlib

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli import sync
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_config_paths, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.output import echo_json, items_envelope, resolve_output_mode

app = typer.Typer(
    help=(
        "Mirror hosted skills into local skill directories, so every machine "
        "and agent reads the current version. Point a target at the directory "
        "your tool loads skills from (`--preset claude`, `agents`, `codex`, or "
        "`cursor`, or any path), and one edit to a hosted skill reaches all of "
        "them on the next pull."
        "\n\n"
        "Run with no subcommand, this brings the local mirror up to date (the "
        "same work as `goodeye skills sync pull`) and then prints where each "
        "skill stands afterward (the same view as `goodeye skills sync "
        "status`). Pass a subcommand (`target`, `pull`, `status`, `push`, "
        "`auto`) to run just that step. The `--force` and `--yes` options "
        "apply to the pull the bare command runs; they are ignored when a "
        "subcommand is invoked. Requires authentication."
    ),
    short_help=(
        "Mirror hosted skills into local skill directories, so every machine "
        "and agent reads the current version."
    ),
    invoke_without_command=True,
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


@app.callback(invoke_without_command=True)
def _sync_root(
    ctx: typer.Context,
    target: str | None = typer.Option(
        None,
        "--target",
        help="Operate on a single configured target directory instead of all of them.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Overwrite local edits with the registry copy during the pull. "
            "Applies to the bare command's pull; ignored when a subcommand is given."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help=(
            "Skip the confirmation prompt before removing a deleted skill's local copy. "
            "Applies to the bare command's pull; ignored when a subcommand is given."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the status summary as JSON."),
    table_output: bool = typer.Option(
        False, "--table", help="Print the status summary as a table."
    ),
) -> None:
    """Run the bare `sync` command: pull every target, then report status.

    Internal note, not user-facing help: `Typer(help=...)` on the group above
    takes precedence over this docstring, so the text users read lives there.
    Keep this short and keep the user-facing wording in one place.
    """
    if ctx.invoked_subcommand is not None:
        return

    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    state = sync.load_sync_state(paths)

    with _client(require_auth=True) as client:
        sync.pull(
            client,
            config,
            state,
            slugs=[],
            target_path=target,
            force=force,
            yes=yes,
            paths=paths,
        )
        # Reload the index the pull just persisted so the status pass sees the
        # post-pull state (entries it materialized, dropped, or converged).
        post_pull_state = sync.load_sync_state(paths)
        result = sync.status(client, config, post_pull_state, target_path=target)

    if mode == "json":
        echo_json(items_envelope(result.items))
        return

    _render_status(result)


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
        help="Directory to mirror skills into. Omit when using --preset.",
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help=(
            "Named target directory instead of a path: claude "
            "(~/.claude/skills), agents or codex (both ~/.agents/skills, the "
            "shared location Codex reads), or cursor (~/.cursor/skills)."
        ),
    ),
    scope: str | None = typer.Option(
        None,
        "--scope",
        help="Which skills to mirror here: owned, all, or selected.",
    ),
    only: list[str] = typer.Option(
        [],
        "--only",
        help=(
            "Skill slug or glob (repeatable). With an existing target it is appended to "
            "the selected allowlist (duplicates ignored). Only valid with --scope selected."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the added target as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print the added target as a table."),
) -> None:
    """Add a sync target, or add skills to an existing target's selected allowlist.

    Without --only, creates a new target directory. With --only (and --scope
    selected), manages individual skills: a missing target is created with
    them allowlisted; an existing target has them appended (duplicates ignored).
    This is local configuration: it does not contact the registry. Run
    `goodeye skills sync pull` afterward to materialize them.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    only_list = list(only)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)

    # Resolve the raw path or preset to a stored path for existence check.
    # An unknown preset raises ValidationFailed; suppress only that so an
    # unexpected error propagates rather than being silently swallowed into
    # the create path below.
    raw_path: str | None = None
    if path is not None or preset is not None:
        with contextlib.suppress(ValidationFailed):
            raw_path = sync.resolve_preset(preset) if preset is not None else path

    # Determine whether an existing target matches.
    existing_target: sync.SyncTarget | None = None
    if raw_path is not None:
        existing_target = sync.find_target_by_path(config, raw_path)

    if only_list and existing_target is not None:
        # Append path: existing target + --only supplied.
        explicit_scope: sync.SyncScope | None = None
        if scope is not None:
            explicit_scope = _coerce_scope(scope)
        added, already_present = sync.append_to_allowlist(
            config,
            path=raw_path,  # type: ignore[arg-type]
            entries=only_list,
            explicit_scope=explicit_scope,
        )
        sync.save_sync_config(config, paths)

        stored_path = sync.normalize_target_path(raw_path)  # type: ignore[arg-type]
        if mode == "json":
            # Carry automatic_sync_enabled here too, so `target add --json`
            # reports it whether it created a target or appended to one.
            echo_json(
                {
                    "path": existing_target.path,
                    "scope": existing_target.scope,
                    "added": added,
                    "already_present": already_present,
                    "automatic_sync_enabled": sync.automatic_sync_enabled(config),
                }
            )
            return
        console = Console()
        if added:
            console.print(
                f"[green]Added[/green] {len(added)} skill(s) to the allowlist of "
                f"{stored_path}: {', '.join(added)}"
            )
            pull_args = " ".join(added)
            console.print(
                f"[yellow]Next:[/yellow] run "
                f"`goodeye skills sync pull {pull_args}` to materialize them."
            )
        else:
            console.print(
                f"{', '.join(already_present)} already in the allowlist of "
                f"{stored_path}; nothing to add."
            )
        return

    # Create path: either no --only, or --only with no existing target. This
    # branch creates a new target (optionally a selected one seeded from --only
    # when --scope selected is given), or raises at the add_target layer when
    # --only is used without --scope selected (only valid with scope=selected).
    coerced_scope = _coerce_scope(scope) if scope is not None else "owned"
    target = sync.add_target(
        config,
        path=path,
        preset=preset,
        scope=coerced_scope,
        only=only_list,
    )
    sync.save_sync_config(config, paths)

    # Having a target is what turns automatic sync on, so there is nothing to
    # set here: the new target changes the answer on its own. This only reports
    # it, because a user configuring their first target has no other way to know
    # the mirror will now keep itself current.
    automatic = sync.automatic_sync_enabled(config)

    if mode == "json":
        # Additive: the target's own fields stay at the top level so existing
        # readers of this payload keep working.
        echo_json({**target.model_dump(), "automatic_sync_enabled": automatic})
        return
    console = Console()
    console.print(f"[green]Added[/green] sync target {target.path} (scope={target.scope})")
    if automatic:
        console.print(
            f"Automatic sync is on (every {config.auto.interval_seconds} seconds). "
            "Turn it off with `goodeye skills sync auto off`."
        )
    else:
        # Reached only when the user turned it off themselves. Say so, so a
        # target that never refreshes is not a mystery later.
        console.print(
            "[yellow]Automatic sync is off[/yellow], so this target updates only when "
            "you run `goodeye skills sync`. Turn it on with `goodeye skills sync auto on`."
        )


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
    only: list[str] = typer.Option(
        [],
        "--only",
        help=(
            "Skill slug or glob to drop from the target's selected allowlist (repeatable). "
            "Without --only the whole target is removed."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print a one-line confirmation."),
) -> None:
    """Remove a whole sync target, or remove individual skills from its allowlist.

    Without --only, the entire target is removed and its directory is no longer
    synced. With --only, only the named skills are dropped from the target's
    allowlist and the target itself is kept.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    only_list = list(only)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    stored_path = sync.normalize_target_path(path)

    if only_list:
        # Prune path: drop named entries from the allowlist, keep the target.
        removed_entries, absent_entries = sync.prune_from_allowlist(
            config, path=path, entries=only_list
        )
        sync.save_sync_config(config, paths)

        if mode == "json":
            echo_json({"path": stored_path, "removed": removed_entries, "absent": absent_entries})
            return

        console = Console()
        if removed_entries:
            console.print(
                f"[green]Removed[/green] {len(removed_entries)} skill(s) from the "
                f"allowlist of {stored_path}: {', '.join(removed_entries)}"
            )
        else:
            console.print(f"{', '.join(absent_entries)} not in the allowlist of {stored_path}.")
        return

    # Whole-target removal path.
    removed = sync.remove_target(config, path)
    sync.save_sync_config(config, paths)

    if mode == "json":
        echo_json({"path": stored_path, "removed": removed})
        return

    console = Console()
    if removed:
        console.print(f"[green]Removed[/green] sync target {stored_path}")
    else:
        console.print(f"[yellow]No sync target[/yellow] found for {stored_path}")


auto_app = typer.Typer(
    help="Turn automatic sync on or off, or show the current setting.",
    invoke_without_command=True,
)
app.add_typer(auto_app, name="auto")


def _auto_status_payload(config: sync.SyncConfig, state: sync.SyncState) -> dict[str, object]:
    """Build the reportable view of the automatic-sync setting and last run.

    ``enabled`` reports the resolved answer, not the stored preference, so the
    payload keeps its existing always-boolean shape and says what will actually
    happen.
    """
    last = state.last_auto_pull_at
    return {
        "enabled": sync.automatic_sync_enabled(config),
        "interval_seconds": config.auto.interval_seconds,
        "last_auto_pull_at": last.isoformat() if last is not None else None,
    }


@auto_app.callback(invoke_without_command=True)
def _auto_root(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print the setting as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print the setting as a table."),
) -> None:
    """Show whether automatic sync is on, with the interval and last run.

    Automatic sync is on once you have a sync target, unless you set it yourself
    with `on` or `off`, which always wins and is never overridden by adding more
    targets. When on, the CLI refreshes the safe set of your configured targets
    (new and behind-registry skills) in the background after a command finishes,
    no more often than the interval. It never overwrites local edits, never
    deletes a local copy, and never blocks your command.
    """
    if ctx.invoked_subcommand is not None:
        return

    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    state = sync.load_sync_state(paths)
    payload = _auto_status_payload(config, state)

    if mode == "json":
        echo_json(payload)
        return

    console = Console()
    automatic = sync.automatic_sync_enabled(config)
    status_word = "on" if automatic else "off"
    color = "green" if automatic else "yellow"
    console.print(f"Automatic sync is [{color}]{status_word}[/{color}].")
    console.print(f"Interval: {config.auto.interval_seconds} seconds.")
    last = payload["last_auto_pull_at"]
    console.print(f"Last automatic sync: {last if last is not None else 'never'}.")


@auto_app.command("on")
def auto_on(
    interval: int | None = typer.Option(
        None,
        "--interval",
        help="Minimum seconds between automatic syncs (defaults to the current setting).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the setting as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print the setting as a table."),
) -> None:
    """Turn automatic sync on, optionally setting the interval.

    Only needed to set an interval, or to turn it back on after `off`: it is
    already on once you have a sync target. Local edits are always preserved and
    nothing is ever deleted automatically. Requires at least one configured sync
    target to do anything.
    """
    if interval is not None and interval <= 0:
        raise ValidationFailed(
            slug="validation_error",
            message="--interval must be a positive number of seconds.",
        )
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    config.auto.enabled = True
    # Stating a preference is what makes it stick across later target adds.
    config.auto.explicitly_set = True
    if interval is not None:
        config.auto.interval_seconds = interval
    sync.save_sync_config(config, paths)
    state = sync.load_sync_state(paths)

    if mode == "json":
        echo_json(_auto_status_payload(config, state))
        return

    console = Console()
    console.print(
        f"[green]Automatic sync is on[/green] (interval: {config.auto.interval_seconds} seconds)."
    )
    if not config.targets:
        console.print(
            "[yellow]Next:[/yellow] add a target with "
            "`goodeye skills sync target add <dir>` so there is something to keep fresh."
        )


@auto_app.command("off")
def auto_off(
    json_output: bool = typer.Option(False, "--json", help="Print the setting as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print the setting as a table."),
) -> None:
    """Turn automatic sync off.

    The interval setting is kept so turning it back on resumes the same cadence.
    This is remembered: adding sync targets later will not turn it back on.
    """
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    paths = get_config_paths()
    config = sync.load_sync_config(paths)
    config.auto.enabled = False
    # Stating a preference is what makes it stick: adding a target later will
    # not turn automatic sync back on.
    config.auto.explicitly_set = True
    sync.save_sync_config(config, paths)
    state = sync.load_sync_state(paths)

    if mode == "json":
        echo_json(_auto_status_payload(config, state))
        return

    console = Console()
    console.print("[yellow]Automatic sync is off.[/yellow]")


_SKIPPED_ACTIONS = frozenset({"skipped-modified", "skipped-conflict"})


def _pull_hints(items: list[sync.PullItem]) -> list[str]:
    """Build neutral next-step hints for a pull pass.

    Skills that kept local edits point at ``--force``. A skill gone from
    the registry but kept on disk points at the same pull to remove it: ``--yes``
    when the caller merely declined the prompt, ``--force`` when the local copy
    has un-pushed edits the pull preserved (``--yes`` alone will not discard
    those). Every command named here exists today.
    """
    hints: list[str] = []
    if any(item.action in _SKIPPED_ACTIONS for item in items):
        hints.append(
            "some skills kept their local edits; re-run with --force to overwrite "
            "them with the registry copy"
        )
    gone = sum(1 for item in items if item.action == "deleted-on-server")
    if gone:
        hints.append(
            f"{gone} skill(s) are gone from the registry but kept on disk; re-run "
            "`goodeye skills sync pull --yes` to remove their local copies "
            "(use --force if they have local edits)"
        )
    incomplete = sum(1 for item in items if item.action == "pulled-incomplete")
    if incomplete:
        hints.append(
            f"{incomplete} skill(s) pulled with missing sibling files (some assets could "
            "not be retrieved); those files were left out of the local directory and were not "
            "recorded as synced"
        )
    return hints


@app.command("pull")
def pull(
    slugs: list[str] = typer.Argument(
        None,
        help="Skill slugs to pull. Omit to pull everything in scope.",
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
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the confirmation prompt before removing a deleted skill's local copy.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """Pull hosted skills down to the configured local directories.

    Each in-scope skill is written to <target>/<slug>/SKILL.md. A local skill
    file that has been edited since the last pull is preserved unless --force is
    given. A skill that has been deleted on the registry has its local copy
    removed after a confirmation prompt (--yes skips the prompt); this only ever
    removes the local directory and never deletes anything on the registry.
    Requires authentication.
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
            yes=yes,
            paths=paths,
        )

    if mode == "json":
        echo_json(items_envelope(result.items))
        return

    console = Console()
    if not result.items:
        console.print("[dim]No skills in scope to pull.[/dim]")
        return
    table = Table(title="Pulled skills")
    table.add_column("Slug", no_wrap=True)
    table.add_column("Target", no_wrap=True)
    table.add_column("Action")
    for item in result.items:
        table.add_row(item.slug, item.target_path, item.action)
    console.print(table)

    hints = _pull_hints(result.items)
    if hints:
        console.print(f"[yellow]Next:[/yellow] {'; '.join(hints)}.")


def _render_status(result: sync.StatusResult) -> None:
    """Print a status pass as a table plus next-step hints.

    Shared by the bare `sync` command (which pulls then shows status) and the
    `status` subcommand so the two render an identical view. Only the rich/table
    path: callers handle the `--json` branch before invoking this.
    """
    console = Console()
    if not result.items:
        console.print("[dim]No skills in scope.[/dim]")
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


def _status_hints(items: list[sync.StatusItem]) -> list[str]:
    """Build neutral next-step hints that name only commands that exist now.

    Each hint points at the command that reconciles that state, and every
    command named here exists today. ``behind-server`` skills update with
    ``pull``. ``modified-local`` skills upload with ``push`` (their
    ``next_action`` is ``push``). ``conflict`` skills moved on both sides, so
    they point at ``pull`` first to merge, then ``status`` to recheck before a
    later push (their ``next_action`` is ``resolve``); they are never sent
    straight to push. ``untracked`` local directories become hosted skills
    through ``skills publish``.
    """
    hints: list[str] = []
    behind = sum(1 for i in items if i.state == "behind-server")
    modified = sum(1 for i in items if i.state == "modified-local")
    conflicted = sum(1 for i in items if i.state == "conflict")
    untracked = sum(1 for i in items if i.state == "untracked")
    if behind:
        hints.append(f"run `goodeye skills sync pull` to update {behind} behind-server skill(s)")
    if modified:
        hints.append(
            f"{modified} skill(s) have local edits; run `goodeye skills sync push` to upload them"
        )
    if conflicted:
        hints.append(
            f"{conflicted} skill(s) moved on both sides; run `goodeye skills sync pull` "
            "to merge, then `goodeye skills sync status` before pushing"
        )
    if untracked:
        hints.append(
            f"{untracked} local skill(s) are not yet in the registry; create them "
            "with `goodeye skills publish`"
        )
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

    For each in-scope skill this compares what the registry reports against
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

    _render_status(result)


def _push_hints(items: list[sync.PushItem]) -> list[str]:
    """Build neutral next-step hints for a push pass.

    A conflict points the caller at the existing ``pull`` and ``status``
    commands. A diverged skill points at ``--target`` to pick the copy to
    keep. An untracked local directory points at ``skills publish``, the only
    path that creates a new hosted skill. A ``pull-required`` copy was
    deferred because the push changed sibling files it does not have, so it points
    at ``pull`` to refresh. Every command named here exists today. A ``converged``
    sibling needs no hint: it is already reconciled.
    """
    hints: list[str] = []
    conflicts = sum(1 for i in items if i.action == "conflict")
    # A skill diverged across targets emits one item per copy; count the
    # distinct skills so the hint reads as one decision per skill.
    diverged = len({i.workflow_id for i in items if i.action == "diverged"})
    untracked = sum(1 for i in items if i.action == "untracked")
    pull_required = sum(1 for i in items if i.action == "pull-required")
    if conflicts:
        hints.append(
            f"{conflicts} skill(s) conflict; run `goodeye skills sync pull` "
            "to merge then push again (check `goodeye skills sync status` first)"
        )
    if diverged:
        hints.append(
            f"{diverged} skill(s) were edited differently across targets; re-run "
            "`goodeye skills sync push --target <dir>` to pick the copy to keep"
        )
    if untracked:
        hints.append(
            f"{untracked} local skill(s) are not in the registry; create them "
            "with `goodeye skills publish`"
        )
    if pull_required:
        hints.append(
            f"{pull_required} other copy(ies) of a pushed skill changed sibling files; "
            "run `goodeye skills sync pull` to refresh them"
        )
    return hints


@app.command("push")
def push(
    slugs: list[str] = typer.Argument(
        None,
        help="Skill slugs to push. Omit to push every locally edited skill in scope.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Operate on a single configured target directory instead of all of them.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """Push locally edited skills back to the registry.

    Only skills whose on-disk SKILL.md differs from the last sync are sent;
    each send includes the full directory tree, not just SKILL.md.
    Each upload is optimistic-locked: if the registry moved since the last sync,
    the skill is reported as a conflict and left untouched, and you reconcile
    with `goodeye skills sync pull` before pushing again. Renaming through
    push is not supported (the directory name is the skill identity), and a
    skill you hold only a view grant on is never uploaded. Requires
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
        console.print("[dim]No locally edited skills to push.[/dim]")
        return
    table = Table(title="Pushed skills")
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
    "auto_app",
    "auto_off",
    "auto_on",
    "pull",
    "push",
    "status",
    "target_add",
    "target_app",
    "target_list",
    "target_remove",
]
