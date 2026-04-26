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
def test_templates_get_json_skips_wrappers(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
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
