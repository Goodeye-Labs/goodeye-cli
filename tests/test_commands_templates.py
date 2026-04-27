"""Tests for the templates subcommand group."""

from __future__ import annotations

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
def test_templates_get_wraps_body_with_agent_markers(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Stdout is wrapped so the calling agent knows to execute the body."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/templates/@h/example").mock(
        return_value=httpx.Response(200, text="# template body")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "get", "@h/example"])
    assert result.exit_code == 0, result.output
    assert "# template body" in result.output
    assert "# Goodeye workflow" in result.output
    assert "execute the instructions below" in result.output
    assert "# End of Goodeye workflow." in result.output


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
    # The previous "ephemeral / will be promoted on signup" prose is gone.
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
def test_templates_delete_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/templates/tpl_01").mock(
        return_value=httpx.Response(200, json={"template_id": "tpl_01", "deleted": True})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "delete", "tpl_01", "--reason", "stale"])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output
    assert "tpl_01" in result.output


@respx.mock
def test_templates_delete_idempotent_suffix(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/templates/tpl_01").mock(
        return_value=httpx.Response(
            200,
            json={"template_id": "tpl_01", "deleted": True, "idempotent": True},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "delete", "tpl_01"])
    assert result.exit_code == 0, result.output
    assert "idempotent" in result.output


@respx.mock
def test_templates_delete_forbidden_errors(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    from goodeye_cli.errors import Forbidden

    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/templates/tpl_01").mock(
        return_value=httpx.Response(403, json={"error": "forbidden", "message": "owner only"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "delete", "tpl_01"])
    assert result.exit_code != 0
    assert isinstance(result.exception, Forbidden)


@respx.mock
def test_templates_undelete_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/undelete").mock(
        return_value=httpx.Response(200, json={"template_id": "tpl_01", "deleted": False})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "undelete", "tpl_01"])
    assert result.exit_code == 0, result.output
    assert "Undeleted" in result.output
    assert "tpl_01" in result.output


@respx.mock
def test_templates_undelete_idempotent_suffix(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/tpl_01/undelete").mock(
        return_value=httpx.Response(
            200,
            json={"template_id": "tpl_01", "deleted": False, "idempotent": True},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "undelete", "tpl_01"])
    assert result.exit_code == 0, result.output
    assert "idempotent" in result.output


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
    respx.post(f"{SERVER}/v1/templates/tpl_01/transfer-ownership").mock(
        return_value=httpx.Response(
            200,
            json={
                "template_id": "tpl_01",
                "owner_user_id": "user_2",
                "transferred": True,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["templates", "transfer-ownership", "tpl_01", "new@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert "Transferred" in result.output
    assert "user_2" in result.output


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
    assert "already belongs to" in result.output


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
