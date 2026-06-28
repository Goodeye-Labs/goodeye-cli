"""Tests for the post-login shared-with-you banner.

After a successful interactive login the CLI prints a one-line nudge
when workflows are shared with the user or invitations are pending.
The banner is silent on non-TTY sessions and on any list-endpoint error.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths

SERVER = "https://example.test"
DEVICE_URI = "https://api.workos.com/user_management/authorize/device"
TOKEN_URI = "https://api.workos.com/user_management/authenticate"

_CLIENT_CONFIG_BODY = {
    "workos_client_id": "client_X",
    "workos_device_authorization_uri": DEVICE_URI,
    "workos_token_uri": TOKEN_URI,
}

_WORKFLOW_ITEMS = [
    {"id": "wf-01", "name": "alpha", "current_version": 1},
    {"id": "wf-02", "name": "beta", "current_version": 2},
]

_INVITATION_ITEMS = [
    {
        "id": "inv-01",
        "kind": "team_membership",
        "target_id": "t-01",
        "target_label": "@acme",
        "proposed_by_handle": "alice",
        "proposed_to_handle": "bob",
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
    },
]


def _env(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


def test_login_banner_interactive_shows_counts(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Interactive login prints shared-workflow and invitation counts when both are nonzero."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_banner1",
        ),
        patch("goodeye_cli.commands.login.save_client_config"),
        patch("goodeye_cli.commands.login._is_tty", return_value=True),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        respx.get(f"{SERVER}/v1/workflows").mock(
            return_value=httpx.Response(
                200, json={"items": _WORKFLOW_ITEMS, "next_cursor": None}
            )
        )
        respx.get(f"{SERVER}/v1/invitations").mock(
            return_value=httpx.Response(
                200, json={"items": _INVITATION_ITEMS, "next_cursor": None}
            )
        )

        runner = CliRunner()
        result = runner.invoke(app, ["login"])

    assert result.exit_code == 0, result.output
    # Normalize whitespace to handle Rich's line wrapping in the captured output.
    flat = " ".join(result.output.split())
    assert "shared with you" in flat
    assert "pending invitation" in flat
    assert "goodeye workflows list --filter shared-with-me" in flat
    assert "goodeye invitations list" in flat
    # Confirm the counts appear
    assert "2" in flat  # 2 workflows
    assert "1" in flat  # 1 invitation


def test_login_banner_no_tty_prints_nothing(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Non-TTY session produces no banner and does not call the list endpoints."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_banner2",
        ),
        patch("goodeye_cli.commands.login.save_client_config"),
        patch("goodeye_cli.commands.login._is_tty", return_value=False),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        wf_route = respx.get(f"{SERVER}/v1/workflows").mock(
            return_value=httpx.Response(
                200, json={"items": _WORKFLOW_ITEMS, "next_cursor": None}
            )
        )
        inv_route = respx.get(f"{SERVER}/v1/invitations").mock(
            return_value=httpx.Response(
                200, json={"items": _INVITATION_ITEMS, "next_cursor": None}
            )
        )

        runner = CliRunner()
        result = runner.invoke(app, ["login"])

    assert result.exit_code == 0, result.output
    assert "shared with you" not in result.output
    # List endpoints must not be called when not a TTY
    assert wf_route.call_count == 0
    assert inv_route.call_count == 0


def test_login_banner_api_error_prints_nothing_and_login_succeeds(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When a list endpoint returns an error the banner is suppressed and login exits 0."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_banner3",
        ),
        patch("goodeye_cli.commands.login.save_client_config"),
        patch("goodeye_cli.commands.login._is_tty", return_value=True),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        respx.get(f"{SERVER}/v1/workflows").mock(
            return_value=httpx.Response(
                500,
                json={"error": "server_error", "message": "Internal error."},
            )
        )

        runner = CliRunner()
        result = runner.invoke(app, ["login"])

    assert result.exit_code == 0, result.output
    assert "shared with you" not in result.output
    assert "Signed in" in result.output
