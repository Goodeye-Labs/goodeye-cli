"""CLI tests for template snapshot verifier runs and fork verifier display."""

from __future__ import annotations

import json

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


def _sample_run_json(*, remaining: int | None) -> dict:
    return {
        "verifier_run_id": "run-uuid",
        "anonymous_verifier_run_id": None,
        "verifier_id": "ver-uuid",
        "template_version_id": "tv-uuid",
        "template_version": 1,
        "verifier_version": 1,
        "status": "success",
        "passed": True,
        "reasoning": "ok",
        "duration_ms": 12,
        "remaining_anonymous_runs": remaining,
        "created_at": "2026-01-01T00:00:00+00:00",
        "error_code": None,
        "error_message": None,
    }


@respx.mock
def test_run_template_verifier_anonymous_json(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/sample@1/verifiers/tone/runs").mock(
        return_value=httpx.Response(200, json=_sample_run_json(remaining=4)),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "run-verifier",
            "sample@1",
            "tone",
            "--input",
            "output=hi",
            "--anonymous",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    req = route.calls.last.request
    assert req.headers.get("Authorization") is None
    body = json.loads(req.content.decode())
    assert body["inputs"] == {"output": "hi"}
    out = json.loads(result.stdout)
    assert out["passed"] is True
    assert out["remaining_anonymous_runs"] == 4


@respx.mock
def test_run_template_verifier_authenticated_json(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/templates/sample@1/verifiers/tone/runs").mock(
        return_value=httpx.Response(
            200,
            json=_sample_run_json(remaining=None)
            | {"passed": False, "reasoning": "no", "verifier_run_id": "auth-run"},
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "run-verifier",
            "sample@1",
            "tone",
            "--input",
            "output=hi",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") == "Bearer good_live_EXAMPLE"
    out = json.loads(result.stdout)
    assert out["passed"] is False


@respx.mock
def test_run_template_verifier_json_exits_nonzero_on_runtime_error(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/sample@1/verifiers/tone/runs").mock(
        return_value=httpx.Response(
            200,
            json=_sample_run_json(remaining=None)
            | {
                "status": "error",
                "passed": None,
                "reasoning": None,
                "error_code": "runtime_error",
                "error_message": "judge failed",
            },
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "run-verifier",
            "sample@1",
            "tone",
            "--input",
            "output=hi",
            "--json",
        ],
    )
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["status"] == "error"
    assert out["error_code"] == "runtime_error"


@respx.mock
def test_run_template_verifier_anonymous_limit_exit_code(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/sample@1/verifiers/tone/runs").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": "anonymous_limit_exceeded",
                "message": "You have used your free daily runs. Sign up to continue.",
                "signup_url": "https://goodeyelabs.com/signup",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "templates",
            "run-verifier",
            "sample@1",
            "tone",
            "--input",
            "output=hi",
            "--anonymous",
        ],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "signup" in combined.lower() or "sign up" in combined.lower()


@respx.mock
def test_fork_prints_auto_deployed_verifiers(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "11111111-1111-1111-1111-111111111111",
                "slug": "sample",
                "name": "sample",
                "parent_template_id": "22222222-2222-2222-2222-222222222222",
                "parent_template_version": 1,
                "version_token": None,
                "redirected": False,
                "deprecation_warning": None,
                "verifiers": [
                    {"name": "tone", "verifier_id": "33333333-3333-3333-3333-333333333333"},
                    {"name": "factual", "verifier_id": "44444444-4444-4444-4444-444444444444"},
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "sample"])
    assert result.exit_code == 0, result.output
    assert "tone" in result.stdout
    assert "factual" in result.stdout
    assert "33333333-3333-3333-3333-333333333333" in result.stdout


@respx.mock
def test_fork_with_no_verifiers_omits_verifier_section(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/templates/fork").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "11111111-1111-1111-1111-111111111111",
                "slug": "sample",
                "name": "sample",
                "parent_template_id": "22222222-2222-2222-2222-222222222222",
                "parent_template_version": 1,
                "version_token": None,
                "redirected": False,
                "deprecation_warning": None,
                "verifiers": [],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["templates", "fork", "sample"])
    assert result.exit_code == 0, result.output
    assert "verifier" not in result.stdout.lower()
