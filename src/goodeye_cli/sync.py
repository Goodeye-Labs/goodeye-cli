"""Local sync configuration and target-directory resolution.

This module owns the on-disk ``sync.json`` config and the in-memory model
of where the caller wants their registry workflows mirrored locally. A
sync target is a directory on disk plus the scope of workflows to mirror
into it; the actual pull/push of workflow bodies lives in later layers and
is not implemented here.

All paths are stored in a portable form: a directory under the user's home
is collapsed to a leading ``~`` so the config survives being copied between
machines or shared across accounts, and matches the form the presets use.
"""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from goodeye_cli.config import ConfigPaths, _load_json, _write_json_0600
from goodeye_cli.errors import Conflict, Forbidden, NotFound, ValidationFailed
from goodeye_cli.frontmatter import (
    coerce_outcome,
    coerce_required_text,
    coerce_tags,
    parse_front_matter,
)
from goodeye_cli.prompts import confirm_destructive

if TYPE_CHECKING:
    from goodeye_cli.client import GoodeyeClient
    from goodeye_cli.wire import WorkflowSummary

SyncScope = Literal["owned", "all", "selected"]

# A workflow's registry name doubles as its on-disk slug and directory name.
# We only materialize names that are safe filesystem path segments: lowercase
# alphanumerics and hyphens, starting with an alphanumeric. A name that does
# not match is skipped during a pull rather than written to disk.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Per-target scope mapped to the server-side ``filter`` value for listing.
# ``selected`` lists everything visible and then narrows locally by glob.
_SCOPE_TO_FILTER: dict[SyncScope, str] = {
    "owned": "mine",
    "all": "all",
    "selected": "all",
}

# The set form of ``SyncScope``, for runtime validation of user-supplied
# values. Kept in sync with the ``Literal`` above by construction.
SYNC_SCOPES: frozenset[str] = frozenset(("owned", "all", "selected"))

# Friendly named target directories mapped to their portable home-relative
# path. The values match the ``~`` storage form so a preset target and an
# equivalent hand-typed path dedupe cleanly.
PRESETS: dict[str, str] = {
    "claude": "~/.claude/skills",
    "agents": "~/.agents/skills",
    "cursor": "~/.cursor/skills",
}


class _SyncBase(BaseModel):
    """Shared config: ignore unknown fields for forward-compat."""

    model_config = ConfigDict(extra="ignore")


class SyncTarget(_SyncBase):
    """One local directory to mirror registry workflows into.

    ``path`` is stored in portable form (a leading ``~`` for home-relative
    paths). ``scope`` selects which workflows belong here; ``selected`` lists
    slugs or glob patterns and is only meaningful when ``scope`` is
    ``"selected"``. ``link`` requests symlink materialization in later layers;
    this layer only stores the flag.
    """

    path: str
    scope: SyncScope = "owned"
    selected: list[str] = Field(default_factory=list)
    link: bool = False


class SyncConfig(_SyncBase):
    """The full local sync configuration persisted to ``sync.json``."""

    version: int = 1
    # Reserved for a future per-config principal binding; currently unused. The
    # identity guard stamps and checks SyncState.identity (the index), not this
    # field, so configuring a target stays a purely local action that never
    # reaches out to the registry. Kept here so the persisted schema is stable
    # if a config-level binding is added later.
    identity: str | None = None
    targets: list[SyncTarget] = Field(default_factory=list)


def expand_target_path(path: str) -> Path:
    """Expand a stored target path to an absolute filesystem path.

    Expands a leading ``~`` and collapses ``..``/``.`` segments lexically
    (no filesystem access, no symlink following) so equivalent spellings of
    the same directory compare equal. Used both for filesystem operations and
    for comparing or deduping targets by their fully resolved form.
    """
    expanded = Path(path).expanduser()
    return Path(os.path.normpath(expanded))


def normalize_target_path(path: str) -> str:
    """Collapse a home-directory prefix back to ``~`` for portable storage.

    A path inside the user's home directory is stored as ``~/...`` so the
    config stays portable and matches the preset form. ``..``/``.`` segments
    are collapsed lexically first so the stored path is already clean. Paths
    outside home are returned as their expanded absolute form unchanged.
    """
    expanded = expand_target_path(path)
    home = Path.home()
    try:
        relative = expanded.relative_to(home)
    except ValueError:
        return str(expanded)
    if relative == Path():
        return "~"
    return f"~/{relative.as_posix()}"


def load_sync_config(paths: ConfigPaths) -> SyncConfig:
    """Load the sync config, returning a default when the file is absent.

    Raises ``ValidationFailed`` when the file exists but cannot be parsed.
    """
    if not paths.sync_file.exists():
        return SyncConfig()
    data = _load_json(paths.sync_file)
    if data is None:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not parse sync config at {paths.sync_file}.",
            hint="Fix the JSON by hand, or delete the file to start fresh.",
        )
    return SyncConfig.model_validate(data)


def save_sync_config(config: SyncConfig, paths: ConfigPaths) -> Path:
    """Persist the sync config to disk. Returns the path written."""
    _write_json_0600(paths.sync_file, config.model_dump(mode="json"))
    return paths.sync_file


def resolve_preset(preset: str) -> str:
    """Resolve a named preset to its portable target path.

    Raises ``ValidationFailed`` listing the valid presets on unknown input.
    """
    resolved = PRESETS.get(preset)
    if resolved is None:
        valid = ", ".join(sorted(PRESETS))
        raise ValidationFailed(
            slug="validation_error",
            message=f"Unknown preset {preset!r}.",
            hint=f"Valid presets: {valid}.",
        )
    return resolved


def add_target(
    config: SyncConfig,
    *,
    path: str | None,
    preset: str | None,
    scope: SyncScope,
    only: list[str],
) -> SyncTarget:
    """Add a sync target to ``config`` in place and return the new target.

    Exactly one of ``path`` or ``preset`` must be supplied. ``only`` may only
    be used with ``scope="selected"``. A target whose expanded path matches an
    existing one is rejected as a conflict.
    """
    if (path is None) == (preset is None):
        raise ValidationFailed(
            slug="validation_error",
            message="Provide exactly one of a path or --preset.",
            hint="Pass a directory path, or --preset (e.g. claude), but not both.",
        )
    if only and scope != "selected":
        raise ValidationFailed(
            slug="validation_error",
            message="--only is only valid with --scope selected.",
            hint="Add --scope selected, or drop --only.",
        )

    raw_path = resolve_preset(preset) if preset is not None else path
    assert raw_path is not None  # narrowed by the exactly-one check above

    expanded = expand_target_path(raw_path)
    if not expanded.is_absolute():
        raise ValidationFailed(
            slug="validation_error",
            message="Sync target must be an absolute path or under ~/.",
            hint="Use an absolute path (e.g. /srv/skills) or a home-relative "
            "path (e.g. ~/skills).",
        )
    stored_path = normalize_target_path(raw_path)

    for existing in config.targets:
        if expand_target_path(existing.path) == expanded:
            raise Conflict(
                slug="conflict",
                message=f"A sync target already points at {stored_path}.",
                hint="Remove it first with `goodeye workflows sync target remove`, "
                "or edit the sync config by hand.",
            )

    target = SyncTarget(path=stored_path, scope=scope, selected=list(only))
    config.targets.append(target)
    return target


def remove_target(config: SyncConfig, path: str) -> bool:
    """Remove the target matching ``path`` (by expanded form) in place.

    Returns whether a target was removed.
    """
    expanded = expand_target_path(path)
    kept = [t for t in config.targets if expand_target_path(t.path) != expanded]
    removed = len(kept) != len(config.targets)
    config.targets = kept
    return removed


def list_targets(config: SyncConfig) -> list[SyncTarget]:
    """Return a copy of the configured sync targets."""
    return list(config.targets)


# ----- sync-state index models -----


class SyncVerifierBinding(_SyncBase):
    """One verifier a synced workflow references, recorded in the index.

    Mirrors the verifier reference carried on a workflow so a later push can
    reattach the same bindings without re-deriving them from the body.
    """

    name: str
    verifier_id: str
    version: int | None = None


class FileState(_SyncBase):
    """SHA-256 and metadata for one sibling file in a skill directory.

    Tracks every file that lives alongside SKILL.md so a sibling edit
    registers as drift on the next sync pass. ``executable`` preserves the
    file's execute permission across rounds.

    Kept intentionally minimal: a 500-workflow bundle stores 500+ of these.
    """

    path: str
    sha256: str
    executable: bool = False


class SyncEntry(_SyncBase):
    """A single synced workflow tracked in the local index.

    Identified by ``(slug, target_path)``: the same workflow may be mirrored
    into more than one target. ``body_sha256`` is the hash of the body last
    written to (or read from) disk, used to detect local edits on the next
    pass. ``read_only`` records that the caller holds only a view grant, so
    later pushes know not to attempt an upload.

    ``files`` tracks sibling files in the skill directory (everything except
    SKILL.md). This field was added after initial release; state files written
    by older CLI versions omit it and load with ``files == []``.
    """

    workflow_id: str
    slug: str
    target_path: str
    synced_version: int
    version_token: str | None = None
    body_sha256: str
    verifier_bindings: list[SyncVerifierBinding] = Field(default_factory=list)
    effective_role: str = "owner"
    read_only: bool = False
    files: list[FileState] = Field(default_factory=list)


class SyncState(_SyncBase):
    """The local sync index persisted to ``sync-state.json``.

    Records what was last mirrored where, so subsequent pulls and pushes can
    detect drift on either side. A pull persists this index even when it raises
    partway through, so each ``SKILL.md`` already written has a matching entry;
    re-running the pull reads the index back and resumes from where it left off
    rather than re-pulling already-written workflows as untracked.
    """

    version: int = 1
    identity: str | None = None
    entries: list[SyncEntry] = Field(default_factory=list)


def load_sync_state(paths: ConfigPaths) -> SyncState:
    """Load the sync index, returning a default when the file is absent.

    Raises ``ValidationFailed`` when the file exists but cannot be parsed.
    """
    if not paths.sync_state_file.exists():
        return SyncState()
    data = _load_json(paths.sync_state_file)
    if data is None:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not parse sync index at {paths.sync_state_file}.",
            hint="Fix the JSON by hand, or delete the file to re-sync from the registry.",
        )
    return SyncState.model_validate(data)


def save_sync_state(state: SyncState, paths: ConfigPaths) -> Path:
    """Persist the sync index to disk. Returns the path written."""
    _write_json_0600(paths.sync_state_file, state.model_dump(mode="json"))
    return paths.sync_state_file


def body_sha256(body: str) -> str:
    """Return the hex SHA-256 of a workflow body, encoded as UTF-8."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ----- identity guard -----


def ensure_identity(client: GoodeyeClient, state: SyncState, *, allow_stamp: bool) -> bool:
    """Guard the local mirror against being mixed across two accounts.

    The local working copy belongs to one authenticated principal. This
    resolves the current principal (by email) and compares it to the identity
    last stamped on the index:

    - Unstamped index: when ``allow_stamp`` is True, stamp the current email on
      the state in place and return True (the caller's existing index save
      persists it). When ``allow_stamp`` is False (read-only callers like
      status), do nothing and return False, so a read pass never writes.
    - Stamped to a different principal: raise ``Conflict`` before any work, so
      one account's edits never land in another account's mirror.
    - Stamped to the same principal: return False (nothing to persist).

    Returns whether the state was mutated (a fresh stamp), so callers can tell a
    first run from a steady-state one.
    """
    current = client.get_me().email
    if state.identity is None:
        if allow_stamp:
            state.identity = current
            return True
        return False
    if state.identity != current:
        raise Conflict(
            slug="conflict",
            message=(
                f"This local sync was set up for {state.identity}, but you are signed "
                f"in as {current}. Refusing to mix two accounts' workflows."
            ),
            hint=(
                "Sign in as the original account, or reset the local sync (remove the "
                "sync config and index, then re-run) to start fresh under this account."
            ),
        )
    return False


def find_entry(state: SyncState, *, slug: str, target_path: str) -> SyncEntry | None:
    """Return the index entry for ``(slug, target_path)``, or None.

    Matching is by slug and the normalized target path, so the same workflow in
    two different targets stays distinct.
    """
    normalized = normalize_target_path(target_path)
    for entry in state.entries:
        if entry.slug == slug and normalize_target_path(entry.target_path) == normalized:
            return entry
    return None


def upsert_entry(state: SyncState, entry: SyncEntry) -> None:
    """Replace the matching ``(slug, target_path)`` entry, or append it.

    Operates on the in-memory ``state`` so the caller controls when it is saved.
    """
    normalized = normalize_target_path(entry.target_path)
    for index, existing in enumerate(state.entries):
        if existing.slug == entry.slug and (
            normalize_target_path(existing.target_path) == normalized
        ):
            state.entries[index] = entry
            return
    state.entries.append(entry)


# ----- scope selection + change detection -----


def scope_filter(scope: SyncScope) -> str:
    """Map a target scope to the server-side ``list_workflows`` filter."""
    return _SCOPE_TO_FILTER[scope]


def slug_in_target_scope(target: SyncTarget, slug: str) -> bool:
    """Return whether ``slug`` falls within ``target``'s scope.

    For ``owned``/``all`` targets the server filter already scoped membership,
    so every slug belongs (True). For ``selected`` targets a slug belongs only
    when it matches one of the configured glob patterns. Used to keep the
    listing-narrowing, the tracked-entry classification, and the untracked-dir
    scan all gated on the same scope predicate, so a slug outside a ``selected``
    target's globs is consistently ignored rather than mis-reported.
    """
    if target.scope != "selected":
        return True
    return any(fnmatch.fnmatch(slug, p) for p in target.selected)


def select_for_target(
    target: SyncTarget,
    summaries: list[WorkflowSummary],
    *,
    slugs: list[str] | None = None,
) -> list[WorkflowSummary]:
    """Narrow listed workflows to those belonging to ``target``.

    For ``selected`` targets, keep only summaries whose slug (the workflow
    name) matches a configured glob pattern. For ``owned``/``all``, the server
    filter already scoped the listing, so all returned rows belong here. When
    explicit ``slugs`` are supplied (from ``pull`` arguments), intersect with
    them as well.
    """
    chosen = [s for s in summaries if slug_in_target_scope(target, s.name)]
    if slugs:
        wanted = set(slugs)
        chosen = [s for s in chosen if s.name in wanted]
    return chosen


def local_skill_dir(target: SyncTarget, slug: str) -> Path:
    """Return the absolute ``<target>/<slug>/`` directory for ``slug``."""
    return expand_target_path(target.path) / slug


def local_skill_path(target: SyncTarget, slug: str) -> Path:
    """Return the absolute ``SKILL.md`` path for ``slug`` under ``target``."""
    return local_skill_dir(target, slug) / "SKILL.md"


def read_local_body(target: SyncTarget, slug: str) -> str | None:
    """Return the on-disk body for ``slug``, or None when no file exists."""
    path = local_skill_path(target, slug)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def is_modified_locally(entry: SyncEntry, body: str | None) -> bool:
    """Return whether the local body differs from the recorded hash.

    A missing body is not a local modification (there is nothing to lose).
    """
    if body is None:
        return False
    return body_sha256(body) != entry.body_sha256


def tree_modified_locally(entry: SyncEntry, target: SyncTarget) -> bool:
    """Return True if the body OR any tracked sibling on disk diverges from its recorded hash.

    Used as the deletion guard in ``_reconcile_deletions`` so that an un-pushed
    sibling edit also blocks deletion, not just a body edit. A missing body or a
    missing sibling file is not treated as a local modification (there is nothing
    to lose), so a partial on-disk state does not prevent cleanup.
    """
    # Check the body first.
    if is_modified_locally(entry, read_local_body(target, entry.slug)):
        return True
    # Check each tracked sibling.
    slug_dir = local_skill_dir(target, entry.slug)
    for file_state in entry.files:
        if not _is_safe_sibling_path(slug_dir, file_state.path):
            continue
        sibling_path = slug_dir / file_state.path
        if not sibling_path.exists():
            # Missing sibling - not a local modification (treat as nothing to lose).
            continue
        on_disk_sha = _sibling_sha256(sibling_path)
        if on_disk_sha != file_state.sha256:
            return True
    return False


def server_moved(entry: SyncEntry, summary: WorkflowSummary) -> bool:
    """Return whether the registry advanced past the recorded version.

    Compares the recorded ``version_token`` against the summary's. When either
    side lacks a token we treat it as moved so we re-fetch rather than risk
    serving stale content.
    """
    if entry.version_token is None or summary.version_token is None:
        return True
    return entry.version_token != summary.version_token


# ----- pull orchestration -----

PullAction = Literal[
    "pulled",
    "pulled-incomplete",
    "up-to-date",
    "skipped-modified",
    "skipped-conflict",
    "skipped-unsafe-name",
    "deleted-on-server",
    "deleted-local",
]


class PullItem(_SyncBase):
    """One per-(slug, target) outcome from a pull pass."""

    slug: str
    target_path: str
    action: PullAction
    workflow_id: str | None = None


class PullResult(_SyncBase):
    """The full set of per-item outcomes from a pull pass."""

    items: list[PullItem] = Field(default_factory=list)


def _targets_to_process(config: SyncConfig, target_path: str | None) -> list[SyncTarget]:
    """Resolve which configured targets a pull should operate on.

    With no ``target_path`` this is every configured target (an empty list
    when none are configured, which the caller reports as nothing in scope).
    With one, it is the single target whose expanded path matches; an
    unmatched path raises ``NotFound``.
    """
    if target_path is None:
        return list(config.targets)
    wanted = expand_target_path(target_path)
    matches = [t for t in config.targets if expand_target_path(t.path) == wanted]
    if not matches:
        raise NotFound(
            slug="not_found",
            message=f"No sync target configured for {normalize_target_path(target_path)}.",
            hint="List targets with `goodeye workflows sync target list`.",
        )
    return matches


def _list_all_for_target(
    client: GoodeyeClient,
    target: SyncTarget,
    *,
    include_deleted: bool = False,
) -> list[WorkflowSummary]:
    """List every workflow visible to ``target``, following all pages.

    ``include_deleted`` surfaces the caller's own soft-deleted workflows so a
    fetch-free pass can spot a tracked workflow that was deleted server-side.
    """
    from goodeye_cli.output import fetch_pages

    filter_ = scope_filter(target.scope)
    items, _ = fetch_pages(
        lambda page_cursor: client.list_workflows(
            filter_=filter_, cursor=page_cursor, include_deleted=include_deleted
        ),
        cursor=None,
        all_pages=True,
    )
    return list(items)


_log = logging.getLogger(__name__)


def _write_skill_file(path: Path, body: str) -> None:
    """Write a workflow body to ``path`` with ordinary file permissions.

    Unlike the index/config JSON, a ``SKILL.md`` is content the agent loads, so
    it gets the user's normal umask rather than 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _is_safe_sibling_path(slug_dir: Path, rel_path: str) -> bool:
    """Return True if ``rel_path`` resolves inside ``slug_dir`` and has no dangerous segments.

    Rejects empty segments, ``.`` segments (to avoid confusion), ``..`` segments
    (path traversal), and anything that resolves outside the skill directory.
    This is a defence-in-depth check: well-formed server paths never trigger it,
    but a crafted or buggy payload must not write outside the per-skill directory.
    """
    parts = Path(rel_path).parts
    if not parts:
        return False
    # No empty, dot, or double-dot segments.
    for segment in parts:
        if segment in ("", ".", ".."):
            return False
    resolved_target = slug_dir.resolve()
    resolved_child = (slug_dir / rel_path).resolve()
    try:
        resolved_child.relative_to(resolved_target)
    except ValueError:
        return False
    return True


def _sibling_sha256(path: Path) -> str:
    """Return the hex SHA-256 of a file at ``path``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sibling_file(path: Path, envelope: dict[str, Any]) -> None:
    """Write one sibling file from a file-fetch envelope.

    Handles text (``content`` key), binary (``content_base64`` key), or skips
    when the envelope carries an ``error`` key. The executable bit is applied
    according to the envelope's ``executable`` field.
    """
    if "error" in envelope:
        _log.warning(
            "skipping sibling %s: server returned error %r",
            envelope.get("path", "?"),
            envelope["error"],
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if "content" in envelope:
        path.write_text(envelope["content"], encoding="utf-8")
    elif "content_base64" in envelope:
        path.write_bytes(base64.b64decode(envelope["content_base64"]))
    else:
        _log.warning("skipping sibling %s: envelope has neither content nor error", path)
        return
    if envelope.get("executable"):
        current_mode = os.stat(path).st_mode
        os.chmod(path, current_mode | 0o111)


def _remove_skill_dir(target: SyncTarget, slug: str, entry: SyncEntry) -> None:
    """Remove ONLY the tracked files for ``slug``, then clean up now-empty directories.

    Unlike the old ``shutil.rmtree``, this never destroys files the caller never
    synced. Author-local or untracked files in the skill directory are left in
    place. The set of tracked files is ``SKILL.md`` plus each path in
    ``entry.files``.

    After removing tracked files we walk up the directory tree from each leaf
    subdirectory toward (but not beyond) the slug directory, calling ``os.rmdir``
    at each level. ``os.rmdir`` raises ``OSError`` when the directory still holds
    any files or subdirectories, so an author file silently blocks the removal of
    its containing directory. The slug directory itself is also removed if empty,
    but we never remove the target directory or anything above it.
    """
    slug_dir = local_skill_dir(target, slug)
    if not slug_dir.is_dir():
        return

    # Build the set of tracked absolute paths to remove.
    tracked: list[Path] = [slug_dir / "SKILL.md"]
    for file_state in entry.files:
        if _is_safe_sibling_path(slug_dir, file_state.path):
            tracked.append(slug_dir / file_state.path)

    # Remove tracked files.
    subdirs_to_try: set[Path] = set()
    for p in tracked:
        if p.exists():
            # Record the parent dir (if it is a subdirectory of slug_dir, not slug_dir itself)
            # so we can try rmdir-ing it later.
            if p.parent != slug_dir:
                subdirs_to_try.add(p.parent)
            try:
                p.unlink()
            except OSError as exc:
                _log.warning("could not remove tracked file %s: %s", p, exc)

    # Try to remove empty subdirectories, deepest first, stopping at slug_dir.
    # Sort by depth (number of parts) descending so we try children before parents.
    sorted_subdirs = sorted(subdirs_to_try, key=lambda d: len(d.parts), reverse=True)
    for d in sorted_subdirs:
        # Walk upward from d toward slug_dir (exclusive), attempting rmdir at each level.
        current = d
        while current != slug_dir and current.is_relative_to(slug_dir):
            try:
                os.rmdir(current)
            except OSError:
                break  # Not empty; stop ascending this branch.
            current = current.parent

    # Finally try to remove the slug dir itself.
    with contextlib.suppress(OSError):
        os.rmdir(slug_dir)


def pull(
    client: GoodeyeClient,
    config: SyncConfig,
    state: SyncState,
    *,
    slugs: list[str],
    target_path: str | None,
    force: bool,
    yes: bool,
    paths: ConfigPaths,
) -> PullResult:
    """Mirror registry workflows onto disk for the configured targets.

    For each target, lists the workflows in scope, then for each one decides
    whether to write it: an unchanged local copy is left alone, a locally
    edited copy is preserved unless ``force`` is set, and anything else is
    fetched and written verbatim. After materializing the live workflows, a
    tracked entry whose workflow is gone server-side (soft-deleted or no longer
    visible) has its local copy removed, but only with confirmation (``yes``
    skips the prompt for agents and non-interactive callers); removing a local
    directory never touches the registry. The index is updated in memory as
    each workflow is written and persisted in a ``finally`` even if a later
    fetch raises, so files written before a failure are tracked and a re-run
    resumes from where it left off rather than re-pulling them as untracked.
    """
    result = PullResult()
    # Guard before any work: a mismatched identity aborts here, and a first run
    # stamps the principal on the state (persisted by the index save below).
    ensure_identity(client, state, allow_stamp=True)
    targets = _targets_to_process(config, target_path)
    slug_args = list(slugs)

    try:
        for target in targets:
            # Include the caller's soft-deleted rows so a tracked workflow that
            # was deleted server-side is detectable without an extra fetch.
            summaries = _list_all_for_target(client, target, include_deleted=True)
            live = [s for s in summaries if s.deleted_at is None]
            selected = select_for_target(target, live, slugs=slug_args or None)
            for summary in selected:
                result.items.append(_pull_one(client, state, target, summary, force=force))
            result.items.extend(
                _reconcile_deletions(state, target, live, slug_args=slug_args, force=force, yes=yes)
            )
    finally:
        # Persist whatever the index accumulated, including on a mid-loop raise:
        # any SKILL.md already written has a matching entry, so a re-run treats
        # it as tracked instead of clobbering or duplicating it.
        save_sync_state(state, paths)
    return result


def _reconcile_deletions(
    state: SyncState,
    target: SyncTarget,
    live: list[WorkflowSummary],
    *,
    slug_args: list[str],
    force: bool,
    yes: bool,
) -> list[PullItem]:
    """Remove local copies of tracked workflows that are gone server-side.

    A tracked entry for this target whose slug is in scope but whose workflow is
    absent from the live set (soft-deleted, or no longer visible such as a
    revoked share) is reconciled: with confirmation its local directory is
    removed and its index entry dropped (``deleted-local``); without it the
    entry is reported as ``deleted-on-server`` and left intact. An out-of-scope
    entry for a ``selected`` target is simply not in scope and is never pruned.

    Un-pushed local edits are never silently discarded. A gone-server-side entry
    whose on-disk body diverged from the recorded hash is preserved (reported
    ``deleted-on-server``) unless ``force`` is set, mirroring how ``_pull_one``
    refuses to overwrite a locally edited file without ``--force``. Without this
    guard, ``confirm_destructive`` auto-approves on a non-TTY (the agent path),
    so an agent run would otherwise delete a vanished workflow's directory along
    with edits that were never pushed.
    """
    stored_target = normalize_target_path(target.path)
    wanted = set(slug_args)
    live_ids = {s.id for s in live}

    items: list[PullItem] = []
    surviving: list[SyncEntry] = []
    for entry in state.entries:
        if normalize_target_path(entry.target_path) != stored_target:
            surviving.append(entry)
            continue
        if not slug_in_target_scope(target, entry.slug):
            surviving.append(entry)
            continue
        if wanted and entry.slug not in wanted:
            surviving.append(entry)
            continue
        # Identity is the workflow id, not the slug: a slug can be reused after a
        # delete, so a live workflow sharing this entry's slug but carrying a
        # different id does not keep the tracked (now-deleted) workflow alive. A
        # reused slug that the caller pulled into the index already rewrote this
        # entry's id to the live one via `upsert_entry`; anything still pointing
        # at an id absent from the live set is genuinely gone.
        if entry.workflow_id in live_ids:
            surviving.append(entry)
            continue

        # Protect un-pushed local edits. If the on-disk body or any tracked
        # sibling diverged from the recorded hash, removal would discard work the
        # registry never received, and the deleted workflow cannot be re-pulled to
        # recover it. Keep it and report `deleted-on-server` unless the caller
        # forced the pull. This is the same `--force` gate `_pull_one` applies to
        # a locally edited file, and it stops a non-TTY agent run (where
        # `confirm_destructive` auto-approves) from silently destroying local edits
        # -- including edits to sibling files, not just the SKILL.md body.
        if not force and tree_modified_locally(entry, target):
            surviving.append(entry)
            items.append(
                PullItem(
                    slug=entry.slug,
                    target_path=stored_target,
                    action="deleted-on-server",
                    workflow_id=entry.workflow_id,
                )
            )
            continue

        confirmed = confirm_destructive(
            f"Workflow {entry.slug!r} is gone from the registry. "
            f"Remove its local copy under {stored_target}?",
            yes=yes,
        )
        if confirmed:
            _remove_skill_dir(target, entry.slug, entry)
            items.append(
                PullItem(
                    slug=entry.slug,
                    target_path=stored_target,
                    action="deleted-local",
                    workflow_id=entry.workflow_id,
                )
            )
            # Drop the entry by not carrying it into the surviving list.
            continue
        surviving.append(entry)
        items.append(
            PullItem(
                slug=entry.slug,
                target_path=stored_target,
                action="deleted-on-server",
                workflow_id=entry.workflow_id,
            )
        )
    state.entries = surviving
    return items


def _sibling_needs_fetch(
    row: Any,
    slug_dir: Path,
    old_files: dict[str, FileState],
) -> bool:
    """Return True when a manifest row needs to be fetched from the server.

    A sibling is skipped (returns False) when its recorded sha matches the manifest
    row and the local file already exists on disk.
    """
    existing = old_files.get(row.path)
    local_sibling = slug_dir / row.path
    return not (existing is not None and existing.sha256 == row.sha256 and local_sibling.exists())


# Maximum number of paths sent in a single batch file fetch. The server caps
# the aggregate inline bytes per batch response (paths that overflow come back
# as references rather than content), and it has no hard cap on the request path
# count, so the client bounds the request itself to keep any single call modest.
# Paths that overflow the server's aggregate budget are recovered one at a time
# through the single-file route, which carries no aggregate cap.
_FETCH_BATCH_PATHS = 100


def _write_one_envelope(slug_dir: Path, workflow_id: str, envelope: dict[str, Any]) -> str | None:
    """Write a single file envelope to disk, returning its path on success.

    Returns ``None`` (without writing) when the path is unsafe or the envelope
    carries an ``error`` / has no content. Callers use the returned path to track
    exactly which siblings landed on disk.
    """
    env_path = envelope.get("path", "")
    if not _is_safe_sibling_path(slug_dir, env_path):
        _log.warning("skipping unsafe path %r in file envelope for %s", env_path, workflow_id)
        return None
    if "error" in envelope or not ("content" in envelope or "content_base64" in envelope):
        return None
    _write_sibling_file(slug_dir / env_path, envelope)
    return env_path


def _fetch_and_write_siblings(
    client: GoodeyeClient,
    workflow_id: str,
    slug_dir: Path,
    to_fetch: list[str],
) -> set[str]:
    """Batch-fetch ``to_fetch`` paths and write each file to disk.

    Chunks the request so no single batch call sends an unbounded path list, and
    recovers any path the server returns as ``batch_response_cap_exceeded`` (its
    content would have pushed the batch past the aggregate inline budget) by
    re-fetching it alone through the single-file route, which has no aggregate
    cap. Unsafe envelope paths and remaining error envelopes are skipped with a
    warning. Returns the set of paths actually written to disk so the caller can
    avoid recording a skipped file as synced.
    """
    written: set[str] = set()
    for start in range(0, len(to_fetch), _FETCH_BATCH_PATHS):
        chunk = to_fetch[start : start + _FETCH_BATCH_PATHS]
        batch_result = client.get_workflow_files(workflow_id, chunk)
        for envelope in batch_result.get("files", []):
            if envelope.get("error") == "batch_response_cap_exceeded":
                # The file was dropped from the batch to honor the aggregate
                # inline budget. Re-fetch it on its own, where no aggregate cap
                # applies, so a large-but-individually-fetchable sibling still
                # lands on disk instead of being silently skipped.
                env_path = envelope.get("path", "")
                if not _is_safe_sibling_path(slug_dir, env_path):
                    _log.warning(
                        "skipping unsafe path %r in file envelope for %s", env_path, workflow_id
                    )
                    continue
                single = client.get_workflow_file(workflow_id, env_path)
                landed = _write_one_envelope(slug_dir, workflow_id, single)
            else:
                landed = _write_one_envelope(slug_dir, workflow_id, envelope)
            if landed is not None:
                written.add(landed)
    return written


def _remove_dropped_siblings(
    slug_dir: Path,
    old_files: dict[str, FileState],
    new_manifest_paths: set[str],
) -> None:
    """Remove local files for paths that were in the old manifest but absent from the new one."""
    for dropped_path in old_files:
        if dropped_path not in new_manifest_paths and _is_safe_sibling_path(slug_dir, dropped_path):
            dropped_local = slug_dir / dropped_path
            if dropped_local.exists():
                try:
                    dropped_local.unlink()
                except OSError as exc:
                    _log.warning("could not remove dropped sibling %s: %s", dropped_local, exc)


def _build_file_states(
    slug_dir: Path, new_manifest: list[Any], landed_paths: set[str]
) -> tuple[list[FileState], list[str]]:
    """Build the updated ``FileState`` list from the server manifest rows.

    Only records a path as synced when it is actually present on disk: a path is
    on disk when it was just written (in ``landed_paths``) or it was already
    current and skipped from the fetch (its local copy still exists). A path the
    server could not deliver (over the inline binary ceiling, or otherwise
    missing on disk) is left out of the recorded state so the next pull retries
    it, and is reported in the returned ``missing`` list so the caller can flag
    an incomplete pull instead of falsely reporting success.

    Returns ``(states, missing)``.
    """
    new_file_states: list[FileState] = []
    missing: list[str] = []
    for row in new_manifest:
        if not _is_safe_sibling_path(slug_dir, row.path):
            continue
        local_sibling = slug_dir / row.path
        if row.path not in landed_paths and not local_sibling.exists():
            # The server did not deliver this sibling (e.g. a binary over the
            # inline ceiling) and it is not already on disk. Do not record it as
            # synced; the directory is incomplete.
            missing.append(row.path)
            continue
        sha = row.sha256
        if sha is None:
            sha = _sibling_sha256(local_sibling) if local_sibling.exists() else ""
        new_file_states.append(FileState(path=row.path, sha256=sha, executable=row.executable))
    return new_file_states, missing


def _sync_sibling_files(
    client: GoodeyeClient,
    detail: Any,
    slug_dir: Path,
    old_files: dict[str, FileState],
) -> tuple[list[FileState], list[str]]:
    """Fetch, write, and remove sibling files so the skill directory matches the server manifest.

    Returns ``(states, missing)``: the updated ``FileState`` list for the new
    manifest, and the list of manifest paths the server could not deliver to
    disk. Called from ``_pull_one`` after the ``SKILL.md`` body has already been
    written.
    """
    # Exclude any synthesized SKILL.md row - the body is already written by the caller.
    new_manifest = [row for row in detail.files if row.path != "SKILL.md"]
    new_manifest_paths = {row.path for row in new_manifest}

    # Determine which siblings need fetching (new or changed sha), skipping unsafe paths.
    to_fetch = [
        row.path
        for row in new_manifest
        if _is_safe_sibling_path(slug_dir, row.path)
        and _sibling_needs_fetch(row, slug_dir, old_files)
    ]
    landed_paths: set[str] = set()
    if to_fetch:
        landed_paths = _fetch_and_write_siblings(client, detail.id, slug_dir, to_fetch)

    _remove_dropped_siblings(slug_dir, old_files, new_manifest_paths)
    return _build_file_states(slug_dir, new_manifest, landed_paths)


def _pull_one(
    client: GoodeyeClient,
    state: SyncState,
    target: SyncTarget,
    summary: WorkflowSummary,
    *,
    force: bool,
) -> PullItem:
    """Pull a single workflow into one target and update the index in place."""
    slug = summary.name
    stored_target = normalize_target_path(target.path)
    if not SLUG_RE.match(slug):
        return PullItem(slug=slug, target_path=stored_target, action="skipped-unsafe-name")

    entry = find_entry(state, slug=slug, target_path=target.path)
    local_body = read_local_body(target, slug)

    # Protect local edits. An entry whose disk copy diverged from the recorded
    # hash, or an untracked pre-existing SKILL.md, is not overwritten unless the
    # caller forces it. The divergence check spans the whole tree, not just the
    # SKILL.md body, so an un-pushed edit to a sibling file also blocks an
    # unforced overwrite (a forced pull can drop server-removed siblings, and a
    # body-only guard would let it discard a locally edited one). A conflict
    # means both sides moved relative to a recorded sync point: a tracked entry
    # whose local tree diverged AND whose server token advanced. An untracked
    # file has no recorded base, so it is reported as plain modified, never a
    # conflict.
    tracked_edit = entry is not None and tree_modified_locally(entry, target)
    untracked_present = entry is None and local_body is not None
    if (tracked_edit or untracked_present) and not force:
        is_conflict = entry is not None and tracked_edit and server_moved(entry, summary)
        action: PullAction = "skipped-conflict" if is_conflict else "skipped-modified"
        return PullItem(
            slug=slug,
            target_path=stored_target,
            action=action,
            workflow_id=summary.id,
        )

    # Already current: an entry exists, the server has not advanced, and the
    # local tree matches what we recorded. No fetch needed.
    if (
        entry is not None
        and not server_moved(entry, summary)
        and local_body is not None
        and not tree_modified_locally(entry, target)
    ):
        return PullItem(
            slug=slug,
            target_path=stored_target,
            action="up-to-date",
            workflow_id=summary.id,
        )

    detail = client.get_workflow(summary.id)
    assert not isinstance(detail, str)  # JSON path: accept_markdown is False
    path = local_skill_path(target, slug)
    _write_skill_file(path, detail.body)

    slug_dir = local_skill_dir(target, slug)
    old_files: dict[str, FileState] = {f.path: f for f in (entry.files if entry else [])}
    new_file_states, missing_siblings = _sync_sibling_files(client, detail, slug_dir, old_files)
    if missing_siblings:
        _log.warning(
            "workflow %s (%s): %d sibling file(s) could not be retrieved and were left out "
            "of the local directory: %s",
            slug,
            detail.id,
            len(missing_siblings),
            ", ".join(sorted(missing_siblings)),
        )

    effective_role = detail.effective_role or summary.effective_role or "owner"
    # Record the validated slug (the name we checked against SLUG_RE and used to
    # build the file path), so the recorded slug can never diverge from the path
    # on disk even if the detail payload reports a different name.
    upsert_entry(
        state,
        SyncEntry(
            workflow_id=detail.id,
            slug=slug,
            target_path=stored_target,
            synced_version=detail.version,
            version_token=detail.version_token,
            body_sha256=body_sha256(detail.body),
            verifier_bindings=[
                SyncVerifierBinding(name=v.name, verifier_id=v.verifier_id, version=v.version)
                for v in detail.verifiers
            ],
            effective_role=effective_role,
            read_only=effective_role == "view",
            files=new_file_states,
        ),
    )
    return PullItem(
        slug=slug,
        target_path=stored_target,
        action="pulled-incomplete" if missing_siblings else "pulled",
        workflow_id=detail.id,
    )


# ----- status reporting -----

# The states a tracked or local workflow can be in, and the action the caller
# would take to reconcile each. ``status`` only reports these; it never writes
# the index or any SKILL.md, and it never fetches a workflow body.
SyncStatusState = Literal[
    "clean",
    "modified-local",
    "behind-server",
    "conflict",
    "deleted-on-server",
    "untracked",
]
SyncNextAction = Literal["none", "push", "pull", "resolve", "publish"]


class StatusItem(_SyncBase):
    """One per-(slug, target) drift classification from a status pass.

    ``synced_version`` is the version last recorded in the local index (None for
    an untracked local directory). ``server_version`` is the registry's
    ``current_version`` for the workflow, the live version a pull would
    materialize (None when the workflow is gone server-side or untracked); it is
    not the detail-only ``version`` field.
    ``read_only`` carries through whether the caller holds only a view grant, so
    a locally edited but unpushable workflow reports ``next_action`` ``none``.
    """

    slug: str
    workflow_id: str | None = None
    target_path: str
    state: SyncStatusState
    read_only: bool = False
    synced_version: int | None = None
    server_version: int | None = None
    next_action: SyncNextAction


class StatusResult(_SyncBase):
    """The full set of per-item classifications from a status pass."""

    items: list[StatusItem] = Field(default_factory=list)


def untracked_local_slugs(target: SyncTarget) -> list[str]:
    """Return slugs of immediate child dirs under ``target`` holding a SKILL.md.

    Scans a single level of ``<target>/<slug>/SKILL.md`` and returns the slug of
    each child directory that contains a ``SKILL.md``. A target directory that
    does not exist yields an empty list. The caller filters these against the
    index to find local directories the registry does not yet track.
    """
    base = expand_target_path(target.path)
    if not base.is_dir():
        return []
    slugs: list[str] = []
    for child in base.iterdir():
        if child.is_dir() and (child / "SKILL.md").is_file():
            slugs.append(child.name)
    return sorted(slugs)


def _classify_tracked(
    entry: SyncEntry,
    summary: WorkflowSummary | None,
    *,
    target: SyncTarget,
) -> StatusItem:
    """Classify one tracked index entry against its live summary (if any).

    Reads only the local body hash and the summary's ``version_token``: no body
    is fetched. A summary that is absent or carries a ``deleted_at`` means the
    workflow is gone server-side.
    """
    stored_target = normalize_target_path(target.path)
    read_only = entry.effective_role == "view"
    base = StatusItem(
        slug=entry.slug,
        workflow_id=entry.workflow_id,
        target_path=stored_target,
        state="clean",
        read_only=read_only,
        synced_version=entry.synced_version,
        server_version=None,
        next_action="none",
    )

    if summary is None or summary.deleted_at is not None:
        return base.model_copy(update={"state": "deleted-on-server", "next_action": "resolve"})

    base = base.model_copy(update={"server_version": summary.current_version})
    # Whole-tree drift: a sibling-file edit counts as modified just like a body
    # edit, so status does not report a directory with un-pushed sibling work as
    # clean.
    modified = tree_modified_locally(entry, target)
    moved = server_moved(entry, summary)

    if modified and moved:
        return base.model_copy(update={"state": "conflict", "next_action": "resolve"})
    if modified:
        # A read-only workflow can be edited on disk but the edits cannot be
        # pushed back, so there is no action to recommend.
        action: SyncNextAction = "none" if read_only else "push"
        return base.model_copy(update={"state": "modified-local", "next_action": action})
    if moved:
        return base.model_copy(update={"state": "behind-server", "next_action": "pull"})
    return base


def status(
    client: GoodeyeClient,
    config: SyncConfig,
    state: SyncState,
    *,
    target_path: str | None,
) -> StatusResult:
    """Report drift between the registry and the local mirror, without writing.

    For each target in scope this does a single listing pass (following all
    pages, with the caller's soft-deleted workflows included so deletion is
    detectable fetch-free), then classifies each tracked index entry by
    comparing recorded version tokens and the local body hash. It never fetches
    a workflow body and never writes the index or any SKILL.md. Local
    directories that hold a SKILL.md but have no index entry are reported as
    untracked.
    """
    result = StatusResult()
    # Guard without writing: a mismatched identity aborts a read-only pass too,
    # but an unstamped index is left untouched (no stamp on a read).
    ensure_identity(client, state, allow_stamp=False)
    targets = _targets_to_process(config, target_path)
    # Cache the listing per server-filter string so targets sharing a scope do
    # not each re-list. The filter already encodes the scope (owned vs all).
    listings: dict[str, list[WorkflowSummary]] = {}

    for target in targets:
        filter_ = scope_filter(target.scope)
        summaries = listings.get(filter_)
        if summaries is None:
            summaries = _list_all_for_target(client, target, include_deleted=True)
            listings[filter_] = summaries
        result.items.extend(_status_for_target(target, summaries, state))
    return result


def _status_for_target(
    target: SyncTarget,
    summaries: list[WorkflowSummary],
    state: SyncState,
) -> list[StatusItem]:
    """Classify every tracked entry and untracked local dir for one target."""
    # Narrow a `selected` target to its globs; owned/all already match the
    # server filter, so this is a passthrough for them.
    in_scope = select_for_target(target, summaries)
    by_id = {s.id: s for s in in_scope}
    stored_target = normalize_target_path(target.path)

    items: list[StatusItem] = []
    tracked_slugs: set[str] = set()
    for entry in state.entries:
        if normalize_target_path(entry.target_path) != stored_target:
            continue
        # Gate tracked classification on the same scope predicate as the
        # listing: a `selected` target whose globs were narrowed after a pull
        # would otherwise find no summary for an out-of-glob slug in the
        # narrowed listing and mis-report a still-live workflow as deleted.
        if not slug_in_target_scope(target, entry.slug):
            continue
        tracked_slugs.add(entry.slug)
        summary = by_id.get(entry.workflow_id)
        items.append(_classify_tracked(entry, summary, target=target))

    for slug in untracked_local_slugs(target):
        if slug in tracked_slugs:
            continue
        # Same scope gate on the untracked scan, so an out-of-glob local dir is
        # not reported as untracked for a `selected` target it does not belong
        # to. Keeps the tracked and untracked paths consistent.
        if not slug_in_target_scope(target, slug):
            continue
        items.append(
            StatusItem(
                slug=slug,
                workflow_id=None,
                target_path=stored_target,
                state="untracked",
                next_action="publish",
            )
        )
    return items


# ----- push orchestration -----

# A workflow's registry identity is its name, which equals its on-disk slug.
# Front-matter that carries one of these keys with a different value is a rename
# attempt, which push refuses: renaming is not supported through sync.
_IDENTITY_KEYS = ("name", "slug")

# What a single push attempt did for one tracked entry (or untracked local dir).
# ``pushed`` uploaded a local edit; ``conflict`` means the registry moved since
# the last sync and the caller must pull first; ``skipped-read-only`` means the
# caller holds only a view grant; ``skipped-invalid`` means the edited body's
# metadata was rejected locally before any upload; ``untracked`` flags a local
# directory the registry does not track, which push never creates; ``converged``
# means a sibling copy of a just-pushed workflow in another target was rewritten
# to match (no second upload); ``diverged`` means the same workflow was edited
# differently in two or more targets and every copy was refused pending the
# caller picking a source with ``--target`` or reconciling the copies.
PushAction = Literal[
    "pushed",
    "conflict",
    "skipped-read-only",
    "skipped-invalid",
    "untracked",
    "converged",
    "diverged",
]

# The reason attached to a ``skipped-read-only`` push item. Shared so the
# per-entry upload path and the up-front read-only split in a multi-target
# group report the same message.
_READ_ONLY_DETAIL = (
    "You hold only a view grant on this workflow, so local edits cannot be pushed back."
)


class PushItem(_SyncBase):
    """One per-(slug, target) outcome from a push pass.

    ``detail`` carries a human-readable reason for the non-``pushed`` actions
    (the validation message, the rename refusal, the pull-first hint, or the
    publish hint), and is None for a clean ``pushed``.
    """

    slug: str
    workflow_id: str | None = None
    target_path: str
    action: PushAction
    detail: str | None = None


class PushResult(_SyncBase):
    """The full set of per-item outcomes from a push pass."""

    items: list[PushItem] = Field(default_factory=list)


def _verifier_payload(entry: SyncEntry) -> list[dict[str, Any]]:
    """Build the save-payload verifier bindings from an index entry.

    Mirrors the publish path's binding shape (``name`` + ``verifier_id``) and
    additionally preserves a pinned ``version`` when the recorded binding has
    one, so a push never drops a version pin the workflow carried.
    """
    payload: list[dict[str, Any]] = []
    for binding in entry.verifier_bindings:
        row: dict[str, Any] = {"name": binding.name, "verifier_id": binding.verifier_id}
        if binding.version is not None:
            row["version"] = binding.version
        payload.append(row)
    return payload


def _push_metadata(body: str, slug: str) -> tuple[str, str, list[str]] | str:
    """Derive (description, outcome, tags) from an edited body's front-matter.

    Returns the validated metadata tuple, or a human-readable error string when
    the body attempts a rename or omits a required facet. The caller turns an
    error string into a ``skipped-invalid`` item; the registry is never reached
    for these local failures.
    """
    front_matter, _stripped = parse_front_matter(body)
    for key in _IDENTITY_KEYS:
        edited_identity = front_matter.get(key)
        if isinstance(edited_identity, str) and edited_identity.strip() != slug:
            return (
                "Renaming is not supported through sync push: the directory name "
                f"({slug!r}) is the workflow identity. Revert the front-matter "
                f"`{key}` or rename the directory and publish a new workflow."
            )

    try:
        description = coerce_required_text(
            front_matter.get("description"),
            field_name="description",
            missing_message="Missing `description` in the workflow front-matter.",
        )
        outcome = coerce_outcome(front_matter.get("outcome"))
        # Push cannot clear tags: an absent/empty `tags:` coerces to [], and
        # save_workflow drops a falsy tags value rather than emptying the list,
        # so removing the line leaves the registry tags untouched (same as publish).
        tags = coerce_tags(front_matter.get("tags"))
    except ValidationFailed as exc:
        return exc.message
    if outcome is None:
        return "Missing `outcome` in the workflow front-matter."
    return description, outcome, tags


@dataclass
class _PushCandidate:
    """One modified-local tracked entry queued for a push pass.

    Bundles the entry with the target it lives in and the on-disk body that
    differs from the recorded hash, so grouping across targets and the eventual
    upload share the same pre-read body (no second disk read mid-pass). It holds
    the live ``entry`` object from ``state.entries`` (by reference) so a push
    mutates the index in place.
    """

    entry: SyncEntry
    target: SyncTarget
    body: str


def push(
    client: GoodeyeClient,
    config: SyncConfig,
    state: SyncState,
    *,
    slugs: list[str],
    target_path: str | None,
    paths: ConfigPaths,
) -> PushResult:
    """Upload locally edited workflows back to the registry, optimistic-locked.

    Detection of what to push is purely local: a tracked entry whose on-disk
    body hash differs from the recorded one is a candidate. No listing or fetch
    is performed; whether the registry moved underneath is resolved by the
    server, which returns a conflict when the recorded version token is stale.
    A read-only (view-grant) entry is never uploaded, a front-matter rename is
    refused, and a missing required facet fails locally before any round-trip.

    When the same workflow is mirrored into more than one target and edited in
    several of them, the copies are kept coherent: identical edits push once and
    converge the siblings; differing edits are refused as ``diverged`` unless
    the caller picks the source with ``--target``. After a successful upload the
    other target copies of that workflow are rewritten to match (no second
    upload) and reported as ``converged``.

    The index is persisted once in a ``finally`` so a mid-loop failure still
    records every entry already pushed, and a re-run resumes cleanly.
    """
    result = PushResult()
    # Guard before any upload: a mismatched identity aborts here, and a first
    # run stamps the principal on the state (persisted by the index save below).
    ensure_identity(client, state, allow_stamp=True)
    targets = _targets_to_process(config, target_path)
    slug_args = set(slugs)

    # Fetch ignore defaults once so all push candidates share the same spec.
    ignore_defaults: list[str] | None = None
    try:
        cfg = client.get_client_config()
        ignore_defaults = cfg.ignore_defaults if cfg.ignore_defaults else None
    except Exception:
        pass

    try:
        candidates = _collect_push_candidates(state, targets, slug_args=slug_args)
        # Group the modified-local copies by workflow identity so a workflow
        # mirrored into several targets is pushed once and its siblings kept
        # coherent rather than racing each other to the registry.
        by_workflow: dict[str, list[_PushCandidate]] = {}
        for candidate in candidates:
            by_workflow.setdefault(candidate.entry.workflow_id, []).append(candidate)
        for group in by_workflow.values():
            result.items.extend(
                _push_workflow_group(
                    client,
                    state,
                    config,
                    group,
                    scoped=target_path is not None,
                    ignore_defaults=ignore_defaults,
                )
            )
        result.items.extend(_untracked_push_items(state, targets, slug_args=slug_args))
    finally:
        # Persist whatever the index accumulated, including on a mid-loop raise:
        # every entry already pushed has its new version/token/hash recorded, so
        # a re-run does not re-upload it as still-modified.
        save_sync_state(state, paths)
    return result


def _collect_push_candidates(
    state: SyncState,
    targets: list[SyncTarget],
    *,
    slug_args: set[str],
) -> list[_PushCandidate]:
    """Gather every modified-local tracked entry across the processed targets.

    An entry is a candidate when it lives in one of the targets, its slug is in
    that target's scope and (if given) the slug args, and its on-disk body OR
    any tracked sibling differs from the recorded hash. Read-only and invalid
    entries are kept here: they are classified per-entry at upload time, not
    silently dropped.
    """
    by_path = {normalize_target_path(t.path): t for t in targets}
    candidates: list[_PushCandidate] = []
    for entry in state.entries:
        stored_target = normalize_target_path(entry.target_path)
        target = by_path.get(stored_target)
        if target is None:
            continue
        if not slug_in_target_scope(target, entry.slug):
            continue
        if slug_args and entry.slug not in slug_args:
            continue
        local_body = read_local_body(target, entry.slug)
        if local_body is None:
            continue
        # A candidate when the body or any tracked sibling drifted.
        body_drifted = is_modified_locally(entry, local_body)
        tree_drifted = tree_modified_locally(entry, target)
        if not body_drifted and not tree_drifted:
            continue
        candidates.append(_PushCandidate(entry=entry, target=target, body=local_body))
    return candidates


def _push_workflow_group(
    client: GoodeyeClient,
    state: SyncState,
    config: SyncConfig,
    group: list[_PushCandidate],
    *,
    scoped: bool,
    ignore_defaults: list[str] | None = None,
) -> list[PushItem]:
    """Push one workflow's modified copies, keeping multi-target copies coherent.

    A single modified copy pushes normally. Two or more identical copies push
    once and converge the rest. Two or more differing copies are refused as
    ``diverged`` when no ``--target`` narrowed the run; with a single processed
    target the group already holds just that copy, so the user's pick resolves
    the divergence and the siblings converge to it.

    A read-only (view-grant) copy can never be uploaded, so it is reported
    ``skipped-read-only`` up front and removed from the writable set before the
    divergence test. Without this, a view-grant workflow mirrored into two
    targets and edited differently would be mis-reported as ``diverged`` (a
    state that asks the caller to pick a copy to push) when neither copy is
    pushable at all.
    """
    items: list[PushItem] = []
    writable: list[_PushCandidate] = []
    for candidate in group:
        if candidate.entry.read_only:
            items.append(_read_only_item(candidate))
        else:
            writable.append(candidate)
    if not writable:
        return items

    if len(writable) > 1 and not scoped:
        bodies = {c.body for c in writable}
        if len(bodies) > 1:
            items.extend(_diverged_item(c) for c in writable)
            return items

    # Either a lone candidate, identical copies, or a single scoped copy: push
    # the first and converge any siblings to the just-pushed body.
    source = writable[0]
    item = _push_candidate(
        client,
        source.entry,
        target=source.target,
        body=source.body,
        ignore_defaults=ignore_defaults,
    )
    items.append(item)
    if item.action != "pushed":
        # An upload that did not land (conflict, read-only, invalid) leaves the
        # siblings untouched; there is nothing coherent to converge them to.
        return items
    items.extend(_converge_siblings(state, config, source))
    return items


def _read_only_item(candidate: _PushCandidate) -> PushItem:
    """Build the ``skipped-read-only`` item for one view-grant copy.

    Reported up front for a read-only copy in a multi-target group so a
    view-grant workflow mirrored into several targets never reaches the
    divergence branch (it is not pushable, so there is no copy to pick).
    """
    return PushItem(
        slug=candidate.entry.slug,
        workflow_id=candidate.entry.workflow_id,
        target_path=normalize_target_path(candidate.target.path),
        action="skipped-read-only",
        detail=_READ_ONLY_DETAIL,
    )


def _diverged_item(candidate: _PushCandidate) -> PushItem:
    """Build the ``diverged`` item for one copy of a multi-target workflow."""
    return PushItem(
        slug=candidate.entry.slug,
        workflow_id=candidate.entry.workflow_id,
        target_path=normalize_target_path(candidate.target.path),
        action="diverged",
        detail=(
            "This workflow was edited differently in more than one target. Pick the "
            "copy to keep with `--target <dir>`, or reconcile the copies so they "
            "match, then push again."
        ),
    )


def _converge_siblings(
    state: SyncState,
    config: SyncConfig,
    source: _PushCandidate,
) -> list[PushItem]:
    """Bring every other target copy of a just-pushed workflow into line with it.

    For each sibling index entry (same workflow id, different target) the action
    depends on the sibling's own on-disk state, so an un-pushed local edit in a
    sibling is never silently destroyed:

    - Local file missing: skipped silently. A directory the user deleted is not
      resurrected.
    - Modified locally and the local body differs from the pushed body: left
      untouched and reported ``diverged``. The sibling holds a distinct local
      edit that this push did not include; overwriting it would lose work.
    - Modified locally but the local body already equals the pushed body: no
      rewrite is needed; its index entry is advanced to clean and it reports
      ``converged``.
    - Clean (local body present and matching the recorded hash): rewritten to
      the pushed body, its index entry advanced, and reported ``converged``.

    Siblings are found from the full config so a ``--target`` run still
    converges (or flags) targets outside the processed set. This per-sibling
    safety net is in addition to the in-scope divergence detection that runs
    before the push.
    """
    by_path = {normalize_target_path(t.path): t for t in config.targets}
    source_target = normalize_target_path(source.target.path)
    items: list[PushItem] = []
    for entry in state.entries:
        if entry is source.entry:
            continue
        if entry.workflow_id != source.entry.workflow_id:
            continue
        stored_target = normalize_target_path(entry.target_path)
        if stored_target == source_target:
            continue
        target = by_path.get(stored_target)
        if target is None:
            continue

        local_body = read_local_body(target, entry.slug)
        if local_body is None:
            # The user deleted this sibling's directory. Do not recreate it.
            continue

        modified = is_modified_locally(entry, local_body)
        if modified and local_body != source.body:
            # A distinct un-pushed edit lives here. Refuse to clobber it; the
            # caller reconciles it manually (e.g. push it from this target, or
            # discard it). Its index entry is left untouched.
            items.append(_diverged_sibling_item(entry, stored_target))
            continue

        if not modified:
            # Clean sibling: rewrite its file to the pushed body.
            _write_skill_file(local_skill_path(target, entry.slug), source.body)
        # Either rewritten from clean, or already equal to the pushed body on
        # disk; either way the recorded state advances to the pushed version so
        # the sibling reads clean afterward with no second upload.
        entry.synced_version = source.entry.synced_version
        entry.version_token = source.entry.version_token
        entry.body_sha256 = source.entry.body_sha256
        entry.verifier_bindings = [b.model_copy() for b in source.entry.verifier_bindings]
        items.append(
            PushItem(
                slug=entry.slug,
                workflow_id=entry.workflow_id,
                target_path=stored_target,
                action="converged",
                detail=f"Rewritten to match the copy pushed from {source_target}.",
            )
        )
    return items


def _diverged_sibling_item(entry: SyncEntry, stored_target: str) -> PushItem:
    """Build the ``diverged`` item for a sibling whose local edit was preserved.

    Distinct from ``_diverged_item``: this is reported during convergence when a
    sibling copy held its own un-pushed edit that the push did not include, so
    its file and index entry were left exactly as they were.
    """
    return PushItem(
        slug=entry.slug,
        workflow_id=entry.workflow_id,
        target_path=stored_target,
        action="diverged",
        detail=(
            "A different local edit here was left untouched; reconcile it manually "
            "(push it from this target, or discard it), then push again."
        ),
    )


def _untracked_push_items(
    state: SyncState,
    targets: list[SyncTarget],
    *,
    slug_args: set[str],
) -> list[PushItem]:
    """Flag local directories with no index entry across the processed targets."""
    items: list[PushItem] = []
    for target in targets:
        stored_target = normalize_target_path(target.path)
        tracked = {
            e.slug for e in state.entries if normalize_target_path(e.target_path) == stored_target
        }
        for slug in untracked_local_slugs(target):
            if slug in tracked:
                continue
            if not slug_in_target_scope(target, slug):
                continue
            if slug_args and slug not in slug_args:
                continue
            items.append(
                PushItem(
                    slug=slug,
                    workflow_id=None,
                    target_path=stored_target,
                    action="untracked",
                    detail=(
                        "This local directory is not tracked by the registry. Create it "
                        "with `goodeye workflows publish` rather than sync push."
                    ),
                )
            )
    return items


def build_files_payload(
    skill_dir: Path,
    recorded_files: list[FileState] | None,
    ignore_defaults: list[str] | None,
) -> tuple[list[dict[str, Any]], list[FileState]]:
    """Build the ``files`` payload for ``save_workflow`` from a skill directory.

    Walks *skill_dir* recursively.  For each file (POSIX-relative path from the
    skill root):

    - ``SKILL.md`` is always skipped (it is the body, not a sibling).
    - Files whose relative path is ignored by the effective ignore spec are
      skipped *before* reading their bytes, so large cache directories are never
      read.
    - The remaining files are checked against *recorded_files*.  When the
      recorded sha256 for a path matches the on-disk sha256, a reference entry
      (``{path, sha256, executable}``) is emitted so the server can carry the
      blob forward without re-uploading it.  Otherwise the file is sent inline:
      text files as a UTF-8 ``content`` string, binary files (NUL byte or
      invalid UTF-8) as a base64-encoded ``content`` string.

    Returns ``(payload, states)``, both sorted lexicographically by path:
    *payload* is the wire list for ``save_workflow``; *states* is the matching
    ``FileState`` list (each sha256 computed over the raw on-disk bytes) for the
    local sync index, so a binary file (whose inline ``content`` is base64) is
    recorded under its true byte sha256 and converges to a reference next push.
    """
    from goodeye_cli.ignore import build_ignore_spec

    matcher = build_ignore_spec(skill_dir, ignore_defaults)
    recorded_map: dict[str, FileState] = {f.path: f for f in (recorded_files or [])}

    entries: list[dict[str, Any]] = []
    states: list[FileState] = []
    for abs_path in sorted(skill_dir.rglob("*")):
        # Skip symlinks. ``is_file()`` and ``read_bytes()`` both follow links, so
        # an in-tree symlink pointing outside the skill directory would otherwise
        # upload the target's bytes. This mirrors the containment defense the pull
        # side applies when writing siblings back to disk.
        if abs_path.is_symlink():
            continue
        if not abs_path.is_file():
            continue
        try:
            rel = abs_path.relative_to(skill_dir).as_posix()
        except ValueError:
            continue
        if rel == "SKILL.md":
            continue
        if matcher.is_ignored(rel):
            continue

        raw = abs_path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        executable = bool(os.stat(abs_path).st_mode & 0o100)
        states.append(FileState(path=rel, sha256=sha, executable=executable))

        recorded = recorded_map.get(rel)
        if recorded is not None and recorded.sha256 == sha:
            # Reference entry: server already has this blob.
            entries.append({"path": rel, "sha256": sha, "executable": executable})
        else:
            # Inline entry: text or binary.
            try:
                content_str = raw.decode("utf-8")
                if "\x00" in content_str:
                    raise ValueError("NUL byte")
                entries.append({"path": rel, "content": content_str, "executable": executable})
            except (UnicodeDecodeError, ValueError):
                entries.append(
                    {
                        "path": rel,
                        "content": base64.b64encode(raw).decode("ascii"),
                        "executable": executable,
                    }
                )

    return entries, states


def _push_candidate(
    client: GoodeyeClient,
    entry: SyncEntry,
    *,
    target: SyncTarget,
    body: str,
    ignore_defaults: list[str] | None = None,
) -> PushItem:
    """Upload one modified entry's body, mutating the entry in place on success.

    The body is the already-read on-disk content that differs from the recorded
    hash, so this does not re-read disk. Returns the per-item outcome; a
    non-``pushed`` action means nothing was uploaded.
    """
    stored_target = normalize_target_path(target.path)
    base = PushItem(
        slug=entry.slug,
        workflow_id=entry.workflow_id,
        target_path=stored_target,
        action="pushed",
    )

    if entry.read_only:
        return base.model_copy(update={"action": "skipped-read-only", "detail": _READ_ONLY_DETAIL})

    metadata = _push_metadata(body, entry.slug)
    if isinstance(metadata, str):
        return base.model_copy(update={"action": "skipped-invalid", "detail": metadata})
    description, outcome, tags = metadata

    # Build the sibling-file payload for this skill directory.
    skill_dir = local_skill_dir(target, entry.slug)
    files_payload, file_states = build_files_payload(skill_dir, list(entry.files), ignore_defaults)

    try:
        save_result = client.save_workflow(
            name=entry.slug,
            description=description,
            body=body,
            outcome=outcome,
            tags=tags,
            expected_version_token=entry.version_token,
            source="manual",
            verifiers=_verifier_payload(entry),
            files=files_payload,
        )
    except Conflict:
        return base.model_copy(
            update={
                "action": "conflict",
                "detail": (
                    "The registry moved since the last sync. Run "
                    "`goodeye workflows sync pull` to merge (or `pull --force` to "
                    "discard local edits), then push again."
                ),
            }
        )
    except Forbidden as exc:
        # Belt and suspenders: the recorded role said writable but the server
        # rejected the write. Surface it as read-only with the server message.
        return base.model_copy(update={"action": "skipped-read-only", "detail": exc.message})
    except ValidationFailed as exc:
        # The server rejected this one body (e.g. a missing version token on an
        # existing workflow, which the index model permits as None). Report it as
        # one skipped item so the rest of the push pass still runs instead of
        # aborting on the first invalid entry.
        return base.model_copy(update={"action": "skipped-invalid", "detail": exc.message})

    entry.synced_version = save_result.version
    entry.version_token = save_result.version_token
    entry.body_sha256 = body_sha256(body)
    entry.verifier_bindings = [
        SyncVerifierBinding(name=v.name, verifier_id=v.verifier_id, version=v.version)
        for v in save_result.verifiers
    ]
    # Record the file states (each sha256 is over the raw on-disk bytes, so a
    # binary file converges to a reference on the next push rather than re-uploading).
    entry.files = file_states
    return base.model_copy(update={"workflow_id": save_result.workflow_id})


__all__ = [
    "PRESETS",
    "SLUG_RE",
    "SYNC_SCOPES",
    "FileState",
    "PullAction",
    "PullItem",
    "PullResult",
    "PushAction",
    "PushItem",
    "PushResult",
    "StatusItem",
    "StatusResult",
    "SyncConfig",
    "SyncEntry",
    "SyncNextAction",
    "SyncScope",
    "SyncState",
    "SyncStatusState",
    "SyncTarget",
    "SyncVerifierBinding",
    "add_target",
    "body_sha256",
    "build_files_payload",
    "ensure_identity",
    "expand_target_path",
    "find_entry",
    "is_modified_locally",
    "list_targets",
    "load_sync_config",
    "load_sync_state",
    "local_skill_dir",
    "local_skill_path",
    "normalize_target_path",
    "pull",
    "read_local_body",
    "remove_target",
    "resolve_preset",
    "save_sync_config",
    "save_sync_state",
    "scope_filter",
    "select_for_target",
    "server_moved",
    "slug_in_target_scope",
    "status",
    "untracked_local_slugs",
    "upsert_entry",
]
