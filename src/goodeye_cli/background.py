"""Best-effort automatic-pull tail for the local skill mirror.

Once the user has a sync target, the CLI keeps the safe set of its configured
targets fresh in the background; `skills sync auto on` and `off` state a
preference that always wins, and ``sync.automatic_sync_enabled`` resolves the
two into the single answer this gate reads. The work runs as a tail
after the user's command finishes (registered through ``atexit`` in ``app.py``),
so it never delays or alters the exit status of the command the user actually
ran. Everything here is best-effort: the gate is a handful of local file reads,
and the tail swallows every exception so a broken automatic pull can never break
or change the outcome of the user's command.

The split mirrors the existing background update notice: a cheap synchronous
gate decides whether to register the tail at all, and the tail does the network
work only when the gate passed.
"""

from __future__ import annotations

import contextlib
import errno
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from goodeye_cli import sync
from goodeye_cli import update as update_checks
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths, get_api_key, get_config_paths, get_server

# How long a lock file may sit before it is treated as abandoned and stolen. A
# crashed process that never released its lock must not wedge automatic pull
# forever; a real pull finishes well inside this bound.
LOCK_STALE_SECONDS = 15 * 60


def should_run_auto_pull(
    args: Sequence[str],
    env: Mapping[str, str],
    config: sync.SyncConfig,
    state: sync.SyncState,
    *,
    authenticated: bool,
    now: datetime,
) -> bool:
    """Decide whether to register the automatic-pull tail for this invocation.

    All of the following must hold (each is a local read or a comparison, so the
    common case is cheap):

    - the invocation is not suppressed (not CI, ``--json``, ``--help``,
      ``--version``, the bare/``help``/``update`` forms, or an explicit
      ``skills sync ...`` command),
    - credentials exist (the caller is authenticated),
    - automatic sync resolves to on and at least one target is configured,
    - the throttle interval has elapsed since the last automatic pull.
    """
    if update_checks.should_suppress_auto_pull(args, env):
        return False
    if not authenticated:
        return False
    if not sync.automatic_sync_enabled(config):
        return False
    if not config.targets:
        return False
    return sync.auto_is_due(state, config, now)


def _acquire_lock(lock_file: Path, *, now: float) -> bool:
    """Try to take the exclusive automatic-pull lock, stealing a stale one.

    Creates ``lock_file`` with ``O_CREAT | O_EXCL`` so only one process holds it
    at a time. A lock older than ``LOCK_STALE_SECONDS`` is treated as abandoned
    (the holder crashed without releasing) and stolen. Returns ``True`` when the
    lock is held by this process, ``False`` when another live process holds it.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        # Lock exists. Steal it if it is stale, then try once more.
        try:
            age = now - lock_file.stat().st_mtime
        except OSError:
            # Vanished between the failed create and the stat; race lost, skip.
            return False
        if age < LOCK_STALE_SECONDS:
            return False
        try:
            lock_file.unlink()
        except OSError:
            return False
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            # Another process stole it first; let it run.
            return False
    os.close(fd)
    return True


def _release_lock(lock_file: Path) -> None:
    """Remove the automatic-pull lock, ignoring an already-gone file."""
    with contextlib.suppress(OSError):
        lock_file.unlink()


def format_auto_pull_summary(result: sync.PullResult) -> str | None:
    """Build the one-line stderr summary for an automatic pull.

    Returns ``None`` when nothing was written and nothing was skipped (the
    silent case). Otherwise summarizes what changed and always surfaces skipped
    local edits with the exact follow-up command, so an opted-in user still
    learns they have local work to reconcile.
    """
    pulled = sum(1 for i in result.items if i.action == "pulled")
    incomplete = sum(1 for i in result.items if i.action == "pulled-incomplete")
    skipped = sum(1 for i in result.items if i.action in ("skipped-modified", "skipped-conflict"))
    gone = sum(1 for i in result.items if i.action == "deleted-on-server")
    up_to_date = sum(1 for i in result.items if i.action == "up-to-date")

    written = pulled + incomplete
    if written == 0 and skipped == 0 and gone == 0:
        return None

    parts: list[str] = []
    if written:
        parts.append(f"{written} updated")
    if up_to_date:
        parts.append(f"{up_to_date} up to date")
    summary = "auto-synced: " + (", ".join(parts) if parts else "no changes")

    follow_up = ""
    if skipped:
        summary += f"; {skipped} skipped (local edits)"
        follow_up = " Next: goodeye skills sync status"
    if gone:
        summary += f"; {gone} gone from the registry"
        follow_up = " Next: goodeye skills sync status"
    return summary + follow_up


def run_auto_pull_tail(
    paths: ConfigPaths | None = None,
    *,
    server: str | None = None,
    api_key: str | None = None,
    now: datetime | None = None,
    transport: object | None = None,
) -> None:
    """Run the automatic pull as a best-effort tail.

    Acquires the exclusive lock, claims the throttle window immediately (so a
    transient failure waits out the next interval rather than retrying on every
    command), builds the authenticated client, runs the safe-set pull in auto
    mode, and prints a one-line stderr summary only when something changed or was
    skipped. Every exception is swallowed: this can never break or change the
    exit status of the user's command.
    """
    try:
        p = paths or get_config_paths()
        moment = now or datetime.now(UTC)

        if not _acquire_lock(p.sync_lock_file, now=time.time()):
            return
        try:
            config = sync.load_sync_config(p)
            state = sync.load_sync_state(p)

            # Claim the throttle window before any network work. This closes the
            # residual race where two processes both pass the gate before either
            # stamps, and means a transient failure simply waits out the next
            # interval rather than retrying on every subsequent command.
            sync.stamp_auto_pull(state, moment)
            sync.save_sync_state(state, p)
            # Reload so the pull's own index save preserves the stamp we just
            # wrote (the pull mutates and re-saves this state object).
            state = sync.load_sync_state(p)

            resolved_server = server if server is not None else get_server(p)
            resolved_key = api_key if api_key is not None else get_api_key(p)
            client_kwargs: dict[str, object] = {"api_key": resolved_key}
            if transport is not None:
                client_kwargs["transport"] = transport
            with GoodeyeClient(resolved_server, **client_kwargs) as client:  # type: ignore[arg-type]
                result = sync.pull(
                    client,
                    config,
                    state,
                    slugs=[],
                    target_path=None,
                    force=False,
                    yes=False,
                    auto=True,
                    paths=p,
                )
            summary = format_auto_pull_summary(result)
            if summary is not None:
                print(summary, file=sys.stderr)
        finally:
            _release_lock(p.sync_lock_file)
    except Exception:
        # Best-effort: a failed automatic pull must never surface a traceback or
        # change the exit status of the user's command.
        return
