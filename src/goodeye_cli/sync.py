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

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from goodeye_cli.config import ConfigPaths, _load_json, _write_json_0600
from goodeye_cli.errors import Conflict, ValidationFailed

SyncScope = Literal["owned", "all", "selected"]

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


__all__ = [
    "PRESETS",
    "SYNC_SCOPES",
    "SyncConfig",
    "SyncScope",
    "SyncTarget",
    "add_target",
    "expand_target_path",
    "list_targets",
    "load_sync_config",
    "normalize_target_path",
    "remove_target",
    "resolve_preset",
    "save_sync_config",
]
