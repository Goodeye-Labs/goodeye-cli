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
        "workflow_id": "wfl_001",
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


def test_file_state_tracks_executable(tmp_config_paths: ConfigPaths) -> None:
    """A FileState with executable=True round-trips True after save/load."""
    entry = _base_entry(files=[FileState(path="deploy.sh", sha256="deadbeef", executable=True)])
    state = SyncState(entries=[entry])

    save_sync_state(state, tmp_config_paths)
    reloaded = load_sync_state(tmp_config_paths)

    assert reloaded.entries[0].files[0].executable is True
