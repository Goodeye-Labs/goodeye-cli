from __future__ import annotations

import json as _json

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_teach_workflow_client_posts_structured_payload() -> None:
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "new_version": 2,
                "rounds_run": 1,
                "rubric_edits": [{"verifier_ref": "clarity", "before": "a", "after": "b"}],
                "verifiers_added": [],
                "verifiers_removed": [],
                "scenarios_used": [],
                "unresolved": [],
                "post_teach_expectation": "updated",
                "trigger_context_echo": {"source": "cli-test"},
                "human_report": "updated",
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.teach_workflow(
            "wf_1",
            scenario={"sample": "edge"},
            trigger_context={"source": "cli-test"},
            max_rounds=2,
        )

    body = _json.loads(route.calls.last.request.content.decode())
    assert body == {
        "scenario": {"sample": "edge"},
        "trigger_context": {"source": "cli-test"},
        "max_rounds": 2,
    }
    assert result.workflow_id == "wf_1"
    assert result.new_version == 2
    assert result.rubric_edits[0]["verifier_ref"] == "clarity"


@respx.mock
def test_workflows_teach_command_prints_human_report(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "new_version": None,
                "rounds_run": 1,
                "rubric_edits": [],
                "verifiers_added": [],
                "verifiers_removed": [],
                "scenarios_used": [{"source": "fully_synthetic"}],
                "unresolved": [],
                "post_teach_expectation": "unchanged",
                "trigger_context_echo": None,
                "human_report": "Ran 1 teaching round(s); 0 calibration change(s) recorded.",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "teach", "wf_1", "--max-rounds", "1"])

    assert result.exit_code == 0, result.output
    body = _json.loads(route.calls.last.request.content.decode())
    assert body["max_rounds"] == 1
    assert body["scenario"] is None
    assert body["trigger_context"] is None
    assert "Ran 1 teaching round" in result.output
    assert "new_version: none" in result.output.lower()


@respx.mock
def test_workflows_teach_command_can_print_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "new_version": 3,
                "rounds_run": 2,
                "rubric_edits": [],
                "verifiers_added": [],
                "verifiers_removed": [],
                "scenarios_used": [],
                "unresolved": [],
                "post_teach_expectation": "updated",
                "trigger_context_echo": {"source": "cli"},
                "human_report": "updated",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflows",
            "teach",
            "wf_1",
            "--scenario-json",
            '{"sample":"edge"}',
            "--trigger-context-json",
            '{"source":"cli"}',
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.output)
    assert parsed["workflow_id"] == "wf_1"
    assert parsed["new_version"] == 3
