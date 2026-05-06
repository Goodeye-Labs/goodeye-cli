"""Tests for the workflows subcommand group."""

from __future__ import annotations

import json as _json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.commands.workflows import _parse_front_matter, _parse_workflow_verifier_flags
from goodeye_cli.config import ConfigPaths, save_credentials
from goodeye_cli.errors import ValidationFailed

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
def test_workflows_search_posts_to_search_endpoint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "w1",
                        "slug": "one",
                        "name": "one",
                        "rank": 1,
                        "match_reason": "Matches chart critique.",
                    }
                ],
                "query": "chart critique",
                "limit": 5,
                "search_mode": "llm",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "search", "chart critique"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.method == "POST"
    assert req.url.path == "/v1/workflows/search"
    body = _json.loads(req.content.decode())
    assert body["query"] == "chart critique"
    assert "Matches chart critique" in result.output


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
    assert "unknown" not in sent
    # Body round-trips with front matter intact so Goodeye can return the same
    # workflow body.
    assert sent["body"].startswith("---\n")
    assert "# Hello" in sent["body"]
    assert sent["expected_version_token"] is None


@respx.mock
def test_publish_reads_markdown_from_stdin(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    markdown = (
        "---\n"
        "name: stdin-workflow\n"
        "description: Save a generated workflow without a local file.\n"
        "outcome: Reduce local workflow artifacts\n"
        "tags: [agent, stdin]\n"
        "---\n"
        "# Body\n\n"
        "Use this generated workflow body.\n"
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_stdin",
                "version": 1,
                "version_token": "tok-stdin",
                "name": "stdin-workflow",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", "-"], input=markdown)

    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "stdin-workflow"
    assert sent["description"] == "Save a generated workflow without a local file."
    assert sent["outcome"] == "Reduce local workflow artifacts"
    assert sent["tags"] == ["agent", "stdin"]
    assert sent["body"] == markdown
    assert sent["expected_version_token"] is None


@respx.mock
def test_publish_forwards_verifier_bindings(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "with-v.md"
    workflow_file.write_text(
        "---\nname: with-v\ndescription: Workflow with verifier bindings.\n---\n# Body\n",
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_v1",
                "version": 1,
                "version_token": "tok-v",
                "name": "with-v",
            },
        )
    )
    runner = CliRunner()
    vid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            str(workflow_file),
            "--verifier",
            f"tone={vid}",
            "--verifier",
            f"factual={vid}",
        ],
    )
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["verifiers"] == [
        {"name": "tone", "verifier_id": vid},
        {"name": "factual", "verifier_id": vid},
    ]


@respx.mock
def test_publish_update_without_verifier_flags_preserves_server_bindings(
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
    assert "verifiers" not in sent
    assert "new-token" in result.output


@respx.mock
def test_publish_clear_verifiers_sends_explicit_empty_list(
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
                "verifiers": [],
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
            "--clear-verifiers",
        ],
    )

    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["verifiers"] == []


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
def test_publish_stdin_missing_description_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", "-"], input="---\nname: no-desc\n---\nBody\n")

    assert result.exit_code != 0
    assert result.exception is not None
    assert "description" in str(result.exception).lower()


def test_publish_unreadable_file_errors(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "unreadable.md"
    workflow_file.write_text("---\nname: unreadable\ndescription: Cannot read.\n---\nBody\n")
    original_read_text = Path.read_text

    def raise_for_workflow_file(path: Path, *args, **kwargs) -> str:
        if path == workflow_file:
            raise PermissionError("permission denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raise_for_workflow_file)
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "read" in str(result.exception).lower()
    assert str(workflow_file) in str(result.exception)


def test_publish_missing_file_errors_cleanly(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    missing_file = tmp_path / "missing.md"
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", str(missing_file)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "not found" in str(result.exception).lower()
    assert str(missing_file) in str(result.exception)


def test_publish_directory_path_errors_cleanly(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", str(tmp_path)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "not a file" in str(result.exception).lower()
    assert str(tmp_path) in str(result.exception)


def test_publish_invalid_utf8_file_errors_cleanly(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "invalid.md"
    workflow_file.write_bytes(b"\xff")
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "utf-8" in str(result.exception).lower()
    assert str(workflow_file) in str(result.exception)


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
        "unknown: ignored\n"
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
    assert "unknown" not in sent


@respx.mock
def test_publish_source_flag_is_forwarded(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """`--source teach` must reach the server as the literal "teach", not the file body."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text("---\nname: hello\ndescription: Say hi.\n---\n# Hello\n")
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "hello",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file), "--source", "teach"])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["source"] == "teach"


@respx.mock
def test_publish_omits_source_when_flag_absent(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """No --source flag -> source must not be populated with the markdown body."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text("---\nname: hello\ndescription: Say hi.\n---\n# Hello\n")
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 1,
                "version_token": "tok-1",
                "name": "hello",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    # Either omitted entirely or explicitly null -- never the file body.
    assert sent.get("source") in (None,)


@respx.mock
def test_publish_unknown_front_matter_is_not_special(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "legacy.md"
    workflow_file.write_text(
        "---\n"
        "name: unknown-front-matter-workflow\n"
        "description: A workflow with an unknown front-matter block.\n"
        "unknown:\n"
        "  outcome: Reduce refund-row errors\n"
        "  tags: [csv, stripe]\n"
        "  detail:\n"
        "    name: error_rate\n"
        "    definition: rows mislabeled / total\n"
        "  checks: []\n"
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
                "name": "unknown-front-matter-workflow",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert "outcome" not in sent
    assert "tags" not in sent
    assert "unknown" not in sent
    assert "deprecated" not in result.output.lower()
    assert "checks" not in result.output


@respx.mock
def test_publish_top_level_outcome_ignores_unknown_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "mixed.md"
    workflow_file.write_text(
        "---\n"
        "name: mixed-workflow\n"
        "description: Has top-level outcome and an unknown block.\n"
        "outcome: Top-level value\n"
        "unknown:\n"
        "  outcome: Ignored value\n"
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
    assert sent["outcome"] == "Top-level value"
    assert "unknown" not in sent


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
    grant = runner.invoke(app, ["workflows", "grant", "wf_1", "analytics", "admin"])
    grants = runner.invoke(app, ["workflows", "grants", "wf_1"])
    revoke = runner.invoke(app, ["workflows", "revoke-grant", "wf_1", "analytics"])
    leave = runner.invoke(app, ["workflows", "leave", "wf_1", "--yes"])

    assert grant.exit_code == 0, grant.output
    grant_body = _json.loads(grant_route.calls.last.request.content.decode())
    assert grant_body["grantee_email_or_at_team_handle"] == "analytics"
    assert "@analytics" in grants.output
    assert revoke.exit_code == 0, revoke.output
    revoke_body = _json.loads(revoke_route.calls.last.request.content.decode())
    assert revoke_body["grantee_email_or_at_team_handle"] == "analytics"
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


def test_parse_front_matter_extracts_unknown_nested_fields() -> None:
    source = "---\nname: foo\ndescription: bar\nunknown:\n  outcome: x\n---\nBody text\n"
    fm, body = _parse_front_matter(source)
    assert fm == {"name": "foo", "description": "bar", "unknown": {"outcome": "x"}}
    assert body == "Body text\n"


def test_parse_front_matter_without_front_matter_returns_source() -> None:
    fm, body = _parse_front_matter("just body\n")
    assert fm == {}
    assert body == "just body\n"


def test_parse_workflow_verifier_flags_rejects_route_unsafe_names() -> None:
    with pytest.raises(ValidationFailed, match="name"):
        _parse_workflow_verifier_flags(["tone/check=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])


def test_parse_workflow_verifier_flags_rejects_deploy_incompatible_names() -> None:
    with pytest.raises(ValidationFailed, match="name"):
        _parse_workflow_verifier_flags(["tone_check=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])
