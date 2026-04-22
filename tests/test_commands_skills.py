"""Tests for the skills subcommand group."""

from __future__ import annotations

import json as _json
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
                        "name": "one",
                        "visibility": "public",
                        "current_version": 1,
                        "description": "first skill",
                    },
                    {
                        "id": "skl_02",
                        "name": "two",
                        "visibility": "private",
                        "current_version": 3,
                        "description": "second skill",
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
                    {"id": "skl_01", "name": "a", "visibility": "public", "current_version": 1}
                ],
                "next_cursor": "c1",
            },
        ),
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": "skl_02", "name": "b", "visibility": "public", "current_version": 1}
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
                "name": "example",
                "visibility": "public",
                "version": 1,
                "body": "hi",
                "description": "example skill",
                "manifest": {},
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "get", "example", "--json"])
    assert result.exit_code == 0, result.output
    assert '"name": "example"' in result.output


@respx.mock
def test_publish_minimal_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Claude-Code-style minimal skill: just name + description + body."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "hello.md"
    skill_file.write_text(
        "---\n"
        "name: hello\n"
        "description: Say hi to the world. Use when onboarding.\n"
        "---\n"
        "# Hello\n\nGreet the user.\n"
    )
    route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={
                "skill_id": "skl_01",
                "version": 1,
                "name": "hello",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "publish", str(skill_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "hello"
    assert sent["description"].startswith("Say hi")
    assert sent["visibility"] == "private"
    # No manifest / no tags in the payload when front-matter doesn't define them.
    assert "manifest" not in sent
    assert "tags" not in sent
    # Body round-trips with the front-matter intact so consumers can drop the
    # downloaded file into ~/.claude/skills/hello/SKILL.md unchanged.
    assert sent["body"].startswith("---\n")
    assert "# Hello" in sent["body"]


@respx.mock
def test_publish_accepts_slug_alias_in_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Transitional: older authored files may still use `slug:` instead of `name:`."""
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "legacy.md"
    skill_file.write_text(
        "---\nslug: my-skill\ndescription: test desc\n---\nBody\n",
    )
    route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={
                "skill_id": "skl_01",
                "version": 1,
                "name": "my-skill",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "publish", str(skill_file)])
    assert result.exit_code == 0, result.output
    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["name"] == "my-skill"


@respx.mock
def test_publish_missing_description_errors(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "no-desc.md"
    skill_file.write_text("---\nname: no-desc\n---\nBody\n")
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "publish", str(skill_file)])
    assert result.exit_code != 0
    # ValidationFailed bubbles up as an exception under CliRunner; inspect
    # the exception message rather than captured output.
    assert result.exception is not None
    assert "description" in str(result.exception).lower()


@respx.mock
def test_publish_tags_and_manifest(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "rich.md"
    skill_file.write_text(
        "---\n"
        "name: rich-skill\n"
        "description: A skill with verifier metadata.\n"
        "tags: [csv, stripe]\n"
        "manifest:\n"
        "  outcome: Reduce refund-row errors\n"
        "---\n"
        "# Body\n",
    )
    route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={
                "skill_id": "skl_01",
                "version": 1,
                "name": "rich-skill",
                "visibility": "private",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "publish", str(skill_file)])
    assert result.exit_code == 0, result.output

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent["tags"] == ["csv", "stripe"]
    assert sent["manifest"] == {"outcome": "Reduce refund-row errors"}


@respx.mock
def test_publish_public_flag_overrides_front_matter(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    skill_file = tmp_path / "skill.md"
    skill_file.write_text(
        "---\nname: my-skill\ndescription: test\nvisibility: private\n---\nBody\n",
    )
    route = respx.post(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(
            201,
            json={
                "skill_id": "skl_01",
                "version": 1,
                "name": "my-skill",
                "visibility": "public",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "publish", str(skill_file), "--public"])
    assert result.exit_code == 0, result.output

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
    source = "---\nname: foo\ndescription: bar\nmanifest:\n  outcome: x\n---\nBody text\n"
    fm, body = _parse_front_matter(source)
    assert fm == {"name": "foo", "description": "bar", "manifest": {"outcome": "x"}}
    assert body == "Body text\n"


def test_parse_front_matter_without_front_matter_returns_source() -> None:
    fm, body = _parse_front_matter("just body\n")
    assert fm == {}
    assert body == "just body\n"
