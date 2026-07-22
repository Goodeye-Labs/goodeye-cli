"""Tests for `goodeye skills put-file` and `goodeye skills rm-file`.

These commands change named paths in a hosted skill and keep the rest, unlike
`goodeye skills publish <dir>`, which replaces the whole tree. The suite covers
the content sources, how text and binary pick their wire field, how the
expected version token is resolved, how the server's errors surface, and the
local mirror refresh that keeps a later `sync push` from reporting drift that is
not real or sending the old copy back and reverting the change. That refresh is
version-aware: it moves the mirrors recorded at the version the change was
written against onto the version it produced, directory and index together, and
leaves mirrors at any other version for a pull.
"""

from __future__ import annotations

import base64
import hashlib
import json as _json
import os
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials
from goodeye_cli.errors import Conflict, Forbidden, GoodeyeError, NotFound, ValidationFailed
from goodeye_cli.sync import (
    FileState,
    SyncEntry,
    SyncState,
    SyncTarget,
    body_sha256,
    build_files_payload,
    is_modified_locally,
    load_sync_state,
    read_local_body,
    save_sync_state,
    tree_push_drifted,
)

SERVER = "https://example.test"

_SKILL_MD = "---\nname: my-skill\ndescription: A test skill.\n---\n\nDo the work.\n"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _detail_route(*, token: str = "tok-1", slug: str = "my-skill") -> respx.Route:
    """Mock the read `put-file`/`rm-file` use to resolve the current token."""
    return respx.get(f"{SERVER}/v1/skills/{slug}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "skl_01",
                "name": slug,
                "version": 2,
                "body": _SKILL_MD,
                "version_token": token,
            },
        )
    )


def _patch_route(
    *,
    slug: str = "my-skill",
    version: int = 3,
    token: str = "tok-2",
    changed: list[str] | None = None,
    deleted: list[str] | None = None,
    carried_forward: int = 2,
) -> respx.Route:
    return respx.patch(f"{SERVER}/v1/skills/{slug}/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "skill_id": "skl_01",
                "version": version,
                "version_token": token,
                "name": slug,
                "slug": slug,
                "changed": changed if changed is not None else ["notes.md"],
                "deleted": deleted if deleted is not None else [],
                "carried_forward": carried_forward,
                "authoring_notes": [],
            },
        )
    )


def _sent(route: respx.Route) -> dict:
    return _json.loads(route.calls.last.request.content.decode())


def _me_route(email: str = "owner@example.com") -> respx.Route:
    """Mock the read the sync identity guard makes before a push."""
    return respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": email})
    )


def _seed_target(path: str) -> None:
    """Configure a sync target through the CLI, as a user would."""
    add = CliRunner().invoke(app, ["skills", "sync", "target", "add", path, "--scope", "owned"])
    assert add.exit_code == 0, add.output


# ----- content sources -----


@respx.mock
def test_put_file_sends_local_text_file_inline(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--from-file sends the file's text through `content`, and names one path."""
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_text("fresh notes\n", encoding="utf-8")
    _detail_route()
    route = _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    sent = _sent(route)
    assert sent["files"] == [{"path": "notes.md", "content": "fresh notes\n"}]
    assert sent["delete_paths"] == []


@respx.mock
def test_put_file_reads_content_from_stdin(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """--stdin is the other content source, for generated agent output."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _detail_route()
    route = _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["skills", "put-file", "my-skill", "notes.md", "--stdin"],
        input="piped notes\n",
    )
    assert result.exit_code == 0, result.output

    sent = _sent(route)
    assert sent["files"] == [{"path": "notes.md", "content": "piped notes\n"}]


def test_put_file_rejects_both_content_sources(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--from-file and --stdin together is a usage error, not a silent winner."""
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_text("fresh notes\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local), "--stdin"],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)


def test_put_file_requires_a_content_source(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """Neither source given is a usage error: there is nothing to write."""
    _setup_creds(monkeypatch, tmp_config_paths)

    runner = CliRunner()
    result = runner.invoke(app, ["skills", "put-file", "my-skill", "notes.md"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)


def test_put_file_rejects_a_missing_local_file(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(tmp_path / "gone.md")],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)


# ----- text vs binary -----


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"test", id="text-that-looks-like-base64"),
        pytest.param("café\n".encode(), id="utf8-text"),
        pytest.param(b"\x00\x01\x02", id="nul-bytes"),
        pytest.param(b"\xff\xfe\xfa", id="invalid-utf8"),
    ],
)
@respx.mock
def test_put_file_picks_the_same_wire_field_as_the_snapshot_builder(
    raw: bytes, tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A binary file goes out as content_base64 while text goes through content.

    The choice must match the whole-tree snapshot payload builder exactly, so a
    file patched here and the same file uploaded by a directory publish are
    never sent through different channels.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "asset.bin"
    local.write_bytes(raw)
    _detail_route()
    route = _patch_route(changed=["asset.bin"])

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "asset.bin", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output
    sent_entry = _sent(route)["files"][0]

    skill_dir = tmp_path / "snapshot"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (skill_dir / "asset.bin").write_bytes(raw)
    snapshot_payload, _states = build_files_payload(skill_dir, None, [])
    snapshot_entry = next(e for e in snapshot_payload if e["path"] == "asset.bin")

    channels = {"content", "content_base64"}
    assert channels & set(sent_entry) == channels & set(snapshot_entry)
    if "content_base64" in sent_entry:
        assert sent_entry["content_base64"] == base64.b64encode(raw).decode("ascii")
    else:
        assert sent_entry["content"] == raw.decode("utf-8")


# ----- carry-forward flags -----


@respx.mock
def test_put_file_omits_flags_the_caller_did_not_pass(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """An unset flag must be absent from the wire entry, not sent as a default.

    On this route an absent `executable` or `purpose` means "keep what the file
    already had", so sending a default would silently reset a label the caller
    never mentioned.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_text("fresh notes\n", encoding="utf-8")
    _detail_route()
    route = _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    entry = _sent(route)["files"][0]
    assert "executable" not in entry
    assert "purpose" not in entry


@respx.mock
def test_put_file_sends_explicit_flags(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "run.sh"
    local.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _detail_route()
    route = _patch_route(changed=["run.sh"])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "skills",
            "put-file",
            "my-skill",
            "run.sh",
            "--from-file",
            str(local),
            "--executable",
            "--purpose",
            "script",
        ],
    )
    assert result.exit_code == 0, result.output

    entry = _sent(route)["files"][0]
    assert entry["executable"] is True
    assert entry["purpose"] == "script"


@respx.mock
def test_put_file_sends_no_executable_as_an_explicit_false(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "run.sh"
    local.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _detail_route()
    route = _patch_route(changed=["run.sh"])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "skills",
            "put-file",
            "my-skill",
            "run.sh",
            "--from-file",
            str(local),
            "--no-executable",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _sent(route)["files"][0]["executable"] is False


# ----- token resolution -----


@respx.mock
def test_put_file_resolves_the_current_token_with_a_read(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_text("fresh notes\n", encoding="utf-8")
    detail = _detail_route(token="tok-current")
    route = _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output
    assert detail.called
    assert _sent(route)["expected_version_token"] == "tok-current"


@respx.mock
def test_put_file_uses_a_supplied_token_without_reading(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--expected-version-token stays available for scripting and skips the read."""
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_text("fresh notes\n", encoding="utf-8")
    detail = _detail_route(token="tok-current")
    route = _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "skills",
            "put-file",
            "my-skill",
            "notes.md",
            "--from-file",
            str(local),
            "--expected-version-token",
            "tok-scripted",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not detail.called
    assert _sent(route)["expected_version_token"] == "tok-scripted"


@respx.mock
def test_rm_file_resolves_the_current_token_with_a_read(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    detail = _detail_route(token="tok-current")
    route = _patch_route(changed=[], deleted=["notes.md"])

    runner = CliRunner()
    result = runner.invoke(app, ["skills", "rm-file", "my-skill", "notes.md"])
    assert result.exit_code == 0, result.output
    assert detail.called

    sent = _sent(route)
    assert sent["expected_version_token"] == "tok-current"
    assert sent["files"] == []
    assert sent["delete_paths"] == ["notes.md"]


@respx.mock
def test_rm_file_uses_a_supplied_token_without_reading(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    detail = _detail_route(token="tok-current")
    route = _patch_route(changed=[], deleted=["notes.md"])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "skills",
            "rm-file",
            "my-skill",
            "notes.md",
            "--expected-version-token",
            "tok-scripted",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not detail.called
    assert _sent(route)["expected_version_token"] == "tok-scripted"


# ----- sync-index refresh -----


def _write_index(
    tmp_config_paths: ConfigPaths,
    *,
    slug: str,
    target_path: str,
    files: list[FileState],
    body: str = _SKILL_MD,
    skill_id: str = "skl_01",
) -> None:
    state = SyncState(
        identity="owner@example.com",
        entries=[
            SyncEntry(
                skill_id=skill_id,
                slug=slug,
                target_path=target_path,
                synced_version=2,
                version_token="tok-1",
                body_sha256=body_sha256(body),
                files=files,
            )
        ],
    )
    save_sync_state(state, tmp_config_paths)


@respx.mock
def test_put_file_updates_the_tracked_file_state(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A tracked path's recorded sha follows the write, keeping the label intact."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(tmp_path / "skills"),
        files=[
            FileState(
                path="notes.md", sha256=_sha(b"stale\n"), executable=True, purpose="reference"
            )
        ],
    )
    local = tmp_path / "notes.md"
    local.write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    recorded = {f.path: f for f in entry.files}
    assert recorded["notes.md"].sha256 == _sha(b"fresh notes\n")
    # Flags the caller did not pass carry the recorded value forward, matching
    # what the server does with the same absent fields.
    assert recorded["notes.md"].executable is True
    assert recorded["notes.md"].purpose == "reference"
    # The content and the sync point move together: an entry that claimed the
    # new content while still claiming the old version would send a superseded
    # token on the next push and be told it conflicts.
    assert entry.synced_version == 3
    assert entry.version_token == "tok-2"


@respx.mock
def test_put_file_leaves_a_manifest_new_path_out_of_the_record(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A path the manifest never held is not recorded on a guess.

    The response carries no per-file metadata, so there is nothing to record the
    executable mark and role label from. Writing invented values would be worse
    than recording nothing: a full push reads those flags back out of the index
    and would send the invented ones, clearing what the server holds. Left out,
    the path reads as ordinary drift that the next full push resolves from disk.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(tmp_path / "skills"),
        files=[FileState(path="other.md", sha256=_sha(b"other\n"))],
    )
    local = tmp_path / "notes.md"
    local.write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    recorded = {f.path: f for f in entry.files}
    assert set(recorded) == {"other.md"}
    assert recorded["other.md"].sha256 == _sha(b"other\n")
    # The version still moved: the registry did write a new version.
    assert entry.synced_version == 3
    assert entry.version_token == "tok-2"


@respx.mock
def test_put_file_leaves_the_index_alone_when_no_target_tracks_the_slug(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    _write_index(
        tmp_config_paths,
        slug="another-skill",
        skill_id="skl_other",
        target_path=str(tmp_path / "skills"),
        files=[FileState(path="notes.md", sha256=_sha(b"untouched\n"))],
    )
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")
    local = tmp_path / "notes.md"
    local.write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


@respx.mock
def test_put_file_on_the_runbook_updates_the_recorded_body_hash(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """SKILL.md is the body, not a sibling, so it never enters the file manifest."""
    _setup_creds(monkeypatch, tmp_config_paths)
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(tmp_path / "skills"),
        files=[FileState(path="notes.md", sha256=_sha(b"notes\n"))],
    )
    new_body = _SKILL_MD + "\nOne more step.\n"
    local = tmp_path / "SKILL.md"
    local.write_text(new_body, encoding="utf-8")
    _detail_route()
    _patch_route(changed=["SKILL.md"])

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "SKILL.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.body_sha256 == body_sha256(new_body)
    assert [f.path for f in entry.files] == ["notes.md"]


@respx.mock
def test_put_file_updates_every_target_mirroring_the_skill(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """One skill mirrored into two targets has both copies recorded."""
    _setup_creds(monkeypatch, tmp_config_paths)
    stale = FileState(path="notes.md", sha256=_sha(b"stale\n"))
    state = SyncState(
        identity="owner@example.com",
        entries=[
            SyncEntry(
                skill_id="skl_01",
                slug="my-skill",
                target_path=str(tmp_path / "claude"),
                synced_version=2,
                version_token="tok-1",
                body_sha256=body_sha256(_SKILL_MD),
                files=[stale],
            ),
            SyncEntry(
                skill_id="skl_01",
                slug="my-skill",
                target_path=str(tmp_path / "agents"),
                synced_version=2,
                version_token="tok-1",
                body_sha256=body_sha256(_SKILL_MD),
                files=[stale],
            ),
        ],
    )
    save_sync_state(state, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    reloaded = load_sync_state(tmp_config_paths)
    assert [entry.files[0].sha256 for entry in reloaded.entries] == [
        _sha(b"fresh notes\n"),
        _sha(b"fresh notes\n"),
    ]
    assert [entry.version_token for entry in reloaded.entries] == ["tok-2", "tok-2"]


@respx.mock
def test_put_file_leaves_a_target_recorded_at_another_version_untouched(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Two mirrors of one skill can sit at different versions.

    The change is written against one version, so only the mirrors recorded at
    that version describe the content it started from. A mirror left behind at
    an older version has a different base: recording the new file state on it
    would claim content it was never given, and moving its sync point forward
    would claim a version it never received. It is left for a pull.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    behind = SyncEntry(
        skill_id="skl_01",
        slug="my-skill",
        target_path=str(tmp_path / "agents"),
        synced_version=1,
        version_token="tok-older",
        body_sha256=body_sha256("an older body\n"),
        files=[FileState(path="notes.md", sha256=_sha(b"older\n"), purpose="reference")],
    )
    state = SyncState(
        identity="owner@example.com",
        entries=[
            SyncEntry(
                skill_id="skl_01",
                slug="my-skill",
                target_path=str(tmp_path / "claude"),
                synced_version=2,
                version_token="tok-1",
                body_sha256=body_sha256(_SKILL_MD),
                files=[FileState(path="notes.md", sha256=_sha(b"stale\n"))],
            ),
            behind,
        ],
    )
    save_sync_state(state, tmp_config_paths)
    before_behind = behind.model_dump()
    local = tmp_path / "notes.md"
    local.write_bytes(b"fresh notes\n")
    _detail_route(token="tok-1")
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(local)]
    )
    assert result.exit_code == 0, result.output

    reloaded = {entry.target_path: entry for entry in load_sync_state(tmp_config_paths).entries}
    current = reloaded[str(tmp_path / "claude")]
    assert current.files[0].sha256 == _sha(b"fresh notes\n")
    assert current.synced_version == 3
    assert current.version_token == "tok-2"
    # Nothing about the older mirror changed, not the file state and not the
    # version it reports being synced at.
    assert reloaded[str(tmp_path / "agents")].model_dump() == before_behind


@respx.mock
def test_rm_file_drops_the_tracked_file_state(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(tmp_path / "skills"),
        files=[
            FileState(path="notes.md", sha256=_sha(b"notes\n")),
            FileState(path="other.md", sha256=_sha(b"other\n")),
        ],
    )
    _detail_route()
    _patch_route(changed=[], deleted=["notes.md"])

    runner = CliRunner()
    result = runner.invoke(app, ["skills", "rm-file", "my-skill", "notes.md"])
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    assert [f.path for f in entry.files] == ["other.md"]
    assert entry.synced_version == 3
    assert entry.version_token == "tok-2"


@respx.mock
def test_rm_file_leaves_the_index_alone_when_no_target_tracks_the_slug(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    _write_index(
        tmp_config_paths,
        slug="another-skill",
        skill_id="skl_other",
        target_path=str(tmp_path / "skills"),
        files=[FileState(path="notes.md", sha256=_sha(b"untouched\n"))],
    )
    before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")
    _detail_route()
    _patch_route(changed=[], deleted=["notes.md"])

    runner = CliRunner()
    result = runner.invoke(app, ["skills", "rm-file", "my-skill", "notes.md"])
    assert result.exit_code == 0, result.output
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == before


@respx.mock
def test_put_file_from_a_mirrored_directory_leaves_no_push_drift(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The whole point: writing a mirrored file must not manufacture drift.

    Editing a file inside a sync target and sending it with put-file leaves the
    server holding exactly what is on disk, so the next push must see nothing to
    do. Without the index refresh the recorded sha stays stale and push reports
    a change that is not real.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    slug_dir = target_dir / "my-skill"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (slug_dir / "notes.md").write_bytes(b"stale\n")
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(target_dir),
        files=[FileState(path="notes.md", sha256=_sha(b"stale\n"))],
    )
    target = SyncTarget(path=str(target_dir), scope="owned")
    assert not tree_push_drifted(load_sync_state(tmp_config_paths).entries[0], target, [])

    # The user edits the mirrored file and sends just that path.
    (slug_dir / "notes.md").write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(slug_dir / "notes.md")],
    )
    assert result.exit_code == 0, result.output

    assert not tree_push_drifted(load_sync_state(tmp_config_paths).entries[0], target, [])


@respx.mock
def test_put_file_from_outside_the_mirror_updates_the_local_copy(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Content from anywhere else still lands in the mirrored directory.

    The push builds its snapshot from that directory, so a copy left holding the
    old bytes is sent straight back and reverts the change, and the pull that
    would repair it sees a sync point already at the new version and refuses the
    directory as locally modified instead.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    slug_dir = target_dir / "my-skill"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (slug_dir / "notes.md").write_bytes(b"stale\n")
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(target_dir),
        files=[
            FileState(
                path="notes.md", sha256=_sha(b"stale\n"), executable=True, purpose="reference"
            )
        ],
    )
    source = tmp_path / "elsewhere" / "notes.md"
    source.parent.mkdir()
    source.write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(source)]
    )
    assert result.exit_code == 0, result.output

    assert (slug_dir / "notes.md").read_bytes() == b"fresh notes\n"
    # The mark the manifest already held is applied to the copy, so the file on
    # disk carries what the registry holds rather than the source file's mode.
    assert os.stat(slug_dir / "notes.md").st_mode & 0o100
    target = SyncTarget(path=str(target_dir), scope="owned")
    assert not tree_push_drifted(load_sync_state(tmp_config_paths).entries[0], target, [])


@respx.mock
def test_put_file_on_the_runbook_from_outside_the_mirror_rewrites_it(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The runbook is the body, and its mirrored copy has to move with it too."""
    _setup_creds(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    slug_dir = target_dir / "my-skill"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    _write_index(tmp_config_paths, slug="my-skill", target_path=str(target_dir), files=[])
    new_body = _SKILL_MD + "\nOne more step.\n"
    source = tmp_path / "elsewhere" / "SKILL.md"
    source.parent.mkdir()
    source.write_text(new_body, encoding="utf-8")
    _detail_route()
    _patch_route(changed=["SKILL.md"])

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "SKILL.md", "--from-file", str(source)]
    )
    assert result.exit_code == 0, result.output

    target = SyncTarget(path=str(target_dir), scope="owned")
    assert read_local_body(target, "my-skill") == new_body
    assert not tree_push_drifted(load_sync_state(tmp_config_paths).entries[0], target, [])


@respx.mock
def test_put_file_creates_no_local_copy_where_none_was_mirrored(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A target whose directory was never materialized is left for a pull.

    Writing a lone file into a directory that holds no skill would leave a
    fragment with no runbook beside it, which the pull path already handles by
    fetching the whole thing.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(target_dir),
        files=[FileState(path="notes.md", sha256=_sha(b"stale\n"))],
    )
    source = tmp_path / "notes.md"
    source.write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()

    runner = CliRunner()
    result = runner.invoke(
        app, ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(source)]
    )
    assert result.exit_code == 0, result.output

    assert not target_dir.exists()
    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.version_token == "tok-2"


@respx.mock
def test_rm_file_removes_the_local_copy(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A removal has to reach the directory, or the next push puts the file back.

    A file left on disk after it is gone from the registry is part of the
    snapshot the push builds, so it is uploaded again and the removal is undone.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    slug_dir = target_dir / "my-skill"
    (slug_dir / "references").mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (slug_dir / "references" / "rubric.md").write_bytes(b"rubric\n")
    (slug_dir / "other.md").write_bytes(b"other\n")
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(target_dir),
        files=[
            FileState(path="other.md", sha256=_sha(b"other\n")),
            FileState(path="references/rubric.md", sha256=_sha(b"rubric\n")),
        ],
    )
    _detail_route()
    _patch_route(changed=[], deleted=["references/rubric.md"])

    runner = CliRunner()
    result = runner.invoke(app, ["skills", "rm-file", "my-skill", "references/rubric.md"])
    assert result.exit_code == 0, result.output

    assert not (slug_dir / "references" / "rubric.md").exists()
    # Only the named path goes: every other file in the directory stays.
    assert (slug_dir / "other.md").read_bytes() == b"other\n"
    target = SyncTarget(path=str(target_dir), scope="owned")
    assert not tree_push_drifted(load_sync_state(tmp_config_paths).entries[0], target, [])


@respx.mock
def test_put_file_then_a_further_local_edit_pushes_cleanly(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The ordinary authoring loop: send one file, keep editing, then push.

    The push must go out against the version the change created. Sending the
    superseded one is rejected by the server, and the resulting conflict has no
    way out: the pull it points at refuses while a local edit is present, so the
    only unblock is a forced pull, which discards that edit. No second writer
    appears in this story, so any conflict here would be manufactured.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    _me_route()
    target_dir = tmp_path / "skills"
    _seed_target(str(target_dir))
    slug_dir = target_dir / "my-skill"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (slug_dir / "notes.md").write_bytes(b"stale\n")
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(target_dir),
        files=[FileState(path="notes.md", sha256=_sha(b"stale\n"))],
    )

    (slug_dir / "notes.md").write_bytes(b"fresh notes\n")
    _detail_route()
    _patch_route()
    runner = CliRunner()
    patched = runner.invoke(
        app,
        ["skills", "put-file", "my-skill", "notes.md", "--from-file", str(slug_dir / "notes.md")],
    )
    assert patched.exit_code == 0, patched.output

    # The author keeps working: one more edit before pushing.
    (slug_dir / "SKILL.md").write_text(_SKILL_MD + "\nOne more step.\n", encoding="utf-8")
    save_route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            200,
            json={
                "skill_id": "skl_01",
                "version": 4,
                "name": "my-skill",
                "version_token": "tok-3",
                "verifiers": [],
            },
        )
    )
    pushed = runner.invoke(app, ["skills", "sync", "push"])
    assert pushed.exit_code == 0, pushed.output

    item = _json.loads(pushed.output)["items"][0]
    assert item["action"] == "pushed"
    assert _sent(save_route)["expected_version_token"] == "tok-2"


@respx.mock
def test_put_file_on_a_crlf_runbook_records_the_hash_a_read_recomputes(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A runbook with CRLF line endings must not be left drifting forever.

    The recorded body hash is compared against one recomputed from the body read
    back as text, and that read translates CRLF to LF. Recording the hash of the
    bytes as sent would never match it again, so the entry would report drift on
    every status and push with no local edit behind it.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    slug_dir = target_dir / "my-skill"
    slug_dir.mkdir(parents=True)
    crlf_body = _SKILL_MD.replace("\n", "\r\n")
    (slug_dir / "SKILL.md").write_bytes(crlf_body.encode("utf-8"))
    _write_index(
        tmp_config_paths,
        slug="my-skill",
        target_path=str(target_dir),
        files=[],
        body="an older body\n",
    )
    _detail_route()
    _patch_route(changed=["SKILL.md"])

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["skills", "put-file", "my-skill", "SKILL.md", "--from-file", str(slug_dir / "SKILL.md")],
    )
    assert result.exit_code == 0, result.output

    entry = load_sync_state(tmp_config_paths).entries[0]
    target = SyncTarget(path=str(target_dir), scope="owned")
    assert not is_modified_locally(entry, read_local_body(target, "my-skill"))
    assert not tree_push_drifted(entry, target, [])


# ----- server errors -----


@pytest.mark.parametrize(
    ("status", "slug", "expected"),
    [
        pytest.param(400, "validation_error", ValidationFailed, id="rejected-request"),
        pytest.param(409, "conflict", Conflict, id="version-moved"),
        pytest.param(403, "forbidden", Forbidden, id="no-edit-access"),
        pytest.param(404, "not_found", NotFound, id="unknown-skill"),
    ],
)
@pytest.mark.parametrize("command", ["put-file", "rm-file"])
@respx.mock
def test_file_commands_surface_server_errors(
    command: str,
    status: int,
    slug: str,
    expected: type[GoodeyeError],
    tmp_path: Path,
    tmp_config_paths: ConfigPaths,
    monkeypatch,
) -> None:
    """A rejected change fails the command with the server's own error."""
    _setup_creds(monkeypatch, tmp_config_paths)
    local = tmp_path / "notes.md"
    local.write_bytes(b"fresh notes\n")
    _detail_route()
    respx.patch(f"{SERVER}/v1/skills/my-skill/files").mock(
        return_value=httpx.Response(status, json={"error": slug, "message": "Nope."})
    )

    args = ["skills", command, "my-skill", "notes.md"]
    if command == "put-file":
        args += ["--from-file", str(local)]

    result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    assert isinstance(result.exception, expected)
    assert result.exception.slug == slug


# ----- help text -----


def _help_text(plain, command: str) -> str:
    """Return the command's help as one line, so wrapping cannot hide a phrase."""
    result = CliRunner().invoke(app, ["skills", command, "--help"])
    assert result.exit_code == 0, result.output
    return " ".join(plain(result.output).split())


def test_put_file_help_contrasts_with_publish(plain) -> None:
    """Naming publish is not enough: the help has to say how the two differ."""
    text = _help_text(plain, "put-file")
    assert "Only the path you name changes" in text
    assert "the rest of the skill's files ride forward untouched" in text
    assert "unlike `goodeye skills publish <dir>`, which replaces the whole tree" in text
    assert "any path missing from the directory is deleted" in text
    assert "—" not in text


def test_rm_file_help_contrasts_with_publish(plain) -> None:
    text = _help_text(plain, "rm-file")
    assert "Only the path you name is removed" in text
    assert "the rest of the skill's files ride forward untouched" in text
    assert "unlike `goodeye skills publish <dir>`, which replaces the whole tree" in text
    assert "any path missing from the directory is deleted" in text
    assert "—" not in text
