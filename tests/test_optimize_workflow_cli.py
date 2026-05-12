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

SKILL_MD_FIXTURE = "# Optimize pack\n\nFollow these steps to run the optimization loop."
REFERENCES_FIXTURE = {
    "references/scoring.md": "# Scoring\n\nScoring rubric body.",
    "references/loop.md": "# Loop\n\nLoop body.",
}


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_optimize_workflow_client_omits_query_when_default() -> None:
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/optimize").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": REFERENCES_FIXTURE,
                "max_iterations": 20,
            },
        )
    )

    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.optimize_workflow("wf_1")

    assert route.calls.last.request.content == b""
    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/workflows/wf_1/optimize"
    assert parse_qs(parsed.query) == {}
    assert result.workflow_id == "wf_1"
    assert result.max_iterations == 20
    assert result.skill_md == SKILL_MD_FIXTURE
    assert result.references == REFERENCES_FIXTURE


@respx.mock
def test_optimize_workflow_client_passes_max_iterations() -> None:
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/optimize").mock(
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
        result = client.optimize_workflow("wf_1", max_iterations=35)

    parsed = urlparse(str(route.calls.last.request.url))
    assert parse_qs(parsed.query) == {"max_iterations": ["35"]}
    assert result.max_iterations == 35


@respx.mock
def test_workflows_optimize_command_prints_skill_md_and_references(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/optimize").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": REFERENCES_FIXTURE,
                "max_iterations": 20,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize", "wf_1"])

    assert result.exit_code == 0, result.output
    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.path == "/v1/workflows/wf_1/optimize"
    assert parse_qs(parsed.query) == {}
    assert "Optimize pack" in result.output
    assert "wf_1" in result.output
    # Reference files were appended as labeled sub-sections.
    assert "# Reference files" in result.output
    assert "## references/scoring.md" in result.output
    assert "## references/loop.md" in result.output
    assert "Scoring rubric body." in result.output


@respx.mock
def test_workflows_optimize_command_passes_max_iterations(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/workflows/wf_1/optimize").mock(
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
    result = runner.invoke(app, ["workflows", "optimize", "wf_1", "--max-iterations", "35"])

    assert result.exit_code == 0, result.output
    parsed = urlparse(str(route.calls.last.request.url))
    assert parse_qs(parsed.query) == {"max_iterations": ["35"]}
    assert "max_iterations" in result.output
    assert "35" in result.output


def test_workflows_optimize_rejects_max_iterations_below_one(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize", "wf_1", "--max-iterations", "0"])
    assert result.exit_code != 0


def test_workflows_optimize_rejects_max_iterations_above_cap(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize", "wf_1", "--max-iterations", "1001"])
    assert result.exit_code != 0


@respx.mock
def test_workflows_optimize_accepts_max_iterations_above_legacy_cap(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Values above the legacy 50 cap but inside 1..1000 must be accepted.

    1001 alone fails under either a 50 or 1000 cap and so cannot catch a
    silent regression of typer's ``max=`` back to 50. Pin the contract
    with an in-range value above 50.
    """
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/workflows/wf_1/optimize").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_id": "wf_1",
                "skill_md": SKILL_MD_FIXTURE,
                "references": {},
                "max_iterations": 200,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize", "wf_1", "--max-iterations", "200"])
    assert result.exit_code == 0, result.output
    assert "200" in result.output


def test_workflows_optimize_help_documents_publish_metadata() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "optimize", "--help"])

    assert result.exit_code == 0, result.output
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    normalized = " ".join(plain_output.split())
    assert "--source optimization" in normalized
    assert "--expected-version-token" in normalized
    assert "--max-iterations" in normalized
