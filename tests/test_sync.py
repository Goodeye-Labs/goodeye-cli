"""Tests for the local sync configuration and target engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import httpx
import pytest
import respx

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import Conflict, NotFound, ServerError, ValidationFailed
from goodeye_cli.sync import (
    PRESETS,
    PushItem,
    SyncConfig,
    SyncEntry,
    SyncState,
    SyncTarget,
    SyncVerifierBinding,
    add_target,
    body_sha256,
    ensure_identity,
    expand_target_path,
    find_entry,
    is_modified_locally,
    list_targets,
    load_sync_config,
    load_sync_state,
    local_skill_dir,
    local_skill_path,
    normalize_target_path,
    pull,
    push,
    read_local_body,
    remove_target,
    resolve_preset,
    save_sync_config,
    save_sync_state,
    scope_filter,
    select_for_target,
    server_moved,
    slug_in_target_scope,
    status,
    untracked_local_slugs,
    upsert_entry,
)
from goodeye_cli.wire import WorkflowSummary

SERVER = "https://example.test"


def test_load_returns_default_when_file_missing(tmp_config_paths: ConfigPaths) -> None:
    config = load_sync_config(tmp_config_paths)
    assert config == SyncConfig()
    assert config.version == 1
    assert config.identity is None
    assert config.targets == []


def test_identity_defaults_to_none(tmp_config_paths: ConfigPaths) -> None:
    # A fresh config never stamps an identity: configuring targets is local.
    assert SyncConfig().identity is None
    config = load_sync_config(tmp_config_paths)
    assert config.identity is None


def test_save_then_load_roundtrip(tmp_config_paths: ConfigPaths) -> None:
    config = SyncConfig(
        identity="user@example.com",
        targets=[SyncTarget(path="~/.claude/skills", scope="owned")],
    )
    written = save_sync_config(config, tmp_config_paths)
    assert written == tmp_config_paths.sync_file
    assert written.exists()

    reloaded = load_sync_config(tmp_config_paths)
    assert reloaded == config
    assert reloaded.identity == "user@example.com"
    assert reloaded.targets[0].path == "~/.claude/skills"


def test_add_target_by_path(tmp_config_paths: ConfigPaths) -> None:
    config = SyncConfig()
    target = add_target(config, path="~/work/skills", preset=None, scope="owned", only=[])
    assert target.path == "~/work/skills"
    assert target.scope == "owned"
    assert target.selected == []
    assert target.link is False
    assert config.targets == [target]


def test_add_target_by_preset_expands_and_stores_tilde(tmp_config_paths: ConfigPaths) -> None:
    config = SyncConfig()
    target = add_target(config, path=None, preset="claude", scope="owned", only=[])
    assert target.path == PRESETS["claude"]
    assert target.path == "~/.claude/skills"


def test_add_target_normalizes_absolute_home_path_to_tilde() -> None:
    config = SyncConfig()
    absolute = str(Path.home() / "mirror" / "skills")
    target = add_target(config, path=absolute, preset=None, scope="owned", only=[])
    assert target.path == "~/mirror/skills"


def test_add_target_selected_scope_stores_only(tmp_config_paths: ConfigPaths) -> None:
    config = SyncConfig()
    target = add_target(
        config,
        path="~/skills",
        preset=None,
        scope="selected",
        only=["refunds-*", "onboarding"],
    )
    assert target.scope == "selected"
    assert target.selected == ["refunds-*", "onboarding"]


def test_resolve_preset_unknown_raises(tmp_config_paths: ConfigPaths) -> None:
    with pytest.raises(ValidationFailed) as exc:
        resolve_preset("vim")
    assert "claude" in str(exc.value)


def test_add_target_unknown_preset_raises() -> None:
    config = SyncConfig()
    with pytest.raises(ValidationFailed):
        add_target(config, path=None, preset="nope", scope="owned", only=[])


def test_add_target_requires_path_or_preset() -> None:
    config = SyncConfig()
    with pytest.raises(ValidationFailed, match="exactly one"):
        add_target(config, path=None, preset=None, scope="owned", only=[])


def test_add_target_rejects_both_path_and_preset() -> None:
    config = SyncConfig()
    with pytest.raises(ValidationFailed, match="exactly one"):
        add_target(config, path="~/skills", preset="claude", scope="owned", only=[])


def test_add_target_only_requires_selected_scope() -> None:
    config = SyncConfig()
    with pytest.raises(ValidationFailed, match="selected"):
        add_target(config, path="~/skills", preset=None, scope="owned", only=["x"])


def test_add_target_duplicate_path_raises_conflict() -> None:
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    with pytest.raises(Conflict):
        add_target(config, path="~/skills", preset=None, scope="all", only=[])


def test_add_target_duplicate_detected_across_path_forms() -> None:
    # The ``~`` form and the equivalent absolute form must dedupe to one target.
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    absolute = str(Path.home() / "skills")
    with pytest.raises(Conflict):
        add_target(config, path=absolute, preset=None, scope="owned", only=[])


def test_add_target_rejects_relative_path() -> None:
    # A relative path would resolve CWD-relative and is not portable.
    config = SyncConfig()
    with pytest.raises(ValidationFailed, match="absolute"):
        add_target(config, path="skills", preset=None, scope="owned", only=[])


def test_add_target_rejects_dotdot_relative_path() -> None:
    config = SyncConfig()
    with pytest.raises(ValidationFailed, match="absolute"):
        add_target(config, path="../skills", preset=None, scope="owned", only=[])


def test_add_target_dedupes_dotdot_against_clean_form() -> None:
    # ``~/foo/../skills`` collapses to ``~/skills`` and dedupes against it.
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    with pytest.raises(Conflict):
        add_target(config, path="~/foo/../skills", preset=None, scope="all", only=[])


def test_add_target_normalizes_dotdot_in_stored_path() -> None:
    config = SyncConfig()
    target = add_target(config, path="~/foo/../skills", preset=None, scope="owned", only=[])
    assert target.path == "~/skills"


def test_remove_target_found() -> None:
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    assert remove_target(config, "~/skills") is True
    assert config.targets == []


def test_remove_target_matches_equivalent_absolute_form() -> None:
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    absolute = str(Path.home() / "skills")
    assert remove_target(config, absolute) is True
    assert config.targets == []


def test_remove_target_matches_dotdot_form() -> None:
    # A ``..`` spelling collapses to the stored clean form and removes it.
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    assert remove_target(config, "~/foo/../skills") is True
    assert config.targets == []


def test_remove_target_not_found() -> None:
    config = SyncConfig()
    add_target(config, path="~/skills", preset=None, scope="owned", only=[])
    assert remove_target(config, "~/other") is False
    assert len(config.targets) == 1


def test_list_targets_returns_targets() -> None:
    config = SyncConfig()
    first = add_target(config, path="~/a", preset=None, scope="owned", only=[])
    second = add_target(config, path="~/b", preset=None, scope="all", only=[])
    assert list_targets(config) == [first, second]


def test_load_malformed_file_raises(tmp_config_paths: ConfigPaths) -> None:
    tmp_config_paths.sync_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_config_paths.sync_file.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_sync_config(tmp_config_paths)
    assert str(tmp_config_paths.sync_file) in str(exc.value)


def test_save_writes_json_payload(tmp_config_paths: ConfigPaths) -> None:
    config = SyncConfig(targets=[SyncTarget(path="~/skills", scope="all")])
    save_sync_config(config, tmp_config_paths)
    with tmp_config_paths.sync_file.open(encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["version"] == 1
    assert on_disk["identity"] is None
    assert on_disk["targets"][0]["path"] == "~/skills"
    assert on_disk["targets"][0]["scope"] == "all"


def test_normalize_path_outside_home_stays_absolute() -> None:
    # A path that is not under the home directory keeps its absolute form.
    outside = "/tmp/goodeye-skills-fixture"
    assert normalize_target_path(outside) == str(Path(outside))


def test_expand_target_path_expands_tilde() -> None:
    assert expand_target_path("~/skills") == Path.home() / "skills"


# ----- sync-state index -----


def test_load_state_returns_default_when_file_missing(tmp_config_paths: ConfigPaths) -> None:
    state = load_sync_state(tmp_config_paths)
    assert state == SyncState()
    assert state.version == 1
    assert state.identity is None
    assert state.entries == []


def test_state_save_then_load_roundtrip(tmp_config_paths: ConfigPaths) -> None:
    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="skl_01",
                slug="incident-postmortem",
                target_path="~/.claude/skills",
                synced_version=3,
                version_token="v-tok",
                body_sha256=body_sha256("hello"),
                verifier_bindings=[
                    SyncVerifierBinding(name="tone-check", verifier_id="vrf_1", version=2)
                ],
                effective_role="owner",
            )
        ],
    )
    written = save_sync_state(state, tmp_config_paths)
    assert written == tmp_config_paths.sync_state_file
    assert written.exists()

    reloaded = load_sync_state(tmp_config_paths)
    assert reloaded == state
    assert reloaded.entries[0].verifier_bindings[0].verifier_id == "vrf_1"


def test_load_state_malformed_file_raises(tmp_config_paths: ConfigPaths) -> None:
    tmp_config_paths.sync_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_config_paths.sync_state_file.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_sync_state(tmp_config_paths)
    assert str(tmp_config_paths.sync_state_file) in str(exc.value)


def test_save_state_writes_0600(tmp_config_paths: ConfigPaths) -> None:
    save_sync_state(SyncState(), tmp_config_paths)
    mode = tmp_config_paths.sync_state_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_body_sha256_is_stable_and_matches_hashlib() -> None:
    import hashlib

    body = "front-matter\n\nbody text"
    assert body_sha256(body) == body_sha256(body)
    assert body_sha256(body) == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_body_sha256_differs_on_change() -> None:
    assert body_sha256("a") != body_sha256("b")


def _entry(slug: str, target_path: str, *, token: str = "tok") -> SyncEntry:
    return SyncEntry(
        workflow_id=f"skl_{slug}",
        slug=slug,
        target_path=target_path,
        synced_version=1,
        version_token=token,
        body_sha256=body_sha256(f"body-{slug}"),
    )


def test_find_entry_matches_slug_and_normalized_path() -> None:
    state = SyncState(entries=[_entry("alpha", "~/skills")])
    absolute = str(Path.home() / "skills")
    found = find_entry(state, slug="alpha", target_path=absolute)
    assert found is not None
    assert found.workflow_id == "skl_alpha"


def test_find_entry_misses_other_slug_or_target() -> None:
    state = SyncState(entries=[_entry("alpha", "~/skills")])
    assert find_entry(state, slug="beta", target_path="~/skills") is None
    assert find_entry(state, slug="alpha", target_path="~/other") is None


def test_upsert_entry_replaces_in_place() -> None:
    state = SyncState(entries=[_entry("alpha", "~/skills", token="old")])
    upsert_entry(state, _entry("alpha", "~/skills", token="new"))
    assert len(state.entries) == 1
    assert state.entries[0].version_token == "new"


def test_upsert_entry_appends_new() -> None:
    state = SyncState(entries=[_entry("alpha", "~/skills")])
    upsert_entry(state, _entry("beta", "~/skills"))
    assert {e.slug for e in state.entries} == {"alpha", "beta"}


# ----- scope + change detection primitives -----


def test_scope_filter_mapping() -> None:
    assert scope_filter("owned") == "mine"
    assert scope_filter("all") == "all"
    assert scope_filter("selected") == "all"


def _summary(name: str, *, token: str = "tok") -> WorkflowSummary:
    return WorkflowSummary(id=f"skl_{name}", name=name, current_version=1, version_token=token)


def test_select_for_target_owned_keeps_all() -> None:
    target = SyncTarget(path="~/skills", scope="owned")
    summaries = [_summary("a"), _summary("b")]
    assert select_for_target(target, summaries) == summaries


def test_select_for_target_selected_applies_globs() -> None:
    target = SyncTarget(path="~/skills", scope="selected", selected=["refunds-*", "onboarding"])
    summaries = [_summary("refunds-flow"), _summary("onboarding"), _summary("unrelated")]
    chosen = select_for_target(target, summaries)
    assert {s.name for s in chosen} == {"refunds-flow", "onboarding"}


def test_select_for_target_intersects_slug_args() -> None:
    target = SyncTarget(path="~/skills", scope="all")
    summaries = [_summary("a"), _summary("b"), _summary("c")]
    chosen = select_for_target(target, summaries, slugs=["b"])
    assert {s.name for s in chosen} == {"b"}


def test_local_skill_path_layout() -> None:
    target = SyncTarget(path="~/skills", scope="owned")
    assert local_skill_path(target, "alpha") == Path.home() / "skills" / "alpha" / "SKILL.md"


def test_read_local_body_none_when_missing(tmp_path: Path) -> None:
    target = SyncTarget(path=str(tmp_path), scope="owned")
    assert read_local_body(target, "alpha") is None


def test_read_local_body_returns_contents(tmp_path: Path) -> None:
    target = SyncTarget(path=str(tmp_path), scope="owned")
    skill = tmp_path / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("body text", encoding="utf-8")
    assert read_local_body(target, "alpha") == "body text"


def test_is_modified_locally() -> None:
    entry = _entry("alpha", "~/skills")
    entry.body_sha256 = body_sha256("original")
    assert is_modified_locally(entry, "original") is False
    assert is_modified_locally(entry, "edited") is True
    assert is_modified_locally(entry, None) is False


def test_server_moved() -> None:
    entry = _entry("alpha", "~/skills", token="v1")
    assert server_moved(entry, _summary("alpha", token="v1")) is False
    assert server_moved(entry, _summary("alpha", token="v2")) is True


def test_server_moved_true_when_token_missing() -> None:
    entry = SyncEntry(
        workflow_id="x",
        slug="alpha",
        target_path="~/skills",
        synced_version=1,
        version_token=None,
        body_sha256=body_sha256("b"),
    )
    assert server_moved(entry, _summary("alpha", token="v1")) is True


# ----- pull engine -----

# The identity the guard resolves for pull/push/status engine tests. The engine
# stamps this on a fresh index and accepts it on a stamped one, so the guard is
# transparent to tests that do not specifically exercise a mismatch.
DEFAULT_EMAIL = "owner@example.com"


def _me_route(email: str = DEFAULT_EMAIL) -> respx.Route:
    """Register the GET /v1/me route the identity guard calls."""
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
    verifiers: list[dict] | None = None,
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
            "verifiers": verifiers or [],
        },
    )


@respx.mock
def test_pull_materializes_skill_and_index(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    state = SyncState()

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {
                    "id": "skl_01",
                    "name": "incident-postmortem",
                    "current_version": 1,
                    "version_token": "tok-1",
                    "effective_role": "owner",
                }
            ]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(
            id_="skl_01",
            name="incident-postmortem",
            body="---\nname: incident-postmortem\n---\nrun it",
            token="tok-1",
            verifiers=[{"name": "tone", "verifier_id": "vrf_1", "version": 2}],
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

    assert [(i.slug, i.action) for i in result.items] == [("incident-postmortem", "pulled")]
    written = target_dir / "incident-postmortem" / "SKILL.md"
    assert written.read_text(encoding="utf-8") == "---\nname: incident-postmortem\n---\nrun it"

    reloaded = load_sync_state(tmp_config_paths)
    entry = reloaded.entries[0]
    assert entry.workflow_id == "skl_01"
    assert entry.slug == "incident-postmortem"
    assert entry.synced_version == 1
    assert entry.version_token == "tok-1"
    assert entry.body_sha256 == body_sha256("---\nname: incident-postmortem\n---\nrun it")
    assert entry.verifier_bindings == [
        SyncVerifierBinding(name="tone", verifier_id="vrf_1", version=2)
    ]
    assert entry.read_only is False


@respx.mock
def test_pull_view_role_is_read_only(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="all")])
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {
                    "id": "skl_v",
                    "name": "shared-doc",
                    "current_version": 1,
                    "version_token": "t",
                    "effective_role": "view",
                }
            ]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_v").mock(
        return_value=_detail_response(
            id_="skl_v", name="shared-doc", body="body", effective_role="view"
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
    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.effective_role == "view"
    assert entry.read_only is True


@respx.mock
def test_pull_skips_modified_without_force_and_overwrites_with_force(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])

    # An already-synced entry whose disk copy has been hand-edited since.
    skill = target_dir / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("locally edited", encoding="utf-8")
    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="skl_a",
                slug="alpha",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                body_sha256=body_sha256("server original"),
            )
        ]
    )

    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_a", "name": "alpha", "current_version": 2, "version_token": "t2"}]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body="server v2", token="t2")
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
    # Server token also moved (t1 -> t2), so this is a conflict, not plain modified.
    assert [(i.slug, i.action) for i in result.items] == [("alpha", "skipped-conflict")]
    assert skill.read_text(encoding="utf-8") == "locally edited"
    assert detail_route.call_count == 0  # no body fetch while protecting local edits
    assert list_route.call_count == 1

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        forced = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=True,
            yes=False,
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in forced.items] == [("alpha", "pulled")]
    assert skill.read_text(encoding="utf-8") == "server v2"
    assert detail_route.call_count == 1


@respx.mock
def test_pull_skipped_modified_when_server_not_moved(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    skill = target_dir / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("locally edited", encoding="utf-8")
    state = SyncState(
        entries=[
            SyncEntry(
                workflow_id="skl_a",
                slug="alpha",
                target_path=normalize_target_path(str(target_dir)),
                synced_version=1,
                version_token="t1",
                body_sha256=body_sha256("server original"),
            )
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t1"}]
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
    assert [(i.slug, i.action) for i in result.items] == [("alpha", "skipped-modified")]


@respx.mock
def test_pull_untracked_present_file_is_skipped_modified(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # A pre-existing SKILL.md with no index entry has no recorded sync point, so
    # it is reported as plain modified (not a conflict) and left untouched
    # without a body fetch unless the caller forces the overwrite.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    skill = target_dir / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("hand written", encoding="utf-8")

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t"}]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body="server body")
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
    assert [(i.slug, i.action) for i in result.items] == [("alpha", "skipped-modified")]
    assert skill.read_text(encoding="utf-8") == "hand written"
    assert detail_route.call_count == 0  # no body fetch while protecting the file
    # Nothing was written to disk, so the file stays untracked in the index.
    assert load_sync_state(tmp_config_paths).entries == []


@respx.mock
def test_pull_slug_filter_applies_to_every_target(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # The same positional slug args narrow every configured target, not just the
    # first: a `pull beta` against two targets writes beta into both and pulls
    # alpha into neither.
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="all"),
            SyncTarget(path=str(second), scope="all"),
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t"},
                {"id": "skl_b", "name": "beta", "current_version": 1, "version_token": "t"},
            ]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_b").mock(
        return_value=_detail_response(id_="skl_b", name="beta", body="beta body")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=["beta"],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in result.items] == [("beta", "pulled"), ("beta", "pulled")]
    assert (first / "beta" / "SKILL.md").exists()
    assert (second / "beta" / "SKILL.md").exists()
    assert not (first / "alpha").exists()
    assert not (second / "alpha").exists()


@respx.mock
def test_pull_up_to_date_skips_second_fetch(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    state = SyncState()

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok-1"}]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="run it", token="tok-1")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        first = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )
        assert [i.action for i in first.items] == ["pulled"]
        assert detail_route.call_count == 1

        # Second run: nothing changed on either side, so no body fetch.
        state2 = load_sync_state(tmp_config_paths)
        second = pull(
            client,
            config,
            state2,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )
    assert [i.action for i in second.items] == ["up-to-date"]
    assert detail_route.call_count == 1  # no extra body fetch


@respx.mock
def test_pull_persists_index_when_a_fetch_raises_midway(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # A mid-loop failure must not discard the entries for workflows already
    # written: the index is saved in a finally, so a re-run resumes from it
    # rather than treating the written files as untracked.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t"},
                {"id": "skl_b", "name": "beta", "current_version": 1, "version_token": "t"},
            ]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body="alpha body")
    )
    # beta's body fetch fails: alpha was already written before the raise.
    respx.get(f"{SERVER}/v1/workflows/skl_b").mock(return_value=httpx.Response(500))

    state = SyncState()
    with (
        GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client,
        pytest.raises(ServerError),
    ):
        pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )

    # alpha's file is on disk and its entry was persisted despite the failure.
    assert (target_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha body"
    persisted = load_sync_state(tmp_config_paths)
    assert [e.slug for e in persisted.entries] == ["alpha"]
    assert persisted.entries[0].body_sha256 == body_sha256("alpha body")


@respx.mock
def test_pull_narrows_by_slug_args(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="all")])
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t"},
                {"id": "skl_b", "name": "beta", "current_version": 1, "version_token": "t"},
            ]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_b").mock(
        return_value=_detail_response(id_="skl_b", name="beta", body="beta body")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=["beta"],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in result.items] == [("beta", "pulled")]
    assert (target_dir / "beta" / "SKILL.md").exists()
    assert not (target_dir / "alpha").exists()


@respx.mock
def test_pull_applies_selected_globs(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(
        targets=[SyncTarget(path=str(target_dir), scope="selected", selected=["refunds-*"])]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {"id": "skl_r", "name": "refunds-flow", "current_version": 1, "version_token": "t"},
                {"id": "skl_o", "name": "onboarding", "current_version": 1, "version_token": "t"},
            ]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_r").mock(
        return_value=_detail_response(id_="skl_r", name="refunds-flow", body="refunds body")
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
    assert [(i.slug, i.action) for i in result.items] == [("refunds-flow", "pulled")]


@respx.mock
def test_pull_skips_unsafe_name(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="all")])
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_bad").mock(
        return_value=_detail_response(id_="skl_bad", name="../escape", body="x")
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_bad", "name": "../escape", "current_version": 1, "version_token": "t"}]
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
    assert [(i.slug, i.action) for i in result.items] == [("../escape", "skipped-unsafe-name")]
    assert detail_route.call_count == 0  # never fetched
    assert load_sync_state(tmp_config_paths).entries == []


@respx.mock
def test_pull_follows_all_list_pages(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    respx.get(f"{SERVER}/v1/workflows").mock(
        side_effect=[
            _list_response(
                [{"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t"}],
                next_cursor="c1",
            ),
            _list_response(
                [{"id": "skl_b", "name": "beta", "current_version": 1, "version_token": "t"}],
                next_cursor=None,
            ),
        ]
    )
    respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body="a")
    )
    respx.get(f"{SERVER}/v1/workflows/skl_b").mock(
        return_value=_detail_response(id_="skl_b", name="beta", body="b")
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
    assert {i.slug for i in result.items} == {"alpha", "beta"}


@respx.mock
def test_pull_unknown_target_path_raises_not_found(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    config = SyncConfig(targets=[SyncTarget(path=str(tmp_path / "a"), scope="owned")])
    with (
        GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client,
        pytest.raises(NotFound),
    ):
        pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=str(tmp_path / "nope"),
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )


@respx.mock
def test_pull_target_path_selects_single_target(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="owned"),
            SyncTarget(path=str(second), scope="owned"),
        ]
    )
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t"}]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body="a")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=str(second),
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )
    # Only the second target was listed/written.
    assert list_route.call_count == 1
    assert (second / "alpha" / "SKILL.md").exists()
    assert not (first / "alpha").exists()


# ----- status engine -----


def _write_skill(target_dir: Path, slug: str, body: str) -> Path:
    """Write ``<target_dir>/<slug>/SKILL.md`` with ``body`` and return its path."""
    skill = target_dir / slug / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body, encoding="utf-8")
    return skill


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
    role: str = "owner",
) -> SyncEntry:
    """Build an index entry whose recorded hash matches ``body``."""
    return SyncEntry(
        workflow_id=id_,
        slug=slug,
        target_path=normalize_target_path(str(target_dir)),
        synced_version=version,
        version_token=token,
        body_sha256=body_sha256(body),
        effective_role=role,
        read_only=role == "view",
    )


def test_untracked_local_slugs_lists_skill_dirs(tmp_path: Path) -> None:
    target = SyncTarget(path=str(tmp_path), scope="owned")
    _write_skill(tmp_path, "alpha", "a")
    _write_skill(tmp_path, "beta", "b")
    # A bare directory with no SKILL.md is not a workflow dir.
    (tmp_path / "scratch").mkdir()
    # A stray top-level file is ignored.
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    assert untracked_local_slugs(target) == ["alpha", "beta"]


def test_untracked_local_slugs_empty_when_dir_missing(tmp_path: Path) -> None:
    target = SyncTarget(path=str(tmp_path / "absent"), scope="owned")
    assert untracked_local_slugs(target) == []


@respx.mock
def test_status_clean(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = "server body"
    _write_skill(target_dir, "alpha", body)
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_a", slug="alpha", body=body, token="t1")]
    )

    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="skl_a", name="alpha", token="t1")])
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body=body)
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.slug == "alpha"
    assert item.state == "clean"
    assert item.next_action == "none"
    assert item.synced_version == 1
    assert item.server_version == 1
    assert item.read_only is False
    assert detail_route.call_count == 0  # no body fetch
    assert list_route.call_count == 1


@respx.mock
def test_status_modified_local(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # Recorded hash is the server body; the file on disk diverges.
    _write_skill(target_dir, "alpha", "locally edited")
    state = SyncState(
        entries=[
            _tracked_entry(target_dir, id_="skl_a", slug="alpha", body="server body", token="t1")
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_a", name="alpha", token="t1", version=4)]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_a").mock(
        return_value=_detail_response(id_="skl_a", name="alpha", body="x")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    assert item.state == "modified-local"
    assert item.next_action == "push"
    assert item.synced_version == 1
    assert item.server_version == 4
    assert item.read_only is False
    assert detail_route.call_count == 0


@respx.mock
def test_status_modified_local_read_only_has_no_action(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="all")])
    _write_skill(target_dir, "shared", "locally edited")
    state = SyncState(
        entries=[
            _tracked_entry(
                target_dir, id_="skl_s", slug="shared", body="server body", token="t1", role="view"
            )
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_s", name="shared", token="t1", role="view")]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    # A read-only workflow can be edited on disk but the edits cannot be pushed,
    # so the state is still modified-local but there is no recommended action.
    assert item.state == "modified-local"
    assert item.read_only is True
    assert item.next_action == "none"


@respx.mock
def test_status_behind_server(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = "server body"
    _write_skill(target_dir, "alpha", body)
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_a", slug="alpha", body=body, token="t1")]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_a", name="alpha", token="t2", version=2)]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    assert item.state == "behind-server"
    assert item.next_action == "pull"
    assert item.synced_version == 1
    assert item.server_version == 2


@respx.mock
def test_status_behind_server_when_local_file_missing(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # A tracked entry whose SKILL.md was removed is treated as not locally
    # edited (nothing to push), so a moved server classifies it as behind-server.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    state = SyncState(
        entries=[
            _tracked_entry(target_dir, id_="skl_a", slug="alpha", body="server body", token="t1")
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_a", name="alpha", token="t2", version=2)]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    assert item.state == "behind-server"
    assert item.next_action == "pull"


@respx.mock
def test_status_conflict(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # Both sides moved: file diverged from the recorded hash AND the token moved.
    _write_skill(target_dir, "alpha", "locally edited")
    state = SyncState(
        entries=[
            _tracked_entry(target_dir, id_="skl_a", slug="alpha", body="server body", token="t1")
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_a", name="alpha", token="t2", version=3)]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    assert item.state == "conflict"
    assert item.next_action == "resolve"
    assert item.synced_version == 1
    assert item.server_version == 3


@respx.mock
def test_status_deleted_on_server_via_deleted_at(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = "server body"
    _write_skill(target_dir, "alpha", body)
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_a", slug="alpha", body=body, token="t1")]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                _summary_dict(
                    id_="skl_a", name="alpha", token="t1", deleted_at="2026-06-01T00:00:00Z"
                )
            ]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    assert item.state == "deleted-on-server"
    assert item.next_action == "resolve"
    # The server version is left unset for a gone workflow.
    assert item.server_version is None
    assert item.synced_version == 1


@respx.mock
def test_status_deleted_on_server_via_absent_summary(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # A tracked workflow that does not appear in the listing at all (e.g. a
    # revoked share) is treated the same as deleted.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="all")])
    body = "server body"
    _write_skill(target_dir, "gone", body)
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_g", slug="gone", body=body, token="t1")]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="skl_other", name="other", token="t1")])
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    item = result.items[0]
    assert item.slug == "gone"
    assert item.state == "deleted-on-server"
    assert item.next_action == "resolve"
    assert item.server_version is None


@respx.mock
def test_status_untracked_local_dir(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # A local dir with a SKILL.md and no index entry is untracked.
    _write_skill(target_dir, "draft", "local draft")
    respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, SyncState(), target_path=None)
    item = result.items[0]
    assert item.slug == "draft"
    assert item.state == "untracked"
    assert item.next_action == "publish"
    assert item.workflow_id is None
    assert item.synced_version is None
    assert item.server_version is None


@respx.mock
def test_status_does_not_write_index_or_files(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    skill = _write_skill(target_dir, "alpha", "locally edited")
    state = SyncState(
        entries=[
            _tracked_entry(target_dir, id_="skl_a", slug="alpha", body="server body", token="t1")
        ]
    )
    save_sync_state(state, tmp_config_paths)
    index_before = tmp_config_paths.sync_state_file.read_text(encoding="utf-8")

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_a", name="alpha", token="t2", version=2)]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        status(client, config, state, target_path=None)

    # The index file and the on-disk SKILL.md are byte-for-byte unchanged.
    assert tmp_config_paths.sync_state_file.read_text(encoding="utf-8") == index_before
    assert skill.read_text(encoding="utf-8") == "locally edited"


@respx.mock
def test_status_selected_scope_applies_globs(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(
        targets=[SyncTarget(path=str(target_dir), scope="selected", selected=["refunds-*"])]
    )
    body = "server body"
    _write_skill(target_dir, "refunds-flow", body)
    state = SyncState(
        entries=[
            _tracked_entry(target_dir, id_="skl_r", slug="refunds-flow", body=body, token="t1")
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                _summary_dict(id_="skl_r", name="refunds-flow", token="t1"),
                _summary_dict(id_="skl_o", name="onboarding", token="t1"),
            ]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    # Only the glob-matching workflow is in scope and classified.
    assert [i.slug for i in result.items] == ["refunds-flow"]
    assert result.items[0].state == "clean"


@respx.mock
def test_status_target_path_selects_single_target(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="owned"),
            SyncTarget(path=str(second), scope="owned"),
        ]
    )
    body = "server body"
    _write_skill(second, "beta", body)
    state = SyncState(
        entries=[_tracked_entry(second, id_="skl_b", slug="beta", body=body, token="t1")]
    )
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="skl_b", name="beta", token="t1")])
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=str(second))
    # Only the requested target was listed and classified.
    assert list_route.call_count == 1
    assert [i.slug for i in result.items] == ["beta"]
    assert result.items[0].target_path == normalize_target_path(str(second))


@respx.mock
def test_status_unknown_target_path_raises_not_found(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    config = SyncConfig(targets=[SyncTarget(path=str(tmp_path / "a"), scope="owned")])
    with (
        GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client,
        pytest.raises(NotFound),
    ):
        status(client, config, SyncState(), target_path=str(tmp_path / "nope"))


@respx.mock
def test_status_caches_listing_across_shared_scope(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # Two targets sharing the same scope (and thus server filter) reuse one
    # listing call rather than re-listing per target.
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="owned"),
            SyncTarget(path=str(second), scope="owned"),
        ]
    )
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="skl_a", name="alpha", token="t1")])
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        status(client, config, SyncState(), target_path=None)
    assert list_route.call_count == 1


def test_slug_in_target_scope_owned_and_all_accept_everything() -> None:
    # Owned/all targets are server-filtered, so every slug is in scope.
    for scope in ("owned", "all"):
        target = SyncTarget(path="~/skills", scope=scope)  # type: ignore[arg-type]
        assert slug_in_target_scope(target, "anything") is True
        assert slug_in_target_scope(target, "refunds-flow") is True


def test_slug_in_target_scope_selected_matches_globs() -> None:
    target = SyncTarget(path="~/skills", scope="selected", selected=["refunds-*", "exact"])
    assert slug_in_target_scope(target, "refunds-flow") is True
    assert slug_in_target_scope(target, "exact") is True
    assert slug_in_target_scope(target, "onboarding") is False
    # An empty pattern list matches nothing.
    empty = SyncTarget(path="~/skills", scope="selected", selected=[])
    assert slug_in_target_scope(empty, "refunds-flow") is False


@respx.mock
def test_status_selected_out_of_glob_tracked_entry_is_omitted(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # A tracked index entry whose slug falls outside the target's current globs
    # is not classified at all (not reported deleted-on-server), even though the
    # workflow is still live server-side. This guards the case where a user
    # narrows --only after pulling.
    target_dir = tmp_path / "skills"
    config = SyncConfig(
        targets=[SyncTarget(path=str(target_dir), scope="selected", selected=["refunds-*"])]
    )
    body = "server body"
    _write_skill(target_dir, "refunds-flow", body)
    state = SyncState(
        entries=[
            _tracked_entry(target_dir, id_="skl_r", slug="refunds-flow", body=body, token="t1"),
            # Tracked from an earlier, wider glob; now out of scope but still live.
            _tracked_entry(target_dir, id_="skl_o", slug="onboarding", body=body, token="t1"),
        ]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                _summary_dict(id_="skl_r", name="refunds-flow", token="t1"),
                _summary_dict(id_="skl_o", name="onboarding", token="t1"),
            ]
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)
    # Only the in-glob entry is reported; the out-of-glob one is omitted, not
    # mis-classified as deleted-on-server.
    assert [i.slug for i in result.items] == ["refunds-flow"]
    assert result.items[0].state == "clean"
    assert "deleted-on-server" not in {i.state for i in result.items}


@respx.mock
def test_status_selected_out_of_glob_untracked_dir_is_omitted(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # An untracked local dir whose slug is outside the target's globs is not
    # reported as untracked: the untracked scan honors the same scope gate as
    # the tracked classification.
    target_dir = tmp_path / "skills"
    config = SyncConfig(
        targets=[SyncTarget(path=str(target_dir), scope="selected", selected=["refunds-*"])]
    )
    _write_skill(target_dir, "refunds-draft", "in-scope draft")
    _write_skill(target_dir, "onboarding", "out-of-scope draft")
    respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, SyncState(), target_path=None)
    # Only the in-glob local dir is reported untracked; the other is omitted.
    assert [i.slug for i in result.items] == ["refunds-draft"]
    assert result.items[0].state == "untracked"


@respx.mock
def test_status_per_target_isolation_tracked_vs_untracked(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # The same slug is tracked under target A but only present as an untracked
    # local dir under target B in a single run. A reports it clean; B reports it
    # untracked. Per-target index matching keeps the two from bleeding together.
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="owned"),
            SyncTarget(path=str(second), scope="owned"),
        ]
    )
    body = "server body"
    _write_skill(first, "shared", body)
    _write_skill(second, "shared", body)
    state = SyncState(
        # Tracked only under the first target.
        entries=[_tracked_entry(first, id_="skl_s", slug="shared", body=body, token="t1")]
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response([_summary_dict(id_="skl_s", name="shared", token="t1")])
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)

    by_target = {(i.target_path, i.slug): i for i in result.items}
    first_item = by_target[(normalize_target_path(str(first)), "shared")]
    second_item = by_target[(normalize_target_path(str(second)), "shared")]
    assert first_item.state == "clean"
    assert second_item.state == "untracked"
    assert second_item.next_action == "publish"


@respx.mock
def test_status_distinct_scopes_list_separately(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # Two targets with DIFFERENT scopes map to different server filters, so the
    # listing endpoint is hit once per filter (two distinct listings), and items
    # from both targets appear. This is the opposite direction from the
    # shared-scope cache test.
    owned_dir = tmp_path / "owned"
    all_dir = tmp_path / "all"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(owned_dir), scope="owned"),
            SyncTarget(path=str(all_dir), scope="all"),
        ]
    )
    body = "server body"
    _write_skill(owned_dir, "mine-flow", body)
    _write_skill(all_dir, "shared-flow", body)
    state = SyncState(
        entries=[
            _tracked_entry(owned_dir, id_="skl_m", slug="mine-flow", body=body, token="t1"),
            _tracked_entry(all_dir, id_="skl_x", slug="shared-flow", body=body, token="t1"),
        ]
    )

    # scope "owned" -> filter "mine"; scope "all" -> filter "all".
    mine_route = respx.get(f"{SERVER}/v1/workflows", params__contains={"filter": "mine"}).mock(
        return_value=_list_response([_summary_dict(id_="skl_m", name="mine-flow", token="t1")])
    )
    all_route = respx.get(f"{SERVER}/v1/workflows", params__contains={"filter": "all"}).mock(
        return_value=_list_response([_summary_dict(id_="skl_x", name="shared-flow", token="t1")])
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = status(client, config, state, target_path=None)

    # Two distinct server filters mean two listing calls, not a collapsed cache.
    assert mine_route.call_count == 1
    assert all_route.call_count == 1
    assert {i.slug for i in result.items} == {"mine-flow", "shared-flow"}
    assert all(i.state == "clean" for i in result.items)


# ----- push engine -----


def _push_body(
    *,
    slug: str,
    description: str = "Draft a postmortem from an incident transcript.",
    outcome: str = "Reduce mean-time-to-postmortem from days to hours.",
    tags: list[str] | None = None,
    name: str | None = None,
    identity_key: str = "name",
    extra_body: str = "do the work",
) -> str:
    """Build a workflow body with front-matter for a push test.

    The identity line uses ``identity_key`` (``name`` by default, or ``slug``
    to exercise the slug-key arm of the rename refusal). Its value defaults to
    ``slug``; pass a different ``name`` to exercise the rename-refusal path.
    Omit a field by passing an empty string to drop it from the front-matter
    (used for the missing-required-field cases).
    """
    identity_value = name if name is not None else slug
    lines = ["---", f"{identity_key}: {identity_value}"]
    if description:
        lines.append(f"description: {description}")
    if outcome:
        lines.append(f"outcome: {outcome}")
    if tags is not None:
        rendered = ", ".join(tags)
        lines.append(f"tags: [{rendered}]")
    lines.extend(["---", "", extra_body, ""])
    return "\n".join(lines)


def _save_response(
    *,
    workflow_id: str,
    name: str,
    version: int = 2,
    token: str = "tok-2",
    verifiers: list[dict] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "workflow_id": workflow_id,
            "version": version,
            "name": name,
            "version_token": token,
            "verifiers": verifiers or [],
        },
    )


def _modified_entry(
    target_dir: Path,
    *,
    id_: str,
    slug: str,
    token: str = "tok-1",
    role: str = "owner",
    verifiers: list[SyncVerifierBinding] | None = None,
) -> SyncEntry:
    """An index entry recording a hash that no on-disk edit will match.

    The recorded ``body_sha256`` is for a placeholder body, so any real local
    body written by a test reads as modified and becomes a push candidate.
    """
    return SyncEntry(
        workflow_id=id_,
        slug=slug,
        target_path=normalize_target_path(str(target_dir)),
        synced_version=1,
        version_token=token,
        body_sha256=body_sha256(f"recorded-base-{slug}"),
        verifier_bindings=verifiers or [],
        effective_role=role,
        read_only=role == "view",
    )


@respx.mock
def test_push_uploads_modified_entry_and_updates_index(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = _push_body(slug="incident-postmortem", tags=["sre", "postmortem"])
    _write_skill(target_dir, "incident-postmortem", body)
    state = SyncState(
        entries=[
            _modified_entry(
                target_dir,
                id_="skl_01",
                slug="incident-postmortem",
                token="tok-1",
                verifiers=[SyncVerifierBinding(name="tone", verifier_id="vrf_1", version=3)],
            )
        ]
    )

    # Listing or fetching during a push is a bug: route only POST /v1/workflows.
    list_route = respx.get(f"{SERVER}/v1/workflows")
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_01")
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(
            workflow_id="skl_01",
            name="incident-postmortem",
            version=2,
            token="tok-2",
            verifiers=[{"name": "tone", "verifier_id": "vrf_1", "version": 3}],
        )
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            paths=tmp_config_paths,
        )

    # No listing or per-workflow fetch happened.
    assert list_route.call_count == 0
    assert detail_route.call_count == 0
    assert save_route.call_count == 1

    sent = json.loads(save_route.calls[0].request.content)
    assert sent["name"] == "incident-postmortem"  # identity is the slug
    assert sent["body"] == body  # full local body, verbatim
    assert sent["description"] == "Draft a postmortem from an incident transcript."
    assert sent["outcome"] == "Reduce mean-time-to-postmortem from days to hours."
    assert sent["tags"] == ["sre", "postmortem"]
    assert sent["expected_version_token"] == "tok-1"
    assert sent["source"] == "manual"
    # The version pin on the recorded binding is preserved in the outgoing payload.
    assert sent["verifiers"] == [{"name": "tone", "verifier_id": "vrf_1", "version": 3}]

    assert [(i.slug, i.action) for i in result.items] == [("incident-postmortem", "pushed")]

    reloaded = load_sync_state(tmp_config_paths)
    entry = reloaded.entries[0]
    assert entry.synced_version == 2
    assert entry.version_token == "tok-2"
    assert entry.body_sha256 == body_sha256(body)
    assert entry.verifier_bindings == [
        SyncVerifierBinding(name="tone", verifier_id="vrf_1", version=3)
    ]


@respx.mock
def test_push_omits_version_for_unpinned_binding(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = _push_body(slug="alpha")
    _write_skill(target_dir, "alpha", body)
    state = SyncState(
        entries=[
            _modified_entry(
                target_dir,
                id_="skl_a",
                slug="alpha",
                verifiers=[SyncVerifierBinding(name="tone", verifier_id="vrf_1", version=None)],
            )
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    sent = json.loads(save_route.calls[0].request.content)
    # An unpinned binding carries no version key (it is not sent as null).
    assert sent["verifiers"] == [{"name": "tone", "verifier_id": "vrf_1"}]


@respx.mock
def test_push_edited_metadata_reaches_request(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # The user's edited front-matter wins: changed description/outcome/tags are
    # what reach the registry, not the values recorded at the last sync.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = _push_body(
        slug="alpha",
        description="A freshly edited description.",
        outcome="A freshly edited outcome.",
        tags=["edited"],
    )
    _write_skill(target_dir, "alpha", body)
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")])
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    sent = json.loads(save_route.calls[0].request.content)
    assert sent["description"] == "A freshly edited description."
    assert sent["outcome"] == "A freshly edited outcome."
    assert sent["tags"] == ["edited"]


@respx.mock
def test_push_conflict_leaves_entry_unchanged(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = _push_body(slug="alpha")
    _write_skill(target_dir, "alpha", body)
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha", token="t1")])
    recorded_hash = state.entries[0].body_sha256

    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            409,
            json={"error": "conflict", "message": "version token mismatch"},
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    item = result.items[0]
    assert item.action == "conflict"
    assert item.detail is not None and "pull" in item.detail

    reloaded = load_sync_state(tmp_config_paths)
    entry = reloaded.entries[0]
    # Untouched: same token and the original recorded hash, not the local one.
    assert entry.version_token == "t1"
    assert entry.synced_version == 1
    assert entry.body_sha256 == recorded_hash


@respx.mock
def test_push_unmodified_entry_emits_nothing(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = _push_body(slug="alpha")
    _write_skill(target_dir, "alpha", body)
    # The recorded hash matches the on-disk body, so there is nothing to push.
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_a", slug="alpha", body=body, token="t1")]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    assert result.items == []
    assert save_route.call_count == 0


@respx.mock
def test_push_missing_local_file_emits_nothing(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # A tracked entry with no SKILL.md on disk is not a push candidate.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")])
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    assert result.items == []
    assert save_route.call_count == 0


@respx.mock
def test_push_read_only_entry_is_skipped_with_no_save(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="all")])
    body = _push_body(slug="shared-doc")
    _write_skill(target_dir, "shared-doc", body)
    state = SyncState(
        entries=[_modified_entry(target_dir, id_="skl_v", slug="shared-doc", role="view")]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    item = result.items[0]
    assert item.action == "skipped-read-only"
    assert item.detail is not None and "view grant" in item.detail
    # A view-role entry never reaches the registry.
    assert save_route.call_count == 0


@respx.mock
def test_push_rename_in_front_matter_is_skipped_invalid(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # The on-disk slug is "alpha" but the edited front-matter renames to "beta".
    body = _push_body(slug="alpha", name="beta")
    _write_skill(target_dir, "alpha", body)
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")])
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    item = result.items[0]
    assert item.action == "skipped-invalid"
    assert item.detail is not None and "Renaming is not supported" in item.detail
    assert save_route.call_count == 0


@respx.mock
def test_push_slug_rename_in_front_matter_is_skipped_invalid(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # The on-disk slug is "alpha" but the edited front-matter carries a `slug:`
    # key renaming to "beta". `slug:` is the more likely real-world key, since it
    # matches the directory-name convention; the rename refusal covers it too.
    body = _push_body(slug="alpha", name="beta", identity_key="slug")
    _write_skill(target_dir, "alpha", body)
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")])
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    item = result.items[0]
    assert item.action == "skipped-invalid"
    assert item.detail is not None and "Renaming is not supported" in item.detail
    assert "`slug`" in item.detail  # the message names the offending key
    assert save_route.call_count == 0


@respx.mock
def test_push_missing_required_outcome_is_skipped_invalid(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # An empty outcome drops the key from the front-matter entirely.
    body = _push_body(slug="alpha", outcome="")
    _write_skill(target_dir, "alpha", body)
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")])
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    item = result.items[0]
    assert item.action == "skipped-invalid"
    assert item.detail is not None and "outcome" in item.detail
    assert save_route.call_count == 0


@respx.mock
def test_push_forbidden_is_reported_read_only(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # The recorded role said writable, but the server rejects the write.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    body = _push_body(slug="alpha")
    _write_skill(target_dir, "alpha", body)
    state = SyncState(entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")])
    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            403,
            json={"error": "forbidden", "message": "insufficient role to write"},
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    item = result.items[0]
    assert item.action == "skipped-read-only"
    assert item.detail == "insufficient role to write"


@respx.mock
def test_push_untracked_local_dir_is_flagged_with_no_save(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    # A local directory the index does not track.
    _write_skill(target_dir, "brand-new", _push_body(slug="brand-new"))
    state = SyncState()
    save_route = respx.post(f"{SERVER}/v1/workflows")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    item = result.items[0]
    assert item.slug == "brand-new"
    assert item.action == "untracked"
    assert item.detail is not None and "publish" in item.detail
    # Push never creates a new workflow.
    assert save_route.call_count == 0


@respx.mock
def test_push_target_option_scopes_to_one(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="owned"),
            SyncTarget(path=str(second), scope="owned"),
        ]
    )
    _write_skill(first, "alpha", _push_body(slug="alpha"))
    _write_skill(second, "beta", _push_body(slug="beta"))
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha"),
            _modified_entry(second, id_="skl_b", slug="beta"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_b", name="beta")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(
            client, config, state, slugs=[], target_path=str(second), paths=tmp_config_paths
        )
    # Only the second target's entry was pushed.
    assert [(i.slug, i.action) for i in result.items] == [("beta", "pushed")]
    assert save_route.call_count == 1
    sent = json.loads(save_route.calls[0].request.content)
    assert sent["name"] == "beta"


@respx.mock
def test_push_positional_slug_narrowing(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    _write_skill(target_dir, "alpha", _push_body(slug="alpha"))
    _write_skill(target_dir, "beta", _push_body(slug="beta"))
    state = SyncState(
        entries=[
            _modified_entry(target_dir, id_="skl_a", slug="alpha"),
            _modified_entry(target_dir, id_="skl_b", slug="beta"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(
            client, config, state, slugs=["alpha"], target_path=None, paths=tmp_config_paths
        )
    assert [(i.slug, i.action) for i in result.items] == [("alpha", "pushed")]
    assert save_route.call_count == 1
    sent = json.loads(save_route.calls[0].request.content)
    assert sent["name"] == "alpha"


@respx.mock
def test_push_selected_target_applies_globs(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(
        targets=[SyncTarget(path=str(target_dir), scope="selected", selected=["refunds-*"])]
    )
    _write_skill(target_dir, "refunds-flow", _push_body(slug="refunds-flow"))
    _write_skill(target_dir, "unrelated", _push_body(slug="unrelated"))
    state = SyncState(
        entries=[
            _modified_entry(target_dir, id_="skl_r", slug="refunds-flow"),
            _modified_entry(target_dir, id_="skl_u", slug="unrelated"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_r", name="refunds-flow")
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    # Only the in-glob entry is pushed; the out-of-glob one is ignored entirely.
    assert [(i.slug, i.action) for i in result.items] == [("refunds-flow", "pushed")]
    assert save_route.call_count == 1


@respx.mock
def test_push_persists_index_when_a_save_raises_midway(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    # The first entry pushes cleanly; the second raises a non-conflict server
    # error. The finally block must still persist the first entry's new state.
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    _write_skill(target_dir, "alpha", _push_body(slug="alpha"))
    _write_skill(target_dir, "beta", _push_body(slug="beta"))
    state = SyncState(
        entries=[
            _modified_entry(target_dir, id_="skl_a", slug="alpha", token="t-a"),
            _modified_entry(target_dir, id_="skl_b", slug="beta", token="t-b"),
        ]
    )

    def _save(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["name"] == "alpha":
            return _save_response(workflow_id="skl_a", name="alpha", token="tok-a2")
        return httpx.Response(500, json={"error": "internal_error", "message": "boom"})

    respx.post(f"{SERVER}/v1/workflows").mock(side_effect=_save)

    with (
        GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client,
        pytest.raises(ServerError),
    ):
        push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    # The index was persisted in the finally: alpha advanced, beta did not.
    persisted = load_sync_state(tmp_config_paths)
    by_slug = {e.slug: e for e in persisted.entries}
    assert by_slug["alpha"].version_token == "tok-a2"
    assert by_slug["beta"].version_token == "t-b"


# ----- identity guard -----


@respx.mock
def test_ensure_identity_stamps_when_unset_and_allowed() -> None:
    route = _me_route("first@example.com")
    state = SyncState()
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        stamped = ensure_identity(client, state, allow_stamp=True)
    assert stamped is True
    assert state.identity == "first@example.com"
    assert route.call_count == 1


@respx.mock
def test_ensure_identity_does_not_stamp_when_disallowed() -> None:
    _me_route("first@example.com")
    state = SyncState()
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        stamped = ensure_identity(client, state, allow_stamp=False)
    assert stamped is False
    assert state.identity is None


@respx.mock
def test_ensure_identity_same_principal_is_noop() -> None:
    _me_route("same@example.com")
    state = SyncState(identity="same@example.com")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        stamped = ensure_identity(client, state, allow_stamp=True)
    assert stamped is False
    assert state.identity == "same@example.com"


@respx.mock
def test_ensure_identity_mismatch_raises() -> None:
    _me_route("intruder@example.com")
    state = SyncState(identity="owner@example.com")
    with (
        GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client,
        pytest.raises(Conflict) as exc,
    ):
        ensure_identity(client, state, allow_stamp=True)
    message = str(exc.value)
    assert "owner@example.com" in message
    assert "intruder@example.com" in message


@respx.mock
def test_pull_stamps_identity_on_first_run(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route("owner@example.com")
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))
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
    # The stamp is persisted by the index save the pull already performs.
    assert load_sync_state(tmp_config_paths).identity == "owner@example.com"


@respx.mock
def test_pull_mismatch_aborts_before_any_work(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route("intruder@example.com")
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))
    state = SyncState(identity="owner@example.com")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client, pytest.raises(Conflict):
        pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=False,
            paths=tmp_config_paths,
        )
    # The guard fired before any listing, and the index on disk is untouched.
    assert list_route.call_count == 0
    assert not tmp_config_paths.sync_state_file.exists()


@respx.mock
def test_status_does_not_stamp_when_identity_unset(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route("owner@example.com")
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))
    state = SyncState()
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        status(client, config, state, target_path=None)
    # Status is read-only: it never stamps, and never writes the index.
    assert state.identity is None
    assert not tmp_config_paths.sync_state_file.exists()


@respx.mock
def test_status_mismatch_raises(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
    _me_route("intruder@example.com")
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))
    state = SyncState(identity="owner@example.com")
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client, pytest.raises(Conflict):
        status(client, config, state, target_path=None)
    assert list_route.call_count == 0


@respx.mock
def test_push_mismatch_aborts_before_any_save(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route("intruder@example.com")
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    _write_skill(target_dir, "alpha", _push_body(slug="alpha"))
    save_route = respx.post(f"{SERVER}/v1/workflows")
    state = SyncState(
        identity="owner@example.com",
        entries=[_modified_entry(target_dir, id_="skl_a", slug="alpha")],
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client, pytest.raises(Conflict):
        push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)
    assert save_route.call_count == 0


# ----- deleted-on-server reconcile (pull) -----


@respx.mock
def test_pull_removes_local_copy_of_deleted_workflow_when_confirmed(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    target = SyncTarget(path=str(target_dir), scope="owned")
    config = SyncConfig(targets=[target])
    body = "registry body"
    _write_skill(target_dir, "gone", body)
    state = SyncState(entries=[_tracked_entry(target_dir, id_="skl_gone", slug="gone", body=body)])

    # The workflow lists as soft-deleted (deleted_at set), so it is not live.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_gone", name="gone", deleted_at="2026-01-01T00:00:00Z")]
        )
    )
    # Registered to prove no server-side delete is ever issued.
    delete_route = respx.delete(f"{SERVER}/v1/workflows/skl_gone")

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            state,
            slugs=[],
            target_path=None,
            force=False,
            yes=True,  # non-interactive auto-approve
            paths=tmp_config_paths,
        )

    assert [(i.slug, i.action) for i in result.items] == [("gone", "deleted-local")]
    # The whole slug directory is gone, and no server-delete was called.
    assert not local_skill_dir(target, "gone").exists()
    assert delete_route.call_count == 0
    # The index entry was dropped.
    assert load_sync_state(tmp_config_paths).entries == []


@respx.mock
def test_pull_keeps_local_copy_of_deleted_workflow_when_declined(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    target = SyncTarget(path=str(target_dir), scope="owned")
    config = SyncConfig(targets=[target])
    body = "registry body"
    _write_skill(target_dir, "gone", body)
    state = SyncState(entries=[_tracked_entry(target_dir, id_="skl_gone", slug="gone", body=body)])
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_gone", name="gone", deleted_at="2026-01-01T00:00:00Z")]
        )
    )
    # Simulate an interactive terminal that declines the prompt: patch the
    # references the confirm helper actually consults (a TTY plus a confirm that
    # returns False).
    stdin = mock.MagicMock()
    stdin.isatty.return_value = True
    monkeypatch.setattr("goodeye_cli.prompts.sys.stdin", stdin)
    monkeypatch.setattr("goodeye_cli.prompts.typer.confirm", lambda *a, **k: False)

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

    assert [(i.slug, i.action) for i in result.items] == [("gone", "deleted-on-server")]
    # The local copy survives, and the entry stays tracked.
    assert local_skill_path(target, "gone").read_text(encoding="utf-8") == body
    assert [e.slug for e in load_sync_state(tmp_config_paths).entries] == ["gone"]


@respx.mock
def test_pull_treats_entirely_absent_entry_as_gone(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    target = SyncTarget(path=str(target_dir), scope="owned")
    config = SyncConfig(targets=[target])
    _write_skill(target_dir, "revoked", "shared body")
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_rev", slug="revoked", body="shared body")]
    )
    # The workflow is not in the listing at all (e.g. a revoked share).
    respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))

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

    assert [(i.slug, i.action) for i in result.items] == [("revoked", "deleted-local")]
    assert not local_skill_dir(target, "revoked").exists()
    assert load_sync_state(tmp_config_paths).entries == []


@respx.mock
def test_pull_does_not_materialize_deleted_workflow(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = SyncConfig(targets=[SyncTarget(path=str(target_dir), scope="owned")])
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [_summary_dict(id_="skl_d", name="ghost", deleted_at="2026-01-01T00:00:00Z")]
        )
    )
    # A fetch of the deleted workflow body would be a bug: it is never materialized.
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_d")

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = pull(
            client,
            config,
            SyncState(),
            slugs=[],
            target_path=None,
            force=False,
            yes=True,
            paths=tmp_config_paths,
        )

    # Nothing tracked it, so there is no deletion to reconcile and nothing pulled.
    assert result.items == []
    assert detail_route.call_count == 0
    assert not local_skill_dir(SyncTarget(path=str(target_dir), scope="owned"), "ghost").exists()


@respx.mock
def test_pull_out_of_glob_entry_is_not_pruned(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    # A `selected` target whose globs no longer cover the tracked slug.
    target = SyncTarget(path=str(target_dir), scope="selected", selected=["refunds-*"])
    config = SyncConfig(targets=[target])
    _write_skill(target_dir, "onboarding", "kept body")
    state = SyncState(
        entries=[_tracked_entry(target_dir, id_="skl_on", slug="onboarding", body="kept body")]
    )
    # The listing has nothing in glob scope, so onboarding is absent from the
    # narrowed live set, but it is out-of-scope, not deleted.
    respx.get(f"{SERVER}/v1/workflows").mock(return_value=_list_response([]))

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

    # No deletion item, the file survives, and the entry stays tracked.
    assert result.items == []
    assert local_skill_path(target, "onboarding").read_text(encoding="utf-8") == "kept body"
    assert [e.slug for e in load_sync_state(tmp_config_paths).entries] == ["onboarding"]


# ----- multi-target convergence (push) -----


def _two_target_config(first: Path, second: Path) -> SyncConfig:
    return SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="all"),
            SyncTarget(path=str(second), scope="all"),
        ]
    )


@respx.mock
def test_push_converges_sibling_after_single_edit(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    edited = _push_body(slug="alpha", extra_body="edited in A")
    _write_skill(first, "alpha", edited)
    # The sibling in B still holds the recorded base body (clean).
    base_body = "recorded-base-alpha"
    _write_skill(second, "alpha", base_body)
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            # B's entry matches the on-disk base, so B is not itself a candidate.
            SyncEntry(
                workflow_id="skl_a",
                slug="alpha",
                target_path=normalize_target_path(str(second)),
                synced_version=1,
                version_token="tok-1",
                body_sha256=body_sha256(base_body),
            ),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha", version=2, token="tok-2")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    # A single upload, plus a converge of the B sibling (no second save).
    assert save_route.call_count == 1
    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "converged") in actions

    # B now holds the pushed body and reads clean (its index matches disk).
    assert (
        local_skill_path(SyncTarget(path=str(second), scope="all"), "alpha").read_text(
            encoding="utf-8"
        )
        == edited
    )
    persisted = {
        (e.slug, normalize_target_path(e.target_path)): e
        for e in load_sync_state(tmp_config_paths).entries
    }
    sib = persisted[("alpha", normalize_target_path(str(second)))]
    assert sib.version_token == "tok-2"
    assert sib.synced_version == 2
    assert sib.body_sha256 == body_sha256(edited)


@respx.mock
def test_push_identical_edits_upload_once_both_clean(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    edited = _push_body(slug="alpha", extra_body="same edit")
    _write_skill(first, "alpha", edited)
    _write_skill(second, "alpha", edited)
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            _modified_entry(second, id_="skl_a", slug="alpha", token="tok-1"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha", version=2, token="tok-2")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert save_route.call_count == 1
    actions = sorted(i.action for i in result.items)
    assert actions == ["converged", "pushed"]
    # Both index entries are clean against their on-disk bodies afterward.
    for entry in load_sync_state(tmp_config_paths).entries:
        assert entry.version_token == "tok-2"
        assert entry.body_sha256 == body_sha256(edited)


@respx.mock
def test_push_diverged_edits_are_refused_without_upload(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    _write_skill(first, "alpha", _push_body(slug="alpha", extra_body="edit A"))
    _write_skill(second, "alpha", _push_body(slug="alpha", extra_body="edit B"))
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            _modified_entry(second, id_="skl_a", slug="alpha", token="tok-1"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows")

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    # No upload happened, and both copies are reported diverged.
    assert save_route.call_count == 0
    assert sorted(i.action for i in result.items) == ["diverged", "diverged"]
    # Neither index entry advanced.
    for entry in load_sync_state(tmp_config_paths).entries:
        assert entry.version_token == "tok-1"


@respx.mock
def test_push_target_pushes_picked_copy_and_converges_clean_other(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    a_body = _push_body(slug="alpha", extra_body="edit A")
    _write_skill(first, "alpha", a_body)
    # B is CLEAN: its on-disk body matches its recorded hash, so --target first
    # pushes A and converges the clean B sibling to it.
    base_body = "recorded-base-alpha"
    _write_skill(second, "alpha", base_body)
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            SyncEntry(
                workflow_id="skl_a",
                slug="alpha",
                target_path=normalize_target_path(str(second)),
                synced_version=1,
                version_token="tok-1",
                body_sha256=body_sha256(base_body),
            ),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha", version=2, token="tok-2")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        # --target picks A as the source; the clean B converges to it.
        result = push(
            client, config, state, slugs=[], target_path=str(first), paths=tmp_config_paths
        )

    assert save_route.call_count == 1
    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "converged") in actions
    # B's on-disk body was rewritten to A's, even though B was outside --target.
    assert (
        local_skill_path(SyncTarget(path=str(second), scope="all"), "alpha").read_text(
            encoding="utf-8"
        )
        == a_body
    )


@respx.mock
def test_push_target_preserves_sibling_unpushed_edit(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """`--target first` must never clobber a different un-pushed edit in `second`.

    The scoped path skips in-scope divergence detection, so convergence is the
    only safety net: a modified sibling whose body differs from the pushed body
    is left untouched and reported ``diverged``, not silently overwritten.
    """
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    a_body = _push_body(slug="alpha", extra_body="edit A")
    b_body = _push_body(slug="alpha", extra_body="a DIFFERENT un-pushed edit in B")
    _write_skill(first, "alpha", a_body)
    _write_skill(second, "alpha", b_body)
    b_entry = _modified_entry(second, id_="skl_a", slug="alpha", token="tok-1")
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            b_entry,
        ]
    )
    # Snapshot B's recorded state to prove it is untouched afterward.
    b_recorded_hash = b_entry.body_sha256
    b_recorded_token = b_entry.version_token
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha", version=2, token="tok-2")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(
            client, config, state, slugs=[], target_path=str(first), paths=tmp_config_paths
        )

    # A pushed, B refused as diverged, and only one upload happened.
    assert save_route.call_count == 1
    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "diverged") in actions
    diverged = next(i for i in result.items if i.action == "diverged")
    assert diverged.detail is not None and "left untouched" in diverged.detail

    # B's file is byte-for-byte unchanged: its un-pushed edit survives.
    assert (
        local_skill_path(SyncTarget(path=str(second), scope="all"), "alpha").read_text(
            encoding="utf-8"
        )
        == b_body
    )
    # B's index entry is unchanged (still its old base hash and token), so a
    # later push still sees its edit rather than reading clean.
    persisted = {
        normalize_target_path(e.target_path): e for e in load_sync_state(tmp_config_paths).entries
    }
    b_persisted = persisted[normalize_target_path(str(second))]
    assert b_persisted.body_sha256 == b_recorded_hash
    assert b_persisted.version_token == b_recorded_token


@respx.mock
def test_push_unscoped_preserves_out_of_glob_sibling_edit(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """An unscoped push must not clobber a sibling that is out of its glob scope.

    A `selected` target whose glob excludes the slug never enters in-scope
    divergence detection, so convergence is again the only safety net. A
    differing local edit in that out-of-glob sibling is preserved and reported
    ``diverged``.
    """
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = SyncConfig(
        targets=[
            SyncTarget(path=str(first), scope="all"),
            # `second` only mirrors `refunds-*`, so `alpha` is out of its scope
            # and never becomes its own push candidate.
            SyncTarget(path=str(second), scope="selected", selected=["refunds-*"]),
        ]
    )
    a_body = _push_body(slug="alpha", extra_body="edit A")
    b_body = _push_body(slug="alpha", extra_body="out-of-glob edit in B")
    _write_skill(first, "alpha", a_body)
    _write_skill(second, "alpha", b_body)
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            _modified_entry(second, id_="skl_a", slug="alpha", token="tok-1"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha", version=2, token="tok-2")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert save_route.call_count == 1
    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert (normalize_target_path(str(second)), "diverged") in actions
    # The out-of-glob sibling's differing edit is intact on disk.
    assert (
        local_skill_path(SyncTarget(path=str(second), scope="all"), "alpha").read_text(
            encoding="utf-8"
        )
        == b_body
    )


@respx.mock
def test_push_convergence_does_not_recreate_deleted_sibling(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A sibling whose local directory the user deleted is not resurrected."""
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    a_body = _push_body(slug="alpha", extra_body="edit A")
    _write_skill(first, "alpha", a_body)
    # B is tracked in the index but its on-disk directory is gone (user deleted).
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_a", slug="alpha", token="tok-1"),
            SyncEntry(
                workflow_id="skl_a",
                slug="alpha",
                target_path=normalize_target_path(str(second)),
                synced_version=1,
                version_token="tok-1",
                body_sha256=body_sha256("recorded-base-alpha"),
            ),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha", version=2, token="tok-2")
    )

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    assert save_route.call_count == 1
    # A pushed; B emits nothing (no converged, no diverged) and stays absent.
    actions = {(i.target_path, i.action) for i in result.items}
    assert (normalize_target_path(str(first)), "pushed") in actions
    assert not local_skill_dir(SyncTarget(path=str(second), scope="all"), "alpha").exists()
    second_items = [i for i in result.items if i.target_path == normalize_target_path(str(second))]
    assert second_items == []


@respx.mock
def test_push_read_only_mirrored_in_two_targets_is_skipped_not_diverged(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
    """A view-grant workflow edited differently in two targets reports
    ``skipped-read-only`` for each copy, never ``diverged``, and never uploads.
    """
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = _two_target_config(first, second)
    _write_skill(first, "shared-doc", _push_body(slug="shared-doc", extra_body="edit A"))
    _write_skill(second, "shared-doc", _push_body(slug="shared-doc", extra_body="edit B"))
    state = SyncState(
        entries=[
            _modified_entry(first, id_="skl_v", slug="shared-doc", role="view"),
            _modified_entry(second, id_="skl_v", slug="shared-doc", role="view"),
        ]
    )
    save_route = respx.post(f"{SERVER}/v1/workflows")

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        result = push(client, config, state, slugs=[], target_path=None, paths=tmp_config_paths)

    # Both copies are reported read-only, neither diverged, and nothing uploaded.
    assert sorted(i.action for i in result.items) == ["skipped-read-only", "skipped-read-only"]
    assert all("view grant" in (i.detail or "") for i in result.items)
    assert save_route.call_count == 0


def test_push_item_model_is_constructible() -> None:
    # The per-item shape carries an optional human-readable detail.
    item = PushItem(slug="alpha", target_path="~/skills", action="pushed")
    assert item.detail is None
    flagged = PushItem(slug="beta", target_path="~/skills", action="conflict", detail="pull first")
    assert flagged.detail == "pull first"
