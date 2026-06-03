"""Tests for the `goodeye workflows sync target` subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import AuthRequired, Conflict, ValidationFailed

SERVER = "https://example.test"


def _redirect_config(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    # The config path resolver honors XDG_CONFIG_HOME, so pointing it at the
    # temp dir redirects sync.json into the fixture's tree.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))


def _setup_auth(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    monkeypatch.setenv("GOODEYE_API_KEY", "good_live_EXAMPLE")
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)


def _seed_target(
    monkeypatch, tmp_config_paths: ConfigPaths, path: str, scope: str = "owned"
) -> str:
    """Add a sync target via the CLI and return its stored path."""
    runner = CliRunner()
    add = runner.invoke(app, ["workflows", "sync", "target", "add", path, "--scope", scope])
    assert add.exit_code == 0, add.output
    return json.loads(add.output)["path"]


def _list_response(items: list[dict], next_cursor: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"items": items, "next_cursor": next_cursor})


def _detail_response(*, id_: str, name: str, body: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": id_,
            "name": name,
            "version": 1,
            "body": body,
            "version_token": "tok",
            "effective_role": "owner",
            "verifiers": [],
        },
    )


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


# ----- pull -----


def test_pull_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    _seed_target(monkeypatch, tmp_config_paths, str(tmp_path / "skills"))
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_pull_json_default_shape(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))

    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok"}]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="alpha body")
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["items"][0]["slug"] == "alpha"
    assert payload["items"][0]["action"] == "pulled"
    assert payload["items"][0]["workflow_id"] == "skl_01"
    assert (target_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha body"


@respx.mock
def test_pull_table_mode(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    # A wide terminal keeps Rich from truncating the slug/action cells.
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok"}]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="alpha body")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--table"])
    assert result.exit_code == 0, result.output
    assert "Pulled workflows" in result.output
    assert "alpha" in result.output
    assert "pulled" in result.output


@respx.mock
def test_pull_table_hint_on_skipped(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    # A wide terminal keeps Rich from truncating the action cell.
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    # Pre-existing untracked local file -> skipped-modified, no force.
    skill = target_dir / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("hand written", encoding="utf-8")
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok"}]
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--table"])
    assert result.exit_code == 0, result.output
    # An untracked file (no index entry) is reported as plain modified, never a
    # conflict: there is no recorded sync point for both sides to have moved past.
    assert "skipped-modified" in result.output
    assert "Next:" in result.output
    assert "--force" in result.output
    assert skill.read_text(encoding="utf-8") == "hand written"


@respx.mock
def test_pull_force_overwrites(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    skill = target_dir / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("hand written", encoding="utf-8")
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok"}]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="registry body")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--force"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["items"][0]["action"] == "pulled"
    assert skill.read_text(encoding="utf-8") == "registry body"


@respx.mock
def test_pull_target_option_scopes_to_one(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _seed_target(monkeypatch, tmp_config_paths, str(first))
    _seed_target(monkeypatch, tmp_config_paths, str(second))
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok"}]
        )
    )
    respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="alpha body")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--target", str(second)])
    assert result.exit_code == 0, result.output
    assert list_route.call_count == 1  # only one target listed
    assert (second / "alpha" / "SKILL.md").exists()
    assert not (first / "alpha").exists()


def test_pull_no_targets_renders_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--table"])
    assert result.exit_code == 0, result.output
    assert "No workflows in scope" in result.output
