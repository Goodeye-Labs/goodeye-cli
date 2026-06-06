"""Tests for ``goodeye image-generators`` CLI commands."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"
runner = CliRunner()

GENERATOR_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _setup_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    save_credentials({"api_key": "good_live_EXAMPLE_test", "server": SERVER}, tmp_config_paths)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)


def _setup_no_creds(monkeypatch, tmp_config_paths: ConfigPaths) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("GOODEYE_API_KEY", raising=False)
    monkeypatch.delenv("GOODEYE_SERVER", raising=False)
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)


def _deploy_response() -> dict:
    return {
        "generator_id": GENERATOR_UUID,
        "name": "my-generator",
        "description": "Generates product images.",
        "current_version": 1,
        "version": 1,
        "version_token": "tok-abc",
        "status": "active",
        "scope": "user",
        "provider": "fal",
        "model": "fal-ai/some-model",
        "generation_contract": "text_to_image",
        "config_hash": "abc123",
        "created_at": "2026-06-01T00:00:00+00:00",
    }


def _generator_summary() -> dict:
    return {
        "generator_id": GENERATOR_UUID,
        "name": "my-generator",
        "description": "Generates product images.",
        "current_version": 2,
        "version_token": "tok-abc",
        "status": "active",
        "scope": "user",
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T01:00:00+00:00",
    }


def _generator_detail() -> dict:
    return {
        "generator_id": GENERATOR_UUID,
        "name": "my-generator",
        "description": "Generates product images.",
        "current_version": 2,
        "version": 2,
        "version_token": "tok-abc",
        "status": "active",
        "scope": "user",
        "provider": "fal",
        "model": "fal-ai/some-model",
        "generation_contract": "text_to_image",
        "default_params": {"width": 1024},
        "config_hash": "abc123",
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T01:00:00+00:00",
    }


def _run_response(*, status: str = "success") -> dict:
    base: dict = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "model_tier_or_model": "system:image-standard",
        "image_url": "https://example.com/image.png",
        "image_urls": ["https://example.com/image.png"],
        "width": 1024,
        "height": 1024,
        "num_images": 1,
        "cost_usd": "0.04",
        "duration_ms": 3000,
        "status": status,
        "created_at": "2026-06-01T00:00:00+00:00",
        "error_code": None,
        "error_message": None,
    }
    if status == "error":
        base["image_url"] = None
        base["image_urls"] = []
        base["width"] = None
        base["height"] = None
        base["cost_usd"] = "0"
        base["duration_ms"] = None
        base["error_code"] = "provider_error"
        base["error_message"] = "Provider timed out."
    return base


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_deploy_posts_payload(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["name"] == "my-generator"
        assert body["model"] == "fal-ai/some-model"
        assert body["generation_contract"] == "text_to_image"
        return httpx.Response(201, json=_deploy_response())

    respx.post(f"{SERVER}/v1/image-generators").mock(side_effect=respond)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "deploy",
            "--name",
            "my-generator",
            "--description",
            "Generates product images.",
            "--model",
            "fal-ai/some-model",
            "--generation-contract",
            "text_to_image",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Deployed" in result.output
    assert "my-generator" in result.output


@respx.mock
def test_image_generators_deploy_with_params_json(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["default_params"] == {"width": 1024, "height": 768}
        return httpx.Response(201, json=_deploy_response())

    respx.post(f"{SERVER}/v1/image-generators").mock(side_effect=respond)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "deploy",
            "--name",
            "my-generator",
            "--description",
            "Generates product images.",
            "--model",
            "fal-ai/some-model",
            "--generation-contract",
            "text_to_image",
            "--params-json",
            '{"width":1024,"height":768}',
        ],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_image_generators_deploy_with_version_token(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["expected_version_token"] == "tok-prev"
        resp = _deploy_response()
        resp["version"] = 2
        resp["current_version"] = 2
        return httpx.Response(201, json=resp)

    respx.post(f"{SERVER}/v1/image-generators").mock(side_effect=respond)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "deploy",
            "--name",
            "my-generator",
            "--description",
            "Generates product images.",
            "--model",
            "fal-ai/some-model",
            "--generation-contract",
            "text_to_image",
            "--expected-version-token",
            "tok-prev",
        ],
    )
    assert result.exit_code == 0, result.output


def test_image_generators_deploy_invalid_params_json_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "deploy",
            "--name",
            "my-generator",
            "--description",
            "d",
            "--model",
            "fal-ai/m",
            "--generation-contract",
            "text_to_image",
            "--params-json",
            "{not json",
        ],
    )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "must be valid json" in str(result.exception).lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_list_prints_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.get(f"{SERVER}/v1/image-generators").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [_generator_summary()],
                "next_cursor": "more",
            },
        )
    )
    result = runner.invoke(app, ["image-generators", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"items", "next_cursor"}
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "my-generator"
    assert data["next_cursor"] == "more"
    params = dict(route.calls.last.request.url.params)
    assert params["limit"] == "25"


@respx.mock
def test_image_generators_list_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/image-generators").mock(
        return_value=httpx.Response(
            200,
            json={"items": [_generator_summary()], "next_cursor": None},
        )
    )
    result = runner.invoke(app, ["image-generators", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "my-generator" in result.output


@respx.mock
def test_image_generators_list_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/image-generators").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "next_cursor": None},
        )
    )
    result = runner.invoke(app, ["image-generators", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "No image generators" in result.output


@respx.mock
def test_image_generators_list_all_follows_cursor(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    s1 = dict(_generator_summary())
    s2 = dict(_generator_summary())
    s2["generator_id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    s2["name"] = "second-generator"
    route = respx.get(f"{SERVER}/v1/image-generators").mock(
        side_effect=[
            httpx.Response(200, json={"items": [s1], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [s2], "next_cursor": None}),
        ]
    )
    result = runner.invoke(
        app,
        ["image-generators", "list", "--all", "--cursor", "start", "--limit", "1"],
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    assert '"my-generator"' in result.output
    assert '"second-generator"' in result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_show_prints_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/image-generators/{GENERATOR_UUID}").mock(
        return_value=httpx.Response(200, json=_generator_detail())
    )
    result = runner.invoke(app, ["image-generators", "show", GENERATOR_UUID, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["generator_id"] == GENERATOR_UUID
    assert data["version"] == 2
    assert data["config_hash"] == "abc123"


@respx.mock
def test_image_generators_show_forwards_version(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("version") == "1"
        return httpx.Response(200, json=_generator_detail())

    respx.get(f"{SERVER}/v1/image-generators/{GENERATOR_UUID}").mock(side_effect=check)
    result = runner.invoke(
        app,
        ["image-generators", "show", GENERATOR_UUID, "--version", "1", "--json"],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_image_generators_show_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/image-generators/{GENERATOR_UUID}").mock(
        return_value=httpx.Response(200, json=_generator_detail())
    )
    result = runner.invoke(app, ["image-generators", "show", GENERATOR_UUID])
    assert result.exit_code == 0, result.output
    assert "my-generator" in result.output
    assert "text_to_image" in result.output


# ---------------------------------------------------------------------------
# generate - success paths
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_generate_prints_url_to_stdout(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """generate prints the image URL on stdout (pipeable) and exits 0."""
    _setup_creds(monkeypatch, tmp_config_paths)
    # No --generator, no --model: default path (system:image-standard as path placeholder)
    respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(
        return_value=httpx.Response(201, json=_run_response())
    )
    result = runner.invoke(
        app,
        ["image-generators", "generate", "--prompt", "a red cat"],
    )
    assert result.exit_code == 0, result.output
    assert "https://example.com/image.png" in result.output


@respx.mock
def test_image_generators_generate_with_generator_flag(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--generator puts the generator UUID in the path, model field absent from body."""
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith(f"/v1/image-generators/{GENERATOR_UUID}/runs")
        body = json.loads(request.content.decode())
        assert "model" not in body or body.get("model") is None
        return httpx.Response(201, json=_run_response())

    respx.post(f"{SERVER}/v1/image-generators/{GENERATOR_UUID}/runs").mock(side_effect=check)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "a red cat",
            "--generator",
            GENERATOR_UUID,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "https://example.com/image.png" in result.output


@respx.mock
def test_image_generators_generate_with_model_flag(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--model puts the model slug in the body and uses placeholder path."""
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        # Path must be the default-tier placeholder
        assert "/v1/image-generators/system:image-standard/runs" in str(request.url)
        body = json.loads(request.content.decode())
        assert body["model"] == "fal-ai/some-model"
        return httpx.Response(201, json=_run_response())

    respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(side_effect=check)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "a red cat",
            "--model",
            "fal-ai/some-model",
        ],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_image_generators_generate_multiple_urls_each_on_own_line(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Multiple images: each URL goes on a separate stdout line."""
    _setup_creds(monkeypatch, tmp_config_paths)
    multi_resp = dict(_run_response())
    multi_resp["image_url"] = None
    multi_resp["image_urls"] = [
        "https://example.com/image1.png",
        "https://example.com/image2.png",
    ]
    multi_resp["num_images"] = 2
    respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(
        return_value=httpx.Response(201, json=multi_resp)
    )
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "two cats",
            "--num-images",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "https://example.com/image1.png" in result.output
    assert "https://example.com/image2.png" in result.output


@respx.mock
def test_image_generators_generate_forwards_provenance_flags(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["workflow_id"] == "wf_123"
        assert body["workflow_version"] == 5
        assert body["workflow_ref"] == "my-workflow"
        assert body["run_id"] == "trace-xyz"
        return httpx.Response(201, json=_run_response())

    respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(side_effect=check)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "a cat",
            "--workflow-id",
            "wf_123",
            "--workflow-version",
            "5",
            "--workflow-ref",
            "my-workflow",
            "--run-id",
            "trace-xyz",
        ],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_image_generators_generate_forwards_optional_flags(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["reference_image_url"] == "https://example.com/ref.png"
        assert body["seed"] == 42
        assert body["param_overrides"] == {"aspect_ratio": "16:9"}
        assert body["version"] == 3
        return httpx.Response(201, json=_run_response())

    respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(side_effect=check)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "a cat",
            "--reference-image-url",
            "https://example.com/ref.png",
            "--seed",
            "42",
            "--params-json",
            '{"aspect_ratio":"16:9"}',
            "--version",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# generate - error paths
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_generate_error_status_exits_nonzero(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """status='error' in the response causes non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(
        return_value=httpx.Response(201, json=_run_response(status="error"))
    )
    result = runner.invoke(
        app,
        ["image-generators", "generate", "--prompt", "a cat"],
    )
    assert result.exit_code != 0
    # Error info should be in the output somewhere
    assert "provider_error" in result.output or "Provider timed out" in result.output


def test_image_generators_generate_generator_and_model_together_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--generator and --model together exit non-zero before any HTTP call."""
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "a cat",
            "--generator",
            GENERATOR_UUID,
            "--model",
            "fal-ai/some-model",
        ],
    )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "mutually exclusive" in str(result.exception).lower()


def test_image_generators_generate_invalid_params_json_errors(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "generate",
            "--prompt",
            "a cat",
            "--params-json",
            "{not json",
        ],
    )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "must be valid json" in str(result.exception).lower()


# ---------------------------------------------------------------------------
# generate --anonymous
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_generate_anonymous_omits_authorization(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--anonymous omits the Authorization header."""
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(
        return_value=httpx.Response(201, json=_run_response())
    )
    result = runner.invoke(
        app,
        ["image-generators", "generate", "--prompt", "a cat", "--anonymous"],
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") is None


@respx.mock
def test_image_generators_generate_anonymous_works_without_credentials(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--anonymous succeeds with no stored credentials."""
    _setup_no_creds(monkeypatch, tmp_config_paths)
    route = respx.post(f"{SERVER}/v1/image-generators/system:image-standard/runs").mock(
        return_value=httpx.Response(201, json=_run_response())
    )
    result = runner.invoke(
        app,
        ["image-generators", "generate", "--prompt", "a cat", "--anonymous"],
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("Authorization") is None


# ---------------------------------------------------------------------------
# generate uses long request timeout
# ---------------------------------------------------------------------------


def test_image_generators_generate_uses_long_request_timeout(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    captured: dict[str, float | None] = {}

    class FakeClient:
        def __init__(self, server: str, *, api_key: str | None, timeout: float | None = None):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def generate_image(self, *args: object, **kwargs: object):
            return SimpleNamespace(
                status="success",
                image_url="https://example.com/image.png",
                image_urls=["https://example.com/image.png"],
                cost_usd="0.04",
                duration_ms=3000,
                run_id="run_1",
                error_code=None,
                error_message=None,
            )

    monkeypatch.setattr("goodeye_cli.commands.image_generators.GoodeyeClient", FakeClient)

    result = runner.invoke(app, ["image-generators", "generate", "--prompt", "a cat"])

    assert result.exit_code == 0, result.output
    assert captured["timeout"] == 300.0


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


@respx.mock
def test_image_generators_revoke_auto_approves_when_stdin_not_tty(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """CliRunner stdin is not a TTY; confirm auto-approves."""
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/image-generators/{GENERATOR_UUID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "generator_id": GENERATOR_UUID,
                "name": "my-generator",
                "revoked": True,
            },
        )
    )
    result = runner.invoke(app, ["image-generators", "revoke", GENERATOR_UUID])
    assert result.exit_code == 0, result.output
    assert "Revoked" in result.output


def test_image_generators_revoke_human_decline_exits_zero(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """User-cancel is exit 0; API call does not fire."""
    from unittest import mock

    _setup_creds(monkeypatch, tmp_config_paths)
    with mock.patch(
        "goodeye_cli.commands.image_generators.confirm_destructive", return_value=False
    ):
        result = runner.invoke(app, ["image-generators", "revoke", GENERATOR_UUID])
    assert result.exit_code == 0, result.output
    assert "Cancelled" in result.output


# ---------------------------------------------------------------------------
# auth-required paths
# ---------------------------------------------------------------------------


def test_image_generators_list_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["image-generators", "list"])
    assert result.exit_code != 0


def test_image_generators_deploy_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(
        app,
        [
            "image-generators",
            "deploy",
            "--name",
            "x",
            "--description",
            "d",
            "--model",
            "m",
            "--generation-contract",
            "text_to_image",
        ],
    )
    assert result.exit_code != 0


def test_image_generators_show_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["image-generators", "show", GENERATOR_UUID])
    assert result.exit_code != 0


def test_image_generators_revoke_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["image-generators", "revoke", GENERATOR_UUID])
    assert result.exit_code != 0


def test_image_generators_generate_requires_auth_without_anonymous(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(
        app,
        ["image-generators", "generate", "--prompt", "a cat"],
    )
    assert result.exit_code != 0
