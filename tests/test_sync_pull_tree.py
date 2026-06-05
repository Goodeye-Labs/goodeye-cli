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
    deleted_at: str | None = None,
) -> dict:
    payload: dict = {
        "id": id_,
        "name": name,
        "current_version": version,
        "version_token": token,
        "effective_role": role,
    }
    if deleted_at is not None:
        payload["deleted_at"] = deleted_at
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
            [_summary_dict(id_="wf_gone", name="gone", deleted_at="2026-01-01T00:00:00Z")]
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
                _summary_dict(id_="wf_clean", name="clean-wf", deleted_at="2026-01-01T00:00:00Z"),
                _summary_dict(id_="wf_dirty", name="dirty-wf", deleted_at="2026-01-01T00:00:00Z"),
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
            [_summary_dict(id_="wf_mod", name="modified-wf", deleted_at="2026-01-01T00:00:00Z")]
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
