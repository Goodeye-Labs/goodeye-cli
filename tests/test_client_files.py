"""Tests for multi-file workflow client methods."""

from __future__ import annotations

import json as _json

import httpx
import respx

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.wire import ClientConfig, WorkflowDetail

SERVER = "https://example.test"


@respx.mock
def test_save_payload_omits_files_when_none() -> None:
    """When `files` is not passed, the POST body must NOT contain a `files` key."""
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_01",
                "version": 1,
                "version_token": "tok",
                "name": "example",
                "verifiers": [],
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        client.save_workflow(name="example", description="desc", body="body")

    body = _json.loads(route.calls.last.request.content.decode())
    assert "files" not in body


@respx.mock
def test_save_payload_sends_files_when_present() -> None:
    """When `files` is passed (even empty list), the key must appear in the body."""
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_02",
                "version": 1,
                "version_token": "tok2",
                "name": "with-files",
                "verifiers": [],
            },
        )
    )
    file_entry = {
        "path": "scripts/run.sh",
        "executable": True,
        "purpose": "entrypoint",
        "content": "#!/bin/bash\necho hi",
    }
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        client.save_workflow(
            name="with-files",
            description="desc",
            body="body",
            files=[file_entry],
        )

    body = _json.loads(route.calls.last.request.content.decode())
    assert "files" in body
    assert len(body["files"]) == 1
    assert body["files"][0]["path"] == "scripts/run.sh"
    assert body["files"][0]["executable"] is True


@respx.mock
def test_save_payload_sends_empty_files_list() -> None:
    """Passing `files=[]` must send the key with an empty list (server clears the tree)."""
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_03",
                "version": 2,
                "version_token": "tok3",
                "name": "cleared",
                "verifiers": [],
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        client.save_workflow(name="cleared", description="desc", body="body", files=[])

    body = _json.loads(route.calls.last.request.content.decode())
    assert "files" in body
    assert body["files"] == []


@respx.mock
def test_save_payload_omits_image_generators_when_none() -> None:
    """When `image_generators` is not passed, the POST body must NOT contain the key."""
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_ig0",
                "version": 1,
                "version_token": "tok",
                "name": "example",
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        client.save_workflow(name="example", description="desc", body="body")

    body = _json.loads(route.calls.last.request.content.decode())
    assert "image_generators" not in body


@respx.mock
def test_save_payload_sends_image_generators_when_present() -> None:
    """A non-empty `image_generators` list is forwarded verbatim, and the saved
    result parses the bindings back."""
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_ig1",
                "version": 1,
                "version_token": "tok",
                "name": "with-ig",
                "image_generators": [{"name": "hero", "generator_ref": "system:image-standard"}],
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        result = client.save_workflow(
            name="with-ig",
            description="desc",
            body="body",
            image_generators=[{"name": "hero", "generator_ref": "system:image-standard"}],
        )

    body = _json.loads(route.calls.last.request.content.decode())
    assert body["image_generators"] == [{"name": "hero", "generator_ref": "system:image-standard"}]
    assert result.image_generators[0].name == "hero"
    assert result.image_generators[0].generator_ref == "system:image-standard"


@respx.mock
def test_save_payload_sends_empty_image_generators_list() -> None:
    """Passing `image_generators=[]` sends the key with an empty list (clears bindings)."""
    route = respx.post(f"{SERVER}/v1/workflows").mock(
        return_value=httpx.Response(
            201,
            json={
                "workflow_id": "wf_ig2",
                "version": 2,
                "version_token": "tok",
                "name": "cleared-ig",
                "image_generators": [],
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        client.save_workflow(
            name="cleared-ig", description="desc", body="body", image_generators=[]
        )

    body = _json.loads(route.calls.last.request.content.decode())
    assert body["image_generators"] == []


@respx.mock
def test_client_config_parses_ignore_defaults() -> None:
    respx.get(f"{SERVER}/.well-known/goodeye-client-config").mock(
        return_value=httpx.Response(
            200,
            json={
                "workos_client_id": "client_abc",
                "workos_device_authorization_uri": "https://auth.example.com/device",
                "workos_token_uri": "https://auth.example.com/token",
                "ignore_defaults": ["*.pyc", "__pycache__/", ".env"],
            },
        )
    )
    with GoodeyeClient(SERVER) as client:
        config = client.get_client_config()

    assert isinstance(config, ClientConfig)
    assert config.ignore_defaults == ["*.pyc", "__pycache__/", ".env"]


@respx.mock
def test_get_workflow_parses_manifest() -> None:
    respx.get(f"{SERVER}/v1/workflows/wf_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "wf_1",
                "name": "multi-file-skill",
                "version": 3,
                "body": "runbook",
                "description": "a workflow with files",
                "safety_verification_status": "clean",
                "files": [
                    {
                        "path": "scripts/run.sh",
                        "sha256": "aabbcc",
                        "size_bytes": 256,
                        "executable": True,
                        "content_kind": "text",
                        "purpose": "entrypoint",
                        "fetchable_over_mcp": True,
                        "execution_gated": False,
                        "safety_verification_status": "clean",
                    }
                ],
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        result = client.get_workflow("wf_1")

    assert isinstance(result, WorkflowDetail)
    assert result.safety_verification_status == "clean"
    assert len(result.files) == 1
    assert result.files[0].path == "scripts/run.sh"
    assert result.files[0].executable is True
    assert result.files[0].sha256 == "aabbcc"


@respx.mock
def test_get_workflow_file_fetches_single() -> None:
    """get_workflow_file must hit /v1/workflows/{id}/files?path=... and return the envelope."""
    route = respx.get(f"{SERVER}/v1/workflows/wf_1/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "scripts/run.sh",
                "content_kind": "text",
                "size_bytes": 256,
                "executable": True,
                "purpose": "entrypoint",
                "safety_verification_status": "clean",
                "execution_gated": False,
                "content": "#!/bin/bash\necho hi",
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        envelope = client.get_workflow_file("wf_1", "scripts/run.sh")

    params = dict(route.calls.last.request.url.params)
    assert params["path"] == "scripts/run.sh"
    assert isinstance(envelope, dict)
    assert envelope["path"] == "scripts/run.sh"
    assert envelope["content"] == "#!/bin/bash\necho hi"


@respx.mock
def test_get_template_file_raw_returns_bytes() -> None:
    """get_template_file_raw must GET the template file endpoint with format=raw and
    return the response body as raw bytes (not parsed JSON)."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00fake-demo-image"
    route = respx.get(f"{SERVER}/v1/templates/@h/example/files").mock(
        return_value=httpx.Response(
            200,
            content=png_bytes,
            headers={"content-type": "image/png"},
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        data = client.get_template_file_raw("@h/example", "demo/preview.png")

    assert data == png_bytes
    params = dict(route.calls.last.request.url.params)
    assert params["path"] == "demo/preview.png"
    assert params["format"] == "raw"


@respx.mock
def test_get_workflow_files_fetches_batch() -> None:
    """get_workflow_files must hit /v1/workflows/{id}/files with repeated paths= params."""
    route = respx.get(f"{SERVER}/v1/workflows/wf_1/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    {
                        "path": "scripts/run.sh",
                        "content_kind": "text",
                        "size_bytes": 256,
                        "executable": True,
                        "purpose": "entrypoint",
                        "safety_verification_status": "clean",
                        "execution_gated": False,
                        "content": "#!/bin/bash\necho hi",
                    },
                    {
                        "path": "README.md",
                        "content_kind": "text",
                        "size_bytes": 64,
                        "executable": False,
                        "purpose": None,
                        "safety_verification_status": None,
                        "execution_gated": False,
                        "content": "# hello",
                    },
                ]
            },
        )
    )
    with GoodeyeClient(SERVER, api_key="good_live_EXAMPLE_xxx") as client:
        result = client.get_workflow_files("wf_1", ["scripts/run.sh", "README.md"])

    # Verify repeated paths= query params were sent
    raw_query = route.calls.last.request.url.query
    assert b"paths=scripts" in raw_query or "paths" in str(raw_query)
    assert isinstance(result, dict)
    assert "files" in result
    assert len(result["files"]) == 2
    assert result["files"][0]["path"] == "scripts/run.sh"
    assert result["files"][1]["path"] == "README.md"
