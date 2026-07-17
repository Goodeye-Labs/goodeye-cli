"""Tests for per-file sync state tracking in SyncEntry.

Covers the FileState model and the `files` field on SyncEntry:
- round-trip through save/load preserves the list intact
- old state files without a `files` key load cleanly with files==[]
- the executable bit round-trips correctly
"""

from __future__ import annotations

import json

from goodeye_cli.config import ConfigPaths
from goodeye_cli.sync import (
    FileState,
    SyncEntry,
    SyncState,
    body_sha256,
    load_sync_state,
    save_sync_state,
)


def _base_entry(**overrides: object) -> SyncEntry:
    """Build a minimal SyncEntry for use in tests."""
    defaults: dict[str, object] = {
        "skill_id": "wfl_001",
        "slug": "my-workflow",
        "target_path": "~/.claude/skills",
        "synced_version": 1,
        "version_token": "tok-abc",
        "body_sha256": body_sha256("some body text"),
    }
    defaults.update(overrides)
    return SyncEntry(**defaults)  # type: ignore[arg-type]


def test_sync_entry_round_trips_file_states(tmp_config_paths: ConfigPaths) -> None:
    """FileState entries written to disk survive a save/load cycle intact."""
    files = [
        FileState(path="config.yaml", sha256="aabbcc", executable=False),
        FileState(path="run.sh", sha256="112233", executable=True),
    ]
    entry = _base_entry(files=files)
    state = SyncState(entries=[entry])

    save_sync_state(state, tmp_config_paths)
    reloaded = load_sync_state(tmp_config_paths)

    assert len(reloaded.entries) == 1
    reloaded_entry = reloaded.entries[0]
    assert len(reloaded_entry.files) == 2

    assert reloaded_entry.files[0].path == "config.yaml"
    assert reloaded_entry.files[0].sha256 == "aabbcc"
    assert reloaded_entry.files[0].executable is False

    assert reloaded_entry.files[1].path == "run.sh"
    assert reloaded_entry.files[1].sha256 == "112233"
    assert reloaded_entry.files[1].executable is True


def test_old_state_without_files_loads_empty(tmp_config_paths: ConfigPaths) -> None:
    """A sync-state.json written by an older CLI (no `files` key) loads fine.

    The resulting entry must have files==[] and all other fields intact.
    """
    old_state = {
        "version": 1,
        "identity": "user@example.com",
        "entries": [
            {
                "workflow_id": "wfl_legacy",
                "slug": "legacy-workflow",
                "target_path": "~/.claude/skills",
                "synced_version": 2,
                "version_token": "tok-legacy",
                "body_sha256": body_sha256("legacy body"),
                "verifier_bindings": [],
                "effective_role": "owner",
                "read_only": False,
                # Intentionally NO `files` key
            }
        ],
    }

    tmp_config_paths.sync_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_config_paths.sync_state_file.write_text(json.dumps(old_state), encoding="utf-8")

    loaded = load_sync_state(tmp_config_paths)

    assert loaded.identity == "user@example.com"
    assert len(loaded.entries) == 1
    entry = loaded.entries[0]
    assert entry.slug == "legacy-workflow"
    assert entry.files == []


def test_old_workflow_id_key_loads_operates_and_rewrites_as_skill_id(
    tmp_config_paths: ConfigPaths,
) -> None:
    """An index written under the old `workflow_id` key loads, operates, and
    is silently rewritten under `skill_id` on the next save.

    This is a real round-trip through the on-disk file, not a unit test of
    the migration helper alone: no caller has to touch the file by hand, and
    a second load of the rewritten file still resolves the same entry.
    """
    old_state = {
        "version": 1,
        "identity": "user@example.com",
        "entries": [
            {
                "workflow_id": "wfl_legacy",
                "slug": "legacy-workflow",
                "target_path": "~/.claude/skills",
                "synced_version": 2,
                "version_token": "tok-legacy",
                "body_sha256": body_sha256("legacy body"),
                "verifier_bindings": [],
                "effective_role": "owner",
                "read_only": False,
            }
        ],
    }
    tmp_config_paths.sync_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_config_paths.sync_state_file.write_text(json.dumps(old_state), encoding="utf-8")

    loaded = load_sync_state(tmp_config_paths)
    entry = loaded.entries[0]
    assert entry.skill_id == "wfl_legacy"
    assert entry.slug == "legacy-workflow"

    # A save (as any ordinary pull or push pass performs) rewrites the file.
    # No prompt, no flag, no user action: the caller just kept working.
    save_sync_state(loaded, tmp_config_paths)
    on_disk = json.loads(tmp_config_paths.sync_state_file.read_text(encoding="utf-8"))
    rewritten_entry = on_disk["entries"][0]
    assert rewritten_entry["skill_id"] == "wfl_legacy"
    assert "workflow_id" not in rewritten_entry

    # The rewritten file loads cleanly on its own, with the same identity.
    reloaded = load_sync_state(tmp_config_paths)
    assert reloaded.entries[0].skill_id == "wfl_legacy"


def test_entry_with_both_id_keys_keeps_skill_id_and_drops_workflow_id(
    tmp_config_paths: ConfigPaths,
) -> None:
    """An entry carrying both the old and new id keys resolves to `skill_id`.

    The degenerate case (a mixed index with `workflow_id` and `skill_id` in the
    same entry) must keep the canonical `skill_id`, drop the stray
    `workflow_id`, and rewrite cleanly on save. This locks in the migration's
    idempotent skill-id-wins behavior so a later change to the load path cannot
    silently let the legacy key overwrite the current one.
    """
    mixed_state = {
        "version": 1,
        "identity": "user@example.com",
        "entries": [
            {
                "workflow_id": "wfl_stale",
                "skill_id": "wfl_current",
                "slug": "mixed-workflow",
                "target_path": "~/.claude/skills",
                "synced_version": 3,
                "version_token": "tok-mixed",
                "body_sha256": body_sha256("mixed body"),
                "verifier_bindings": [],
                "effective_role": "owner",
                "read_only": False,
            }
        ],
    }
    tmp_config_paths.sync_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_config_paths.sync_state_file.write_text(json.dumps(mixed_state), encoding="utf-8")

    loaded = load_sync_state(tmp_config_paths)
    assert loaded.entries[0].skill_id == "wfl_current"

    save_sync_state(loaded, tmp_config_paths)
    on_disk = json.loads(tmp_config_paths.sync_state_file.read_text(encoding="utf-8"))
    rewritten_entry = on_disk["entries"][0]
    assert rewritten_entry["skill_id"] == "wfl_current"
    assert "workflow_id" not in rewritten_entry


def test_file_state_tracks_executable(tmp_config_paths: ConfigPaths) -> None:
    """A FileState with executable=True round-trips True after save/load."""
    entry = _base_entry(files=[FileState(path="deploy.sh", sha256="deadbeef", executable=True)])
    state = SyncState(entries=[entry])

    save_sync_state(state, tmp_config_paths)
    reloaded = load_sync_state(tmp_config_paths)

    assert reloaded.entries[0].files[0].executable is True
