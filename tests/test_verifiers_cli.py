"""Tests for ``goodeye verifiers`` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials

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
