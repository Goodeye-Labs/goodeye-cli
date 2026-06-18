"""Tests for the workflows subcommand group."""

from __future__ import annotations

import json as _json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.commands.workflows import (
    _parse_front_matter,
    _parse_workflow_image_generator_flags,
    _parse_workflow_verifier_flags,
    _split_version_suffix,
)
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
    result = runner.invoke(app, ["workflows", "list", "--filter", "all", "--table"])
    assert result.exit_code == 0, result.output
    assert "skl_01" in result.output
    assert "skl_02" in result.output


@respx.mock
def test_workflows_list_defaults_to_one_compact_json_page(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_01", "name": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": "c1",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "list", "--filter", "all"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert result.output == (
        '{"items":[{"id":"skl_01","name":"a","current_version":1,'
        '"description":"","outcome":"","tags":[],"updated_at":null,'
        '"owner_user_id":null,"parent_template_id":null,'
        '"parent_template_version":null,"effective_role":null,'
        '"version_token":null,"archived_at":null}],"next_cursor":"c1"}\n'
    )
    params = dict(route.calls.last.request.url.params)
    assert params["filter"] == "all"
    assert params["limit"] == "25"
    assert "cursor" not in params


@respx.mock
def test_workflows_list_default_filter_is_all(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    # With no --filter, the CLI must match the server default ("all" = owned +
    # shared) so workflows shared via grants are not hidden by default.
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_01", "name": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": None,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "list"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    params = dict(route.calls.last.request.url.params)
    assert params["filter"] == "all"


@respx.mock
def test_workflows_list_all_follows_cursor(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
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
    result = runner.invoke(app, ["workflows", "list", "--filter", "all", "--all"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    assert '"skl_01"' in result.output and '"skl_02"' in result.output
    assert result.output.rstrip().endswith('"next_cursor":null}')


@respx.mock
def test_workflows_list_table_prints_next_page_hint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_01", "name": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": "c1",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["workflows", "list", "--filter", "all", "--tag", "ops", "--search", "triage", "--table"],
    )
    assert result.exit_code == 0, result.output
    assert "skl_01" in result.output
    assert "--cursor c1" in result.output
    assert "--filter all" in result.output
    assert "--tag ops" in result.output
    assert "--search triage" in result.output
    # Without --include-archived the hint must not suggest it.
    assert "--include-archived" not in result.output


@respx.mock
def test_workflows_list_next_page_hint_keeps_include_archived(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_01", "name": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": "c1",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["workflows", "list", "--filter", "all", "--include-archived", "--table"],
    )
    assert result.exit_code == 0, result.output
    assert "--cursor c1" in result.output
    # The next-page hint must carry --include-archived so following it does not
    # silently drop archived rows and the "Archived at" column mid-pagination.
    assert "--include-archived" in result.output


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
    assert result.output == (
        '{"items":[{"id":"w1","rank":1,"match_reason":"Matches chart critique.",'
        '"slug":"one","name":"one","description":"","outcome":"","tags":[]}],'
        '"query":"chart critique","limit":5,"search_mode":"llm"}\n'
    )


@respx.mock
def test_workflows_search_table_flag_renders_table(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/workflows/search").mock(
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
    result = runner.invoke(app, ["workflows", "search", "chart critique", "--table"])
    assert result.exit_code == 0, result.output
    assert "Workflow search" in result.output
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
    """Front-matter can provide the required workflow metadata."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text(
        "---\n"
        "name: hello\n"
        "description: Say hi to the world. Use when onboarding.\n"
        "outcome: Help new users complete onboarding.\n"
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
    assert sent["outcome"] == "Help new users complete onboarding."
    # No tags in the payload when front-matter omits them.
    assert "tags" not in sent
    assert "unknown" not in sent
    # Body round-trips with front matter intact so Goodeye can return the same
    # workflow body.
    assert sent["body"].startswith("---\n")
    assert "# Hello" in sent["body"]
    assert sent["expected_version_token"] is None


@respx.mock
def test_publish_surfaces_authoring_notes_to_stderr(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Save-time authoring notes are advisory and print to stderr, not stdout."""
    _setup_creds(monkeypatch, tmp_config_paths)
    markdown = (
        "---\n"
        "name: demo-with-notes\n"
        "description: A workflow that carries a demo README. Use when authoring.\n"
        "outcome: Help authors preview their demo.\n"
        "---\n"
        "# Body\n"
    )
    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_notes",
                "version": 1,
                "version_token": "tok-1",
                "name": "demo-with-notes",
                "authoring_notes": [
                    "An image referenced in demo/README.md was not found among the files.",
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", "-"], input=markdown)
    assert result.exit_code == 0, result.output
    assert "Saved" in result.stdout
    assert "An image referenced in demo/README.md was not found among the files." in result.stderr
    # Advisory notes must not land on stdout.
    assert "demo/README.md" not in result.stdout


@respx.mock
def test_publish_no_authoring_notes_is_silent(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    markdown = (
        "---\n"
        "name: quiet-demo\n"
        "description: A workflow with no demo notes. Use when authoring.\n"
        "outcome: Keep authoring quiet.\n"
        "---\n"
        "# Body\n"
    )
    respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_quiet",
                "version": 1,
                "version_token": "tok-1",
                "name": "quiet-demo",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", "-"], input=markdown)
    assert result.exit_code == 0, result.output
    assert "Saved" in result.stdout
    assert result.stderr == ""


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
def test_publish_accepts_body_only_stdin_with_metadata_flags(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    markdown = "# Workflow body\n\nUse this generated workflow body.\n"
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_body_only",
                "version": 1,
                "version_token": "tok-body-only",
                "name": "body-only-workflow",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            "-",
            "--name",
            "body-only-workflow",
            "--description",
            "Save generated markdown without embedding metadata.",
            "--outcome",
            "Reduce local workflow artifacts",
            "--tag",
            "agent",
            "--tag",
            "stdin",
        ],
        input=markdown,
    )

    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "body-only-workflow"
    assert sent["description"] == "Save generated markdown without embedding metadata."
    assert sent["outcome"] == "Reduce local workflow artifacts"
    assert sent["tags"] == ["agent", "stdin"]
    assert sent["body"] == markdown


@respx.mock
def test_publish_cli_metadata_flags_override_front_matter(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    markdown = (
        "---\n"
        "name: front-name\n"
        "description: Front-matter description.\n"
        "outcome: Front-matter outcome\n"
        "tags: [front, yaml]\n"
        "---\n"
        "# Body\n"
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_override",
                "version": 1,
                "version_token": "tok-override",
                "name": "flag-name",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            "-",
            "--name",
            "flag-name",
            "--description",
            "Flag description.",
            "--outcome",
            "Flag outcome",
            "--tag",
            "flag",
            "--tag",
            "override",
        ],
        input=markdown,
    )

    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "flag-name"
    assert sent["description"] == "Flag description."
    assert sent["outcome"] == "Flag outcome"
    assert sent["tags"] == ["flag", "override"]
    assert sent["body"] == markdown


@respx.mock
def test_publish_forwards_verifier_bindings(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "with-v.md"
    workflow_file.write_text(
        "---\n"
        "name: with-v\n"
        "description: Workflow with verifier bindings.\n"
        "outcome: Keep verifier bindings attached.\n"
        "---\n"
        "# Body\n",
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
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
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
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
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
def test_publish_sends_image_generator_bindings(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_v1",
                "version": 1,
                "version_token": "tok-v",
                "name": "hello",
            },
        )
    )
    gid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            str(workflow_file),
            "--image-generator",
            f"hero={gid}@2",
            "--image-generator",
            "banner=system:image-standard",
        ],
    )
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["image_generators"] == [
        {"name": "hero", "generator_ref": f"{gid}@2"},
        {"name": "banner", "generator_ref": "system:image-standard"},
    ]


@respx.mock
def test_publish_update_without_image_generator_flags_preserves_bindings(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
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
    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert "image_generators" not in sent


@respx.mock
def test_publish_clear_image_generators_sends_explicit_empty_list(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "skl_01",
                "version": 2,
                "version_token": "new-token",
                "name": "hello",
                "image_generators": [],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["workflows", "publish", str(workflow_file), "--clear-image-generators"],
    )
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["image_generators"] == []


@respx.mock
def test_publish_rejects_clear_and_set_image_generators_together(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "hello.md"
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            str(workflow_file),
            "--clear-image-generators",
            "--image-generator",
            "hero=system:image-standard",
        ],
    )
    # ValidationFailed bubbles up as an exception under CliRunner.
    assert isinstance(result.exception, ValidationFailed)
    assert "either --clear-image-generators or --image-generator" in str(result.exception)


@respx.mock
def test_publish_accepts_slug_alias_in_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Transitional: older authored files may still use `slug:` instead of `name:`."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "legacy.md"
    workflow_file.write_text(
        "---\n"
        "slug: my-workflow\n"
        "description: test desc\n"
        "outcome: Keep slug alias working.\n"
        "---\n"
        "Body\n",
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

    result = runner.invoke(
        app,
        ["workflows", "publish", "-"],
        input="---\nname: no-desc\n---\nBody\n",
    )

    assert result.exit_code != 0
    assert result.exception is not None
    assert "description" in str(result.exception).lower()


def test_publish_missing_outcome_errors(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "no-outcome.md"
    workflow_file.write_text("---\nname: no-outcome\ndescription: Has no outcome.\n---\nBody\n")
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])

    assert result.exit_code != 0
    assert result.exception is not None
    assert "outcome" in str(result.exception).lower()


def test_publish_malformed_front_matter_errors_cleanly(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_file = tmp_path / "broken.md"
    workflow_file.write_text("---\nname: broken\ntags: [a, b\n---\nBody\n")
    runner = CliRunner()

    result = runner.invoke(app, ["workflows", "publish", str(workflow_file)])

    # Malformed front-matter must surface as a clean validation error rather
    # than a raw YAMLError escaping to the unexpected-error backstop.
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "not valid YAML" in str(result.exception)


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
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
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
    workflow_file.write_text(
        "---\nname: hello\ndescription: Say hi.\noutcome: Greet users.\n---\n# Hello\n"
    )
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
    result = runner.invoke(
        app,
        [
            "workflows",
            "publish",
            str(workflow_file),
            "--outcome",
            "Required outcome from CLI flag",
        ],
    )
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["outcome"] == "Required outcome from CLI flag"
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
    assert "ermanently deleted" in result.output


@respx.mock
def test_workflows_archive_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/skl_01/archive").mock(
        return_value=httpx.Response(
            200, json={"workflow_id": "skl_01", "name": "skl_01", "archived": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "archive", "skl_01", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "Archived" in result.output
    assert "skl_01" in result.output


@respx.mock
def test_workflows_unarchive_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/skl_01/unarchive").mock(
        return_value=httpx.Response(
            200, json={"workflow_id": "skl_01", "name": "skl_01", "archived": False}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "unarchive", "skl_01"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "Unarchived" in result.output
    assert "skl_01" in result.output


@respx.mock
def test_workflows_delete_version_with_yes_flag(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.delete(f"{SERVER}/v1/workflows/skl_01/versions/3").mock(
        return_value=httpx.Response(
            200, json={"workflow_id": "skl_01", "version": 3, "deleted": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "delete-version", "skl_01", "3", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "ermanently deleted" in result.output
    assert "skl_01" in result.output


@respx.mock
def test_workflows_list_include_archived_sends_param_and_marks_rows(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "skl_live",
                        "name": "live-one",
                        "current_version": 2,
                        "description": "a live workflow",
                        "archived_at": None,
                    },
                    {
                        "id": "skl_gone",
                        "name": "gone-one",
                        "current_version": 1,
                        "description": "an archived workflow",
                        "archived_at": "2026-05-01T12:00:00Z",
                    },
                ],
                "next_cursor": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "list", "--filter", "mine", "--include-archived", "--table"]
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert dict(route.calls.last.request.url.params)["include_archived"] == "true"
    assert "Archived at" in result.output
    assert "2026-05-01" in result.output
    assert "skl_live" in result.output
    assert "skl_gone" in result.output


@respx.mock
def test_workflows_list_omits_archived_column_by_default(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "skl_live",
                        "name": "live-one",
                        "current_version": 1,
                        "description": "a live workflow",
                        "archived_at": None,
                    }
                ],
                "next_cursor": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "list", "--filter", "mine", "--table"])
    assert result.exit_code == 0, result.output
    assert "include_archived" not in dict(route.calls.last.request.url.params)
    assert "Archived at" not in result.output


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
    grants = runner.invoke(app, ["workflows", "grants", "wf_1", "--table"])
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
def test_workflows_grants_defaults_to_compact_json_envelope(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
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

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "grants", "wf_1"])

    assert result.exit_code == 0, result.output
    assert result.output == (
        '{"items":[{"grantee_type":"team","grantee_identifier":"@analytics",'
        '"role":"admin","granted_by":"owner","granted_at":"2026-04-24T00:00:00Z",'
        '"is_via_team":true,"includes_full_history":false,"shared_from_version":null}]}\n'
    )


@respx.mock
def test_grant_include_history_flag_passes_true(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    grant_route = respx.post(f"{SERVER}/v1/workflows/wf_2/grants").mock(
        return_value=httpx.Response(201, json={"workflow_id": "wf_2", "role": "view"})
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "grant", "wf_2", "alice@example.com", "view", "--include-history"]
    )

    assert result.exit_code == 0, result.output
    body = _json.loads(grant_route.calls.last.request.content.decode())
    assert body["include_history"] is True
    assert "full history" in result.output


@respx.mock
def test_grant_omitting_include_history_flag_passes_false(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    grant_route = respx.post(f"{SERVER}/v1/workflows/wf_2/grants").mock(
        return_value=httpx.Response(201, json={"workflow_id": "wf_2", "role": "view"})
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "grant", "wf_2", "alice@example.com", "view"])

    assert result.exit_code == 0, result.output
    body = _json.loads(grant_route.calls.last.request.content.decode())
    assert body["include_history"] is False
    assert "full history" not in result.output


@respx.mock
def test_grants_table_surfaces_history_scope(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_3/grants").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "grantee_type": "user",
                        "grantee_identifier": "alice@example.com",
                        "role": "view",
                        "granted_by": "owner",
                        "granted_at": "2026-05-01T00:00:00Z",
                        "is_via_team": False,
                        "includes_full_history": True,
                        "shared_from_version": None,
                    },
                    {
                        "grantee_type": "user",
                        "grantee_identifier": "bob@example.com",
                        "role": "edit",
                        "granted_by": "owner",
                        "granted_at": "2026-05-02T00:00:00Z",
                        "is_via_team": False,
                        "includes_full_history": False,
                        "shared_from_version": 4,
                    },
                ]
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "grants", "wf_3", "--table"])

    assert result.exit_code == 0, result.output
    assert "full" in result.output
    assert "from v4" in result.output


@respx.mock
def test_grants_json_includes_history_fields(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_3/grants").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "grantee_type": "user",
                        "grantee_identifier": "alice@example.com",
                        "role": "view",
                        "granted_by": "owner",
                        "granted_at": "2026-05-01T00:00:00Z",
                        "is_via_team": False,
                        "includes_full_history": True,
                        "shared_from_version": None,
                    },
                    {
                        "grantee_type": "user",
                        "grantee_identifier": "bob@example.com",
                        "role": "edit",
                        "granted_by": "owner",
                        "granted_at": "2026-05-02T00:00:00Z",
                        "is_via_team": False,
                        "includes_full_history": False,
                        "shared_from_version": 4,
                    },
                ]
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "grants", "wf_3"])

    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.output)
    alice = parsed["items"][0]
    bob = parsed["items"][1]
    assert alice["includes_full_history"] is True
    assert alice["shared_from_version"] is None
    assert bob["includes_full_history"] is False
    assert bob["shared_from_version"] == 4


@respx.mock
def test_workflows_transfer_ownership_command(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    inv_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/transfer-ownership").mock(
        return_value=httpx.Response(
            200,
            json={
                "invitation_id": inv_id,
                "kind": "workflow_ownership_transfer",
                "expires_at": "2026-02-01T00:00:00Z",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "transfer-ownership", "wf_1", "new@example.com"])

    assert result.exit_code == 0, result.output
    body = _json.loads(route.calls.last.request.content.decode())
    assert body["new_owner_user_id_or_email"] == "new@example.com"
    assert "transfer invited" in result.output
    assert inv_id in result.output


@respx.mock
def test_workflows_lineage_renders_not_a_fork(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_1/lineage").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "parent_template_id": None,
                "parent_template_version": None,
                "upstream_latest_version": None,
                "is_upstream_unpublished": None,
                "parent_source_status": None,
                "parent_permanently_deleted": False,
                "parent_template_archived_at": None,
                "parent_template_archive_reason": None,
                "parent_version_deprecated_at": None,
                "parent_version_deprecation_message": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "lineage", "wf_1"])
    assert result.exit_code == 0, result.output
    assert "Not a fork" in result.output


@respx.mock
def test_workflows_lineage_renders_clean_fork(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_1/lineage").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "parent_template_id": "tpl_1",
                "parent_template_version": 2,
                "upstream_latest_version": 3,
                "is_upstream_unpublished": False,
                "parent_source_status": "live",
                "parent_permanently_deleted": False,
                "parent_template_archived_at": None,
                "parent_template_archive_reason": None,
                "parent_version_deprecated_at": None,
                "parent_version_deprecation_message": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "lineage", "wf_1"])
    assert result.exit_code == 0, result.output
    assert "tpl_1" in result.output
    assert "archived" not in result.output.lower()
    assert "permanently deleted" not in result.output.lower()
    assert "deprecated" not in result.output.lower()


@respx.mock
def test_workflows_lineage_surfaces_archived_and_deprecated_parent(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_1/lineage").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "parent_template_id": "tpl_1",
                "parent_template_version": 2,
                "upstream_latest_version": 3,
                "is_upstream_unpublished": False,
                "parent_source_status": "archived",
                "parent_permanently_deleted": False,
                "parent_template_archived_at": "2026-04-01T00:00:00Z",
                "parent_template_archive_reason": "retired",
                "parent_version_deprecated_at": "2026-03-15T00:00:00Z",
                "parent_version_deprecation_message": "please move to v3",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "lineage", "wf_1"])
    assert result.exit_code == 0, result.output
    assert "archived" in result.output.lower()
    assert "deprecated" in result.output.lower()
    assert "please move to v3" in result.output


@respx.mock
def test_workflows_lineage_surfaces_permanently_deleted_parent(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    # A permanently-deleted parent severs the FK: the server returns
    # parent_template_id=None while still pinning the version the fork was
    # taken from and flagging parent_permanently_deleted=true. The CLI must
    # surface the deletion rather than mislabel the fork as "not a fork" or
    # print "template None".
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_1/lineage").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "parent_template_id": None,
                "parent_template_version": 2,
                "upstream_latest_version": None,
                "is_upstream_unpublished": None,
                "parent_source_status": "permanently_deleted",
                "parent_permanently_deleted": True,
                "parent_template_archived_at": None,
                "parent_template_archive_reason": None,
                "parent_version_deprecated_at": None,
                "parent_version_deprecation_message": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "lineage", "wf_1"])
    assert result.exit_code == 0, result.output
    assert "permanently deleted" in result.output.lower()
    assert "not a fork" not in result.output.lower()
    assert "template None" not in result.output
    assert "v2" in result.output


@respx.mock
def test_workflows_lineage_json_includes_all_fields(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/workflows/wf_1/lineage").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "parent_template_id": "tpl_1",
                "parent_template_version": 2,
                "upstream_latest_version": 3,
                "is_upstream_unpublished": False,
                "parent_source_status": "archived",
                "parent_permanently_deleted": False,
                "parent_template_archived_at": "2026-04-01T00:00:00Z",
                "parent_template_archive_reason": "retired",
                "parent_version_deprecated_at": "2026-03-15T00:00:00Z",
                "parent_version_deprecation_message": "please move to v3",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "lineage", "wf_1", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["parent_source_status"] == "archived"
    assert payload["parent_permanently_deleted"] is False
    assert payload["parent_template_archived_at"] == "2026-04-01T00:00:00Z"
    assert payload["parent_template_archive_reason"] == "retired"
    assert payload["parent_version_deprecated_at"] == "2026-03-15T00:00:00Z"
    assert payload["parent_version_deprecation_message"] == "please move to v3"


def test_parse_front_matter_extracts_unknown_nested_fields() -> None:
    source = "---\nname: foo\ndescription: bar\nunknown:\n  outcome: x\n---\nBody text\n"
    fm, body = _parse_front_matter(source)
    assert fm == {"name": "foo", "description": "bar", "unknown": {"outcome": "x"}}
    assert body == "Body text\n"


def test_parse_front_matter_without_front_matter_returns_source() -> None:
    fm, body = _parse_front_matter("just body\n")
    assert fm == {}
    assert body == "just body\n"


def test_parse_front_matter_malformed_yaml_raises_validation() -> None:
    # An unterminated flow sequence is invalid YAML; the parser reports a mark,
    # so the message should name the parse failure with a line/column rather
    # than letting a raw YAMLError escape to the top-level backstop.
    source = "---\nname: ok\ntags: [a, b\n---\nBody\n"
    with pytest.raises(ValidationFailed, match="not valid YAML") as excinfo:
        _parse_front_matter(source)
    assert "line" in str(excinfo.value)


def test_parse_front_matter_malformed_yaml_reports_file_line() -> None:
    # The bad indentation sits on file line 3 (the opening ``---`` is line 1).
    # The reported line must match the file the author sees, not the line index
    # within the stripped YAML block.
    source = "---\nname: ok\n  bad: indent\n---\nBody\n"
    with pytest.raises(ValidationFailed, match="not valid YAML") as excinfo:
        _parse_front_matter(source)
    assert "line 3" in str(excinfo.value)


def test_parse_workflow_verifier_flags_rejects_route_unsafe_names() -> None:
    with pytest.raises(ValidationFailed, match="name"):
        _parse_workflow_verifier_flags(["tone/check=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])


def test_parse_workflow_verifier_flags_rejects_deploy_incompatible_names() -> None:
    with pytest.raises(ValidationFailed, match="name"):
        _parse_workflow_verifier_flags(["tone_check=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])


def test_parse_workflow_verifier_flags_preserves_version_pin() -> None:
    vid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert _parse_workflow_verifier_flags([f"tone={vid}@2"]) == [
        {"name": "tone", "verifier_id": f"{vid}@2"}
    ]


def test_parse_workflow_image_generator_flags_parses_refs() -> None:
    gid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert _parse_workflow_image_generator_flags(
        [f"hero={gid}@2", "banner=system:image-standard"]
    ) == [
        {"name": "hero", "generator_ref": f"{gid}@2"},
        {"name": "banner", "generator_ref": "system:image-standard"},
    ]


def test_parse_workflow_image_generator_flags_rejects_missing_equals() -> None:
    with pytest.raises(ValidationFailed, match="name=generator_ref"):
        _parse_workflow_image_generator_flags(["system:image-standard"])


def test_parse_workflow_image_generator_flags_rejects_empty_ref() -> None:
    with pytest.raises(ValidationFailed, match="non-empty"):
        _parse_workflow_image_generator_flags(["hero="])


def test_parse_workflow_image_generator_flags_rejects_unsafe_names() -> None:
    with pytest.raises(ValidationFailed, match="name"):
        _parse_workflow_image_generator_flags(["hero/image=system:image-standard"])


def test_split_version_suffix_returns_no_version_when_absent() -> None:
    assert _split_version_suffix("my-workflow") == ("my-workflow", None)


def test_split_version_suffix_parses_numeric_suffix() -> None:
    assert _split_version_suffix("my-workflow@3") == ("my-workflow", 3)


def test_split_version_suffix_parses_v_prefixed_suffix() -> None:
    assert _split_version_suffix("my-workflow@v7") == ("my-workflow", 7)


def test_split_version_suffix_ignores_non_version_suffix() -> None:
    # ``@handle/slug`` looks like an identifier, not a version pin.
    assert _split_version_suffix("@alice/my-tpl") == ("@alice/my-tpl", None)


def test_split_version_suffix_strips_only_trailing_version() -> None:
    assert _split_version_suffix("@alice/my-tpl@5") == ("@alice/my-tpl", 5)


@pytest.mark.parametrize("value", ["my-workflow@abc", "my-workflow@1.2", "my-workflow@v1.2"])
def test_split_version_suffix_rejects_non_numeric_suffix(value: str) -> None:
    with pytest.raises(ValidationFailed, match="not a valid version number"):
        _split_version_suffix(value)


@pytest.mark.parametrize("value", ["my-workflow@abc", "my-workflow@1.2"])
def test_check_safety_rejects_non_numeric_suffix_with_clear_error(
    tmp_config_paths: ConfigPaths, monkeypatch, value: str
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "check-safety", value])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "not a valid version number" in str(result.exception)


@respx.mock
def test_workflows_check_safety_clean(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_uuid = "11111111-1111-1111-1111-111111111111"
    route = respx.post(f"{SERVER}/v1/workflows/{workflow_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200,
            json={
                "resource_type": "workflow",
                "resource_id": workflow_uuid,
                "resource_version": 2,
                "status": "clean",
                "block": {
                    "verifier_id": "22222222-2222-2222-2222-222222222222",
                    "verifier_version": 2,
                    "verifier_run_id": "33333333-3333-3333-3333-333333333333",
                    "verdict": "pass",
                    "reasoning": "No prompt-injection patterns detected.",
                },
                "advisory": {
                    "verifier_id": "44444444-4444-4444-4444-444444444444",
                    "verifier_version": 1,
                    "verifier_run_id": "55555555-5555-5555-5555-555555555555",
                    "verdict": "pass",
                    "reasoning": "Workflow scope is well bounded.",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "check-safety", workflow_uuid])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") == "Bearer good_live_EXAMPLE"
    assert "CLEAN" in result.output
    assert "pass" in result.output
    assert "No prompt-injection" in result.output
    assert "Workflow scope" in result.output


@respx.mock
def test_workflows_check_safety_strips_at_version_suffix(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_id = "my-workflow"
    route = respx.post(f"{SERVER}/v1/workflows/{workflow_id}/safety-check").mock(
        return_value=httpx.Response(
            200,
            json={
                "resource_type": "workflow",
                "resource_id": "11111111-1111-1111-1111-111111111111",
                "resource_version": 3,
                "status": "flagged",
                "block": {
                    "verifier_id": "22222222-2222-2222-2222-222222222222",
                    "verifier_version": 2,
                    "verifier_run_id": "33333333-3333-3333-3333-333333333333",
                    "verdict": "pass",
                    "reasoning": "ok",
                },
                "advisory": {
                    "verifier_id": "44444444-4444-4444-4444-444444444444",
                    "verifier_version": 1,
                    "verifier_run_id": "55555555-5555-5555-5555-555555555555",
                    "verdict": "fail",
                    "reasoning": "Scope is broad.",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "check-safety", f"{workflow_id}@3", "--json"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    params = dict(route.calls.last.request.url.params)
    assert params == {"version": "3"}
    payload = _json.loads(result.output)
    assert payload["status"] == "flagged"
    assert payload["advisory"]["verdict"] == "fail"


@respx.mock
def test_workflows_check_safety_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """Without an API key on disk or env, the command fails before any HTTP call."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    route = respx.post(f"{SERVER}/v1/workflows/whatever/safety-check").mock(
        return_value=httpx.Response(500, json={"error": "should not be called"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "check-safety", "whatever"])
    assert result.exit_code != 0
    assert route.call_count == 0


@respx.mock
def test_workflows_check_safety_version_flag_overrides_at_suffix(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """``--version 5`` wins over ``@3`` parsed from the identifier."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_id = "my-workflow"
    route = respx.post(f"{SERVER}/v1/workflows/{workflow_id}/safety-check").mock(
        return_value=httpx.Response(
            200,
            json={
                "resource_type": "workflow",
                "resource_id": "11111111-1111-1111-1111-111111111111",
                "resource_version": 5,
                "status": "clean",
                "block": {
                    "verifier_id": "22222222-2222-2222-2222-222222222222",
                    "verifier_version": 2,
                    "verifier_run_id": "33333333-3333-3333-3333-333333333333",
                    "verdict": "pass",
                    "reasoning": "ok",
                },
                "advisory": {
                    "verifier_id": "44444444-4444-4444-4444-444444444444",
                    "verifier_version": 1,
                    "verifier_run_id": "55555555-5555-5555-5555-555555555555",
                    "verdict": "pass",
                    "reasoning": "ok",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "check-safety", f"{workflow_id}@3", "--version", "5", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    params = dict(route.calls.last.request.url.params)
    assert params == {"version": "5"}


@respx.mock
def test_workflows_check_safety_escapes_rich_markup_in_reasoning(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Server-controlled ``reasoning`` text must not be parsed as Rich markup."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_uuid = "11111111-1111-1111-1111-111111111111"
    hostile = "Contains [bold red]injected[/bold red] markup tags."
    respx.post(f"{SERVER}/v1/workflows/{workflow_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200,
            json={
                "resource_type": "workflow",
                "resource_id": workflow_uuid,
                "resource_version": 1,
                "status": "clean",
                "block": {
                    "verifier_id": "22222222-2222-2222-2222-222222222222",
                    "verifier_version": 1,
                    "verdict": "pass",
                    "reasoning": hostile,
                },
                "advisory": {
                    "verifier_id": "44444444-4444-4444-4444-444444444444",
                    "verifier_version": 1,
                    "verdict": "pass",
                    "reasoning": "ok",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "check-safety", workflow_uuid])
    assert result.exit_code == 0, result.output
    # The literal hostile text must appear verbatim; if Rich parsed the
    # `[bold red]` tags as markup the substring would have been stripped.
    assert "[bold red]injected[/bold red]" in result.output


@respx.mock
def test_workflows_check_safety_handles_null_verifier_id_on_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """An ``error`` placeholder side from the server has null verifier_id and version."""
    _setup_creds(monkeypatch, tmp_config_paths)
    workflow_uuid = "11111111-1111-1111-1111-111111111111"
    respx.post(f"{SERVER}/v1/workflows/{workflow_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200,
            json={
                "resource_type": "workflow",
                "resource_id": workflow_uuid,
                "resource_version": 1,
                "status": "clean",
                "block": {
                    "verifier_id": "22222222-2222-2222-2222-222222222222",
                    "verifier_version": 1,
                    "verifier_run_id": "33333333-3333-3333-3333-333333333333",
                    "verdict": "pass",
                    "reasoning": "ok",
                },
                "advisory": {
                    "verifier_id": None,
                    "verifier_version": None,
                    "verifier_run_id": None,
                    "verdict": "error",
                    "reasoning": "advisory judge unavailable",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "check-safety", workflow_uuid])
    assert result.exit_code == 0, result.output
    assert "error" in result.output
    assert "advisory judge unavailable" in result.output
    assert "verifier_id=unknown" in result.output
