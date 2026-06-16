"""Tests for --referral-code on register-verify, login (device-code), and login-verify.

Verifies:
- Successful redeem after each auth completion command.
- Flag omitted means redeem is NOT called.
- Non-fatal failure: command exits 0, credentials are saved, and the error
  message is printed.
- Initiation hint lines (register / login --email) mention --referral-code.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, load_credentials

SERVER = "https://example.test"
DEVICE_URI = "https://api.workos.com/user_management/authorize/device"
TOKEN_URI = "https://api.workos.com/user_management/authenticate"

_REDEEM_BODY = {
    "status": "pending",
    "credits_granted_usd": "5.00",
    "expires_at": "2026-09-15T00:00:00+00:00",
    "referrer_handle": "alice",
}

_CLIENT_CONFIG_BODY = {
    "workos_client_id": "client_X",
    "workos_device_authorization_uri": DEVICE_URI,
    "workos_token_uri": TOKEN_URI,
}


def _env(monkeypatch, tmp_config_paths: ConfigPaths, *, api_key: str | None = None) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    if api_key is not None:
        monkeypatch.setenv("GOODEYE_API_KEY", api_key)
    else:
        monkeypatch.delenv("GOODEYE_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# register-verify --referral-code
# ---------------------------------------------------------------------------


@respx.mock
def test_register_verify_with_referral_code_success(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """register-verify redeems the code and prints success; credentials are saved."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/register/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_register"})
    )
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(200, json=_REDEEM_BODY)
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "register-verify",
            "--email",
            "e@x.com",
            "--code",
            "123456",
            "--referral-code",
            "GOODEYE-REF1",
        ],
    )

    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_register"
    assert "Referral bonus applied" in result.output
    assert "$5.00" in result.output


@respx.mock
def test_register_verify_without_referral_code_does_not_call_redeem(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """register-verify without --referral-code must not call the redeem endpoint."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/register/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_register2"})
    )
    redeem_route = respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(200, json=_REDEEM_BODY)
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["register-verify", "--email", "e@x.com", "--code", "123456"],
    )

    assert result.exit_code == 0, result.output
    assert redeem_route.call_count == 0
    assert "Referral" not in result.output


@respx.mock
def test_register_verify_referral_failure_is_nonfatal(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When redeem fails (already_referred), register-verify still exits 0 and saves creds."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/register/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_register3"})
    )
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "already_referred",
                "message": "You have already used a referral code.",
                "hint": "Each account may only redeem one referral code.",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "register-verify",
            "--email",
            "e@x.com",
            "--code",
            "123456",
            "--referral-code",
            "GOODEYE-USED",
        ],
    )

    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_register3"
    assert "Referral code not applied" in result.output
    assert "already used a referral code" in result.output


@respx.mock
def test_register_verify_referral_not_eligible_is_nonfatal(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """referral_not_eligible is non-fatal; exit 0 and creds saved."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/register/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_register4"})
    )
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "referral_not_eligible",
                "message": "This referral code is no longer eligible.",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "register-verify",
            "--email",
            "e@x.com",
            "--code",
            "123456",
            "--referral-code",
            "GOODEYE-EXPIRED",
        ],
    )

    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_register4"
    assert "Referral code not applied" in result.output


# ---------------------------------------------------------------------------
# login --referral-code  (interactive device-code path)
# ---------------------------------------------------------------------------


def test_login_device_code_with_referral_code_success(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Interactive login redeems the referral code after device-code auth succeeds."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_login1",
        ) as mock_dcl,
        patch("goodeye_cli.commands.login.save_client_config"),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        respx.post(f"{SERVER}/v1/referrals/redeem").mock(
            return_value=httpx.Response(200, json=_REDEEM_BODY)
        )

        runner = CliRunner()
        result = runner.invoke(app, ["login", "--referral-code", "GOODEYE-REF2"])

    assert result.exit_code == 0, result.output
    assert mock_dcl.called
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_login1"
    assert "Referral bonus applied" in result.output
    assert "$5.00" in result.output


def test_login_device_code_without_referral_code_does_not_call_redeem(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Interactive login without --referral-code must not call the redeem endpoint."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_login2",
        ),
        patch("goodeye_cli.commands.login.save_client_config"),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        redeem_route = respx.post(f"{SERVER}/v1/referrals/redeem").mock(
            return_value=httpx.Response(200, json=_REDEEM_BODY)
        )

        runner = CliRunner()
        result = runner.invoke(app, ["login"])

    assert result.exit_code == 0, result.output
    assert redeem_route.call_count == 0
    assert "Referral" not in result.output


def test_login_device_code_referral_failure_is_nonfatal(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When redeem fails, interactive login still exits 0 and credentials are saved."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_login3",
        ),
        patch("goodeye_cli.commands.login.save_client_config"),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        respx.post(f"{SERVER}/v1/referrals/redeem").mock(
            return_value=httpx.Response(
                409,
                json={
                    "error": "already_referred",
                    "message": "You have already used a referral code.",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(app, ["login", "--referral-code", "GOODEYE-USED2"])

    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_login3"
    assert "Referral code not applied" in result.output
    assert "already used a referral code" in result.output


# ---------------------------------------------------------------------------
# register (no --email) --referral-code  (interactive device-code path)
# ---------------------------------------------------------------------------


def test_register_device_code_with_referral_code_success(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Bare `register` runs the interactive device-code flow and redeems the referral code."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_regdc1",
        ) as mock_dcl,
        patch("goodeye_cli.commands.login.save_client_config"),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        respx.post(f"{SERVER}/v1/referrals/redeem").mock(
            return_value=httpx.Response(200, json=_REDEEM_BODY)
        )

        runner = CliRunner()
        result = runner.invoke(app, ["register", "--referral-code", "GOODEYE-REGX"])

    assert result.exit_code == 0, result.output
    assert mock_dcl.called
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_regdc1"
    assert "Referral bonus applied" in result.output
    assert "$5.00" in result.output


def test_register_device_code_without_referral_code_does_not_call_redeem(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Bare `register` without --referral-code runs interactively and skips redeem."""
    _env(monkeypatch, tmp_config_paths)

    with (
        respx.mock,
        patch(
            "goodeye_cli.commands.login.device_code_login",
            return_value="good_live_EXAMPLE_regdc2",
        ) as mock_dcl,
        patch("goodeye_cli.commands.login.save_client_config"),
    ):
        respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
            return_value=httpx.Response(200, json=_CLIENT_CONFIG_BODY)
        )
        redeem_route = respx.post(f"{SERVER}/v1/referrals/redeem").mock(
            return_value=httpx.Response(200, json=_REDEEM_BODY)
        )

        runner = CliRunner()
        result = runner.invoke(app, ["register"])

    assert result.exit_code == 0, result.output
    assert mock_dcl.called
    assert redeem_route.call_count == 0
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_regdc2"
    assert "Referral" not in result.output


# ---------------------------------------------------------------------------
# login-verify --referral-code
# ---------------------------------------------------------------------------


@respx.mock
def test_login_verify_with_referral_code_success(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """login-verify redeems the referral code after email-code auth completes."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/login/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_lv1"})
    )
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(200, json=_REDEEM_BODY)
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "login-verify",
            "--email",
            "e@x.com",
            "--code",
            "654321",
            "--referral-code",
            "GOODEYE-REF3",
        ],
    )

    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_lv1"
    assert "Referral bonus applied" in result.output
    assert "$5.00" in result.output


@respx.mock
def test_login_verify_without_referral_code_does_not_call_redeem(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """login-verify without --referral-code must not call the redeem endpoint."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/login/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_lv2"})
    )
    redeem_route = respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(200, json=_REDEEM_BODY)
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["login-verify", "--email", "e@x.com", "--code", "654321"],
    )

    assert result.exit_code == 0, result.output
    assert redeem_route.call_count == 0
    assert "Referral" not in result.output


@respx.mock
def test_login_verify_referral_failure_is_nonfatal(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """When redeem fails, login-verify still exits 0 and credentials are saved."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/login/verify").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE_lv3"})
    )
    respx.post(f"{SERVER}/v1/referrals/redeem").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "already_referred",
                "message": "You have already used a referral code.",
                "hint": "Each account may only redeem one referral code.",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "login-verify",
            "--email",
            "e@x.com",
            "--code",
            "654321",
            "--referral-code",
            "GOODEYE-USED3",
        ],
    )

    assert result.exit_code == 0, result.output
    creds = load_credentials(tmp_config_paths)
    assert creds is not None
    assert creds["api_key"] == "good_live_EXAMPLE_lv3"
    assert "Referral code not applied" in result.output
    assert "already used a referral code" in result.output
    assert "Each account may only redeem one referral code" in result.output


# ---------------------------------------------------------------------------
# Initiation hint mentions --referral-code
# ---------------------------------------------------------------------------


@respx.mock
def test_register_initiation_hint_mentions_referral_code(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """register prints a dim line mentioning --referral-code for the verify step."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/register").mock(
        return_value=httpx.Response(202, json={"status": "ok", "message": "Check your email."})
    )

    runner = CliRunner()
    result = runner.invoke(app, ["register", "--email", "e@x.com"])

    assert result.exit_code == 0, result.output
    assert "--referral-code" in result.output


@respx.mock
def test_login_email_initiation_hint_mentions_referral_code(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """login --email prints a dim line mentioning --referral-code for login-verify."""
    _env(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/login").mock(
        return_value=httpx.Response(202, json={"status": "ok", "message": "Check your email."})
    )

    runner = CliRunner()
    result = runner.invoke(app, ["login", "--email", "e@x.com"])

    assert result.exit_code == 0, result.output
    assert "--referral-code" in result.output


# ---------------------------------------------------------------------------
# No em dashes in modified source files
# ---------------------------------------------------------------------------


def test_login_commands_no_em_dash() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "login.py"
    assert "—" not in src.read_text()


def test_register_commands_no_em_dash() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "register.py"
    )
    assert "—" not in src.read_text()


def test_referrals_helper_no_em_dash() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "src" / "goodeye_cli" / "commands" / "referrals.py"
    )
    assert "—" not in src.read_text()
