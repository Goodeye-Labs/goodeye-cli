"""Tests for CLI-facing update notices."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

import goodeye_cli.app as app_module
import goodeye_cli.update as update_module
from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.update import UpdateCheckResult

SERVER = "https://example.test"
NOTICE = "goodeye 0.7.2 is available; run: goodeye update"


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_config_paths: ConfigPaths) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


def _set_invocation_args(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(app_module, "_get_background_notice_args", lambda: args, raising=False)


def _patch_background_result(
    monkeypatch: pytest.MonkeyPatch,
    result: UpdateCheckResult | None,
) -> None:
    def fake_check(*_args: object, **_kwargs: object) -> UpdateCheckResult | None:
        return result

    monkeypatch.setattr(update_module, "check_for_update_background", fake_check)


def test_plain_text_command_emits_update_notice_to_stderr(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _set_invocation_args(monkeypatch, ["logout"])
    _patch_background_result(
        monkeypatch,
        UpdateCheckResult(current_version="0.7.1", latest_version="0.7.2", update_available=True),
    )

    result = CliRunner().invoke(app, ["logout"])

    assert result.exit_code == 0, result.output
    assert NOTICE in result.stderr
    assert NOTICE not in result.stdout


def test_ci_suppresses_update_notice(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _set_invocation_args(monkeypatch, ["logout"])
    _patch_background_result(
        monkeypatch,
        UpdateCheckResult(current_version="0.7.1", latest_version="0.7.2", update_available=True),
    )

    result = CliRunner().invoke(app, ["logout"], env={"CI": "1"})

    assert result.exit_code == 0, result.output
    assert NOTICE not in result.stderr
    assert NOTICE not in result.stdout


@respx.mock
def test_json_invocation_suppresses_update_notice(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    monkeypatch.setenv("GOODEYE_API_KEY", "good_live_EXAMPLE")
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)
    _set_invocation_args(monkeypatch, ["whoami", "--json"])
    _patch_background_result(
        monkeypatch,
        UpdateCheckResult(current_version="0.7.1", latest_version="0.7.2", update_available=True),
    )
    respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(200, json={"email": "e@x.com"}))

    result = CliRunner().invoke(app, ["whoami", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout.strip())["email"] == "e@x.com"
    assert NOTICE not in result.stderr
    assert NOTICE not in result.stdout


@pytest.mark.parametrize("background_result", [None, RuntimeError("boom")])
def test_background_none_or_failure_is_silent_and_preserves_exit_code(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
    background_result: UpdateCheckResult | Exception | None,
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _set_invocation_args(monkeypatch, ["logout"])

    def fake_check(*_args: object, **_kwargs: object) -> UpdateCheckResult | None:
        if isinstance(background_result, Exception):
            raise background_result
        return background_result

    monkeypatch.setattr(update_module, "check_for_update_background", fake_check)

    result = CliRunner().invoke(app, ["logout"])

    assert result.exit_code == 0, result.output
    assert NOTICE not in result.stderr
    assert NOTICE not in result.stdout


def test_format_update_notice_is_concise() -> None:
    formatter = getattr(update_module, "format_update_notice", None)

    assert formatter is not None
    assert (
        formatter(
            UpdateCheckResult(
                current_version="0.7.1",
                latest_version="0.7.2",
                update_available=True,
            )
        )
        == NOTICE
    )


@pytest.mark.parametrize(
    "args,env",
    [
        (["--version"], {}),
        (["--help"], {}),
        (["help"], {}),
        (["whoami", "--help"], {}),
        (["update"], {}),
        (["update", "--check"], {}),
        (["upgrade"], {}),
        (["whoami", "--json"], {}),
        (["--json", "whoami"], {}),
        (["logout"], {"CI": "1"}),
        (["logout"], {"CI": "true"}),
    ],
)
def test_should_suppress_background_notice_for_meta_or_machine_readable_invocations(
    args: list[str],
    env: dict[str, str],
) -> None:
    should_suppress = getattr(update_module, "should_suppress_background_notice", None)

    assert should_suppress is not None
    assert should_suppress(args, env) is True


@pytest.mark.parametrize(
    "args,env",
    [
        (["logout"], {}),
        (["logout"], {"CI": ""}),
        (["workflows", "list"], {"OTHER_ENV": "1"}),
    ],
)
def test_should_not_suppress_background_notice_for_plain_text_commands(
    args: list[str],
    env: dict[str, str],
) -> None:
    should_suppress = getattr(update_module, "should_suppress_background_notice", None)

    assert should_suppress is not None
    assert should_suppress(args, env) is False


@pytest.mark.parametrize(
    "args,env",
    [
        # Inherits every background-notice suppression case.
        (["--version"], {}),
        (["--help"], {}),
        (["whoami", "--json"], {}),
        (["logout"], {"CI": "1"}),
        # Plus any explicit `workflows sync ...` command: never auto-pull while
        # the user is running sync by hand.
        (["workflows", "sync"], {}),
        (["workflows", "sync", "pull"], {}),
        (["workflows", "sync", "push"], {}),
        (["workflows", "sync", "status"], {}),
        (["workflows", "sync", "auto", "on"], {}),
        (["workflows", "sync", "target", "list"], {}),
    ],
)
def test_should_suppress_auto_pull_for_meta_and_sync_commands(
    args: list[str],
    env: dict[str, str],
) -> None:
    should_suppress = getattr(update_module, "should_suppress_auto_pull", None)

    assert should_suppress is not None
    assert should_suppress(args, env) is True


@pytest.mark.parametrize(
    "args",
    [
        ["logout"],
        ["workflows", "list"],
        # A non-sync workflows subcommand is eligible for an automatic pull tail.
        ["workflows", "get", "some-slug"],
        ["templates", "list"],
    ],
)
def test_should_not_suppress_auto_pull_for_ordinary_commands(args: list[str]) -> None:
    should_suppress = getattr(update_module, "should_suppress_auto_pull", None)

    assert should_suppress is not None
    assert should_suppress(args, {}) is False
