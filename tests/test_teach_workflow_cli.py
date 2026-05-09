from __future__ import annotations

import json as _json
import re

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"

SKILL_MD_FIXTURE = "# Teach pack\n\nFollow these steps to run the teach session."


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


def test_workflows_teach_help_documents_required_publish_metadata() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "teach", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    normalized = " ".join(plain_output.split())
    assert (
        "goodeye workflows publish - --name <name> --description <description> "
        "--outcome <outcome> --source teach --expected-version-token "
        "<captured at stage 2>"
    ) in normalized
    assert "goodeye workflows publish - --source teach --expected-version-token" not in normalized


@respx.mock
def test_teach_workflow_client_posts_trigger_context() -> None:
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.teach_workflow(
            "wf_1",
            trigger_context={"source": "cli-test"},
        )

    body = _json.loads(route.calls.last.request.content.decode())
    assert body == {"trigger_context": {"source": "cli-test"}}
    assert result.workflow_id == "wf_1"
    assert result.skill_md == SKILL_MD_FIXTURE


@respx.mock
def test_teach_workflow_client_omits_trigger_context_when_none() -> None:
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.teach_workflow("wf_1")

    body = _json.loads(route.calls.last.request.content.decode())
    assert body == {}
    assert result.workflow_id == "wf_1"


@respx.mock
def test_workflows_teach_command_prints_skill_md(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "teach", "wf_1"])

    assert result.exit_code == 0, result.output
    assert "wf_1" in result.output
    assert "Teach pack" in result.output


@respx.mock
def test_workflows_teach_command_with_trigger_context(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/teach").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
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
            "--trigger-context",
            '{"source":"cli"}',
        ],
    )

    assert result.exit_code == 0, result.output
    body = _json.loads(route.calls.last.request.content.decode())
    assert body == {"trigger_context": {"source": "cli"}}
    assert "wf_1" in result.output
    assert "Teach pack" in result.output
