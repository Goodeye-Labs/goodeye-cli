"""Tests for the workflows subcommand group."""

from __future__ import annotations

import json as _json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.commands.workflows import _parse_front_matter
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_workflows_list_renders_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "skl_01",
                        "name": "one",
                        "visibility": "public",
                        "current_version": 1,
                        "description": "first workflow",
                    },
                    {
                        "id": "skl_02",
                        "name": "two",
                        "visibility": "private",
                        "current_version": 3,
                        "description": "second workflow",
                    },
                ],
                "next_cursor": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "list", "--filter", "all"])
    assert result.exit_code == 0, result.output
    assert "skl_01" in result.output
    assert "skl_02" in result.output


@respx.mock
def test_workflows_list_follows_cursor(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    responses = [
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_01", "name": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": "c1",
            },
        ),
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_02", "name": "b", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": None,
            },
        ),
    ]
    route = respx.get(f"{SERVER}/v1/workflows").mock(side_effect=responses)

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "list", "--filter", "public"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    assert "skl_01" in result.output and "skl_02" in result.output


@respx.mock
def test_workflows_get_markdown_default(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/example").mock(
        return_value=httpx.Response(200, text="# hi\nbody")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "get", "example"])
    assert result.exit_code == 0, result.output
    assert "# hi" in result.output
    # Wrap with agent-execution markers so the calling agent knows to run it.
    assert "# Goodeye workflow" in result.output
    assert "execute the instructions below" in result.output
    assert "# End of Goodeye workflow." in result.output


@respx.mock
def test_workflows_get_json_flag(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "skl_01",
                "name": "example",
                "visibility": "public",
                "version": 1,
                "body": "hi",
                "description": "example workflow",
                "outcome": "ship more reliable refunds",
                "tags": ["demo"],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "get", "example", "--json"])
    assert result.exit_code == 0, result.output
    assert '"name": "example"' in result.output
    assert '"outcome": "ship more reliable refunds"' in result.output
    # JSON output skips the agent-execution wrappers so consumers can parse cleanly.
    assert "# Goodeye workflow" not in result.output
    assert "# End of Goodeye workflow." not in result.output


@respx.mock
def test_publish_minimal_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Claude-Code-style minimal workflow: just name + description + body."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text(
        "---\n"
        "name: hello\n"
        "description: Say hi to the world. Use when onboarding.\n"
        "---\n"
        "# Hello\n\nGreet the user.\n"
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "hello",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "hello"
    assert sent["description"].startswith("Say hi")
    # visibility is no longer a workflow field (dropped with templates).
    assert "visibility" not in sent
    # No discovery facets in the payload when front-matter omits them.
    assert "outcome" not in sent
    assert "tags" not in sent
    # Server dropped the nested manifest field on Apr 22, 2026.
    assert "manifest" not in sent
    # Body round-trips with the front-matter intact so consumers can drop the
    # downloaded file into ~/.claude/skills/hello/SKILL.md unchanged.
    assert sent["body"].startswith("---\n")
    assert "# Hello" in sent["body"]
    assert sent["expected_version_token"] is None


@respx.mock
def test_publish_accepts_expected_version_token_option(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text("---\nname: hello\ndescription: Say hi.\n---\n# Hello\n")
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 2,
                "version_token": "new-token",
                "name": "hello",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            str(workflow_file),
            "--expected-version-token",
            "old-token",
        ],
    )

    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["expected_version_token"] == "old-token"
    assert "new-token" in result.output


@respx.mock
def test_publish_accepts_slug_alias_in_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Transitional: older authored files may still use `slug:` instead of `name:`."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "legacy.md"
    workflow_file.write_text(
        "---\nslug: my-workflow\ndescription: test desc\n---\nBody\n",
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "my-workflow",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "my-workflow"


@respx.mock
def test_publish_missing_description_errors(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "no-desc.md"
    workflow_file.write_text("---\nname: no-desc\n---\nBody\n")
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code != 0
    # ValidationFailed bubbles up as an exception under CliRunner; inspect
    # the exception message rather than captured output.
    assert result.exception is not None
    assert "description" in str(result.exception).lower()


@respx.mock
def test_publish_tags_and_outcome(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "rich.md"
    workflow_file.write_text(
        "---\n"
        "name: rich-workflow\n"
        "description: A workflow with discovery facets.\n"
        "tags: [csv, stripe]\n"
        "outcome: Reduce refund-row errors\n"
        "---\n"
        "# Body\n",
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "rich-workflow",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["tags"] == ["csv", "stripe"]
    assert sent["outcome"] == "Reduce refund-row errors"
    assert "manifest" not in sent


@respx.mock
def test_publish_legacy_manifest_promotes_outcome_and_tags(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Pre-cleanup files nest outcome/tags under manifest:; promote them and warn."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "legacy.md"
    workflow_file.write_text(
        "---\n"
        "name: legacy-workflow\n"
        "description: An old-style workflow with a manifest block.\n"
        "manifest:\n"
        "  outcome: Reduce refund-row errors\n"
        "  tags: [csv, stripe]\n"
        "  kpi:\n"
        "    name: error_rate\n"
        "    definition: rows mislabeled / total\n"
        "  programmatic_verifiers: []\n"
        "---\n"
        "# Body\n",
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "legacy-workflow",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["outcome"] == "Reduce refund-row errors"
    assert sent["tags"] == ["csv", "stripe"]
    assert "manifest" not in sent
    assert "deprecated" in result.output.lower()
    assert "kpi" in result.output
    assert "programmatic_verifiers" in result.output


@respx.mock
def test_publish_top_level_outcome_wins_over_manifest(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When both top-level outcome and manifest.outcome exist, top-level wins."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "mixed.md"
    workflow_file.write_text(
        "---\n"
        "name: mixed-workflow\n"
        "description: Has both shapes.\n"
        "outcome: Top-level wins\n"
        "manifest:\n"
        "  outcome: Legacy loses\n"
        "---\n"
        "# Body\n",
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "mixed-workflow",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["outcome"] == "Top-level wins"


@respx.mock
def test_workflows_delete_with_yes_flag(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/workflows/skl_01").mock(
        return_value=httpx.Response(
            200, json={"workflow_id": "skl_01", "name": "skl_01", "deleted": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "delete", "skl_01", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output


@respx.mock
def test_workflow_grant_commands(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    grant_route = respx.post(f"{SERVER}/v1/workflows/wf_1/grants").mock(
        return_value=httpx.Response(201, json={"workflow_id": "wf_1", "role": "admin"})
    )
    respx.get(f"{SERVER}/v1/workflows/wf_1/grants").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "grantee_type": "team",
                        "grantee_identifier": "@analytics",
                        "role": "admin",
                        "granted_by": "owner",
                        "granted_at": "2026-04-24T00:00:00Z",
                        "is_via_team": True,
                    }
                ]
            },
        )
    )
    revoke_route = respx.delete(f"{SERVER}/v1/workflows/wf_1/grants").mock(
        return_value=httpx.Response(200, json={"workflow_id": "wf_1", "revoked": True})
    )
    respx.post(f"{SERVER}/v1/workflows/wf_1/leave").mock(
        return_value=httpx.Response(200, json={"workflow_id": "wf_1", "removed_direct_grants": 1})
    )

    runner = CliRunner()
    grant = runner.invoke(app, ["workflows", "grant", "wf_1", "@analytics", "admin"])
    grants = runner.invoke(app, ["workflows", "grants", "wf_1"])
    revoke = runner.invoke(app, ["workflows", "revoke-grant", "wf_1", "@analytics"])
    leave = runner.invoke(app, ["workflows", "leave", "wf_1", "--yes"])

    assert grant.exit_code == 0, grant.output
    grant_body = _json.loads(grant_route.calls.last.request.content.decode())
    assert grant_body["grantee_email_or_at_team_handle"] == "@analytics"
    assert "@analytics" in grants.output
    assert revoke.exit_code == 0, revoke.output
    revoke_body = _json.loads(revoke_route.calls.last.request.content.decode())
    assert revoke_body["grantee_email_or_at_team_handle"] == "@analytics"
    assert leave.exit_code == 0, leave.output


@respx.mock
def test_workflows_transfer_ownership_command(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/transfer-ownership").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "owner_user_id": "user_2",
                "transferred": True,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "transfer-ownership", "wf_1", "new@example.com"])

    assert result.exit_code == 0, result.output
    body = _json.loads(route.calls.last.request.content.decode())
    assert body["new_owner_user_id_or_email"] == "new@example.com"
    assert "Transferred" in result.output
    assert "user_2" in result.output


def test_parse_front_matter_extracts_manifest() -> None:
    source = "---\nname: foo\ndescription: bar\nmanifest:\n  outcome: x\n---\nBody text\n"
    fm, body = _parse_front_matter(source)
    assert fm == {"name": "foo", "description": "bar", "manifest": {"outcome": "x"}}
    assert body == "Body text\n"


def test_parse_front_matter_without_front_matter_returns_source() -> None:
    fm, body = _parse_front_matter("just body\n")
    assert fm == {}
    assert body == "just body\n"
