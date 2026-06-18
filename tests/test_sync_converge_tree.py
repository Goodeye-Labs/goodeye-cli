"""Multi-target convergence tests for the sibling file tree.

When a workflow is mirrored into more than one local target and a push from one
copy changes a sibling file, the other copies must end up with the correct
sibling tree, either immediately (when the push left siblings unchanged) or after
the next ordinary pull (when the push changed siblings). A copy whose siblings
are stale must never be reported up to date, and a copy's own un-pushed sibling
edit must never be clobbered.

These cover the gap the body-only convergence tests in ``test_sync.py`` leave:
they assert only on the SKILL.md body and the version token, never on siblings.
"""

from __future__ import annotations

import hashlib
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
    load_sync_state,
    normalize_target_path,
    pull,
    push,
)

SERVER = "https://example.test"


# ---- helpers ---------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _me_route(email: str = "owner@example.com") -> respx.Route:
    return respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": email})
    )


def _body(slug: str, marker: str = "do the work") -> str:
    return (
        f"---\nname: {slug}\n"
        "description: A test workflow.\n"
        "outcome: Achieve the test goal.\n"
        f"---\n\n{marker}\n"
    )


def _save_response(
    *, workflow_id: str, name: str, version: int = 2, token: str = "tok-2"
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


def _list_response(items: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"items": items, "next_cursor": None})


def _summary_dict(*, id_: str, name: str, token: str, version: int) -> dict:
    return {
        "id": id_,
        "name": name,
        "current_version": version,
        "version_token": token,
        "effective_role": "owner",
    }


def _detail_response(
    *, id_: str, name: str, body: str, token: str, version: int, files: list[dict]
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": id_,
            "name": name,
            "version": version,
            "body": body,
            "version_token": token,
            "effective_role": "owner",
            "verifiers": [],
            "files": files,
        },
    )


def _manifest_row(path: str, content: str, *, executable: bool = False) -> dict:
    return {
        "path": path,
        "sha256": _sha256_text(content),
        "size_bytes": len(content),
        "executable": executable,
        "content_kind": "text",
    }


def _file_envelope(path: str, content: str, *, executable: bool = False) -> dict:
    return {"path": path, "executable": executable, "content": content}


def _write_skill(target_dir: Path, slug: str, body: str) -> None:
    skill = target_dir / slug / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body, encoding="utf-8")


def _write_sibling(target_dir: Path, slug: str, rel_path: str, content: str) -> None:
    p = target_dir / slug / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _two_target_config(first: Path, second: Path) -> SyncConfig:
    return SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="all"),
            SyncTarget(path=str(second), scope="all"),
        ]
    )


def _entry(
    target_dir: Path,
    *,
    id_: str,
    slug: str,
    body: str,
    token: str,
    files: list[FileState],
) -> SyncEntry:
    return SyncEntry(
        workflow_id=id_,
        slug=slug,
        target_path=normalize_target_path(str(target_dir)),
        synced_version=1,
        version_token=token,
        body_sha256=body_sha256(body),
        files=files,
    )


# ---- defer a siblings-stale copy to the next pull --------------------------


@respx.mock
def test_push_sibling_change_defers_other_copy_then_pull_heals(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A sibling-only edit pushed from A leaves B behind-server, and a normal pull heals B.

    A and B share an identical body; only A's sibling was edited. Convergence
    must not advance B's token (its on-disk sibling is stale), must report B as
    needing a pull, and the next non-forced pull must bring B's sibling, manifest,
    and token current.
    """
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)

    slug = "alpha"
    shared_body = _body(slug)
    sib_v1 = "helper v1\n"
    sib_v2 = "helper v2\n"

    # A: clean body, sibling edited to v2 (un-pushed) -> a push candidate.
    _write_skill(first, slug, shared_body)
    _write_sibling(first, slug, "helper.sh", sib_v2)
    # B: clean body, sibling still v1 (matches its recorded manifest).
    _write_skill(second, slug, shared_body)
    _write_sibling(second, slug, "helper.sh", sib_v1)

    state = SyncState(
        entries=[
            _entry(
                first,
                id_="skl_a",
                slug=slug,
                body=shared_body,
                token="tok-1",
                files=[FileState(path="helper.sh", sha256=_sha256_text(sib_v1))],
            ),
            _entry(
                second,
                id_="skl_a",
                slug=slug,
                body=shared_body,
                token="tok-1",
                files=[FileState(path="helper.sh", sha256=_sha256_text(sib_v1))],
            ),
        ]
    )

    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    # A uploaded once; B is flagged as needing a pull, not "converged".
    assert save_route.call_count == 1
    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "pull-required") in actions

    # B was left untouched: stale sibling on disk, recorded token and manifest unchanged.
    assert (second / slug / "helper.sh").read_text(encoding="utf-8") == sib_v1
    persisted = {
        normalize_target_path(e.target_path): e for e in load_sync_state(tmp_config_paths).entries
    }
    b_entry = persisted[normalize_target_path(str(second))]
    assert b_entry.version_token == "tok-1"
    assert [(f.path, f.sha256) for f in b_entry.files] == [
        ("helper.sh", _sha256_text(sib_v1))
    ]

    # The next ordinary (non-forced) pull heals B through the existing pull path.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_a", name=slug, token="tok-2", version=2)]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(
            id_="skl_a",
            name=slug,
            body=shared_body,
            token="tok-2",
            version=2,
            files=[_manifest_row("helper.sh", sib_v2)],
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_a/files").mock(
        return_value=httpx.Response(200, json={"files": [_file_envelope("helper.sh", sib_v2)]})
    )

    state2 = load_sync_state(tmp_config_paths)
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        pull_result = pull(
            client,
            config,
            state2,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    pull_actions = {(i.target_path, i.action) for i in pull_result.items}
    # A was already current; B was fetched and refreshed.
    assert (normalize_target_path(str(first)), "up-to-date") in pull_actions
    assert (normalize_target_path(str(second)), "pulled") in pull_actions

    # B's sibling, recorded manifest, and token are now current.
    assert (second / slug / "helper.sh").read_text(encoding="utf-8") == sib_v2
    healed = {
        normalize_target_path(e.target_path): e for e in load_sync_state(tmp_config_paths).entries
    }
    b_healed = healed[normalize_target_path(str(second))]
    assert b_healed.version_token == "tok-2"
    assert [(f.path, f.sha256) for f in b_healed.files] == [
        ("helper.sh", _sha256_text(sib_v2))
    ]


# ---- anti-clobber: B's own un-pushed sibling edit is preserved -------------


@respx.mock
def test_push_does_not_clobber_other_copys_unpushed_sibling_edit(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """B holds its own un-pushed sibling edit; a push from A never destroys it.

    Convergence defers B (no token advance), and the later pull refuses the
    unforced overwrite (B's tree diverged), so B's distinct edit survives both
    steps.
    """
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)

    slug = "beta"
    shared_body = _body(slug)
    sib_v1 = "helper v1\n"
    sib_a = "helper edited in A\n"
    sib_b = "helper edited in B - DISTINCT\n"

    # A: clean body, sibling edited to A's version.
    _write_skill(first, slug, shared_body)
    _write_sibling(first, slug, "helper.sh", sib_a)
    # B: clean body, sibling edited to B's own distinct version (un-pushed).
    _write_skill(second, slug, shared_body)
    _write_sibling(second, slug, "helper.sh", sib_b)

    # Both recorded at the v1 sibling so each reads as a local sibling edit.
    state = SyncState(
        entries=[
            _entry(
                first,
                id_="skl_b",
                slug=slug,
                body=shared_body,
                token="tok-1",
                files=[FileState(path="helper.sh", sha256=_sha256_text(sib_v1))],
            ),
            _entry(
                second,
                id_="skl_b",
                slug=slug,
                body=shared_body,
                token="tok-1",
                files=[FileState(path="helper.sh", sha256=_sha256_text(sib_v1))],
            ),
        ]
    )

    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_b", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "pull-required") in actions

    # B's distinct edit is untouched and its token did not advance.
    assert (second / slug / "helper.sh").read_text(encoding="utf-8") == sib_b
    persisted = {
        normalize_target_path(e.target_path): e for e in load_sync_state(tmp_config_paths).entries
    }
    assert persisted[normalize_target_path(str(second))].version_token == "tok-1"

    # The deferred pull refuses to overwrite B's diverged tree, preserving the edit.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_b", name=slug, token="tok-2", version=2)]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_b").mock(
        return_value=_detail_response(
            id_="skl_b",
            name=slug,
            body=shared_body,
            token="tok-2",
            version=2,
            files=[_manifest_row("helper.sh", sib_a)],
        )
    )

    state2 = load_sync_state(tmp_config_paths)
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        pull_result = pull(
            client,
            config,
            state2,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    pull_actions = {(i.target_path, i.action) for i in pull_result.items}
    assert (normalize_target_path(str(second)), "skipped-conflict") in pull_actions
    # B's own edit is still intact; the conflicted copy was never fetched/overwritten.
    assert detail_route.call_count == 0
    assert (second / slug / "helper.sh").read_text(encoding="utf-8") == sib_b


# ---- regression guard: body-only edit still converges immediately ----------


@respx.mock
def test_push_body_only_edit_converges_other_copy_immediately(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A body-only edit (siblings unchanged) still converges B in place, token advanced.

    This pins the existing fast convergence so the defer-to-pull change does not
    regress the common case.
    """
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)

    slug = "gamma"
    old_body = _body(slug, marker="old body")
    new_body = _body(slug, marker="new body edited in A")
    sib = "helper unchanged\n"

    # A: body edited (a candidate); sibling unchanged.
    _write_skill(first, slug, new_body)
    _write_sibling(first, slug, "helper.sh", sib)
    # B: clean body; sibling unchanged.
    _write_skill(second, slug, old_body)
    _write_sibling(second, slug, "helper.sh", sib)

    state = SyncState(
        entries=[
            _entry(
                first,
                id_="skl_g",
                slug=slug,
                body=old_body,  # recorded base differs from A's on-disk new body
                token="tok-1",
                files=[FileState(path="helper.sh", sha256=_sha256_text(sib))],
            ),
            _entry(
                second,
                id_="skl_g",
                slug=slug,
                body=old_body,  # B's on-disk body matches this (clean)
                token="tok-1",
                files=[FileState(path="helper.sh", sha256=_sha256_text(sib))],
            ),
        ]
    )

    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_g", name=slug)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "converged") in actions

    # B converged in place: new body on disk, token advanced, sibling intact.
    assert (second / slug / "SKILL.md").read_text(encoding="utf-8") == new_body
    assert (second / slug / "helper.sh").read_text(encoding="utf-8") == sib
    b_entry = {
        normalize_target_path(e.target_path): e for e in load_sync_state(tmp_config_paths).entries
    }[normalize_target_path(str(second))]
    assert b_entry.version_token == "tok-2"
    assert [(f.path, f.sha256) for f in b_entry.files] == [("helper.sh", _sha256_text(sib))]
