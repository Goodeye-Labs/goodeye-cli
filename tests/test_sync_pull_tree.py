"""Tests for multi-file skill directory sync.

Covers:
- Writing the full file tree (SKILL.md + siblings) on pull.
- Applying executable bits.
- Incremental fetch (only changed siblings are re-fetched).
- Safe deletion (only tracked files removed; untracked files survive).
- rmdir of empty slug dir after deletion; non-empty slug dir left in place.
- Un-pushed sibling edit blocks deletion.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any

import httpx
import respx

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths
from goodeye_cli.sync import (
    FileState,
    SyncConfig,
    SyncEntry,
    SyncState,
    SyncTarget,
    body_sha256,
    load_sync_state,
    normalize_target_path,
    pull,
)

SERVER = "https://example.test"

# ---- shared helpers --------------------------------------------------------


def _me_route(email: str = "owner@example.com") -> respx.Route:
    return respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": email})
    )


def _list_response(items: list[dict], next_cursor: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"items": items, "next_cursor": next_cursor})


def _detail_response(
    *,
    id_: str,
    name: str,
    body: str,
    version: int = 1,
    token: str = "tok",
    effective_role: str = "owner",
    files: list[dict] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": id_,
            "name": name,
            "version": version,
            "body": body,
            "version_token": token,
            "effective_role": effective_role,
            "verifiers": [],
            "files": files or [],
        },
    )


def _file_envelope(
    path: str,
    content: str | None = None,
    content_base64: str | None = None,
    executable: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a file-fetch envelope as the server would return."""
    env: dict[str, Any] = {"path": path, "executable": executable}
    if content is not None:
        env["content"] = content
    if content_base64 is not None:
        env["content_base64"] = content_base64
    if error is not None:
        env["error"] = error
    return env


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256(text.encode("utf-8"))


def _summary_dict(
    *,
    id_: str,
    name: str,
    token: str = "tok",
    version: int = 1,
    role: str = "owner",
    archived_at: str | None = None,
) -> dict:
    payload: dict = {
        "id": id_,
        "name": name,
        "current_version": version,
        "version_token": token,
        "effective_role": role,
    }
    if archived_at is not None:
        payload["archived_at"] = archived_at
    return payload


def _tracked_entry(
    target_dir: Path,
    *,
    id_: str,
    slug: str,
    body: str,
    token: str = "tok",
    version: int = 1,
    files: list[FileState] | None = None,
) -> SyncEntry:
    return SyncEntry(
        workflow_id=id_,
        slug=slug,
        target_path=normalize_target_path(str(target_dir)),
        synced_version=version,
        version_token=token,
        body_sha256=body_sha256(body),
        files=files or [],
    )


# ---- pull writes full tree -------------------------------------------------


@respx.mock
def test_pull_writes_full_tree(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    """A workflow with SKILL.md + 2 siblings writes all three files with correct content."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    state = SyncState()

    skill_body = "---\nname: my-wf\n---\ndo stuff"
    sibling_text = "helper content"
    sibling_bin = b"\x00\x01\x02\x03binary"
    sibling_bin_b64 = base64.b64encode(sibling_bin).decode()

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="wf1", name="my-wf", token="t1")])
    )
    respx.get(f"{SERVER}/v1/workflows/wf1").mock(
        return_value=_detail_response(
            id_="wf1",
            name="my-wf",
            body=skill_body,
            token="t1",
            files=[
                {
                    "path": "helpers/run.sh",
                    "sha256": _sha256_text(sibling_text),
                    "size_bytes": len(sibling_text),
                    "executable": False,
                    "content_kind": "text",
                },
                {
                    "path": "data/blob.bin",
                    "sha256": _sha256(sibling_bin),
                    "size_bytes": len(sibling_bin),
                    "executable": False,
                    "content_kind": "binary",
                },
            ],
        )
    )
    # Batch file fetch (called with two paths)
    respx.get(f"{SERVER}/v1/workflows/wf1/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    _file_envelope("helpers/run.sh", content=sibling_text),
                    _file_envelope("data/blob.bin", content_base64=sibling_bin_b64),
                ]
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    assert [(i.slug, i.action) for i in result.items] == [("my-wf", "pulled")]

    skill_path = target_dir / "my-wf" / "SKILL.md"
    assert skill_path.read_text(encoding="utf-8") == skill_body

    helper_path = target_dir / "my-wf" / "helpers" / "run.sh"
    assert helper_path.read_text(encoding="utf-8") == sibling_text

    blob_path = target_dir / "my-wf" / "data" / "blob.bin"
    assert blob_path.read_bytes() == sibling_bin

    # Index records the sibling FileState entries.
    entry = load_sync_state(tmp_config_paths).entries[0]
    tracked_paths = {f.path for f in entry.files}
    assert "helpers/run.sh" in tracked_paths
    assert "data/blob.bin" in tracked_paths


# ---- executable bit --------------------------------------------------------


@respx.mock
def test_pull_applies_executable_bit(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    """A sibling marked executable gets +x mode bits; a non-exec one does not."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: exec-wf\n---\nrun"
    exec_content = "#!/bin/sh\necho hi"
    plain_content = "plain text"

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="wf_exec", name="exec-wf", token="t1")])
    )
    respx.get(f"{SERVER}/v1/workflows/wf_exec").mock(
        return_value=_detail_response(
            id_="wf_exec",
            name="exec-wf",
            body=skill_body,
            token="t1",
            files=[
                {
                    "path": "run.sh",
                    "sha256": _sha256_text(exec_content),
                    "size_bytes": len(exec_content),
                    "executable": True,
                    "content_kind": "text",
                },
                {
                    "path": "readme.txt",
                    "sha256": _sha256_text(plain_content),
                    "size_bytes": len(plain_content),
                    "executable": False,
                    "content_kind": "text",
                },
            ],
        )
    )
    respx.get(f"{SERVER}/v1/workflows/wf_exec/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    _file_envelope("run.sh", content=exec_content, executable=True),
                    _file_envelope("readme.txt", content=plain_content, executable=False),
                ]
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    exec_path = target_dir / "exec-wf" / "run.sh"
    plain_path = target_dir / "exec-wf" / "readme.txt"

    assert exec_path.exists()
    assert plain_path.exists()

    exec_mode = os.stat(exec_path).st_mode
    plain_mode = os.stat(plain_path).st_mode

    # exec file has at least one executable bit set
    assert exec_mode & 0o111, f"Expected executable bit, got mode {oct(exec_mode)}"
    # plain file has no executable bits
    assert not (plain_mode & 0o111), f"Expected no executable bit, got mode {oct(plain_mode)}"


@respx.mock
def test_pull_force_clears_stale_executable_bit(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A force-overwrite of a now-non-executable sibling clears the stale +x bit.

    The on-disk sibling was previously executable. The registry has advanced and
    the new manifest marks the same path non-executable. ``write_text`` reuses the
    existing inode, so its mode bits survive the overwrite; the pull must bring the
    mode into line with the authoritative manifest by clearing ``+x``.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: flip-wf\n---\nrun"
    old_content = "#!/bin/sh\necho hi"
    new_content = "no longer a script"

    slug_dir = target_dir / "flip-wf"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    sibling_path = slug_dir / "tool"
    sibling_path.write_text(old_content, encoding="utf-8")
    # Seed the on-disk sibling as executable, as a prior executable pull would.
    os.chmod(sibling_path, os.stat(sibling_path).st_mode | 0o111)
    assert os.stat(sibling_path).st_mode & 0o111

    state = SyncState(
        entries=[
            _tracked_entry(
                target_dir,
                id_="wf_flip",
                slug="flip-wf",
                body=skill_body,
                token="t1",
                files=[
                    FileState(
                        path="tool",
                        sha256=_sha256_text(old_content),
                        executable=True,
                    )
                ],
            )
        ]
    )

    # Registry advanced (new token) and the manifest now marks the path
    # non-executable with new content.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="wf_flip", name="flip-wf", token="t2", version=2)]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/wf_flip").mock(
        return_value=_detail_response(
            id_="wf_flip",
            name="flip-wf",
            body=skill_body,
            token="t2",
            version=2,
            files=[
                {
                    "path": "tool",
                    "sha256": _sha256_text(new_content),
                    "size_bytes": len(new_content),
                    "executable": False,
                    "content_kind": "text",
                }
            ],
        )
    )
    respx.get(f"{SERVER}/v1/workflows/wf_flip/files").mock(
        return_value=httpx.Response(
            200,
            json={"files": [_file_envelope("tool", content=new_content, executable=False)]},
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=True,
            yes=True,
            paths=tmp_config_paths,
        )

    assert sibling_path.read_text(encoding="utf-8") == new_content
    mode = os.stat(sibling_path).st_mode
    assert not (mode & 0o111), f"Expected stale +x cleared, got mode {oct(mode)}"


# ---- incremental fetch -----------------------------------------------------


@respx.mock
def test_pull_incremental_fetches_only_changed(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """Second pull fetches ONLY the changed sibling, not the unchanged one."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: inc-wf\n---\nbody"
    unchanged_content = "unchanged content"
    changed_content_v2 = "new content v2"

    unchanged_sha = _sha256_text(unchanged_content)

    # Pre-populate the disk as if a previous pull already ran.
    slug_dir = target_dir / "inc-wf"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    (slug_dir / "unchanged.txt").write_text(unchanged_content, encoding="utf-8")
    (slug_dir / "changed.txt").write_text("old content", encoding="utf-8")

    # State reflects the first pull.
    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="wf_inc",
                slug="inc-wf",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                body_sha256=body_sha256(skill_body),
                files=[
                    FileState(path="unchanged.txt", sha256=unchanged_sha, executable=False),
                    FileState(
                        path="changed.txt",
                        sha256=_sha256_text("old content"),
                        executable=False,
                    ),
                ],
            )
        ]
    )

    new_changed_sha = _sha256_text(changed_content_v2)

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="wf_inc", name="inc-wf", token="t2", version=2)]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/wf_inc").mock(
        return_value=_detail_response(
            id_="wf_inc",
            name="inc-wf",
            body=skill_body,
            token="t2",
            version=2,
            files=[
                {
                    "path": "unchanged.txt",
                    "sha256": unchanged_sha,
                    "size_bytes": len(unchanged_content),
                    "executable": False,
                    "content_kind": "text",
                },
                {
                    "path": "changed.txt",
                    "sha256": new_changed_sha,
                    "size_bytes": len(changed_content_v2),
                    "executable": False,
                    "content_kind": "text",
                },
            ],
        )
    )

    # The batch endpoint should be called with only "changed.txt".
    files_route = respx.get(f"{SERVER}/v1/workflows/wf_inc/files").mock(
        return_value=httpx.Response(
            200,
            json={"files": [_file_envelope("changed.txt", content=changed_content_v2)]},
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    assert [(i.slug, i.action) for i in result.items] == [("inc-wf", "pulled")]

    # Only the changed file was re-fetched (one request to the files endpoint).
    assert files_route.call_count == 1
    # Verify the request only asked for the changed file, not the unchanged one.
    called_request = files_route.calls[0].request
    url_str = str(called_request.url)
    assert "changed.txt" in url_str
    assert "unchanged.txt" not in url_str

    # Unchanged file content preserved on disk.
    assert (target_dir / "inc-wf" / "unchanged.txt").read_text() == unchanged_content
    # Changed file updated.
    assert (target_dir / "inc-wf" / "changed.txt").read_text() == changed_content_v2


@respx.mock
def test_pull_restores_locally_deleted_sibling_when_server_unchanged(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A tracked sibling deleted locally is restored on a plain pull, even when
    the server has not advanced.

    A missing sibling is intentionally not treated as a local modification (so
    it never blocks a deletion or an unforced overwrite), but the up-to-date
    fast path must still notice the gap and fetch the file back, mirroring how a
    deleted SKILL.md body is restored. Before the fix, the fast path returned
    "up-to-date" and left the sibling missing on disk.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: del-wf\n---\nbody"
    sibling_content = "important helper"
    sibling_sha = _sha256_text(sibling_content)

    # Pre-populate disk as if a previous pull ran, then delete the sibling.
    slug_dir = target_dir / "del-wf"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    sibling_path = slug_dir / "helpers" / "run.sh"
    sibling_path.parent.mkdir(parents=True)
    sibling_path.write_text(sibling_content, encoding="utf-8")
    # User deletes the tracked sibling locally.
    sibling_path.unlink()
    assert not sibling_path.exists()

    state = SyncState(
        entries=[
            _tracked_entry(
                target_dir,
                id_="wf_del",
                slug="del-wf",
                body=skill_body,
                token="t1",
                version=1,
                files=[FileState(path="helpers/run.sh", sha256=sibling_sha, executable=False)],
            )
        ]
    )

    # Server is unchanged (same token t1, same version).
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="wf_del", name="del-wf", token="t1", version=1)]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/wf_del").mock(
        return_value=_detail_response(
            id_="wf_del",
            name="del-wf",
            body=skill_body,
            token="t1",
            version=1,
            files=[
                {
                    "path": "helpers/run.sh",
                    "sha256": sibling_sha,
                    "size_bytes": len(sibling_content),
                    "executable": False,
                    "content_kind": "text",
                }
            ],
        )
    )
    respx.get(f"{SERVER}/v1/workflows/wf_del/files").mock(
        return_value=httpx.Response(
            200,
            json={"files": [_file_envelope("helpers/run.sh", content=sibling_content)]},
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    # The pull did real work (not "up-to-date") and restored the sibling.
    assert [i.action for i in result.items] == ["pulled"]
    assert sibling_path.exists()
    assert sibling_path.read_text() == sibling_content


# ---- safe deletion ---------------------------------------------------------


@respx.mock
def test_delete_only_removes_tracked_files_keeps_untracked(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """Reconcile-deletion removes tracked files but leaves untracked author files and the dir."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "registry body"
    sibling_content = "tracked sibling"
    untracked_content = "local scratch file"

    # Populate disk.
    slug_dir = target_dir / "gone"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    (slug_dir / "tracked.txt").write_text(sibling_content, encoding="utf-8")
    (slug_dir / "scratch.txt").write_text(untracked_content, encoding="utf-8")

    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="wf_gone",
                slug="gone",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                body_sha256=body_sha256(skill_body),
                files=[
                    FileState(
                        path="tracked.txt",
                        sha256=_sha256_text(sibling_content),
                        executable=False,
                    )
                ],
            )
        ]
    )

    # Workflow is gone server-side (soft-deleted).
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="wf_gone", name="gone", archived_at="2026-01-01T00:00:00Z")]
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=True,  # auto-approve removal
            paths=tmp_config_paths,
        )

    assert [(i.slug, i.action) for i in result.items] == [("gone", "deleted-local")]

    # Tracked files are gone.
    assert not (slug_dir / "SKILL.md").exists()
    assert not (slug_dir / "tracked.txt").exists()

    # Untracked author file MUST survive.
    assert (slug_dir / "scratch.txt").exists()
    assert (slug_dir / "scratch.txt").read_text(encoding="utf-8") == untracked_content

    # The slug directory itself must survive (untracked file prevents rmdir).
    assert slug_dir.is_dir()

    # Index entry was dropped.
    assert load_sync_state(tmp_config_paths).entries == []


@respx.mock
def test_delete_rmdirs_only_if_empty(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    """Slug dir with only tracked files is rmdir'd; one with an extra untracked file is left."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    body_clean = "clean body"
    body_dirty = "dirty body"
    sibling_content = "sibling"

    # Slug "clean-wf": only tracked files - dir should be removed.
    clean_dir = target_dir / "clean-wf"
    clean_dir.mkdir(parents=True)
    (clean_dir / "SKILL.md").write_text(body_clean, encoding="utf-8")
    (clean_dir / "helper.txt").write_text(sibling_content, encoding="utf-8")

    # Slug "dirty-wf": tracked files + an untracked scratch file - dir should survive.
    dirty_dir = target_dir / "dirty-wf"
    dirty_dir.mkdir(parents=True)
    (dirty_dir / "SKILL.md").write_text(body_dirty, encoding="utf-8")
    (dirty_dir / "helper.txt").write_text(sibling_content, encoding="utf-8")
    (dirty_dir / "scratch.txt").write_text("author local", encoding="utf-8")

    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="wf_clean",
                slug="clean-wf",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                body_sha256=body_sha256(body_clean),
                files=[FileState(path="helper.txt", sha256=_sha256_text(sibling_content))],
            ),
            SyncEntry(
                workflow_id="wf_dirty",
                slug="dirty-wf",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                body_sha256=body_sha256(body_dirty),
                files=[FileState(path="helper.txt", sha256=_sha256_text(sibling_content))],
            ),
        ]
    )

    # Both workflows are gone server-side.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                _summary_dict(id_="wf_clean", name="clean-wf", archived_at="2026-01-01T00:00:00Z"),
                _summary_dict(id_="wf_dirty", name="dirty-wf", archived_at="2026-01-01T00:00:00Z"),
            ]
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=True,
            paths=tmp_config_paths,
        )

    actions = {i.slug: i.action for i in result.items}
    assert actions["clean-wf"] == "deleted-local"
    assert actions["dirty-wf"] == "deleted-local"

    # clean-wf had only tracked files -> slug dir should be removed.
    assert not clean_dir.exists(), "Expected empty slug dir to be removed"

    # dirty-wf had an extra untracked file -> slug dir must survive.
    assert dirty_dir.is_dir(), "Expected non-empty slug dir to be kept"
    assert (dirty_dir / "scratch.txt").exists()


# ---- un-pushed sibling edit blocks deletion --------------------------------


@respx.mock
def test_unpushed_sibling_edit_blocks_deletion(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A tracked sibling edited on disk causes reconcile to report deleted-on-server, not delete."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "registry body"
    original_sibling = "original sibling content"
    edited_sibling = "locally edited content - DIFFERENT from recorded sha"

    slug_dir = target_dir / "modified-wf"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    # The on-disk sibling is EDITED relative to the recorded sha.
    (slug_dir / "helper.sh").write_text(edited_sibling, encoding="utf-8")

    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="wf_mod",
                slug="modified-wf",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                # Body sha matches disk (only sibling was edited)
                body_sha256=body_sha256(skill_body),
                files=[
                    FileState(
                        path="helper.sh",
                        # Recorded sha is the ORIGINAL (pre-edit) content
                        sha256=_sha256_text(original_sibling),
                        executable=True,
                    )
                ],
            )
        ]
    )

    # Workflow is gone server-side.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="wf_mod", name="modified-wf", archived_at="2026-01-01T00:00:00Z")]
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=True,  # auto-approve would normally delete, but sibling edit protects it
            paths=tmp_config_paths,
        )

    # Must report deleted-on-server, NOT deleted-local.
    assert [(i.slug, i.action) for i in result.items] == [("modified-wf", "deleted-on-server")]

    # SKILL.md and sibling both survive.
    assert (slug_dir / "SKILL.md").exists()
    assert (slug_dir / "helper.sh").exists()
    assert (slug_dir / "helper.sh").read_text(encoding="utf-8") == edited_sibling

    # Tracking entry survives.
    remaining = load_sync_state(tmp_config_paths).entries
    assert [e.slug for e in remaining] == ["modified-wf"]


@respx.mock
def test_unpushed_sibling_edit_blocks_unforced_overwrite(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A sibling edited on disk blocks an unforced pull that would drop that sibling.

    The workflow is still live but has advanced, and the new manifest no longer
    lists the sibling. Without ``--force`` the whole-tree edit guard must trip on
    the sibling edit and skip the pull, so the dropped-sibling removal never runs
    and the locally edited file is preserved. A body-only guard would let the
    overwrite proceed and silently unlink the edited sibling.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "registry body"
    original_sibling = "original sibling content"
    edited_sibling = "locally edited content - DIFFERENT from recorded sha"

    slug_dir = target_dir / "drop-wf"
    slug_dir.mkdir(parents=True)
    (slug_dir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    # The on-disk sibling is EDITED relative to the recorded sha.
    (slug_dir / "helper.sh").write_text(edited_sibling, encoding="utf-8")

    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="wf_drop",
                slug="drop-wf",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                # Body sha matches disk (only the sibling was edited).
                body_sha256=body_sha256(skill_body),
                files=[
                    FileState(
                        path="helper.sh",
                        # Recorded sha is the ORIGINAL (pre-edit) content.
                        sha256=_sha256_text(original_sibling),
                        executable=True,
                    )
                ],
            )
        ]
    )

    # Workflow is still live but the registry advanced (new token) and the new
    # manifest no longer carries the sibling. The detail/files routes must never
    # be reached: the guard returns first.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="wf_drop", name="drop-wf", token="t2", version=2)]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/wf_drop").mock(
        return_value=_detail_response(
            id_="wf_drop", name="drop-wf", body=skill_body, token="t2", version=2, files=[]
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=True,
            paths=tmp_config_paths,
        )

    # Both sides moved relative to the recorded sync point: a conflict, not a
    # plain modification.
    assert [(i.slug, i.action) for i in result.items] == [("drop-wf", "skipped-conflict")]

    # No fetch happened: the guard returned before touching the detail route.
    assert detail_route.call_count == 0

    # The locally edited sibling survives untouched.
    assert (slug_dir / "helper.sh").exists()
    assert (slug_dir / "helper.sh").read_text(encoding="utf-8") == edited_sibling


# ---- undeliverable siblings are not recorded as synced ---------------------


@respx.mock
def test_pull_skips_undeliverable_sibling_and_reports_incomplete(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A sibling no route can deliver is left off disk, not recorded, and flagged.

    A binary over the inline ceiling returns ``binary_too_large_for_inline`` from
    the JSON route; the pull then falls back to the ``format=raw`` byte route. If
    even that cannot deliver the bytes (here it 404s, standing in for a GC'd or
    otherwise missing blob), the sibling must not be written, must not be recorded
    in the index as synced (so the next pull retries it), and the pull must report
    ``pulled-incomplete``.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: big-wf\n---\nbody"
    small_text = "small helper"
    big_bin = b"\x00" * 2048  # stands in for a binary over the inline ceiling

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="wf_big", name="big-wf", token="t1")])
    )
    respx.get(f"{SERVER}/v1/workflows/wf_big").mock(
        return_value=_detail_response(
            id_="wf_big",
            name="big-wf",
            body=skill_body,
            token="t1",
            files=[
                {
                    "path": "small.txt",
                    "sha256": _sha256_text(small_text),
                    "size_bytes": len(small_text),
                    "executable": False,
                    "content_kind": "text",
                },
                {
                    "path": "big.bin",
                    "sha256": _sha256(big_bin),
                    "size_bytes": len(big_bin),
                    "executable": False,
                    "content_kind": "binary",
                },
            ],
        )
    )

    def _files_side_effect(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("format") == "raw":
            # Raw fallback cannot deliver either: the blob is genuinely gone.
            return httpx.Response(404, json={"error": "not_found", "message": "blob missing"})
        return httpx.Response(
            200,
            json={
                "files": [
                    _file_envelope("big.bin", error="binary_too_large_for_inline"),
                    _file_envelope("small.txt", content=small_text),
                ]
            },
        )

    respx.get(f"{SERVER}/v1/workflows/wf_big/files").mock(side_effect=_files_side_effect)

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    assert [(i.slug, i.action) for i in result.items] == [("big-wf", "pulled-incomplete")]

    slug_dir = target_dir / "big-wf"
    # The deliverable sibling landed; the undeliverable one did not.
    assert (slug_dir / "small.txt").read_text(encoding="utf-8") == small_text
    assert not (slug_dir / "big.bin").exists()

    # The undeliverable sibling is NOT recorded as synced, so a later pull retries
    # it instead of treating the directory as complete.
    entry = load_sync_state(tmp_config_paths).entries[0]
    tracked_paths = {f.path for f in entry.files}
    assert "small.txt" in tracked_paths
    assert "big.bin" not in tracked_paths


@respx.mock
def test_pull_recovers_over_ceiling_binary_via_raw(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A binary over the inline ceiling is recovered through the format=raw route.

    The JSON batch route reports ``binary_too_large_for_inline`` for the large
    sibling; the pull falls back to the raw byte route (content-addressed by the
    manifest sha), which streams the bytes whole. The file must land on disk
    intact, be recorded as synced, and the pull must report ``pulled`` (complete).
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: img-wf\n---\nbody"
    small_text = "small helper"
    # A 1500-byte binary that "exceeds the inline ceiling"; raw delivers it whole.
    big_bin = bytes(range(256)) * 6  # 1536 bytes, non-text
    big_sha = _sha256(big_bin)

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="wf_img", name="img-wf", token="t1")])
    )
    respx.get(f"{SERVER}/v1/workflows/wf_img").mock(
        return_value=_detail_response(
            id_="wf_img",
            name="img-wf",
            body=skill_body,
            token="t1",
            files=[
                {
                    "path": "small.txt",
                    "sha256": _sha256_text(small_text),
                    "size_bytes": len(small_text),
                    "executable": False,
                    "content_kind": "text",
                },
                {
                    "path": "demo/big.png",
                    "sha256": big_sha,
                    "size_bytes": len(big_bin),
                    "executable": False,
                    "content_kind": "binary",
                },
            ],
        )
    )

    seen_raw: dict[str, str] = {}

    def _files_side_effect(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("format") == "raw":
            # Raw byte route: record what was asked for, then stream the bytes.
            seen_raw["path"] = params.get("path", "")
            seen_raw["sha256"] = params.get("sha256", "")
            return httpx.Response(200, content=big_bin)
        # Batch route: small inlined, big reported over the per-file ceiling.
        return httpx.Response(
            200,
            json={
                "files": [
                    _file_envelope("small.txt", content=small_text),
                    _file_envelope("demo/big.png", error="binary_too_large_for_inline"),
                ]
            },
        )

    respx.get(f"{SERVER}/v1/workflows/wf_img/files").mock(side_effect=_files_side_effect)

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    # The raw fallback recovered the over-ceiling binary, so the pull is complete.
    assert [(i.slug, i.action) for i in result.items] == [("img-wf", "pulled")]

    slug_dir = target_dir / "img-wf"
    assert (slug_dir / "small.txt").read_text(encoding="utf-8") == small_text
    assert (slug_dir / "demo" / "big.png").read_bytes() == big_bin

    # The raw fetch was content-addressed by the manifest sha for the right path.
    assert seen_raw == {"path": "demo/big.png", "sha256": big_sha}

    # Both siblings are now recorded as synced, so a later pull treats the
    # directory as complete instead of re-fetching.
    entry = load_sync_state(tmp_config_paths).entries[0]
    tracked_paths = {f.path for f in entry.files}
    assert tracked_paths == {"small.txt", "demo/big.png"}


@respx.mock
def test_pull_recovers_batch_cap_overflow_via_single_fetch(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A sibling dropped from the batch for the aggregate cap is recovered alone.

    The batch response marks one path ``batch_response_cap_exceeded``; the client
    must re-fetch that path through the single-file route (no aggregate cap) so it
    still lands on disk and is recorded as synced.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: cap-wf\n---\nbody"
    first_text = "first sibling"
    overflow_text = "overflow sibling content that the batch could not inline"

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="wf_cap", name="cap-wf", token="t1")])
    )
    respx.get(f"{SERVER}/v1/workflows/wf_cap").mock(
        return_value=_detail_response(
            id_="wf_cap",
            name="cap-wf",
            body=skill_body,
            token="t1",
            files=[
                {
                    "path": "a.txt",
                    "sha256": _sha256_text(first_text),
                    "size_bytes": len(first_text),
                    "executable": False,
                    "content_kind": "text",
                },
                {
                    "path": "b.txt",
                    "sha256": _sha256_text(overflow_text),
                    "size_bytes": len(overflow_text),
                    "executable": False,
                    "content_kind": "text",
                },
            ],
        )
    )

    def _files_side_effect(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "path" in params:
            # Single-file fallback route: deliver the overflowed file with content.
            assert params["path"] == "b.txt"
            return httpx.Response(200, json=_file_envelope("b.txt", content=overflow_text))
        # Batch route: first file inlined, second over the aggregate budget.
        return httpx.Response(
            200,
            json={
                "files": [
                    _file_envelope("a.txt", content=first_text),
                    _file_envelope("b.txt", error="batch_response_cap_exceeded"),
                ]
            },
        )

    respx.get(f"{SERVER}/v1/workflows/wf_cap/files").mock(side_effect=_files_side_effect)

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    # The fallback recovered the overflowed file, so the pull is complete.
    assert [(i.slug, i.action) for i in result.items] == [("cap-wf", "pulled")]

    slug_dir = target_dir / "cap-wf"
    assert (slug_dir / "a.txt").read_text(encoding="utf-8") == first_text
    assert (slug_dir / "b.txt").read_text(encoding="utf-8") == overflow_text

    entry = load_sync_state(tmp_config_paths).entries[0]
    tracked_paths = {f.path for f in entry.files}
    assert tracked_paths == {"a.txt", "b.txt"}


# ---- path traversal containment (security) ---------------------------------


@respx.mock
def test_pull_rejects_unsafe_sibling_paths_and_writes_nothing_outside_slug_dir(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A malicious manifest row or fetch envelope must never write outside the slug dir.

    This pins the top security invariant for pull: a server-supplied sibling path
    that escapes the per-skill directory (``..`` traversal or an absolute path),
    whether it arrives in a manifest row or in the ``path`` of a fetch envelope,
    is rejected. Nothing is written outside ``<target>/<slug>/`` and the unsafe
    path is never recorded in the index. A legitimate sibling alongside the
    malicious entries still lands so the rejection is path-scoped, not a blanket
    abort.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    skill_body = "---\nname: evil-wf\n---\nbody"
    good_text = "legitimate helper"

    # A canary file outside the slug directory: if a traversal write lands, it
    # would overwrite this with attacker content. It must survive untouched.
    outside_canary = tmp_path / "escape.sh"
    outside_canary.write_text("ORIGINAL", encoding="utf-8")

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="wf_evil", name="evil-wf", token="t1")])
    )
    respx.get(f"{SERVER}/v1/workflows/wf_evil").mock(
        return_value=_detail_response(
            id_="wf_evil",
            name="evil-wf",
            body=skill_body,
            token="t1",
            files=[
                {
                    "path": "good.txt",
                    "sha256": _sha256_text(good_text),
                    "size_bytes": len(good_text),
                    "executable": False,
                    "content_kind": "text",
                },
                # Directory traversal: would resolve to tmp_path/escape.sh.
                {
                    "path": "../escape.sh",
                    "sha256": _sha256_text("PWNED"),
                    "size_bytes": 5,
                    "executable": True,
                    "content_kind": "text",
                },
                # Absolute path: must not be written to an absolute location.
                {
                    "path": "/etc/passwd",
                    "sha256": _sha256_text("PWNED"),
                    "size_bytes": 5,
                    "executable": False,
                    "content_kind": "text",
                },
            ],
        )
    )

    # The batch fetch is only ever called with safe paths (unsafe manifest rows
    # are filtered before fetching), but a hostile server can still return an
    # unsafe ``path`` inside an envelope. Include such an envelope to exercise the
    # second containment layer in the envelope writer.
    respx.get(f"{SERVER}/v1/workflows/wf_evil/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    _file_envelope("good.txt", content=good_text),
                    _file_envelope("../envelope-escape.sh", content="PWNED"),
                ]
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    # The pull does not crash; the legitimate sibling is delivered.
    assert [i.slug for i in result.items] == ["evil-wf"]
    slug_dir = target_dir / "evil-wf"
    assert (slug_dir / "good.txt").read_text(encoding="utf-8") == good_text

    # Nothing escaped the slug directory.
    assert outside_canary.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (tmp_path / "envelope-escape.sh").exists()
    assert not Path("/etc/passwd").exists() or (
        Path("/etc/passwd").read_text(encoding="utf-8") != "PWNED"
    )

    # Only the safe path is on disk inside the slug dir, and only the safe path
    # is recorded as synced. The unsafe rows are never tracked.
    landed = {p.relative_to(slug_dir).as_posix() for p in slug_dir.rglob("*") if p.is_file()}
    assert landed == {"SKILL.md", "good.txt"}
    entry = load_sync_state(tmp_config_paths).entries[0]
    tracked_paths = {f.path for f in entry.files}
    assert tracked_paths == {"good.txt"}
