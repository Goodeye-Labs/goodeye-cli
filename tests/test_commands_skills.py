"""Tests for the skills subcommand group."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.commands.skills import _parse_front_matter
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


@respx.mock
def test_skills_list_renders_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "skl_01",
                        "slug": "one",
                        "visibility": "public",
                        "current_version": 1,
                    },
                    {
                        "id": "skl_02",
                        "slug": "two",
                        "visibility": "private",
                        "current_version": 3,
                    },
                ],
                "next_cursor": None,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "list", "--filter", "all"])
    assert result.exit_code == 0, result.output
    assert "skl_01" in result.output
    assert "skl_02" in result.output


@respx.mock
def test_skills_list_follows_cursor(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    responses = [
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_01", "slug": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": "c1",
            },
        ),
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_02", "slug": "b", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": None,
            },
        ),
    ]
    route = respx.get(f"{SERVER}/v1/skills").mock(side_effect=responses)

    runner = CliRunner()
    result = runner.invoke(app, ["skills", "list", "--filter", "public"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    assert "skl_01" in result.output and "skl_02" in result.output


@respx.mock
def test_skills_get_markdown_default(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/skills/example").mock(
        return_value=httpx.Response(200, text="# hi\nbody")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "get", "example"])
    assert result.exit_code == 0, result.output
    assert "# hi" in result.output


@respx.mock
def test_skills_get_json_flag(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/skills/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "skl_01",
                "slug": "example",
                "visibility": "public",
                "version": 1,
                "body": "hi",
                "manifest": {},
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "get", "example", "--json"])
    assert result.exit_code == 0, result.output
    assert '"slug": "example"' in result.output


@respx.mock
def test_skills_push_uses_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "skill.md"
    skill_file.write_text(
        "---\n" "slug: my-skill\n" "manifest:\n" "  tags: [data]\n" "---\n" "# Body\n"
    )
    route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={
                "skill_id": "skl_01",
                "version": 1,
                "slug": "my-skill",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "push", str(skill_file)])
    assert result.exit_code == 0, result.output

    import json as _json

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["slug"] == "my-skill"
    assert sent["manifest"] == {"tags": ["data"]}
    assert sent["visibility"] == "private"
    assert "# Body" in sent["body"]


@respx.mock
def test_skills_push_public_flag_overrides_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "skill.md"
    skill_file.write_text("---\nslug: my-skill\nvisibility: private\n---\nBody\n")
    route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={"skill_id": "skl_01", "version": 1, "slug": "my-skill", "visibility": "public"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "push", str(skill_file), "--public"])
    assert result.exit_code == 0, result.output

    import json as _json

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["visibility"] == "public"


@respx.mock
def test_skills_set_visibility(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.put(f"{SERVER}/v1/skills/skl_01/visibility").mock(
        return_value=httpx.Response(200, json={"skill_id": "skl_01", "visibility": "public"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "set-visibility", "skl_01", "public"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_skills_delete_with_yes_flag(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/skills/skl_01").mock(
        return_value=httpx.Response(200, json={"skill_id": "skl_01", "deleted": True})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "delete", "skl_01", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output


def test_parse_front_matter_extracts_manifest() -> None:
    source = "---\nslug: foo\nmanifest:\n  tags:\n    - a\n---\nBody text\n"
    fm, body = _parse_front_matter(source)
    assert fm == {"slug": "foo", "manifest": {"tags": ["a"]}}
    assert body == "Body text\n"


def test_parse_front_matter_without_front_matter_returns_source() -> None:
    fm, body = _parse_front_matter("just body\n")
    assert fm == {}
    assert body == "just body\n"
