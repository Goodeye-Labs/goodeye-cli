"""Tests for login/signup/logout/whoami/design commands."""

from __future__ import annotations

import json

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, load_credentials, save_credentials

SERVER = "https://example.test"
DEVICE_URI = "https://api.workos.com/user_management/authorize/device"
TOKEN_URI = "https://api.workos.com/user_management/authenticate"


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


@respx.mock
def test_whoami_shows_server_when_overridden(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """Non-default GOODEYE_SERVER should surface in the output so callers
    can't mistake a staging instance for prod."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(200, json={"email": "e@x.com"}))
    runner = CliRunner()
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "Server:" in result.output
    assert SERVER in result.output
    assert "e@x.com" in result.output
    assert "User ID" not in result.output


@respx.mock
def test_whoami_shows_handle_when_present(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "email": "e@x.com",
                "handle": "alice",
                "handle_claimed_at": "2026-01-01T00:00:00+00:00",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "Handle: @alice" in result.output
    assert "e@x.com" in result.output


@respx.mock
def test_whoami_hides_server_when_default(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """On the built-in default server, omit the noisy server line."""
    from goodeye_cli.config import DEFAULT_SERVER

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)
    monkeypatch.setenv("GOODEYE_API_KEY", "good_live_EXAMPLE")
    respx.get(f"{DEFAULT_SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": "e@x.com"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "Server:" not in result.output
    assert "e@x.com" in result.output


@respx.mock
def test_whoami_json_includes_server_only_when_overridden(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(200, json={"email": "e@x.com"}))
    runner = CliRunner()
    result = runner.invoke(app, ["whoami", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload == {
        "email": "e@x.com",
        "handle": None,
        "handle_claimed_at": None,
        "server": SERVER,
    }


@respx.mock
def test_whoami_json_omits_server_when_default(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    from goodeye_cli.config import DEFAULT_SERVER

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)
    monkeypatch.setenv("GOODEYE_API_KEY", "good_live_EXAMPLE")
    respx.get(f"{DEFAULT_SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": "e@x.com", "handle": "bob"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["whoami", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload == {"email": "e@x.com", "handle": "bob", "handle_claimed_at": None}
    assert "server" not in payload


def test_whoami_without_credentials_errors(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code != 0


@respx.mock
def test_signup_end_to_end_writes_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    respx.post(f"{SERVER}/v1/signup").mock(
        return_value=httpx.Response(202, json={"status": "code_sent"})
    )
    respx.post(f"{SERVER}/v1/signup/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE"})
    )

    # Patch the magic_auth_flow's code prompt via monkeypatching Console.input.
    # Simpler: patch the module-level prompt_code default by using Typer CliRunner with input.
    runner = CliRunner()
    result = runner.invoke(app, ["signup", "--email", "e@x.com"], input="123456\n")
    assert result.exit_code == 0, result.output

    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE"
    assert creds["server"] == SERVER


@respx.mock
def test_login_with_email_writes_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    respx.post(f"{SERVER}/v1/login").mock(
        return_value=httpx.Response(202, json={"status": "code_sent"})
    )
    respx.post(f"{SERVER}/v1/login/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_L"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["login", "--email", "e@x.com"], input="654321\n")
    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None and creds["api_key"] == "good_live_EXAMPLE_L"


def test_logout_removes_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0, result.output
    assert load_credentials(tmp_config_paths) is None


def test_logout_with_no_credentials_is_friendly(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0, result.output
    assert "No local credentials" in result.output


@respx.mock
def test_design_renders_skill_md_and_references(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """The server returns ``{skill_md, references}``; the CLI should
    concatenate both into one pipe-ready markdown doc."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/design/workflow-prompt").mock(
        return_value=httpx.Response(
            200,
            json={
                "skill_md": "# Designer\n\nStart here.\n",
                "references": {
                    "references/02-kpi.md": "# KPI ranking\n\nDetails.",
                    "references/01-outcome.md": "# Outcome scoping\n\nDetails.",
                },
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["design"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "# Designer" in out
    assert "# Reference files" in out
    # References are emitted in sorted path order so output is deterministic.
    assert out.index("references/01-outcome.md") < out.index("references/02-kpi.md")
    assert "Outcome scoping" in out and "KPI ranking" in out
    # Raw JSON must not leak into the rendered output.
    assert "Unrecognized design payload" not in out
    assert '"skill_md"' not in out


@respx.mock
def test_design_legacy_prompt_key_still_renders(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """Legacy payload shape (``{"prompt": ...}``) remains supported."""
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/design/workflow-prompt").mock(
        return_value=httpx.Response(200, json={"prompt": "Hello designer."})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["design"])
    assert result.exit_code == 0, result.output
    assert "Hello designer." in result.output


@respx.mock
def test_design_json_flag(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/design/workflow-prompt").mock(
        return_value=httpx.Response(200, json={"skill_md": "# x", "references": {}})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["design", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload == {"skill_md": "# x", "references": {}}


def test_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "goodeye" in result.output
