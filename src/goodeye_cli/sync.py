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

import fnmatch
import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from goodeye_cli.config import ConfigPaths, _load_json, _write_json_0600
from goodeye_cli.errors import Conflict, NotFound, ValidationFailed

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
    # Stable identifier for the authenticated principal that owns this config.
    # The registry exposes only email and handle (no opaque user id), so later
    # server-touching operations stamp this with the principal's email on first
    # use. It stays None until then; configuring a target is a purely local
    # action and never reaches out to the registry.
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


class SyncEntry(_SyncBase):
    """A single synced workflow tracked in the local index.

    Identified by ``(slug, target_path)``: the same workflow may be mirrored
    into more than one target. ``body_sha256`` is the hash of the body last
    written to (or read from) disk, used to detect local edits on the next
    pass. ``read_only`` records that the caller holds only a view grant, so
    later pushes know not to attempt an upload.
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


def local_skill_path(target: SyncTarget, slug: str) -> Path:
    """Return the absolute ``SKILL.md`` path for ``slug`` under ``target``."""
    return expand_target_path(target.path) / slug / "SKILL.md"


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
    "up-to-date",
    "skipped-modified",
    "skipped-conflict",
    "skipped-unsafe-name",
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


def _write_skill_file(path: Path, body: str) -> None:
    """Write a workflow body to ``path`` with ordinary file permissions.

    Unlike the index/config JSON, a ``SKILL.md`` is content the agent loads, so
    it gets the user's normal umask rather than 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def pull(
    client: GoodeyeClient,
    config: SyncConfig,
    state: SyncState,
    *,
    slugs: list[str],
    target_path: str | None,
    force: bool,
    paths: ConfigPaths,
) -> PullResult:
    """Mirror registry workflows onto disk for the configured targets.

    For each target, lists the workflows in scope, then for each one decides
    whether to write it: an unchanged local copy is left alone, a locally
    edited copy is preserved unless ``force`` is set, and anything else is
    fetched and written verbatim. The index is updated in memory as each
    workflow is written and persisted in a ``finally`` even if a later fetch
    raises, so files written before a failure are tracked and a re-run resumes
    from where it left off rather than re-pulling them as untracked.
    """
    result = PullResult()
    targets = _targets_to_process(config, target_path)
    slug_args = list(slugs)

    try:
        for target in targets:
            summaries = _list_all_for_target(client, target)
            selected = select_for_target(target, summaries, slugs=slug_args or None)
            for summary in selected:
                result.items.append(_pull_one(client, state, target, summary, force=force))
    finally:
        # Persist whatever the index accumulated, including on a mid-loop raise:
        # any SKILL.md already written has a matching entry, so a re-run treats
        # it as tracked instead of clobbering or duplicating it.
        save_sync_state(state, paths)
    return result


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
    # caller forces it. A conflict means both sides moved relative to a recorded
    # sync point: a tracked entry whose local body diverged AND whose server
    # token advanced. An untracked file has no recorded base, so it is reported
    # as plain modified, never a conflict.
    tracked_edit = entry is not None and is_modified_locally(entry, local_body)
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
    # local file matches what we recorded. No fetch needed.
    if (
        entry is not None
        and not server_moved(entry, summary)
        and local_body is not None
        and not is_modified_locally(entry, local_body)
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
        ),
    )
    return PullItem(
        slug=slug,
        target_path=stored_target,
        action="pulled",
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
    local_body = read_local_body(target, entry.slug)
    modified = is_modified_locally(entry, local_body)
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


__all__ = [
    "PRESETS",
    "SLUG_RE",
    "SYNC_SCOPES",
    "PullAction",
    "PullItem",
    "PullResult",
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
    "expand_target_path",
    "find_entry",
    "is_modified_locally",
    "list_targets",
    "load_sync_config",
    "load_sync_state",
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
