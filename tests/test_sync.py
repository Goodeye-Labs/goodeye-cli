"""Tests for the local sync configuration and target engine."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import Conflict, NotFound, ServerError, ValidationFailed
from goodeye_cli.sync import (
    PRESETS,
    SyncConfig,
    SyncEntry,
    SyncState,
    SyncTarget,
    SyncVerifierBinding,
    add_target,
    body_sha256,
    expand_target_path,
    find_entry,
    is_modified_locally,
    list_targets,
    load_sync_config,
    load_sync_state,
    local_skill_path,
    normalize_target_path,
    pull,
    read_local_body,
    remove_target,
    resolve_preset,
    save_sync_config,
    save_sync_state,
    scope_filter,
    select_for_target,
    server_moved,
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
            paths=tmp_config_paths,
        )
    entry = load_sync_state(tmp_config_paths).entries[0]
    assert entry.effective_role == "view"
    assert entry.read_only is True


@respx.mock
def test_pull_skips_modified_without_force_and_overwrites_with_force(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
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
            client, config, state, slugs=[], target_path=None, force=False, paths=tmp_config_paths
        )
    # Server token also moved (t1 -> t2), so this is a conflict, not plain modified.
    assert [(i.slug, i.action) for i in result.items] == [("alpha", "skipped-conflict")]
    assert skill.read_text(encoding="utf-8") == "locally edited"
    assert detail_route.call_count == 0  # no body fetch while protecting local edits
    assert list_route.call_count == 1

    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        forced = pull(
            client, config, state, slugs=[], target_path=None, force=True, paths=tmp_config_paths
        )
    assert [(i.slug, i.action) for i in forced.items] == [("alpha", "pulled")]
    assert skill.read_text(encoding="utf-8") == "server v2"
    assert detail_route.call_count == 1


@respx.mock
def test_pull_skipped_modified_when_server_not_moved(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
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
            client, config, state, slugs=[], target_path=None, force=False, paths=tmp_config_paths
        )
    assert [(i.slug, i.action) for i in result.items] == [("alpha", "skipped-modified")]


@respx.mock
def test_pull_untracked_present_file_is_skipped_modified(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
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
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in result.items] == [("beta", "pulled"), ("beta", "pulled")]
    assert (first / "beta" / "SKILL.md").exists()
    assert (second / "beta" / "SKILL.md").exists()
    assert not (first / "alpha").exists()
    assert not (second / "alpha").exists()


@respx.mock
def test_pull_up_to_date_skips_second_fetch(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
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
            client, config, state, slugs=[], target_path=None, force=False, paths=tmp_config_paths
        )
        assert [i.action for i in first.items] == ["pulled"]
        assert detail_route.call_count == 1

        # Second run: nothing changed on either side, so no body fetch.
        state2 = load_sync_state(tmp_config_paths)
        second = pull(
            client, config, state2, slugs=[], target_path=None, force=False, paths=tmp_config_paths
        )
    assert [i.action for i in second.items] == ["up-to-date"]
    assert detail_route.call_count == 1  # no extra body fetch


@respx.mock
def test_pull_persists_index_when_a_fetch_raises_midway(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
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
            paths=tmp_config_paths,
        )

    # alpha's file is on disk and its entry was persisted despite the failure.
    assert (target_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha body"
    persisted = load_sync_state(tmp_config_paths)
    assert [e.slug for e in persisted.entries] == ["alpha"]
    assert persisted.entries[0].body_sha256 == body_sha256("alpha body")


@respx.mock
def test_pull_narrows_by_slug_args(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
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
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in result.items] == [("beta", "pulled")]
    assert (target_dir / "beta" / "SKILL.md").exists()
    assert not (target_dir / "alpha").exists()


@respx.mock
def test_pull_applies_selected_globs(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
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
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in result.items] == [("refunds-flow", "pulled")]


@respx.mock
def test_pull_skips_unsafe_name(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
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
            paths=tmp_config_paths,
        )
    assert [(i.slug, i.action) for i in result.items] == [("../escape", "skipped-unsafe-name")]
    assert detail_route.call_count == 0  # never fetched
    assert load_sync_state(tmp_config_paths).entries == []


@respx.mock
def test_pull_follows_all_list_pages(tmp_path: Path, tmp_config_paths: ConfigPaths) -> None:
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
            paths=tmp_config_paths,
        )
    assert {i.slug for i in result.items} == {"alpha", "beta"}


@respx.mock
def test_pull_unknown_target_path_raises_not_found(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
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
            paths=tmp_config_paths,
        )


@respx.mock
def test_pull_target_path_selects_single_target(
    tmp_path: Path, tmp_config_paths: ConfigPaths
) -> None:
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
            paths=tmp_config_paths,
        )
    # Only the second target was listed/written.
    assert list_route.call_count == 1
    assert (second / "alpha" / "SKILL.md").exists()
    assert not (first / "alpha").exists()
