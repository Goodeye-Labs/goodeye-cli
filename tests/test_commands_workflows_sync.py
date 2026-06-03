"""Tests for the `goodeye workflows sync target` subcommands."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import Conflict, ValidationFailed


def _redirect_config(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    # The config path resolver honors XDG_CONFIG_HOME, so pointing it at the
    # temp dir redirects sync.json into the fixture's tree.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))


def test_target_add_by_path_defaults_to_compact_json(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "add", "~/work/skills"])
    assert result.exit_code == 0, result.output
    # CliRunner stdout is not a TTY, so the default mode is compact JSON.
    assert result.output == (
        '{"path":"~/work/skills","scope":"owned","selected":[],"link":false}\n'
    )
    # The target landed in sync.json on disk.
    with tmp_config_paths.sync_file.open(encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["targets"][0]["path"] == "~/work/skills"


def test_target_add_by_preset(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "add", "--preset", "claude"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["path"] == "~/.claude/skills"


def test_target_add_selected_scope_with_only(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "~/skills",
            "--scope",
            "SELECTED",
            "--only",
            "refunds-*",
            "--only",
            "onboarding",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scope"] == "selected"
    assert payload["selected"] == ["refunds-*", "onboarding"]


def test_target_add_table_mode_prints_confirmation(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "add", "~/work/skills", "--table"])
    assert result.exit_code == 0, result.output
    assert "Added" in result.output
    assert "~/work/skills" in result.output


def test_target_add_rejects_both_path_and_preset(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "sync", "target", "add", "~/skills", "--preset", "claude"]
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "exactly one" in str(result.exception)


def test_target_add_rejects_neither_path_nor_preset(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "add"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "exactly one" in str(result.exception)


def test_target_add_rejects_unknown_scope(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "sync", "target", "add", "~/skills", "--scope", "weird"]
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "scope" in str(result.exception).lower()


def test_target_add_duplicate_raises_conflict(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    first = runner.invoke(app, ["workflows", "sync", "target", "add", "~/skills"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["workflows", "sync", "target", "add", "~/skills"])
    assert second.exit_code != 0
    assert isinstance(second.exception, Conflict)


def test_target_list_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert result.exit_code == 0, result.output
    assert result.output == '{"items":[]}\n'


def test_target_list_table_empty_message(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "No sync targets configured" in result.output


def test_target_list_after_add(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(app, ["workflows", "sync", "target", "add", "~/skills"])
    result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["items"][0]["path"] == "~/skills"


def test_target_list_table_renders_rows(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(app, ["workflows", "sync", "target", "add", "--preset", "cursor"])
    result = runner.invoke(app, ["workflows", "sync", "target", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "Sync targets" in result.output
    assert ".cursor/skills" in result.output


def test_target_remove_found(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    # Add a target that stores as the canonical ``~/skills``.
    add_result = runner.invoke(app, ["workflows", "sync", "target", "add", "~/skills"])
    assert json.loads(add_result.output)["path"] == "~/skills"
    # Remove a messy-but-equivalent input: it must normalize to the stored form
    # before matching, so the target is found and removed.
    result = runner.invoke(app, ["workflows", "sync", "target", "remove", "~/foo/../skills"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["removed"] is True
    # The echoed path uses the same ``~``-normalized store form as `add`.
    assert payload["path"] == "~/skills"
    # The config no longer carries the target.
    list_result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert list_result.output == '{"items":[]}\n'


def test_target_remove_not_found(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "remove", "~/skills"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["removed"] is False


def test_target_remove_table_mode(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(app, ["workflows", "sync", "target", "add", "~/skills"])
    result = runner.invoke(app, ["workflows", "sync", "target", "remove", "~/skills", "--table"])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
