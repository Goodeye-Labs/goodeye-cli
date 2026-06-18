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


DEFAULT_EMAIL = "owner@example.com"


def _me_route(email: str = DEFAULT_EMAIL) -> respx.Route:
    """Register the GET /v1/me route the identity guard calls during sync."""
    return respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": email})
    )


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
    # Without --only: recreating a whole target over an existing one is a conflict.
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
    _me_route()
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
    _me_route()
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
    _me_route()
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
    _me_route()
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
    _me_route()
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


@respx.mock
def test_pull_no_targets_renders_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--table"])
    assert result.exit_code == 0, result.output
    assert "No workflows in scope" in result.output


# ----- status -----


def _seed_index_entry(
    tmp_config_paths: ConfigPaths,
    *,
    target_dir: Path,
    workflow_id: str,
    slug: str,
    body: str,
    token: str = "tok",
    version: int = 1,
) -> None:
    """Write a SKILL.md plus a matching index entry recording its hash."""
    from goodeye_cli import sync

    skill = target_dir / slug / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body, encoding="utf-8")
    state = sync.load_sync_state(tmp_config_paths)
    sync.upsert_entry(
        state,
        sync.SyncEntry(
            workflow_id=workflow_id,
            slug=slug,
            target_path=sync.normalize_target_path(str(target_dir)),
            synced_version=version,
            version_token=token,
            body_sha256=sync.body_sha256(body),
        ),
    )
    sync.save_sync_state(state, tmp_config_paths)


def test_status_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    _seed_target(monkeypatch, tmp_config_paths, str(tmp_path / "skills"))
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "status"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_status_json_default_shape(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    # A tracked entry whose disk copy diverges from the recorded hash.
    _seed_index_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body="server body",
        token="tok",
        version=4,
    )
    (target_dir / "alpha" / "SKILL.md").write_text("locally edited", encoding="utf-8")

    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {
                    "id": "skl_01",
                    "name": "alpha",
                    "current_version": 4,
                    "version_token": "tok",
                }
            ]
        )
    )
    # Registered only to prove status never fetches a body.
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="x")
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    item = payload["items"][0]
    assert item["slug"] == "alpha"
    assert item["workflow_id"] == "skl_01"
    assert item["state"] == "modified-local"
    assert item["next_action"] == "push"
    assert item["read_only"] is False
    assert item["synced_version"] == 4
    assert item["server_version"] == 4
    assert list_route.call_count == 1
    assert detail_route.call_count == 0


@respx.mock
def test_status_table_mode(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_index_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body="server body",
        token="t1",
    )
    # Server token advanced past the recorded one: behind-server -> pull.
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 2, "version_token": "t2"}]
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "status", "--table"])
    assert result.exit_code == 0, result.output
    assert "Sync status" in result.output
    assert "alpha" in result.output
    assert "behind-server" in result.output
    # The next-step hint names only a command that exists now.
    assert "Next:" in result.output
    assert "goodeye workflows sync pull" in result.output


@respx.mock
def test_status_table_modified_local_hint_points_at_push(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_index_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body="server body",
        token="tok",
        version=4,
    )
    # Disk diverges from the recorded hash, but the server token is unchanged:
    # modified-local -> push.
    (target_dir / "alpha" / "SKILL.md").write_text("locally edited", encoding="utf-8")
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 4, "version_token": "tok"}]
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "status", "--table"])
    assert result.exit_code == 0, result.output
    assert "modified-local" in result.output
    # The hint names the push command that ships in this feature.
    assert "Next:" in result.output
    assert "goodeye workflows sync push" in result.output


@respx.mock
def test_status_table_conflict_hint_points_at_pull_not_push(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_index_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body="server body",
        token="t1",
        version=4,
    )
    # Both sides moved: disk diverges from the recorded hash AND the server
    # token advanced past the recorded one -> conflict (next_action resolve).
    (target_dir / "alpha" / "SKILL.md").write_text("locally edited", encoding="utf-8")
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 5, "version_token": "t2"}]
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "status", "--table"])
    assert result.exit_code == 0, result.output
    assert "conflict" in result.output
    # A conflict reconciles via pull/resolve, not a straight push.
    assert "Next:" in result.output
    assert "goodeye workflows sync pull" in result.output
    assert "goodeye workflows sync push" not in result.output


@respx.mock
def test_status_no_targets_renders_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "status", "--table"])
    assert result.exit_code == 0, result.output
    assert "No workflows in scope" in result.output


# ----- push -----


def _push_body(
    *,
    slug: str,
    description: str = "Draft a postmortem from an incident transcript.",
    outcome: str = "Reduce mean-time-to-postmortem from days to hours.",
) -> str:
    """A workflow body with front-matter whose name matches its slug."""
    return (
        f"---\nname: {slug}\ndescription: {description}\noutcome: {outcome}\n---\n\ndo the work\n"
    )


def _seed_modified_entry(
    tmp_config_paths: ConfigPaths,
    *,
    target_dir: Path,
    workflow_id: str,
    slug: str,
    body: str,
    token: str = "tok",
    role: str = "owner",
) -> None:
    """Write a SKILL.md and a tracked entry whose recorded hash will not match.

    The entry records a placeholder hash, so the on-disk ``body`` reads as
    modified and becomes a push candidate.
    """
    from goodeye_cli import sync

    skill = target_dir / slug / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body, encoding="utf-8")
    state = sync.load_sync_state(tmp_config_paths)
    sync.upsert_entry(
        state,
        sync.SyncEntry(
            workflow_id=workflow_id,
            slug=slug,
            target_path=sync.normalize_target_path(str(target_dir)),
            synced_version=1,
            version_token=token,
            body_sha256=sync.body_sha256(f"recorded-base-{slug}"),
            effective_role=role,
            read_only=role == "view",
        ),
    )
    sync.save_sync_state(state, tmp_config_paths)


def _save_response(*, workflow_id: str, name: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "workflow_id": workflow_id,
            "version": 2,
            "name": name,
            "version_token": "tok-2",
            "verifiers": [],
        },
    )


def test_push_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    _seed_target(monkeypatch, tmp_config_paths, str(tmp_path / "skills"))
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_push_json_default_shape(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body=_push_body(slug="alpha"),
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_01", name="alpha")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    item = payload["items"][0]
    assert item["slug"] == "alpha"
    assert item["action"] == "pushed"
    assert item["workflow_id"] == "skl_01"
    assert save_route.call_count == 1


@respx.mock
def test_push_table_mode(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body=_push_body(slug="alpha"),
    )
    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_01", name="alpha")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push", "--table"])
    assert result.exit_code == 0, result.output
    assert "Pushed workflows" in result.output
    assert "alpha" in result.output
    assert "pushed" in result.output


@respx.mock
def test_push_table_conflict_hint(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_01",
        slug="alpha",
        body=_push_body(slug="alpha"),
    )
    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            409, json={"error": "conflict", "message": "version token mismatch"}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push", "--table"])
    assert result.exit_code == 0, result.output
    assert "conflict" in result.output
    assert "Next:" in result.output
    # The hint references only commands that exist now.
    assert "goodeye workflows sync pull" in result.output
    assert "goodeye workflows sync status" in result.output


@respx.mock
def test_push_target_option_scopes_to_one(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    _seed_target(monkeypatch, tmp_config_paths, str(first))
    _seed_target(monkeypatch, tmp_config_paths, str(second))
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=first,
        workflow_id="skl_a",
        slug="alpha",
        body=_push_body(slug="alpha"),
    )
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=second,
        workflow_id="skl_b",
        slug="beta",
        body=_push_body(slug="beta"),
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_b", name="beta")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push", "--target", str(second)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [i["slug"] for i in payload["items"]] == ["beta"]
    assert save_route.call_count == 1


@respx.mock
def test_push_no_targets_renders_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push", "--table"])
    assert result.exit_code == 0, result.output
    assert "No locally edited workflows to push" in result.output


# ----- bare `sync` umbrella -----


def test_sync_umbrella_requires_auth(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    _seed_target(monkeypatch, tmp_config_paths, str(tmp_path / "skills"))
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_sync_umbrella_pulls_then_shows_status(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    # status lists with include_archived, pull lists without; both hit the same
    # route, so one mock serves both passes.
    list_route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [{"id": "skl_01", "name": "alpha", "current_version": 1, "version_token": "tok"}]
        )
    )
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=_detail_response(id_="skl_01", name="alpha", body="alpha body")
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync"])
    assert result.exit_code == 0, result.output

    # The pull materialized the workflow on disk and the index.
    assert (target_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha body"
    assert detail_route.call_count == 1
    # The umbrella then rendered the STATUS result (post-pull): alpha is clean.
    payload = json.loads(result.output)
    item = payload["items"][0]
    assert item["slug"] == "alpha"
    assert item["state"] == "clean"
    # Both the pull and status passes listed.
    assert list_route.call_count >= 2


@respx.mock
def test_sync_umbrella_table_mode_renders_status(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
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
    result = runner.invoke(app, ["workflows", "sync", "--table"])
    assert result.exit_code == 0, result.output
    assert "Sync status" in result.output
    assert "alpha" in result.output
    assert "clean" in result.output


@respx.mock
def test_sync_umbrella_identity_mismatch_surfaces_conflict(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    """The bare umbrella aborts before any pull when the signed-in account does
    not match the account this local sync was set up for.
    """
    from goodeye_cli import sync

    _setup_auth(monkeypatch, tmp_config_paths)
    # The local index was stamped for one account.
    _me_route("intruder@example.com")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    state = sync.load_sync_state(tmp_config_paths)
    state.identity = "owner@example.com"
    sync.save_sync_state(state, tmp_config_paths)

    # Registered to prove the guard fires before any listing or materialization.
    list_route = respx.get(f"{SERVER}/v1/workflows")
    detail_route = respx.get(f"{SERVER}/v1/workflows/skl_01")

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync"])

    assert result.exit_code != 0
    assert isinstance(result.exception, Conflict)
    # No pull happened: nothing was listed, fetched, or written to disk.
    assert list_route.call_count == 0
    assert detail_route.call_count == 0
    assert not (target_dir / "alpha").exists()


def test_sync_subcommand_target_list_still_works(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # The callback early-returns for subcommands, so `target list` (a purely
    # local read with no auth) is unaffected by the umbrella.
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert result.exit_code == 0, result.output
    assert result.output == '{"items":[]}\n'


@respx.mock
def test_sync_pull_subcommand_still_works(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    # `sync pull` must dispatch to the pull command, not the umbrella.
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
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
    # The pull renderer reports per-item actions, not status states.
    assert payload["items"][0]["action"] == "pulled"


# ----- pull deletion reconcile (CLI) -----


@respx.mock
def test_pull_yes_removes_deleted_local_copy(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    # A tracked, clean local copy of a workflow that is now soft-deleted.
    _seed_index_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_gone",
        slug="gone",
        body="registry body",
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {
                    "id": "skl_gone",
                    "name": "gone",
                    "current_version": 1,
                    "version_token": "tok",
                    "archived_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
    )
    delete_route = respx.delete(f"{SERVER}/v1/workflows/skl_gone")

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--yes"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["items"][0]["action"] == "deleted-local"
    assert not (target_dir / "gone").exists()
    # The local removal never issued a server-side delete.
    assert delete_route.call_count == 0


@respx.mock
def test_pull_table_hint_on_deleted_on_server(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    target_dir = tmp_path / "skills"
    _seed_target(monkeypatch, tmp_config_paths, str(target_dir))
    _seed_index_entry(
        tmp_config_paths,
        target_dir=target_dir,
        workflow_id="skl_gone",
        slug="gone",
        body="registry body",
    )
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=_list_response(
            [
                {
                    "id": "skl_gone",
                    "name": "gone",
                    "current_version": 1,
                    "version_token": "tok",
                    "archived_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
    )
    # A real terminal where the user declines the prompt. CliRunner reassigns
    # sys.stdin during invoke, so patch the confirm helper the sync engine
    # bound at import time to return False.
    monkeypatch.setattr("goodeye_cli.sync.confirm_destructive", lambda *a, **k: False)

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "pull", "--table"])
    assert result.exit_code == 0, result.output
    # The deletion was reported (the table cell may wrap the long action string,
    # so assert on the unwrapped next-step hint that names a real command).
    assert "Next:" in result.output
    assert "gone from the registry" in result.output
    assert "goodeye workflows sync pull --yes" in result.output
    # The declined copy survives on disk.
    assert (target_dir / "gone" / "SKILL.md").exists()


# ----- multi-target convergence (CLI) -----


@respx.mock
def test_push_converges_sibling_target_once(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    first = tmp_path / "first"
    second = tmp_path / "second"
    _seed_target(monkeypatch, tmp_config_paths, str(first), scope="all")
    _seed_target(monkeypatch, tmp_config_paths, str(second), scope="all")
    # The same workflow is edited identically in both targets.
    body = _push_body(slug="alpha")
    _seed_modified_entry(
        tmp_config_paths, target_dir=first, workflow_id="skl_a", slug="alpha", body=body
    )
    _seed_modified_entry(
        tmp_config_paths, target_dir=second, workflow_id="skl_a", slug="alpha", body=body
    )
    save_route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=_save_response(workflow_id="skl_a", name="alpha")
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    actions = sorted(i["action"] for i in payload["items"])
    assert actions == ["converged", "pushed"]
    # One upload covered both copies.
    assert save_route.call_count == 1


@respx.mock
def test_push_diverged_hint(tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path) -> None:
    _setup_auth(monkeypatch, tmp_config_paths)
    _me_route()
    monkeypatch.setenv("COLUMNS", "200")
    first = tmp_path / "first"
    second = tmp_path / "second"
    _seed_target(monkeypatch, tmp_config_paths, str(first), scope="all")
    _seed_target(monkeypatch, tmp_config_paths, str(second), scope="all")
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=first,
        workflow_id="skl_a",
        slug="alpha",
        body=_push_body(slug="alpha", description="Edit A keeps its own description."),
    )
    _seed_modified_entry(
        tmp_config_paths,
        target_dir=second,
        workflow_id="skl_a",
        slug="alpha",
        body=_push_body(slug="alpha", description="Edit B differs from A."),
    )
    save_route = respx.post(f"{SERVER}/v1/workflows")

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "push", "--table"])
    assert result.exit_code == 0, result.output
    # No upload; the hint points at --target to pick the copy to keep.
    assert save_route.call_count == 0
    assert "Next:" in result.output
    assert "goodeye workflows sync push --target" in result.output


def test_push_pull_required_hint_points_at_pull() -> None:
    """A ``pull-required`` copy yields a hint pointing at the pull command.

    The copy was deferred because the push changed sibling files it does not
    have; the only way to refresh it is an ordinary pull.
    """
    from goodeye_cli.commands.workflows_sync import _push_hints
    from goodeye_cli.sync import PushItem

    items = [
        PushItem(slug="alpha", workflow_id="skl_a", target_path="/a", action="pushed"),
        PushItem(
            slug="alpha",
            workflow_id="skl_a",
            target_path="/b",
            action="pull-required",
            detail="changed sibling files",
        ),
    ]
    hints = _push_hints(items)
    assert any("goodeye workflows sync pull" in h and "sibling" in h for h in hints)


# ----- target add --only: allowlist append -----


def test_target_add_only_appends_to_existing_selected_target_default_mode(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    # Create a selected target with one entry.
    create = runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "selected", "--only", "a"],
    )
    assert create.exit_code == 0, create.output

    # Append two more entries (one new, one already present).
    result = runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "~/skills",
            "--only",
            "b",
            "--only",
            "a",
            "--table",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Added 1 workflow" in result.output
    assert "~/skills" in result.output
    # Pull hint must appear.
    assert "goodeye workflows sync pull" in result.output


def test_target_add_only_appends_to_existing_selected_target_json_mode(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    # Create a selected target.
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "selected", "--only", "x"],
    )
    # Append via --only in JSON mode.
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--only", "y", "--only", "x"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["added"] == ["y"]
    assert payload["already_present"] == ["x"]
    assert payload["path"] == "~/skills"
    assert payload["scope"] == "selected"


def test_target_add_only_all_already_present_prints_nothing_to_add(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "selected", "--only", "a"],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--only", "a", "--table"],
    )
    assert result.exit_code == 0, result.output
    assert "already in the allowlist" in result.output
    assert "nothing to add" in result.output


def test_target_add_only_against_non_selected_target_raises_validation_failed(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "owned"],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--only", "a"],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)


def test_target_add_only_with_conflicting_explicit_scope_raises_conflict(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "selected", "--only", "a"],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "owned", "--only", "b"],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, Conflict)


# ----- target remove --only: allowlist prune -----


def test_target_remove_only_prunes_entries_table_mode(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "~/skills",
            "--scope",
            "selected",
            "--only",
            "a",
            "--only",
            "b",
            "--only",
            "c",
        ],
    )
    result = runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "remove",
            "~/skills",
            "--only",
            "a",
            "--only",
            "c",
            "--table",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Removed 2 workflow" in result.output
    assert "~/skills" in result.output


def test_target_remove_only_prunes_entries_json_mode(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "~/skills",
            "--scope",
            "selected",
            "--only",
            "a",
            "--only",
            "b",
        ],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "remove", "~/skills", "--only", "a", "--only", "x"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["removed"] == ["a"]
    assert payload["absent"] == ["x"]
    assert payload["path"] == "~/skills"


def test_target_remove_only_none_matched_prints_not_in_allowlist(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "selected", "--only", "a"],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "remove", "~/skills", "--only", "z", "--table"],
    )
    assert result.exit_code == 0, result.output
    assert "not in the allowlist" in result.output


def test_target_remove_without_only_still_removes_whole_target(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # Existing test for whole-target removal must still pass unchanged.
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    add_result = runner.invoke(app, ["workflows", "sync", "target", "add", "~/skills"])
    assert json.loads(add_result.output)["path"] == "~/skills"
    result = runner.invoke(app, ["workflows", "sync", "target", "remove", "~/foo/../skills"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["removed"] is True
    assert payload["path"] == "~/skills"
    list_result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert list_result.output == '{"items":[]}\n'


def test_target_remove_only_against_non_selected_target_raises_validation_failed(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "owned"],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "remove", "~/skills", "--only", "a"],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)


def test_target_remove_only_deduplicates_input_prints_correct_count(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # Duplicate --only values must not inflate the reported count or the list.
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "~/skills",
            "--scope",
            "selected",
            "--only",
            "a",
            "--only",
            "b",
        ],
    )
    result = runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "remove",
            "~/skills",
            "--only",
            "a",
            "--only",
            "a",
            "--table",
        ],
    )
    assert result.exit_code == 0, result.output
    # Duplicate input collapses to one removal, so count must be 1.
    assert "Removed 1 workflow" in result.output
    # 'a' must appear exactly once, not twice.
    assert result.output.count(": a") == 1 or "a, a" not in result.output


# ----- Fix 2: readback assertions for append and prune command tests -----


def test_target_add_only_appends_to_allowlist_readback(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # After appending, target list --json must reflect the new entry in selected.
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--scope", "selected", "--only", "x"],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "add", "~/skills", "--only", "y"],
    )
    assert result.exit_code == 0, result.output

    list_result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert list_result.exit_code == 0, list_result.output
    payload = json.loads(list_result.output)
    selected = payload["items"][0]["selected"]
    assert "y" in selected
    assert "x" in selected


def test_target_remove_only_prunes_from_allowlist_readback(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # After pruning, target list --json must show the entry absent from selected.
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "~/skills",
            "--scope",
            "selected",
            "--only",
            "a",
            "--only",
            "b",
        ],
    )
    result = runner.invoke(
        app,
        ["workflows", "sync", "target", "remove", "~/skills", "--only", "a"],
    )
    assert result.exit_code == 0, result.output

    list_result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert list_result.exit_code == 0, list_result.output
    payload = json.loads(list_result.output)
    selected = payload["items"][0]["selected"]
    assert "a" not in selected
    assert "b" in selected


# ----- Fix 3: preset + --only append path -----


def test_target_add_only_with_preset_appends_to_existing_preset_target(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # Create a target via --preset claude, then append to it with --preset claude --only.
    # The preset must resolve to the same path before matching.
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    create = runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "--preset",
            "claude",
            "--scope",
            "selected",
            "--only",
            "existing-wf",
        ],
    )
    assert create.exit_code == 0, create.output

    append = runner.invoke(
        app,
        [
            "workflows",
            "sync",
            "target",
            "add",
            "--preset",
            "claude",
            "--only",
            "new-wf",
        ],
    )
    assert append.exit_code == 0, append.output
    payload = json.loads(append.output)
    assert "new-wf" in payload["added"]

    list_result = runner.invoke(app, ["workflows", "sync", "target", "list"])
    assert list_result.exit_code == 0, list_result.output
    items = json.loads(list_result.output)["items"]
    assert len(items) == 1
    selected = items[0]["selected"]
    assert "existing-wf" in selected
    assert "new-wf" in selected


# ----- workflows sync auto -----


def _load_auto(tmp_config_paths: ConfigPaths):
    from goodeye_cli import sync

    return sync.load_sync_config(tmp_config_paths).auto


def test_auto_on_enables_and_defaults_to_compact_json(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "auto", "on"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["enabled"] is True
    assert payload["interval_seconds"] == 3600
    assert payload["last_auto_pull_at"] is None
    # The config on disk reflects the change.
    assert _load_auto(tmp_config_paths).enabled is True


def test_auto_on_with_interval_sets_interval(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "auto", "on", "--interval", "120"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["interval_seconds"] == 120
    assert _load_auto(tmp_config_paths).interval_seconds == 120


def test_auto_on_rejects_non_positive_interval(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "auto", "on", "--interval", "0"])
    assert result.exit_code != 0
    # The setting was not flipped on by a rejected request.
    assert _load_auto(tmp_config_paths).enabled is False


def test_auto_off_disables_but_keeps_interval(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(app, ["workflows", "sync", "auto", "on", "--interval", "900"])
    result = runner.invoke(app, ["workflows", "sync", "auto", "off"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["enabled"] is False
    # The interval is remembered for the next `on`.
    assert payload["interval_seconds"] == 900
    assert _load_auto(tmp_config_paths).interval_seconds == 900


def test_auto_status_reports_current_setting(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(app, ["workflows", "sync", "auto", "on", "--interval", "600"])
    result = runner.invoke(app, ["workflows", "sync", "auto"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "enabled": True,
        "interval_seconds": 600,
        "last_auto_pull_at": None,
    }


def test_auto_status_human_mode_on_tty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _redirect_config(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    runner.invoke(app, ["workflows", "sync", "auto", "on"])
    # --table forces the human-readable view regardless of TTY detection.
    result = runner.invoke(app, ["workflows", "sync", "auto", "--table"])
    assert result.exit_code == 0, result.output
    assert "Automatic pull is" in result.output
    assert "on" in result.output
    assert "never" in result.output


def test_auto_status_reports_last_pull_time(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    from datetime import UTC, datetime

    from goodeye_cli import sync

    _redirect_config(monkeypatch, tmp_config_paths)
    config = sync.SyncConfig(auto=sync.AutoConfig(enabled=True, interval_seconds=300))
    sync.save_sync_config(config, tmp_config_paths)
    state = sync.SyncState(last_auto_pull_at=datetime(2026, 6, 14, 17, 2, 11, tzinfo=UTC))
    sync.save_sync_state(state, tmp_config_paths)

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "sync", "auto"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["last_auto_pull_at"] == "2026-06-14T17:02:11+00:00"
