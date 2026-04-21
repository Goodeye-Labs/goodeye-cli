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
def test_whoami_prints_user(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"user_id": "usr_01", "email": "e@x.com"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "usr_01" in result.output
    assert "e@x.com" in result.output


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
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE", "user_id": "usr_01"})
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
        return_value=httpx.Response(
            200, json={"api_key": "good_live_EXAMPLE_L", "user_id": "usr_01"}
        )
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
def test_design_prints_prompt(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
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
        return_value=httpx.Response(200, json={"prompt": "Hello."})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["design", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload == {"prompt": "Hello."}


def test_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "goodeye" in result.output
