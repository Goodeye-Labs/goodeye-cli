"""Tests for the `goodeye me ...` subcommand group."""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths

SERVER = "https://example.test"


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


@respx.mock
def test_claim_handle_success(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.patch(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(
            200, json={"handle": "alice", "claimed_at": "2026-04-24T21:45:00+00:00"}
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "claim-handle", "alice"])
    assert result.exit_code == 0, result.output
    assert "alice" in result.output


@respx.mock
def test_claim_handle_reserved_errors_out(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE")
    respx.patch(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(
            409,
            json={"error": "handle_reserved", "message": "handle is reserved"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["me", "claim-handle", "anthropic"])
    assert result.exit_code != 0


def test_claim_handle_without_credentials_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["me", "claim-handle", "alice"])
    assert result.exit_code != 0
