"""Push tests for multi-file skill directory tree upload.

Covers:
- A changed sibling is sent inline; unchanged siblings are sent as sha256 refs.
- Ignored files (matching DEFAULT_IGNORE_PATTERNS) are excluded from the payload.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

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
    build_files_payload,
    normalize_target_path,
    push,
)

SERVER = "https://example.test"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _me_route(email: str = "owner@example.com") -> respx.Route:
    return respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": email})
    )


def _save_response(
    *,
    workflow_id: str,
    name: str,
    version: int = 2,
    token: str = "tok-2",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "workflow_id": workflow_id,
            "version": version,
            "name": name,
            "version_token": token,
            "verifiers": [],
        },
    )


def _push_body(slug: str) -> str:
    return (
        f"---\nname: {slug}\n"
        "description: A test workflow.\n"
        "outcome: Achieve the test goal.\n"
        "---\n\nDo the work.\n"
    )


def _write_skill(target_dir: Path, slug: str, body: str) -> Path:
    skill = target_dir / slug / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body, encoding="utf-8")
    return skill


def _write_sibling(target_dir: Path, slug: str, rel_path: str, content: str) -> Path:
    p = target_dir / slug / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _modified_entry(
    target_dir: Path,
    *,
    id_: str,
    slug: str,
    token: str = "tok-1",
    files: list[FileState] | None = None,
) -> SyncEntry:
    """Entry with a recorded hash that won't match any real on-disk body."""
    return SyncEntry(
        workflow_id=id_,
        slug=slug,
        target_path=normalize_target_path(str(target_dir)),
        synced_version=1,
        version_token=token,
        body_sha256=body_sha256(f"recorded-base-{slug}"),
        files=files or [],
    )


@respx.mock
def test_push_sends_changed_file_inline_others_by_ref(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """When one sibling changes, it is sent inline; unchanged siblings are sent as sha256 refs."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    slug = "my-workflow"
    body = _push_body(slug)
    _write_skill(target_dir, slug, body)

    # Two sibling files: helper.sh (unchanged) and data.json (edited)
    helper_content = "#!/bin/bash\necho hello\n"
    data_original = '{"key": "original"}'
    data_edited = '{"key": "edited"}'

    helper_sha = _sha256_text(helper_content)
    _write_sibling(target_dir, slug, "helper.sh", helper_content)
    _write_sibling(target_dir, slug, "data.json", data_edited)

    # State records both files at their original hashes; helper is current,
    # data.json has been edited (recorded hash differs from what's on disk).
    state = SyncState(
        entries=[
            _modified_entry(
                target_dir,
                id_="skl_01",
                slug=slug,
                files=[
                    FileState(path="helper.sh", sha256=helper_sha, executable=False),
                    FileState(
                        path="data.json", sha256=_sha256_text(data_original), executable=False
                    ),
                ],
            )
        ]
    )

    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_01", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert [(i.slug, i.action) for i in result.items] == [(slug, "pushed")]
    assert save_route.call_count == 1

    sent = json.loads(save_route.calls[0].request.content)
    assert "files" in sent
    files_by_path = {f["path"]: f for f in sent["files"]}

    # Unchanged helper.sh must be a reference entry (sha256 key, no content key).
    assert "helper.sh" in files_by_path
    helper_entry = files_by_path["helper.sh"]
    assert "sha256" in helper_entry
    assert "content" not in helper_entry
    assert helper_entry["sha256"] == helper_sha

    # Edited data.json must be inline (content key, no sha256 key).
    assert "data.json" in files_by_path
    data_entry = files_by_path["data.json"]
    assert "content" in data_entry
    assert "sha256" not in data_entry
    assert data_entry["content"] == data_edited


@respx.mock
def test_push_applies_ignore_before_upload(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    """Files matching the default ignore patterns are excluded from the files payload."""
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    slug = "my-workflow"
    body = _push_body(slug)
    _write_skill(target_dir, slug, body)

    # Write a file that should be included and one that should be ignored.
    _write_sibling(target_dir, slug, "run.sh", "#!/bin/bash\necho hi\n")
    _write_sibling(target_dir, slug, "node_modules/lodash/index.js", "// lodash\n")

    # Entry recorded with no files so everything is treated as changed (inline).
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_02", slug=slug, files=[])])

    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_02", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert [(i.slug, i.action) for i in result.items] == [(slug, "pushed")]
    sent = json.loads(save_route.calls[0].request.content)
    assert "files" in sent
    paths_sent = {f["path"] for f in sent["files"]}

    # run.sh should be included; node_modules entry should be excluded.
    assert "run.sh" in paths_sent
    assert not any(p.startswith("node_modules") for p in paths_sent)


def test_binary_sibling_records_raw_sha_and_converges_to_reference(tmp_path: Path) -> None:
    """A binary sibling is sent inline as base64 but recorded under its raw-byte
    sha256, so a subsequent build sends it as a reference instead of re-uploading."""
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    raw = b"\x00\x01\x02binary\xff\xfe"
    (skill_dir / "data.bin").write_bytes(raw)
    raw_sha = hashlib.sha256(raw).hexdigest()

    # First build with no recorded state: the binary is inline base64.
    payload, states = build_files_payload(skill_dir, None, [])
    bin_entry = {e["path"]: e for e in payload}["data.bin"]
    assert "content" in bin_entry and "sha256" not in bin_entry
    assert bin_entry["content"] == base64.b64encode(raw).decode("ascii")

    # The recorded state uses the RAW-byte sha, not the sha of the base64 string.
    bin_state = {s.path: s for s in states}["data.bin"]
    assert bin_state.sha256 == raw_sha
    assert bin_state.sha256 != hashlib.sha256(bin_entry["content"].encode("utf-8")).hexdigest()

    # Second build using that recorded state: the binary converges to a reference.
    payload2, _ = build_files_payload(skill_dir, states, [])
    bin_entry2 = {e["path"]: e for e in payload2}["data.bin"]
    assert "sha256" in bin_entry2 and "content" not in bin_entry2
    assert bin_entry2["sha256"] == raw_sha


def test_push_skips_symlink_pointing_outside_skill_dir(tmp_path: Path) -> None:
    """An in-tree symlink to a file outside the skill dir is not uploaded.

    ``rglob`` + ``is_file`` follows links, so without a guard the symlink's target
    bytes would be read and uploaded. The push walk must skip symlinks to match
    the containment defense the pull side applies when writing siblings."""
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET CONTENTS OUTSIDE THE SKILL", encoding="utf-8")

    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    real_content = "real sibling"
    (skill_dir / "real.txt").write_text(real_content, encoding="utf-8")
    # In-tree symlink pointing outside the skill directory.
    (skill_dir / "leak.txt").symlink_to(outside)

    payload, states = build_files_payload(skill_dir, None, [])

    paths_sent = {e["path"] for e in payload}
    recorded_paths = {s.path for s in states}

    # The real sibling is uploaded; the symlink is skipped entirely.
    assert "real.txt" in paths_sent
    assert "leak.txt" not in paths_sent
    assert "leak.txt" not in recorded_paths

    # The outside file's contents never appear in any payload entry.
    serialized = json.dumps(payload)
    assert "SECRET CONTENTS OUTSIDE THE SKILL" not in serialized
