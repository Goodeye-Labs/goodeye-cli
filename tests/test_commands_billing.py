"""Tests for the `goodeye billing ...` subcommand group."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import AuthRequired, BillingNotEnabled

SERVER = "https://example.test"

_PORTAL_BODY = {"portal_url": "https://billing.stripe.com/p/session/EXAMPLE"}


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


@respx.mock
def test_billing_portal_prints_url_and_opens_browser(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    route = respx.post(f"{SERVER}/v1/billing/portal").mock(
        return_value=httpx.Response(200, json=_PORTAL_BODY)
    )
    opened: list[str] = []
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", lambda url: opened.append(url))
    runner = CliRunner()
    result = runner.invoke(app, ["billing", "portal"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert _PORTAL_BODY["portal_url"] in result.output
    assert opened == [_PORTAL_BODY["portal_url"]]


@respx.mock
def test_billing_portal_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/portal").mock(
        return_value=httpx.Response(200, json=_PORTAL_BODY)
    )
    monkeypatch.setattr("goodeye_cli.commands.billing._open_url", lambda url: None)
    runner = CliRunner()
    result = runner.invoke(app, ["billing", "portal", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data == {"portal_url": _PORTAL_BODY["portal_url"]}


def test_billing_portal_no_credentials(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _env(monkeypatch, tmp_config_paths, api_key=None)
    runner = CliRunner()
    result = runner.invoke(app, ["billing", "portal"])
    assert result.exit_code != 0
    assert isinstance(result.exception, AuthRequired)


@respx.mock
def test_billing_portal_surfaces_billing_not_enabled(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _env(monkeypatch, tmp_config_paths, api_key="good_live_EXAMPLE_key")
    respx.post(f"{SERVER}/v1/billing/portal").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "billing_not_enabled",
                "message": "self-service subscription billing is not enabled",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["billing", "portal"])
    assert result.exit_code != 0
    assert isinstance(result.exception, BillingNotEnabled)


# ----- module-level guards -----


def test_billing_commands_no_em_dash() -> None:
    """The brand constraint forbids em dashes anywhere in source."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "billing.py"
    )
    assert "—" not in src.read_text()


def test_billing_command_appears_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "billing" in result.output


# Avoid "imported but unused" if pytest is reordered.
_ = pytest
