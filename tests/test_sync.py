"""Tests for the local sync configuration and target engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import Conflict, ValidationFailed
from goodeye_cli.sync import (
    PRESETS,
    SyncConfig,
    SyncTarget,
    add_target,
    expand_target_path,
    list_targets,
    load_sync_config,
    normalize_target_path,
    remove_target,
    resolve_preset,
    save_sync_config,
)


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
