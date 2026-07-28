"""Tests for the sync point `goodeye skills publish` records.

A publish moves the registry. When the directory it publishes is a tracked
mirror, or the skill it names is mirrored somewhere, the local index has to
move with it: a recorded sync point left on the superseded version reads as a
local edit and a moved server at once, which is the `conflict` state that no
pull or push can clear.
"""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli import sync as sync_module
from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials
from goodeye_cli.sync import (
    FileState,
    SyncEntry,
    SyncState,
    SyncTarget,
    body_sha256,
    load_sync_state,
    save_sync_state,
    tree_push_drifted,
)

SERVER = "https://example.test"

_SKILL_MD = (
    "---\n"
    "name: my-skill\n"
    "description: A test skill.\n"
    "outcome: Achieve the test outcome.\n"
    "---\n\n"
    "Do the work.\n"
)


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _save_route(*, version: int = 3, token: str = "tok-new", workflow_id: str = "skl_01"):
    """Mock the save endpoint, returning the version the publish created."""
    return respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": workflow_id,
                "version": version,
                "version_token": token,
                "name": "my-skill",
                "verifiers": [],
            },
        )
    )


def _mirror(tmp_path: Path, *, slug: str = "my-skill", body: str = _SKILL_MD) -> Path:
    """Materialize `<target>/<slug>/` with a SKILL.md and one sibling file."""
    skill_dir = tmp_path / "skills" / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    (skill_dir / "notes.md").write_bytes(b"notes\n")
    return skill_dir


def _track(
    tmp_config_paths: ConfigPaths,
    tmp_path: Path,
    *,
    slug: str = "my-skill",
    skill_id: str = "skl_01",
    synced_version: int = 2,
    version_token: str = "tok-old",
    body: str = _SKILL_MD,
    files: list[FileState] | None = None,
    target_dir: str = "skills",
    extra: list[SyncEntry] | None = None,
) -> None:
    """Record `<target>/<slug>` in the index as a mirror synced at a version."""
    entry = SyncEntry(
        skill_id=skill_id,
        slug=slug,
        target_path=str(tmp_path / target_dir),
        synced_version=synced_version,
        version_token=version_token,
        body_sha256=body_sha256(body),
        files=files if files is not None else [FileState(path="notes.md", sha256=_sha(b"notes\n"))],
    )
    save_sync_state(
        SyncState(identity="owner@example.com", entries=[entry, *(extra or [])]),
        tmp_config_paths,
    )


def _target(tmp_path: Path, target_dir: str = "skills") -> SyncTarget:
    return SyncTarget(path=str(tmp_path / target_dir))


# ----- directory mode -----


@respx.mock
def test_folder_publish_leaves_the_mirror_clean(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The published directory's mirror lands on the version it just created."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path)
    # The local edit that makes the pre-fix index read as a conflict.
    (skill_dir / "notes.md").write_bytes(b"edited notes\n")
    _save_route(version=3, token="tok-new")

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.synced_version == 3
    assert entry.version_token == "tok-new"
    assert not tree_push_drifted(entry, _target(tmp_path), [])


@respx.mock
def test_repeated_folder_publishes_stay_clean(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Publishing twice never drifts into a conflict: the latch cannot form."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path)
    runner = CliRunner()

    for version, token in ((3, "tok-3"), (4, "tok-4")):
        (skill_dir / "notes.md").write_bytes(f"round {version}\n".encode())
        respx.reset()
        _save_route(version=version, token=token)
        result = runner.invoke(app, ["skills", "publish", str(skill_dir)])
        assert result.exit_code == 0, result.output

        entry = load_sync_state(tmp_config_paths).entries[0]
        assert entry.synced_version == version
        assert entry.version_token == token
        assert not tree_push_drifted(entry, _target(tmp_path), [])


@respx.mock
def test_folder_publish_recovers_a_mirror_left_behind(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A mirror stranded at an old version is recovered, not left for a forced pull.

    The whole tree was uploaded, so the registry holds what the directory
    holds regardless of the version the index was sitting at.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path, synced_version=22, version_token="tok-22")
    (skill_dir / "notes.md").write_bytes(b"local work\n")
    _save_route(version=29, token="tok-29")

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.synced_version == 29
    assert not tree_push_drifted(entry, _target(tmp_path), [])


@respx.mock
def test_folder_publish_of_an_untracked_directory_leaves_the_index_alone(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A directory no target tracks writes nothing to the index."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path)
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")

    loose = tmp_path / "elsewhere" / "my-skill"
    loose.mkdir(parents=True)
    (loose / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    _save_route()

    result = CliRunner().invoke(app, ["skills", "publish", str(loose)])
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


@respx.mock
def test_folder_publish_under_another_name_leaves_the_index_alone(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Republished as a different skill, the mirror's own sync point is untouched."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path)
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")
    # A different name resolves to a different skill server-side.
    _save_route(workflow_id="skl_other")

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir), "--name", "other-skill"])
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


@respx.mock
def test_a_second_mirror_of_the_same_skill_is_not_marked_clean(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Only the published copy moves; another target's copy waits for a pull."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    other = SyncEntry(
        skill_id="skl_01",
        slug="my-skill",
        target_path=str(tmp_path / "second"),
        synced_version=2,
        version_token="tok-old",
        body_sha256=body_sha256(_SKILL_MD),
        files=[],
    )
    _track(tmp_config_paths, tmp_path, extra=[other])
    _save_route(version=3, token="tok-new")

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    entries = {e.target_path: e for e in load_sync_state(tmp_config_paths).entries}
    assert entries[str(tmp_path / "skills")].synced_version == 3
    second = entries[str(tmp_path / "second")]
    assert second.synced_version == 2
    assert second.version_token == "tok-old"


# ----- file labels -----


@respx.mock
def test_folder_publish_keeps_recorded_file_labels(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A label the registry already held survives the snapshot upload.

    A label lives only in the registry, so a snapshot that omits it clears it.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(
        tmp_config_paths,
        tmp_path,
        files=[FileState(path="notes.md", sha256=_sha(b"notes\n"), purpose="reference")],
    )
    route = _save_route()

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    uploaded = {f["path"]: f for f in sent["files"]}
    assert uploaded["notes.md"]["purpose"] == "reference"
    # The index keeps it too, so the next push re-sends it.
    recorded = {f.path: f for f in load_sync_state(tmp_config_paths).entries[0].files}
    assert recorded["notes.md"].purpose == "reference"


@respx.mock
def test_folder_publish_does_not_invent_a_label(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A file the mirror never recorded a label for is sent without one."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    (skill_dir / "fresh.md").write_bytes(b"brand new\n")
    _track(
        tmp_config_paths,
        tmp_path,
        files=[FileState(path="notes.md", sha256=_sha(b"notes\n"), purpose="reference")],
    )
    route = _save_route()

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    uploaded = {
        f["path"]: f for f in _json.loads(route.calls.last.request.content.decode())["files"]
    }
    assert "purpose" not in uploaded["fresh.md"]


# ----- file execute bit -----


@respx.mock
def test_folder_publish_keeps_a_recorded_execute_bit(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """An unchanged file keeps the execute bit the registry already held.

    A Windows checkout or a FAT/exFAT mount reports every file as
    non-executable, so reading the bit off disk would clear a flag the registry
    rightly holds and then record that loss in the manifest.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    (skill_dir / "notes.md").chmod(0o644)
    _track(
        tmp_config_paths,
        tmp_path,
        files=[FileState(path="notes.md", sha256=_sha(b"notes\n"), executable=True)],
    )
    route = _save_route()

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    uploaded = {
        f["path"]: f for f in _json.loads(route.calls.last.request.content.decode())["files"]
    }
    assert uploaded["notes.md"]["executable"] is True
    recorded = {f.path: f for f in load_sync_state(tmp_config_paths).entries[0].files}
    assert recorded["notes.md"].executable is True


@respx.mock
def test_folder_publish_takes_the_local_bit_for_a_changed_file(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A file whose content changed carries the bit disk reports now.

    Its content, and any permission meant to go with it, is what is being
    uploaded, so the recorded bit is no longer the better answer.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    (skill_dir / "notes.md").write_bytes(b"edited notes\n")
    (skill_dir / "notes.md").chmod(0o644)
    _track(
        tmp_config_paths,
        tmp_path,
        files=[FileState(path="notes.md", sha256=_sha(b"notes\n"), executable=True)],
    )
    route = _save_route()

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    uploaded = {
        f["path"]: f for f in _json.loads(route.calls.last.request.content.decode())["files"]
    }
    assert uploaded["notes.md"]["executable"] is False
    recorded = {f.path: f for f in load_sync_state(tmp_config_paths).entries[0].files}
    assert recorded["notes.md"].executable is False


# ----- piped mode -----

_NEW_BODY = _SKILL_MD.replace("Do the work.", "Do the improved work.")


@respx.mock
def test_piped_publish_updates_a_clean_mirror(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """With nothing unsaved on disk, the mirror takes the published body."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path, version_token="tok-old")
    _save_route(version=3, token="tok-new")

    result = CliRunner().invoke(
        app,
        ["skills", "publish", "-", "--expected-version-token", "tok-old"],
        input=_NEW_BODY,
    )
    assert result.exit_code == 0, result.output

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == _NEW_BODY
    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.synced_version == 3
    assert entry.version_token == "tok-new"
    assert entry.body_sha256 == body_sha256(_NEW_BODY)


@respx.mock
def test_piped_publish_keeps_unsaved_local_edits(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Unsaved work on disk is never overwritten; it becomes drift to push."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path, version_token="tok-old")
    in_flight = _SKILL_MD.replace("Do the work.", "My own unsaved edit.")
    (skill_dir / "SKILL.md").write_text(in_flight, encoding="utf-8")
    _save_route(version=3, token="tok-new")

    result = CliRunner().invoke(
        app,
        ["skills", "publish", "-", "--expected-version-token", "tok-old"],
        input=_NEW_BODY,
    )
    assert result.exit_code == 0, result.output

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == in_flight
    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.synced_version == 3
    # Measured against what the registry now holds, so the surviving edit reads
    # as ordinary drift a push resolves rather than a conflict.
    assert entry.body_sha256 == body_sha256(_NEW_BODY)


@respx.mock
def test_piped_publish_without_a_token_leaves_the_index_alone(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """With no token there is no way to tell which version was replaced."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path)
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")
    _save_route()

    result = CliRunner().invoke(app, ["skills", "publish", "-"], input=_NEW_BODY)
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


@respx.mock
def test_piped_publish_with_a_stale_token_leaves_the_index_alone(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A mirror on some other version has a different base and waits for a pull."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path, version_token="tok-old")
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")
    _save_route()

    result = CliRunner().invoke(
        app,
        ["skills", "publish", "-", "--expected-version-token", "tok-somewhere-else"],
        input=_NEW_BODY,
    )
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


@respx.mock
def test_piped_publish_clearing_files_leaves_the_index_alone(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Clearing the tree drops files the mirror still lists: a pull reconciles it."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path, version_token="tok-old")
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")
    _save_route()

    result = CliRunner().invoke(
        app,
        [
            "skills",
            "publish",
            "-",
            "--clear-files",
            "--expected-version-token",
            "tok-old",
        ],
        input=_NEW_BODY,
    )
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


# ----- best effort -----


def _boom(*_args, **_kwargs):
    raise OSError("no")


@respx.mock
def test_folder_publish_survives_an_unreadable_index(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """An index that cannot be read says so rather than failing the publish.

    The registry write has already landed by the time the index is touched, so
    raising here would report a completed publish as a failure.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_dir = _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path)
    _save_route()
    monkeypatch.setattr(sync_module, "load_sync_state", _boom)

    result = CliRunner().invoke(app, ["skills", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output
    assert "could not be updated" in result.stderr


@respx.mock
def test_piped_publish_survives_an_unwritable_mirror(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A mirror that cannot be written warns, for the same reason."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _mirror(tmp_path)
    _track(tmp_config_paths, tmp_path, version_token="tok-old")
    _save_route(version=3, token="tok-new")
    monkeypatch.setattr(sync_module, "_write_mirrored_file", _boom)

    result = CliRunner().invoke(
        app,
        ["skills", "publish", "-", "--expected-version-token", "tok-old"],
        input=_NEW_BODY,
    )
    assert result.exit_code == 0, result.output
    assert "could not be updated" in result.stderr
