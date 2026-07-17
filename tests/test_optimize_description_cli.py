from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"

SKILL_MD_FIXTURE = "# Optimize description pack\n\nFollow these steps to tune the description."
REFERENCES_FIXTURE = {
    "references/01-firing-test.md": "# Firing test\n\nFiring test body.",
    "references/02-scenario-set.md": "# Scenario set\n\nScenario body.",
}


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_optimize_description_client_omits_query_when_default() -> None:
    route = respx.post(f"{SERVER}/v1/skills/wf_1/optimize-description").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": REFERENCES_FIXTURE,
                "max_iterations": 10,
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.optimize_description("wf_1")

    assert route.calls.last.request.content == b""
    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/skills/wf_1/optimize-description"
    assert parse_qs(parsed.query) == {}
    assert result.workflow_id == "wf_1"
    assert result.max_iterations == 10
    assert result.skill_md == SKILL_MD_FIXTURE
    assert result.references == REFERENCES_FIXTURE


@respx.mock
def test_optimize_description_client_passes_max_iterations() -> None:
    route = respx.post(f"{SERVER}/v1/skills/wf_1/optimize-description").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": {},
                "max_iterations": 35,
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.optimize_description("wf_1", max_iterations=35)

    parsed = urlparse(str(route.calls.last.request.url))
    assert parse_qs(parsed.query) == {"max_iterations": ["35"]}
    assert result.max_iterations == 35


@respx.mock
def test_workflows_optimize_description_command_prints_skill_md_and_references(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/skills/wf_1/optimize-description").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": REFERENCES_FIXTURE,
                "max_iterations": 10,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize-description", "wf_1"])

    assert result.exit_code == 0, result.output
    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/skills/wf_1/optimize-description"
    assert parse_qs(parsed.query) == {}
    assert "Optimize description pack" in result.output
    assert "wf_1" in result.output
    assert "# Reference files" in result.output
    assert "## references/01-firing-test.md" in result.output
    assert "## references/02-scenario-set.md" in result.output
    assert "Firing test body." in result.output


@respx.mock
def test_workflows_optimize_description_command_passes_max_iterations(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/skills/wf_1/optimize-description").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": {},
                "max_iterations": 35,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "optimize-description", "wf_1", "--max-iterations", "35"]
    )

    assert result.exit_code == 0, result.output
    parsed = urlparse(str(route.calls.last.request.url))
    assert parse_qs(parsed.query) == {"max_iterations": ["35"]}
    assert "max_iterations" in result.output
    assert "35" in result.output


def test_workflows_optimize_description_rejects_max_iterations_below_one(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "optimize-description", "wf_1", "--max-iterations", "0"]
    )
    assert result.exit_code != 0


def test_workflows_optimize_description_rejects_max_iterations_above_cap(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(
        app, ["workflows", "optimize-description", "wf_1", "--max-iterations", "1001"]
    )
    assert result.exit_code != 0


def test_workflows_optimize_description_help_documents_publish_metadata() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize-description", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    normalized = " ".join(plain_output.split())
    assert "--source description_optimization" in normalized
    assert "--expected-version-token" in normalized
    assert "--max-iterations" in normalized
