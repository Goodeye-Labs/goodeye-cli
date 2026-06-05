"""Tests for publish command directory mode and --clear-files flag."""

from __future__ import annotations

import json as _json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials
from goodeye_cli.errors import ValidationFailed

SERVER = "https://example.test"

_SKILL_MD = (
    "---\n"
    "name: my-skill\n"
    "description: A test skill.\n"
    "outcome: Achieve the test outcome.\n"
    "---\n\n"
    "Do the work.\n"
)

_SAVE_RESPONSE = {
    "workflow_id": "skl_01",
    "version": 1,
    "version_token": "tok-1",
    "name": "my-skill",
    "verifiers": [],
}


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_single_file_publish_omits_files_key(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Publishing a single .md file must NOT send a files key."""
    _setup_creds(monkeypatch, tmp_config_paths)
    md_file = tmp_path / "my-skill.md"
    md_file.write_text(_SKILL_MD)

    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(201, json=_SAVE_RESPONSE)
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(md_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert "files" not in sent


@respx.mock
def test_clear_files_sends_empty_list(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--clear-files sends files=[] in the request body."""
    _setup_creds(monkeypatch, tmp_config_paths)
    md_file = tmp_path / "my-skill.md"
    md_file.write_text(_SKILL_MD)

    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(201, json=_SAVE_RESPONSE)
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(md_file), "--clear-files"])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert "files" in sent
    assert sent["files"] == []


@respx.mock
def test_directory_publish_uploads_tree(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Publishing a directory reads SKILL.md as body and includes siblings in files."""
    _setup_creds(monkeypatch, tmp_config_paths)

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD)
    (skill_dir / "run.sh").write_text("#!/bin/bash\necho hi\n")

    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(201, json=_SAVE_RESPONSE)
    )
    # client-config needed to fetch ignore_defaults during directory publish
    respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
        return_value=httpx.Response(
            200,
            json={
                "workos_client_id": "client_id",
                "workos_device_authorization_uri": "https://auth.example.test/device",
                "workos_token_uri": "https://auth.example.test/token",
                "ignore_defaults": [],
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(skill_dir)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    # Body comes from SKILL.md
    assert sent["body"] == _SKILL_MD
    # files key must be present with the sibling
    assert "files" in sent
    paths_sent = {f["path"] for f in sent["files"]}
    assert "run.sh" in paths_sent
    # SKILL.md must NOT appear in the files list
    assert "SKILL.md" not in paths_sent


def test_clear_files_and_directory_are_mutually_exclusive(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--clear-files with a directory input must raise a usage error."""
    _setup_creds(monkeypatch, tmp_config_paths)

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD)

    runner = CliRunner()
    result = runner.invoke(app, ["workflows", "publish", str(skill_dir), "--clear-files"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationFailed)
