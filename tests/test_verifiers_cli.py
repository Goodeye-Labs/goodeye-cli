"""Tests for ``goodeye verifiers`` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials
from goodeye_cli.errors import ValidationFailed

SERVER = "https://example.test"
runner = CliRunner()


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_test", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_verifiers_list_prints_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/verifiers").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "verifier_id": "ver_1",
                        "name": "cta-present",
                        "description": "Use when checking CTAs.",
                        "current_version": 1,
                        "status": "active",
                        "version_token": "token",
                        "updated_at": "2026-05-04T00:00:00+00:00",
                    },
                ],
            },
        )
    )
    result = runner.invoke(app, ["verifiers", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "cta-present"


@respx.mock
def test_verifiers_list_table_shows_role_and_source_workflow(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/verifiers").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "verifier_id": "ver_1",
                        "name": "tone",
                        "description": "Checks tone.",
                        "current_version": 2,
                        "status": "active",
                        "version_token": "token",
                        "updated_at": "2026-05-04T00:00:00+00:00",
                        "role": "exec",
                        "source_workflow_id": "wf_shared",
                    },
                ],
            },
        )
    )
    result = runner.invoke(app, ["verifiers", "list"])
    assert result.exit_code == 0, result.output
    assert "Role" in result.output
    assert "Source wf" in result.output
    assert "exec" in result.output
    assert "wf_shared" in result.output


@respx.mock
def test_verifiers_run_outputs_reasoning(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["inputs"] == {"message": "Thanks for reading."}
        return httpx.Response(
            201,
            json={
                "verifier_run_id": "run_1",
                "verifier_id": "ver_1",
                "version": 1,
                "status": "success",
                "passed": False,
                "reasoning": "The output does not name a concrete next action.",
                "duration_ms": 3,
                "created_at": "2026-05-04T00:00:00+00:00",
            },
        )

    respx.post(f"{SERVER}/v1/verifiers/ver_1/runs").mock(side_effect=check_request)
    result = runner.invoke(
        app,
        [
            "verifiers",
            "run",
            "ver_1",
            "--inputs-json",
            '{"message":"Thanks for reading."}',
        ],
    )
    assert result.exit_code == 0, result.output
    assert "FAIL" in result.output
    assert "concrete next action" in result.output


@respx.mock
def test_verifiers_run_forwards_provenance_flags(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["workflow_id"] == "wf_123"
        assert body["workflow_version"] == 4
        assert body["workflow_ref"] == "lesson-quality"
        assert body["run_id"] == "trace-abc"
        return httpx.Response(
            201,
            json={
                "verifier_run_id": "run_2",
                "verifier_id": "ver_1",
                "version": 1,
                "status": "success",
                "passed": True,
                "reasoning": "ok",
                "duration_ms": 1,
                "created_at": "2026-05-04T00:00:00+00:00",
            },
        )

    respx.post(f"{SERVER}/v1/verifiers/ver_1/runs").mock(side_effect=check_request)
    result = runner.invoke(
        app,
        [
            "verifiers",
            "run",
            "ver_1",
            "--inputs-json",
            '{"message":"hi"}',
            "--workflow-id",
            "wf_123",
            "--workflow-version",
            "4",
            "--workflow-ref",
            "lesson-quality",
            "--run-id",
            "trace-abc",
        ],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_verifiers_deploy_posts_json_body(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    cfg = tmp_path / "v.json"
    payload = {
        "name": "smoke-v",
        "description": "Smoke verifier.",
        "criterion": "Pass if text is polite. Fail if text is rude.",
        "input_contract": "text",
        "input_fields": ["body"],
        "few_shot_examples": [],
    }
    cfg.write_text(json.dumps(payload), encoding="utf-8")

    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content.decode()) == payload
        return httpx.Response(
            201,
            json={
                "verifier_id": "vid",
                "version": 1,
                "version_token": "tok",
                "name": "smoke-v",
                "status": "active",
                "input_contract": "text",
                "config_hash": "abc",
            },
        )

    respx.post(f"{SERVER}/v1/verifiers").mock(side_effect=respond)
    result = runner.invoke(app, ["verifiers", "deploy", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Deployed" in result.output


@respx.mock
def test_verifiers_deploy_reads_json_from_stdin(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    payload = {
        "name": "stdin-v",
        "description": "Verifier from stdin.",
        "criterion": "Pass if text is polite. Fail if text is rude.",
        "input_contract": "text",
        "input_fields": ["body"],
        "few_shot_examples": [],
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content.decode()) == payload
        return httpx.Response(
            201,
            json={
                "verifier_id": "vid-stdin",
                "version": 1,
                "version_token": "tok-stdin",
                "name": "stdin-v",
                "status": "active",
                "input_contract": "text",
                "config_hash": "abc",
            },
        )

    respx.post(f"{SERVER}/v1/verifiers").mock(side_effect=respond)
    result = runner.invoke(app, ["verifiers", "deploy", "-"], input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    assert "Deployed" in result.output


def test_verifiers_deploy_stdin_invalid_json_errors(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    result = runner.invoke(app, ["verifiers", "deploy", "-"], input="{not json")

    assert result.exit_code != 0
    assert result.exception is not None
    assert "must be valid json" in str(result.exception).lower()


def test_verifiers_deploy_file_read_failure_errors_cleanly(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    cfg = tmp_path / "v.json"
    cfg.write_text("{}", encoding="utf-8")

    def fail_read_text(*args, **kwargs) -> str:
        raise PermissionError("permission denied")

    monkeypatch.setattr("goodeye_cli.commands.verifiers.Path.read_text", fail_read_text)

    result = runner.invoke(app, ["verifiers", "deploy", str(cfg)])

    assert result.exit_code != 0
    assert result.exception is not None
    assert "could not read verifier config" in str(result.exception).lower()
    assert "permission denied" in str(result.exception).lower()


def test_verifiers_deploy_missing_file_errors_cleanly(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    cfg = tmp_path / "missing.json"

    result = runner.invoke(app, ["verifiers", "deploy", str(cfg)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "not found" in str(result.exception).lower()
    assert str(cfg) in str(result.exception)


def test_verifiers_deploy_directory_path_errors_cleanly(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    result = runner.invoke(app, ["verifiers", "deploy", str(tmp_path)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "not a file" in str(result.exception).lower()
    assert str(tmp_path) in str(result.exception)


def test_verifiers_deploy_invalid_utf8_file_errors_cleanly(
    tmp_config_paths: ConfigPaths,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    cfg = tmp_path / "invalid.json"
    cfg.write_bytes(b"\xff")

    result = runner.invoke(app, ["verifiers", "deploy", str(cfg)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
    assert "utf-8" in str(result.exception).lower()
    assert str(cfg) in str(result.exception)


@respx.mock
def test_verifiers_show_returns_full_config(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/verifiers/ver_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "verifier_id": "ver_1",
                "name": "cta-present",
                "description": "Use when checking CTAs.",
                "version": 2,
                "criterion": "Pass if there is a CTA. Fail if there is no CTA.",
                "input_contract": "text",
                "input_fields": ["body"],
                "few_shot_examples": [],
                "judge_model_config": {"model": "openai/gpt-5.4"},
                "reasoning_field_description": "explain",
                "config_hash": "deadbeef",
                "status": "active",
            },
        )
    )
    result = runner.invoke(app, ["verifiers", "show", "ver_1", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["verifier_id"] == "ver_1"
    assert data["version"] == 2
    assert data["config_hash"] == "deadbeef"


@respx.mock
def test_verifiers_revoke_auto_approves_when_stdin_not_tty(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """CliRunner's stdin is not a TTY; this is the agent/CI case. The
    confirm should auto-approve so the call proceeds without hanging.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/verifiers/ver_1").mock(
        return_value=httpx.Response(
            200, json={"verifier_id": "ver_1", "name": "cta-present", "revoked": True}
        )
    )
    result = runner.invoke(app, ["verifiers", "revoke", "ver_1"])
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output


@respx.mock
def test_verifiers_run_forwards_system_alias(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """POST path forwards system:<name> unchanged (server resolves seeded judges)."""
    _setup_creds(monkeypatch, tmp_config_paths)
    alias = "system:workflow-design-qa"

    url = "https://example.test/v1/verifiers/system:workflow-design-qa/runs"

    def check_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["inputs"] == {"design": "x"}
        assert str(request.url) == url
        return httpx.Response(
            201,
            json={
                "verifier_run_id": "run_sys",
                "verifier_id": "00000000-0000-0000-0000-000000000001",
                "version": 1,
                "status": "success",
                "passed": True,
                "reasoning": "ok",
                "duration_ms": 1,
                "created_at": "2026-05-04T00:00:00+00:00",
            },
        )

    respx.post(url).mock(side_effect=check_request)
    result = runner.invoke(
        app,
        [
            "verifiers",
            "run",
            alias,
            "--inputs-json",
            '{"design":"x"}',
        ],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_verifiers_show_forwards_owned_name(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    """GET path forwards caller-owned verifier name unchanged."""
    _setup_creds(monkeypatch, tmp_config_paths)
    name = "cta-present"

    def check_request(request: httpx.Request) -> httpx.Response:
        expected = f"https://example.test/v1/verifiers/{name}"
        assert str(request.url) == expected
        return httpx.Response(
            200,
            json={
                "verifier_id": "ver_1",
                "name": "cta-present",
                "description": "Use when checking CTAs.",
                "version": 2,
                "criterion": "Pass if there is a CTA. Fail if there is no CTA.",
                "input_contract": "text",
                "input_fields": ["body"],
                "few_shot_examples": [],
                "judge_model_config": {"model": "openai/gpt-5.4"},
                "reasoning_field_description": "explain",
                "config_hash": "deadbeef",
                "status": "active",
            },
        )

    respx.get(f"{SERVER}/v1/verifiers/{name}").mock(side_effect=check_request)
    result = runner.invoke(app, ["verifiers", "show", name, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["name"] == "cta-present"


def test_verifiers_revoke_human_decline_exits_zero(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Human-decline path: user-cancel is exit 0 and the API call does
    not fire. The prompt helper itself is unit-tested in
    ``test_prompts.py``; here we just stub it returning ``False`` to
    exercise the cancel branch wired into the revoke command.
    """
    from unittest import mock

    _setup_creds(monkeypatch, tmp_config_paths)
    with mock.patch("goodeye_cli.commands.verifiers.confirm_destructive", return_value=False):
        result = runner.invoke(app, ["verifiers", "revoke", "ver_1"])
    assert result.exit_code == 0, result.output
    assert "Cancelled" in result.output
