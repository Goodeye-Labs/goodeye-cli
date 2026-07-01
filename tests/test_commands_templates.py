"""Tests for the templates subcommand group."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


def _setup_no_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)


@respx.mock
def test_templates_list_defaults_to_compact_json_envelope(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "tpl_01",
                        "slug": "demo",
                        "name": "demo",
                        "handle": "h",
                        "owner_user_id": "user_1",
                        "latest_version": 2,
                        "outcome": "demo outcome",
                        "publishing_handle": "h",
                    }
                ],
                "next_cursor": "next",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["templates", "list", "--filter", "all"])

    assert result.exit_code == 0, result.output
    assert result.output == (
        '{"items":[{"id":"tpl_01","slug":"demo","name":"demo","handle":"h",'
        '"owner_user_id":"user_1","latest_version":2,"description":"",'
        '"outcome":"demo outcome","tags":[],"publishing_handle":"h",'
        '"safety_verification_status":"unverified",'
        '"design_checks":{"outcome":"not_checked","runnable":"not_checked",'
        '"well_formed":"not_checked","criteria":[],"guidance":null},'
        '"published_at":null}],"next_cursor":"next"}\n'
    )
    params = dict(route.calls.last.request.url.params)
    assert params["filter"] == "all"
    assert params["limit"] == "25"


@respx.mock
def test_templates_list_table_flag_renders_table(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "tpl_01",
                        "slug": "demo",
                        "name": "demo",
                        "handle": "h",
                        "owner_user_id": "user_1",
                        "latest_version": 2,
                        "outcome": "demo outcome",
                        "publishing_handle": "h",
                    }
                ],
                "next_cursor": None,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["templates", "list", "--table"])

    assert result.exit_code == 0, result.output
    assert "@h/demo" in result.output


@respx.mock
def test_templates_search_allows_anonymous(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    # Search is a public read like `list` and `get`: an un-signed-up user must
    # not be bounced by a client-only gate, and no Authorization header should
    # be sent when there are no credentials on file.
    _setup_no_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [],
                "query": "chart critique",
                "limit": 5,
                "search_mode": "llm",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "search", "chart critique"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "authorization" not in {k.lower() for k in route.calls[0].request.headers}


@respx.mock
def test_templates_search_posts_to_search_endpoint(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "tpl_01",
                        "slug": "template-search",
                        "name": "template-search",
                        "handle": "example",
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
    result = runner.invoke(app, ["templates", "search", "chart critique"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.method == "POST"
    assert req.url.path == "/v1/templates/search"
    assert json.loads(req.content.decode())["query"] == "chart critique"
    assert result.output == (
        '{"items":[{"id":"tpl_01","rank":1,"match_reason":"Matches chart critique.",'
        '"slug":"template-search","name":"template-search","handle":"example",'
        '"description":"","outcome":"","tags":[]}],"query":"chart critique",'
        '"limit":5,"search_mode":"llm"}\n'
    )


@respx.mock
def test_templates_search_table_flag_renders_table(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "tpl_01",
                        "slug": "template-search",
                        "name": "template-search",
                        "handle": "example",
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
    result = runner.invoke(app, ["templates", "search", "chart critique", "--table"])
    assert result.exit_code == 0, result.output
    assert "Template search" in result.output
    assert "Matches chart critique" in result.output


def test_templates_search_rejects_json_and_table(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "search", "x", "--json", "--table"])
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


@respx.mock
def test_templates_get_stdout_renders_server_body(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """CLI outputs the server body verbatim to stdout without adding framing markers."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(
            200, text="This is a Goodeye workflow you are about to run\n\n# template body"
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "# template body" in result.output
    assert "# Goodeye workflow - execute the instructions below" not in result.stdout
    assert "# End of Goodeye workflow." not in result.stdout
    assert result.stdout.startswith("This is a Goodeye workflow you are about to run")


@respx.mock
def test_templates_get_stdout_has_no_execute_markers(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """CLI prints the server markdown body without adding hardcoded framing markers."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(
            200, text="This is a Goodeye workflow you are about to run\n\n# Body\nDo the thing."
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "# Goodeye workflow - execute the instructions below" not in result.stdout
    assert "# End of Goodeye workflow." not in result.stdout
    assert result.stdout.startswith("This is a Goodeye workflow you are about to run")


@respx.mock
def test_templates_get_json_skips_wrappers(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "tpl_01",
                "slug": "example",
                "name": "example",
                "handle": "h",
                "owner_user_id": "usr_01",
                "version": 1,
                "body": "raw body",
                "description": "an example",
                "outcome": "outcome",
                "tags": [],
                "publishing_handle": "h",
                "safety_verification_status": "unverified",
                "published_at": "2026-01-01T00:00:00+00:00",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example", "--json"])
    assert result.exit_code == 0, result.output
    assert '"slug": "example"' in result.output
    assert "# Goodeye workflow" not in result.output
    assert "# End of Goodeye workflow." not in result.output


@respx.mock
def test_templates_get_markdown_default_surfaces_run_guidance(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The default (markdown) fetch path streams the server's run_guidance in-band."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(
            200,
            text="Run this template on behalf of the user, then report back.\n\n# template body",
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "Run this template on behalf of the user, then report back." in result.stdout


@respx.mock
def test_templates_get_json_includes_run_guidance(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The --json fetch path must not silently drop run_guidance."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "tpl_01",
                "slug": "example",
                "name": "example",
                "handle": "h",
                "owner_user_id": "usr_01",
                "version": 1,
                "body": "raw body",
                "description": "an example",
                "outcome": "outcome",
                "tags": [],
                "publishing_handle": "h",
                "safety_verification_status": "unverified",
                "published_at": "2026-01-01T00:00:00+00:00",
                "run_guidance": "Run this template on behalf of the user, then report back.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example", "--json"])
    assert result.exit_code == 0, result.output
    assert '"run_guidance": "Run this template on behalf of the user, then report back."' in (
        result.output
    )


@respx.mock
def test_templates_get_output_writes_body_without_runbook_framing(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """`--output` writes the template body, not the runbook with run-guidance framing.

    Stdout streams the server markdown verbatim (run-guidance directive
    in-band), but the file written by --output carries just the body, fetched
    as the structured record so the runbook framing is omitted.
    """
    _setup_no_creds(monkeypatch, tmp_config_paths)
    body = "# Example template\n\nDo the thing.\n"
    route = respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "tpl_01",
                "slug": "example",
                "name": "example",
                "handle": "h",
                "owner_user_id": "usr_01",
                "version": 1,
                "body": body,
                "description": "an example",
                "outcome": "outcome",
                "tags": [],
                "publishing_handle": "h",
                "safety_verification_status": "unverified",
                "published_at": "2026-01-01T00:00:00+00:00",
            },
        )
    )
    out_path = tmp_path / "example.md"
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example", "-o", str(out_path)])
    assert result.exit_code == 0, result.output
    # --output fetches the structured record (JSON), not the runbook markdown.
    assert route.calls.last.request.headers["accept"] == "application/json"
    written = out_path.read_text(encoding="utf-8")
    assert written == body
    # The run-guidance directive (added on the markdown route) must not leak in.
    assert "run it on the user's behalf" not in written
    assert "you are about to run on the user's behalf" not in written


@respx.mock
def test_templates_get_file_writes_raw_bytes_to_output(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    """`templates get-file` writes the raw bytes from the server to the output path."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00fake-demo-asset"
    route = respx.get(f"{SERVER}/v1/templates/@h/example/files").mock(
        return_value=httpx.Response(
            200,
            content=png_bytes,
            headers={"content-type": "image/png"},
        )
    )
    out_path = tmp_path / "downloads" / "preview.png"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "get-file",
            "@h/example",
            "demo/preview.png",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == png_bytes
    params = dict(route.calls.last.request.url.params)
    assert params["path"] == "demo/preview.png"
    assert params["format"] == "raw"
    # The confirmation prints to stderr; the raw bytes must NOT reach stdout.
    assert "fake-demo-asset" not in result.output


@respx.mock
def test_templates_get_file_forwards_sha256(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    """`templates get-file --sha256` content-addresses the fetch via the query param."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    sha = "a" * 64
    route = respx.get(f"{SERVER}/v1/templates/@h/example/files").mock(
        return_value=httpx.Response(200, content=b"bytes", headers={"content-type": "image/png"})
    )
    out_path = tmp_path / "preview.png"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "get-file",
            "@h/example",
            "demo/preview.png",
            "--output",
            str(out_path),
            "--sha256",
            sha,
        ],
    )
    assert result.exit_code == 0, result.output
    params = dict(route.calls.last.request.url.params)
    assert params["sha256"] == sha
    assert params["format"] == "raw"


@respx.mock
def test_templates_get_file_does_not_print_bytes_to_stdout(
    tmp_config_paths: ConfigPaths, monkeypatch, tmp_path: Path
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example/files").mock(
        return_value=httpx.Response(200, content=b"raw-bytes-here")
    )
    out_path = tmp_path / "asset.bin"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["templates", "get-file", "@h/example", "demo/asset.bin", "-o", str(out_path)],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert out_path.read_bytes() == b"raw-bytes-here"


@respx.mock
def test_templates_fork_authed_prints_workflow_id(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_01",
                "slug": "example",
                "name": "example",
                "parent_template_id": "tpl_01",
                "parent_template_version": 1,
                "version_token": "vt_01",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "Forked" in result.output
    assert "wf_01" in result.output
    # The previous "ephemeral / will be promoted on registration" prose is gone.
    assert "ephemeral" not in result.output.lower()
    assert "promoted" not in result.output.lower()


def test_templates_fork_anonymous_errors_with_auth_required(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Without an API key the CLI now refuses to fork (no more anonymous path)."""
    from goodeye_cli.errors import AuthRequired

    _setup_no_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "@h/example"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)
    assert result.exception.slug == "auth_required"


@respx.mock
def test_templates_get_follows_redirect_and_notes_on_stderr(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When the server redirects @old/slug to @new/slug, the CLI follows and notes it."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@old/example").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{SERVER}/v1/templates/@new/example"}
        )
    )
    respx.get(f"{SERVER}/v1/templates/@new/example").mock(
        return_value=httpx.Response(200, text="# template body via redirect")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@old/example"])
    assert result.exit_code == 0, result.output
    # The CLI runner combines stdout+stderr; both messages land in result.output.
    assert "# template body via redirect" in result.output
    assert "redirected to @new/example" in result.output


@respx.mock
def test_templates_fork_redirect_metadata_emits_stderr_note(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_01",
                "slug": "example",
                "name": "example",
                "parent_template_id": "tpl_01",
                "parent_template_version": 1,
                "version_token": "vt_01",
                "redirected": True,
                "redirected_from_handle": "@old/example",
                "redirected_to_handle": "@new/example",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "@old/example"])
    assert result.exit_code == 0, result.output
    assert "Forked" in result.output
    assert "redirected to @new/example" in result.output


@respx.mock
def test_templates_fork_deprecation_warning_emits_stderr_note(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A fork against a deprecated template version surfaces the warning on stderr."""
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_01",
                "slug": "example",
                "name": "example",
                "parent_template_id": "tpl_01",
                "parent_template_version": 1,
                "version_token": "vt_01",
                "deprecation": {
                    "deprecated_at": "2026-04-25T12:00:00+00:00",
                    "message": "known broken",
                },
                "deprecation_warning": "Forking a deprecated version: known broken",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "Forked" in result.stdout
    assert "wf_01" in result.stdout
    assert "Forking a deprecated version: known broken" in result.stderr


@respx.mock
def test_templates_fork_next_step_emits_stderr_note(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """The fork response's agent-guidance next_step prints to stderr, not stdout."""
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_01",
                "slug": "example",
                "name": "example",
                "parent_template_id": "tpl_01",
                "parent_template_version": 1,
                "version_token": "vt_01",
                "next_step": "Edit the fork and republish it as your own workflow.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "Forked" in result.stdout
    assert "Edit the fork and republish it as your own workflow." in result.stderr
    assert "Edit the fork and republish it as your own workflow." not in result.stdout


@respx.mock
def test_templates_fork_no_next_step_is_silent(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """An older server that omits next_step prints nothing extra."""
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_01",
                "slug": "example",
                "name": "example",
                "parent_template_id": "tpl_01",
                "parent_template_version": 1,
                "version_token": "vt_01",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "Forked" in result.stdout
    assert result.stderr == ""


@respx.mock
def test_templates_archive_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/tpl_01/archive").mock(
        return_value=httpx.Response(
            200, json={"template_id": "tpl_01", "slug": "demo", "archived": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "archive", "tpl_01", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "Archived" in result.output
    assert "tpl_01" in result.output


@respx.mock
def test_templates_archive_with_reason(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/tpl_01/archive").mock(
        return_value=httpx.Response(
            200, json={"template_id": "tpl_01", "slug": "demo", "archived": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "archive", "tpl_01", "--reason", "stale", "--yes"])
    assert result.exit_code == 0, result.output
    import json as _json

    body = _json.loads(route.calls.last.request.content.decode())
    assert body.get("archive_reason") == "stale"


@respx.mock
def test_templates_unarchive_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/tpl_01/unarchive").mock(
        return_value=httpx.Response(
            200, json={"template_id": "tpl_01", "slug": "demo", "archived": False}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "unarchive", "tpl_01"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "Unarchived" in result.output
    assert "tpl_01" in result.output


@respx.mock
def test_templates_delete_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/templates/tpl_01").mock(
        return_value=httpx.Response(
            200, json={"template_id": "tpl_01", "slug": "demo", "deleted": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "delete", "tpl_01", "--yes"])
    assert result.exit_code == 0, result.output
    assert "ermanently deleted" in result.output
    assert "tpl_01" in result.output


@respx.mock
def test_templates_delete_forbidden_errors(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    from goodeye_cli.errors import Forbidden

    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/templates/tpl_01").mock(
        return_value=httpx.Response(403, json={"error": "forbidden", "message": "owner only"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "delete", "tpl_01", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, Forbidden)


@respx.mock
def test_templates_delete_version_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.delete(f"{SERVER}/v1/templates/tpl_01/versions/2/permanent").mock(
        return_value=httpx.Response(
            200, json={"template_id": "tpl_01", "version": 2, "deleted": True}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "delete-version", "tpl_01", "2", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "ermanently deleted" in result.output
    assert "tpl_01" in result.output


@respx.mock
def test_templates_deprecate_version_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/versions/2/deprecate").mock(
        return_value=httpx.Response(
            200,
            json={
                "template_id": "tpl_01",
                "version": 2,
                "deprecated_at": "2026-04-25T12:00:00+00:00",
                "deprecation_message": "use v3 instead",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "deprecate-version",
            "tpl_01",
            "2",
            "--message",
            "use v3 instead",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Deprecated" in result.output
    assert "v2" in result.output
    assert "use v3 instead" in result.output


def test_templates_deprecate_version_requires_message(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "deprecate-version", "tpl_01", "2"])
    assert result.exit_code != 0


@respx.mock
def test_templates_deprecate_version_validation_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    from goodeye_cli.errors import ValidationFailed

    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/versions/2/deprecate").mock(
        return_value=httpx.Response(
            400,
            json={"error": "validation_error", "message": "message must not be empty"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["templates", "deprecate-version", "tpl_01", "2", "--message", "  "],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)


@respx.mock
def test_templates_transfer_ownership_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    inv_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    respx.post(f"{SERVER}/v1/templates/tpl_01/transfer-ownership").mock(
        return_value=httpx.Response(
            200,
            json={
                "invitation_id": inv_id,
                "kind": "template_ownership_transfer",
                "expires_at": "2026-02-01T00:00:00Z",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["templates", "transfer-ownership", "tpl_01", "new@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert "transfer invited" in result.output
    assert inv_id in result.output


@respx.mock
def test_templates_transfer_ownership_accepts_handle(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/tpl_01/transfer-ownership").mock(
        return_value=httpx.Response(
            200,
            json={
                "invitation_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "kind": "template_ownership_transfer",
                "expires_at": "2026-02-01T00:00:00Z",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "transfer-ownership", "tpl_01", "newowner"])

    assert result.exit_code == 0, result.output
    import json as _json

    body = _json.loads(route.calls.last.request.content.decode())
    assert body["new_owner_user_id_or_email"] == "newowner"


@respx.mock
def test_templates_transfer_ownership_already_owned(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/transfer-ownership").mock(
        return_value=httpx.Response(
            200,
            json={
                "template_id": "tpl_01",
                "owner_user_id": "user_2",
                "transferred": False,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "transfer-ownership", "tpl_01", "new@example.com"])
    assert result.exit_code == 0, result.output
    assert "No-op" in result.output


@respx.mock
def test_templates_transfer_ownership_slug_clash(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    from goodeye_cli.errors import Conflict

    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/transfer-ownership").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "conflict",
                "message": "new owner already has a template with this slug",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "transfer-ownership", "tpl_01", "new@example.com"])
    assert result.exit_code != 0
    assert isinstance(result.exception, Conflict)


@respx.mock
def test_templates_transfer_ownership_team_target_rejected(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    from goodeye_cli.errors import ValidationFailed

    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/transfer-ownership").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_grantee_type",
                "message": "templates cannot be owned by a team",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "transfer-ownership", "tpl_01", "@some-team"])
    assert result.exit_code != 0
    # Slug is mapped via the default catch-all ServerError because the slug
    # doesn't appear in errors._SLUG_MAP; either way, the CLI exits non-zero.
    assert result.exception is not None
    # And we can confirm the slug we got back from the server.
    assert getattr(result.exception, "slug", None) == "invalid_grantee_type"
    # Reference unused symbol so the import doesn't go stale.
    assert ValidationFailed is not None


@respx.mock
def test_fork_prints_auto_deployed_verifiers(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "11111111-1111-1111-1111-111111111111",
                "slug": "sample",
                "name": "sample",
                "parent_template_id": "22222222-2222-2222-2222-222222222222",
                "parent_template_version": 1,
                "version_token": None,
                "redirected": False,
                "deprecation_warning": None,
                "verifiers": [
                    {"name": "tone", "verifier_id": "33333333-3333-3333-3333-333333333333"},
                    {"name": "factual", "verifier_id": "44444444-4444-4444-4444-444444444444"},
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "sample"])
    assert result.exit_code == 0, result.output
    assert "tone" in result.stdout
    assert "factual" in result.stdout
    assert "33333333-3333-3333-3333-333333333333" in result.stdout


@respx.mock
def test_fork_with_no_verifiers_omits_verifier_section(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "11111111-1111-1111-1111-111111111111",
                "slug": "sample",
                "name": "sample",
                "parent_template_id": "22222222-2222-2222-2222-222222222222",
                "parent_template_version": 1,
                "version_token": None,
                "redirected": False,
                "deprecation_warning": None,
                "verifiers": [],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "sample"])
    assert result.exit_code == 0, result.output
    assert "verifier" not in result.stdout.lower()


@respx.mock
def test_templates_publish_clean_shows_verified(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_01",
                "version": 1,
                "publishing_handle": "h",
                "safety_verification": {
                    "status": "clean",
                    "advisory_run_id": "11111111-1111-1111-1111-111111111111",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert "Published" in result.output
    assert "verified" in result.output


@respx.mock
def test_templates_publish_renders_authoring_checks(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_01",
                "version": 1,
                "publishing_handle": "h",
                "safety_verification": {"status": "clean"},
                "design_checks": {
                    "outcome": "met",
                    "runnable": "not_met",
                    "well_formed": "not_checked",
                    "criteria": [
                        {"criterion": "workflow-io-contract", "passed": False, "reason": "no io"}
                    ],
                    "guidance": "Run `goodeye workflows audit` to improve the authoring checks.",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert "Checks:" in result.output
    assert "Outcome" in result.output
    assert "not met" in result.output
    assert "not checked" in result.output
    # A failed criterion surfaces the audit guidance.
    assert "audit" in result.output


@respx.mock
def test_templates_publish_shows_verifier_exposure_notice(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    notice = "Publishing makes this template's verifier definitions public."
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_notice",
                "version": 1,
                "publishing_handle": "h",
                "safety_verification": {"status": "clean"},
                "verifier_exposure_notice": notice,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert notice in result.output


@respx.mock
def test_templates_publish_flagged_shows_warning_and_reasoning(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_02",
                "version": 1,
                "publishing_handle": "h",
                "safety_verification": {
                    "status": "flagged",
                    "advisory_run_id": "22222222-2222-2222-2222-222222222222",
                    "advisory_reasoning": "Broad scope detected.",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert "Published" in result.output
    assert "advisory concerns flagged" in result.output
    assert "Broad scope detected." in result.output


@respx.mock
def test_templates_publish_no_safety_block_is_silent(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_03",
                "version": 1,
                "publishing_handle": "h",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert "Published" in result.output
    assert "safety" not in result.output.lower()


@respx.mock
def test_templates_publish_surfaces_authoring_notes_to_stderr(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_notes",
                "version": 1,
                "publishing_handle": "h",
                "authoring_notes": [
                    "A video link in demo/README.md points to a host that will not embed.",
                    "An image referenced in demo/README.md was not found among the files.",
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert "Published" in result.stdout
    assert "A video link in demo/README.md points to a host that will not embed." in result.stderr
    assert "An image referenced in demo/README.md was not found among the files." in result.stderr
    # Notes are advisory and must not pollute stdout.
    assert "demo/README.md" not in result.stdout


@respx.mock
def test_templates_publish_no_authoring_notes_is_silent(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            201,
            json={
                "template_id": "tpl_quiet",
                "version": 1,
                "publishing_handle": "h",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 0, result.output
    assert "Published" in result.stdout
    assert result.stderr == ""


@respx.mock
def test_templates_publish_blocked_exits_2(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            422,
            json={
                "error": "safety_verification_failed",
                "verifier_run_id": "11111111-1111-1111-1111-111111111111",
                "reasoning": "Template contains instruction override patterns.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 2
    assert "safety_verification_failed" in result.output
    assert "instruction override" in result.output


@respx.mock
def test_templates_publish_verifier_unavailable_exits_3(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            503,
            json={
                "error": "safety_verification_unavailable",
                "message": "Safety verifier runtime timed out.",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "publish", "my-workflow"])
    assert result.exit_code == 3
    assert "safety_verification_unavailable" in result.output


@respx.mock
def test_templates_list_table_shows_safety_column(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "tpl_01",
                        "slug": "clean-workflow",
                        "name": "clean-workflow",
                        "handle": "h",
                        "owner_user_id": "user_1",
                        "latest_version": 1,
                        "outcome": "outcome",
                        "publishing_handle": "h",
                        "safety_verification_status": "clean",
                    },
                    {
                        "id": "tpl_02",
                        "slug": "flagged-workflow",
                        "name": "flagged-workflow",
                        "handle": "h",
                        "owner_user_id": "user_1",
                        "latest_version": 1,
                        "outcome": "outcome",
                        "publishing_handle": "h",
                        "safety_verification_status": "flagged",
                    },
                ],
                "next_cursor": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "Safety" in result.output
    assert "verified" in result.output
    assert "advisory" in result.output


def _safety_check_payload(*, status: str, advisory_verdict: str) -> dict:
    return {
        "resource_type": "template",
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "resource_version": 4,
        "status": status,
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
            "verdict": advisory_verdict,
            "reasoning": (
                "Workflow scope is well bounded."
                if advisory_verdict == "pass"
                else "Workflow scope is too broad."
            ),
        },
    }


@respx.mock
def test_templates_check_safety_authenticated_sends_authorization(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    template_uuid = "11111111-1111-1111-1111-111111111111"
    route = respx.post(f"{SERVER}/v1/templates/{template_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200, json=_safety_check_payload(status="clean", advisory_verdict="pass")
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "check-safety", template_uuid])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") == "Bearer good_live_EXAMPLE"
    assert "CLEAN" in result.output
    assert "Workflow scope is well bounded." in result.output


@respx.mock
def test_templates_check_safety_renders_truncation_note(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A truncated re-check surfaces the affected fields in human-readable output."""
    _setup_creds(monkeypatch, tmp_config_paths)
    template_uuid = "11111111-1111-1111-1111-111111111111"
    payload = _safety_check_payload(status="clean", advisory_verdict="pass")
    payload["truncated"] = True
    payload["truncated_fields"] = ["body"]
    respx.post(f"{SERVER}/v1/templates/{template_uuid}/safety-check").mock(
        return_value=httpx.Response(200, json=payload),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "check-safety", template_uuid])
    assert result.exit_code == 0, result.output
    assert "body" in result.output
    assert "truncated" in result.output.lower()


@respx.mock
def test_templates_check_safety_anonymous_omits_authorization(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """``--anonymous`` drops the Authorization header even when an API key is on disk."""
    _setup_creds(monkeypatch, tmp_config_paths)
    template_uuid = "11111111-1111-1111-1111-111111111111"
    route = respx.post(f"{SERVER}/v1/templates/{template_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200, json=_safety_check_payload(status="flagged", advisory_verdict="fail")
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["templates", "check-safety", template_uuid, "--anonymous", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") is None
    payload = json.loads(result.output)
    assert payload["status"] == "flagged"
    assert payload["advisory"]["verdict"] == "fail"


@respx.mock
def test_templates_check_safety_anonymous_works_without_credentials(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """``--anonymous`` succeeds with no credentials configured."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    template_uuid = "11111111-1111-1111-1111-111111111111"
    route = respx.post(f"{SERVER}/v1/templates/{template_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200, json=_safety_check_payload(status="clean", advisory_verdict="pass")
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "check-safety", template_uuid, "--anonymous"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") is None


@respx.mock
def test_templates_check_safety_forwards_version_from_at_suffix(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    template_uuid = "11111111-1111-1111-1111-111111111111"
    route = respx.post(f"{SERVER}/v1/templates/{template_uuid}/safety-check").mock(
        return_value=httpx.Response(
            200, json=_safety_check_payload(status="clean", advisory_verdict="pass")
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "check-safety", f"{template_uuid}@v7"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    params = dict(route.calls.last.request.url.params)
    assert params == {"version": "7"}
