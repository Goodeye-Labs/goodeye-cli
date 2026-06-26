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
    """A binary sibling is sent inline as base64 (in ``content_base64``) but
    recorded under its raw-byte sha256, so a subsequent build sends it as a
    reference instead of re-uploading."""
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    raw = b"\x00\x01\x02binary\xff\xfe"
    (skill_dir / "data.bin").write_bytes(raw)
    raw_sha = hashlib.sha256(raw).hexdigest()

    # First build with no recorded state: the binary is inline base64 in the
    # explicit ``content_base64`` field, never the text ``content`` field.
    payload, states = build_files_payload(skill_dir, None, [])
    bin_entry = {e["path"]: e for e in payload}["data.bin"]
    assert "content_base64" in bin_entry and "content" not in bin_entry
    assert "sha256" not in bin_entry
    assert bin_entry["content_base64"] == base64.b64encode(raw).decode("ascii")

    # The recorded state uses the RAW-byte sha, not the sha of the base64 string.
    bin_state = {s.path: s for s in states}["data.bin"]
    assert bin_state.sha256 == raw_sha
    assert (
        bin_state.sha256 != hashlib.sha256(bin_entry["content_base64"].encode("utf-8")).hexdigest()
    )

    # Second build using that recorded state: the binary converges to a reference.
    payload2, _ = build_files_payload(skill_dir, states, [])
    bin_entry2 = {e["path"]: e for e in payload2}["data.bin"]
    assert "sha256" in bin_entry2 and "content" not in bin_entry2
    assert "content_base64" not in bin_entry2
    assert bin_entry2["sha256"] == raw_sha


def test_unchanged_file_keeps_recorded_executable_bit(tmp_path: Path) -> None:
    """An unchanged file (sha matches recorded) carries forward the recorded
    executable flag rather than re-deriving it from the local OS mode.

    On a filesystem that does not preserve the execute bit (e.g. a Windows
    checkout or a FAT mount), ``os.stat`` would report the file as
    non-executable. Without carrying the recorded bit forward, pushing an
    otherwise-unchanged file would silently clear an executable flag the server
    already holds.
    """
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    script_text = "#!/bin/sh\necho hi\n"
    script = skill_dir / "run.sh"
    script.write_text(script_text, encoding="utf-8")
    # Simulate a filesystem that did not preserve the execute bit on disk.
    script.chmod(0o644)
    script_sha = _sha256_text(script_text)

    # Recorded state says this file is executable (set on a previous pull/push
    # from an exec-bit-preserving filesystem) and its content is unchanged.
    recorded = [FileState(path="run.sh", sha256=script_sha, executable=True)]
    payload, states = build_files_payload(skill_dir, recorded, [])

    entry = {e["path"]: e for e in payload}["run.sh"]
    # Unchanged => reference entry, and the executable flag is preserved.
    assert "sha256" in entry and "content" not in entry
    assert entry["executable"] is True
    assert {s.path: s for s in states}["run.sh"].executable is True


def test_changed_file_uses_local_executable_bit(tmp_path: Path) -> None:
    """A changed file (content differs from recorded) carries the freshly
    observed local execute bit, since its current content and permissions are
    what is being uploaded."""
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    script = skill_dir / "run.sh"
    script.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
    script.chmod(0o644)

    # Recorded state has a different sha (so the file is treated as changed) and
    # claims executable=True; the local bit (cleared) must win for a changed file.
    recorded = [FileState(path="run.sh", sha256="0" * 64, executable=True)]
    payload, states = build_files_payload(skill_dir, recorded, [])

    entry = {e["path"]: e for e in payload}["run.sh"]
    assert "content" in entry  # inline, since changed
    assert entry["executable"] is False
    assert {s.path: s for s in states}["run.sh"].executable is False


def test_short_text_sibling_that_is_valid_base64_sent_as_text(tmp_path: Path) -> None:
    """A text sibling whose content is coincidentally valid base64 (e.g. 'test')
    is sent through the verbatim text ``content`` field and recorded under its
    UTF-8 byte sha, so a push/pull round trip stays lossless and converges to a
    reference (no silent binary corruption)."""
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    for fname, text in (("a.txt", "test"), ("b.txt", "abcd")):
        (skill_dir / fname).write_text(text, encoding="utf-8")

    payload, states = build_files_payload(skill_dir, None, [])
    by_path = {e["path"]: e for e in payload}
    state_by_path = {s.path: s for s in states}
    for fname, text in (("a.txt", "test"), ("b.txt", "abcd")):
        entry = by_path[fname]
        assert "content" in entry and "content_base64" not in entry
        assert entry["content"] == text
        # Recorded sha is over the verbatim UTF-8 bytes the server will store.
        assert state_by_path[fname].sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def test_recorded_purpose_is_reemitted_on_reference_and_inline(tmp_path: Path) -> None:
    """A recorded ``purpose`` is carried forward onto the wire entry for both an
    unchanged path (reference entry) and a changed path (inline entry).

    The push sends a full file snapshot, so dropping the label here would reset
    it to null on the server and strip the role label the designer set across a
    pull-edit-push round trip. A path with no recorded purpose leaves the key off
    its wire entry entirely.
    """
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")

    # Unchanged file: on-disk content matches the recorded sha (reference entry).
    unchanged_text = "stable helper\n"
    (skill_dir / "helper.sh").write_text(unchanged_text, encoding="utf-8")
    unchanged_sha = _sha256_text(unchanged_text)

    # Changed file: on-disk content differs from the recorded sha (inline entry).
    changed_text = "new query body\n"
    (skill_dir / "query.sql").write_text(changed_text, encoding="utf-8")

    recorded = [
        FileState(path="helper.sh", sha256=unchanged_sha, executable=False, purpose="setup script"),
        FileState(path="query.sql", sha256="0" * 64, executable=False, purpose="data query"),
    ]
    payload, states = build_files_payload(skill_dir, recorded, [])
    by_path = {e["path"]: e for e in payload}

    # Unchanged => reference entry (sha, no content) carrying the recorded purpose.
    ref_entry = by_path["helper.sh"]
    assert "sha256" in ref_entry and "content" not in ref_entry
    assert ref_entry["purpose"] == "setup script"

    # Changed => inline entry (content) carrying the recorded purpose.
    inline_entry = by_path["query.sql"]
    assert "content" in inline_entry
    assert inline_entry["purpose"] == "data query"

    # The carried-forward purpose is also recorded back into the local state.
    state_by_path = {s.path: s for s in states}
    assert state_by_path["helper.sh"].purpose == "setup script"
    assert state_by_path["query.sql"].purpose == "data query"


def test_purpose_none_omits_key_on_reference_and_inline(tmp_path: Path) -> None:
    """A path whose recorded purpose is ``None`` (or which has no recorded row)
    leaves the ``purpose`` key off its wire entry, so the server is not handed an
    explicit null. Covers both the reference (unchanged) and inline (changed)
    branches."""
    skill_dir = tmp_path / "skl"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")

    # Unchanged file with a recorded row whose purpose is None.
    unchanged_text = "no label here\n"
    (skill_dir / "helper.sh").write_text(unchanged_text, encoding="utf-8")
    unchanged_sha = _sha256_text(unchanged_text)

    # Brand-new local file with no recorded row at all (also purpose-less).
    (skill_dir / "brand_new.txt").write_text("fresh content\n", encoding="utf-8")

    recorded = [FileState(path="helper.sh", sha256=unchanged_sha, executable=False, purpose=None)]
    payload, states = build_files_payload(skill_dir, recorded, [])
    by_path = {e["path"]: e for e in payload}

    # Reference entry for the unchanged, purpose-None file omits the key.
    ref_entry = by_path["helper.sh"]
    assert "sha256" in ref_entry and "content" not in ref_entry
    assert "purpose" not in ref_entry

    # Inline entry for the brand-new file (no recorded row) omits the key.
    inline_entry = by_path["brand_new.txt"]
    assert "content" in inline_entry
    assert "purpose" not in inline_entry

    # The recorded state also carries no purpose for either path.
    state_by_path = {s.path: s for s in states}
    assert state_by_path["helper.sh"].purpose is None
    assert state_by_path["brand_new.txt"].purpose is None


def _clean_entry(
    target_dir: Path,
    *,
    id_: str,
    slug: str,
    body: str,
    token: str = "tok-1",
    files: list[FileState] | None = None,
) -> SyncEntry:
    """Entry whose recorded body hash matches ``body`` on disk.

    Use when the body is intentionally clean so that only a sibling-tree change
    (a brand-new file, a deletion, an ignore-spec shift) can make it a push
    candidate. This is what isolates whole-tree drift detection from body drift.
    """
    return SyncEntry(
        workflow_id=id_,
        slug=slug,
        target_path=normalize_target_path(str(target_dir)),
        synced_version=1,
        version_token=token,
        body_sha256=body_sha256(body),
        files=files or [],
    )


@respx.mock
def test_push_uploads_brand_new_file_with_no_other_change(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A brand-new non-ignored file, with the body and every tracked sibling
    unchanged, is detected as drift and uploaded.

    This is the reported gap: detection used to look only at tracked siblings,
    so a new file added to an otherwise-clean directory was never seen and push
    selected no candidate. The assertion runs through ``push`` end to end, not
    ``build_files_payload`` in isolation, so it exercises the detection path.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    slug = "my-workflow"
    body = _push_body(slug)
    _write_skill(target_dir, slug, body)

    # An already-tracked sibling, unchanged on disk.
    helper_content = "#!/bin/bash\necho hello\n"
    helper_sha = _sha256_text(helper_content)
    _write_sibling(target_dir, slug, "helper.sh", helper_content)

    # A brand-new, non-ignored file with no recorded row and nothing else changed.
    new_content = "id,value\n1,42\n"
    _write_sibling(target_dir, slug, "data.csv", new_content)

    state = SyncState(
        entries=[
            _clean_entry(
                target_dir,
                id_="skl_new",
                slug=slug,
                body=body,
                files=[FileState(path="helper.sh", sha256=helper_sha, executable=False)],
            )
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_new", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert [(i.slug, i.action) for i in result.items] == [(slug, "pushed")]
    assert save_route.call_count == 1
    sent = json.loads(save_route.calls[0].request.content)
    files_by_path = {f["path"]: f for f in sent["files"]}
    # The brand-new file is uploaded inline.
    assert "data.csv" in files_by_path
    assert files_by_path["data.csv"].get("content") == new_content
    # The unchanged tracked sibling rides along as a reference.
    assert files_by_path["helper.sh"]["sha256"] == helper_sha


@respx.mock
def test_push_propagates_deleted_sibling(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    """Deleting a tracked sibling (body and other siblings unchanged) is detected
    as drift, and the uploaded full snapshot omits the deleted file.

    Push sends a full snapshot, so a file absent from the payload is dropped on
    the server. Detection has to notice the deletion for that snapshot to be sent
    at all.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    slug = "my-workflow"
    body = _push_body(slug)
    _write_skill(target_dir, slug, body)

    helper_content = "#!/bin/bash\necho hello\n"
    helper_sha = _sha256_text(helper_content)
    _write_sibling(target_dir, slug, "helper.sh", helper_content)

    data_content = '{"key": "value"}'
    data_sha = _sha256_text(data_content)
    # data.json is recorded but NOT written to disk: it was deleted locally.

    state = SyncState(
        entries=[
            _clean_entry(
                target_dir,
                id_="skl_del",
                slug=slug,
                body=body,
                files=[
                    FileState(path="helper.sh", sha256=helper_sha, executable=False),
                    FileState(path="data.json", sha256=data_sha, executable=False),
                ],
            )
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_del", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert [(i.slug, i.action) for i in result.items] == [(slug, "pushed")]
    sent = json.loads(save_route.calls[0].request.content)
    paths_sent = {f["path"] for f in sent["files"]}
    assert "helper.sh" in paths_sent
    assert "data.json" not in paths_sent


@respx.mock
def test_push_ignored_new_file_is_not_drift(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    """A new file matched by the effective ignore spec does not trigger drift.

    The ``.goodeyeignore`` itself is recorded and unchanged, so the only on-disk
    change is an ignored file. Detection honors the live ignore spec, so the
    workflow stays clean and push selects no candidate.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    slug = "my-workflow"
    body = _push_body(slug)
    _write_skill(target_dir, slug, body)

    ignore_content = "*.log\n"
    ignore_sha = _sha256_text(ignore_content)
    _write_sibling(target_dir, slug, ".goodeyeignore", ignore_content)

    helper_content = "#!/bin/bash\necho hello\n"
    helper_sha = _sha256_text(helper_content)
    _write_sibling(target_dir, slug, "helper.sh", helper_content)

    # The only new on-disk file is ignored by the recorded .goodeyeignore.
    _write_sibling(target_dir, slug, "debug.log", "noise\n")

    state = SyncState(
        entries=[
            _clean_entry(
                target_dir,
                id_="skl_ign",
                slug=slug,
                body=body,
                files=[
                    FileState(path=".goodeyeignore", sha256=ignore_sha, executable=False),
                    FileState(path="helper.sh", sha256=helper_sha, executable=False),
                ],
            )
        ]
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    # No candidate, nothing uploaded: the ignored file is not spurious drift.
    assert result.items == []


@respx.mock
def test_push_uploads_visible_new_file_skips_ignored_new_file(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A new visible file triggers drift and uploads; a co-present new ignored
    file is excluded from the payload.

    Proves detection and upload share the same live ignore spec: the visible
    file is both seen and sent, the ignored file is neither.
    """
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    slug = "my-workflow"
    body = _push_body(slug)
    _write_skill(target_dir, slug, body)

    ignore_content = "*.log\n"
    ignore_sha = _sha256_text(ignore_content)
    _write_sibling(target_dir, slug, ".goodeyeignore", ignore_content)

    # Two new files: one visible, one ignored by the live .goodeyeignore.
    _write_sibling(target_dir, slug, "notes.md", "# notes\n")
    _write_sibling(target_dir, slug, "debug.log", "noise\n")

    state = SyncState(
        entries=[
            _clean_entry(
                target_dir,
                id_="skl_mix",
                slug=slug,
                body=body,
                files=[FileState(path=".goodeyeignore", sha256=ignore_sha, executable=False)],
            )
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_mix", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert [(i.slug, i.action) for i in result.items] == [(slug, "pushed")]
    sent = json.loads(save_route.calls[0].request.content)
    paths_sent = {f["path"] for f in sent["files"]}
    assert "notes.md" in paths_sent
    assert "debug.log" not in paths_sent
