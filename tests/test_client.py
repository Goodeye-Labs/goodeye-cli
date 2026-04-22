"""Tests for goodeye_cli.client."""

from __future__ import annotations

import httpx
import pytest
import respx

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.errors import (
    AuthRequired,
    InvalidCredentials,
    NotFound,
    RateLimited,
    ServerError,
    ValidationFailed,
)

SERVER = "https://example.test"


@respx.mock
def test_get_me_happy_path() -> None:
    respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(200, json={"email": "e@x.com"}))
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        me = client.get_me()
    assert me.email == "e@x.com"


@respx.mock
def test_authorization_header_is_sent() -> None:
    route = respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(200, json={"email": "e"}))
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE") as client:
        client.get_me()
    assert route.calls.last.request.headers["Authorization"] == "Bearer good_live_EXAMPLE"


@respx.mock
def test_no_auth_header_when_missing_key() -> None:
    route = respx.get(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
    )
    with GoodeyeClient(SERVER) as client:
        client.list_skills(filter_="public")
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_user_agent_is_set() -> None:
    route = respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(200, json={"email": "e"}))
    with GoodeyeClient(SERVER, api_key="k") as client:
        client.get_me()
    assert route.calls.last.request.headers["User-Agent"].startswith("goodeye-cli/")


@respx.mock
def test_error_translation_auth_required() -> None:
    respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(
            401,
            json={"error": "auth_required", "message": "nope", "hint": "run login"},
        )
    )
    with GoodeyeClient(SERVER) as client, pytest.raises(AuthRequired) as exc_info:
        client.get_me()
    assert exc_info.value.slug == "auth_required"
    assert exc_info.value.hint == "run login"
    assert exc_info.value.status_code == 401


@respx.mock
def test_error_translation_invalid_credentials() -> None:
    respx.post(f"{SERVER}/v1/login/verify").mock(
        return_value=httpx.Response(401, json={"error": "invalid_credentials", "message": "bad"})
    )
    with GoodeyeClient(SERVER) as client, pytest.raises(InvalidCredentials):
        client.login_verify("e@x.com", "000000")


@respx.mock
def test_error_translation_not_found() -> None:
    respx.get(f"{SERVER}/v1/skills/nope").mock(
        return_value=httpx.Response(404, json={"error": "not_found", "message": "nope"})
    )
    with GoodeyeClient(SERVER, api_key="k") as client, pytest.raises(NotFound):
        client.get_skill("nope")


@respx.mock
def test_error_translation_rate_limited() -> None:
    respx.post(f"{SERVER}/v1/signup").mock(
        return_value=httpx.Response(429, json={"error": "rate_limited", "message": "slow down"})
    )
    with GoodeyeClient(SERVER) as client, pytest.raises(RateLimited):
        client.signup("e@x.com")


@respx.mock
def test_error_translation_unstructured_500() -> None:
    respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(500, text="oops"))
    with GoodeyeClient(SERVER, api_key="k") as client, pytest.raises(ServerError):
        client.get_me()


@respx.mock
def test_error_translation_unstructured_400() -> None:
    respx.get(f"{SERVER}/v1/me").mock(return_value=httpx.Response(400, text="oops"))
    with GoodeyeClient(SERVER, api_key="k") as client, pytest.raises(ValidationFailed):
        client.get_me()


@respx.mock
def test_get_skill_markdown_returns_raw_text() -> None:
    route = respx.get(f"{SERVER}/v1/skills/example").mock(
        return_value=httpx.Response(
            200, text="# hello\nbody", headers={"content-type": "text/markdown"}
        )
    )
    with GoodeyeClient(SERVER) as client:
        result = client.get_skill("example", accept_markdown=True)
    assert isinstance(result, str)
    assert result == "# hello\nbody"
    assert route.calls.last.request.headers["Accept"] == "text/markdown"


@respx.mock
def test_get_skill_json_returns_detail_model() -> None:
    payload = {
        "id": "skl_1",
        "name": "example",
        "visibility": "public",
        "version": 1,
        "body": "hello",
        "description": "ex",
        "manifest": {"tags": ["x"]},
    }
    respx.get(f"{SERVER}/v1/skills/example").mock(return_value=httpx.Response(200, json=payload))
    with GoodeyeClient(SERVER) as client:
        result = client.get_skill("example", accept_markdown=False)
    assert not isinstance(result, str)
    assert result.name == "example"
    assert result.manifest == {"tags": ["x"]}


@respx.mock
def test_list_skills_params_passthrough() -> None:
    route = respx.get(f"{SERVER}/v1/skills").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
    )
    with GoodeyeClient(SERVER, api_key="k") as client:
        client.list_skills(filter_="mine", tag="data", search="foo", limit=10, cursor="abc")
    params = dict(route.calls.last.request.url.params)
    assert params["filter"] == "mine"
    assert params["tag"] == "data"
    assert params["search"] == "foo"
    assert params["limit"] == "10"
    assert params["cursor"] == "abc"


@respx.mock
def test_create_api_key_returns_secret_once() -> None:
    respx.post(f"{SERVER}/v1/api-keys").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "key_01",
                "name": "test",
                "key": "good_live_EXAMPLE_abc",
                "created_at": "2026-04-21T00:00:00Z",
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.create_api_key("test")
    assert result.key == "good_live_EXAMPLE_abc"


@respx.mock
def test_exchange_sends_hostname() -> None:
    route = respx.post(f"{SERVER}/v1/auth/exchange").mock(
        return_value=httpx.Response(
            200,
            json={"api_key": "good_live_EXAMPLE", "key_id": "key_01"},
        )
    )
    with GoodeyeClient(SERVER, api_key="jwt_placeholder") as client:
        result = client.exchange(hostname="laptop")
    assert result.api_key == "good_live_EXAMPLE"
    import json as _json

    body = _json.loads(route.calls.last.request.content.decode())
    assert body == {"hostname": "laptop"}


@respx.mock
def test_delete_skill_happy_path() -> None:
    respx.delete(f"{SERVER}/v1/skills/skl_01").mock(
        return_value=httpx.Response(200, json={"skill_id": "skl_01", "deleted": True})
    )
    with GoodeyeClient(SERVER, api_key="k") as client:
        result = client.delete_skill("skl_01")
    assert result.deleted is True


@respx.mock
def test_get_design_prompt_roundtrip() -> None:
    respx.get(f"{SERVER}/v1/design/workflow-prompt").mock(
        return_value=httpx.Response(200, json={"prompt": "hello"})
    )
    with GoodeyeClient(SERVER, api_key="k") as client:
        payload = client.get_design_prompt()
    assert payload == {"prompt": "hello"}
