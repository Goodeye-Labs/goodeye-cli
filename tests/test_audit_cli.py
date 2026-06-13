from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"

SKILL_MD_FIXTURE = "# Audit a Goodeye workflow\n\nFollow these steps to audit."
REFERENCES_FIXTURE = {
    "references/01-criteria.md": "# Criteria\n\nThe rubric body.",
    "references/03-report-format.md": "# Report format\n\nThe report template.",
}


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_audit_client_with_id_posts_workflow_route() -> None:
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/audit").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": REFERENCES_FIXTURE,
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.audit_workflow("wf_1")

    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/workflows/wf_1/audit"
    assert result.workflow_id == "wf_1"
    assert result.skill_md == SKILL_MD_FIXTURE
    assert result.references == REFERENCES_FIXTURE


@respx.mock
def test_audit_client_without_id_gets_prompt_route() -> None:
    route = respx.get(f"{SERVER}/v1/audit/workflow-prompt").mock(
        return_value=httpx.Response(
            200,
            json={"skill_md": SKILL_MD_FIXTURE, "references": REFERENCES_FIXTURE},
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.audit_workflow()

    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/audit/workflow-prompt"
    assert result.workflow_id is None
    assert result.skill_md == SKILL_MD_FIXTURE


@respx.mock
def test_workflows_audit_command_with_id_prints_pack(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/workflows/wf_1/audit").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": REFERENCES_FIXTURE,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "audit", "wf_1"])

    assert result.exit_code == 0, result.output
    assert "Audit a Goodeye workflow" in result.output
    assert "wf_1" in result.output
    assert "# Reference files" in result.output
    assert "## references/01-criteria.md" in result.output
    assert "The rubric body." in result.output


@respx.mock
def test_workflows_audit_command_without_id_uses_prompt_route(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/audit/workflow-prompt").mock(
        return_value=httpx.Response(
            200,
            json={"skill_md": SKILL_MD_FIXTURE, "references": REFERENCES_FIXTURE},
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "audit"])

    assert result.exit_code == 0, result.output
    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/audit/workflow-prompt"
    assert "Audit a Goodeye workflow" in result.output
    assert "# Reference files" in result.output


def test_workflows_audit_help_documents_publish_metadata() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "audit", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    normalized = " ".join(plain_output.split())
    assert "--source audit" in normalized
    assert "--expected-version-token" in normalized
