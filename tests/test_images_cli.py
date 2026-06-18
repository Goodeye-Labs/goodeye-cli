"""Tests for ``goodeye images`` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import httpx
import respx
from typer.testing import CliRunner

from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths, save_credentials

SERVER = "https://example.test"
runner = CliRunner()

IMAGE_ID = "img_01aaaaaaaaaaaaaaaaaaaaaaaaaa"


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


def _image_dict(
    *,
    visibility: str = "private",
    expires_at: str | None = None,
    source: str = "upload",
) -> dict:
    return {
        "id": IMAGE_ID,
        "token": "tok_EXAMPLE",
        "url": f"https://example.test/images/{IMAGE_ID}",
        "visibility": visibility,
        "expires_at": expires_at,
        "size_bytes": 12345,
        "content_type": "image/png",
        "source": source,
        "created_at": "2026-06-16T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@respx.mock
def test_images_upload_posts_multipart(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """Upload sends the file bytes as multipart and prints the id/url/token."""
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    def respond(request: httpx.Request) -> httpx.Response:
        # Confirm multipart content-type header is set.
        assert "multipart/form-data" in request.headers.get("content-type", "")
        return httpx.Response(201, json=_image_dict())

    respx.post(f"{SERVER}/v1/images").mock(side_effect=respond)
    result = runner.invoke(app, ["images", "upload", str(img_file)])
    assert result.exit_code == 0, result.output
    assert IMAGE_ID in result.output
    assert "Uploaded" in result.output


@respx.mock
def test_images_upload_with_visibility_public(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")

    def respond(request: httpx.Request) -> httpx.Response:
        # Confirm visibility=public in multipart form data.
        body_text = request.content.decode("latin-1")
        assert "public" in body_text
        return httpx.Response(201, json=_image_dict(visibility="public"))

    respx.post(f"{SERVER}/v1/images").mock(side_effect=respond)
    result = runner.invoke(app, ["images", "upload", str(img_file), "--visibility", "public"])
    assert result.exit_code == 0, result.output
    assert "public" in result.output


@respx.mock
def test_images_upload_with_ttl(tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")

    def respond(request: httpx.Request) -> httpx.Response:
        body_text = request.content.decode("latin-1")
        assert "3600" in body_text
        return httpx.Response(201, json=_image_dict(expires_at="2026-06-16T01:00:00+00:00"))

    respx.post(f"{SERVER}/v1/images").mock(side_effect=respond)
    result = runner.invoke(app, ["images", "upload", str(img_file), "--ttl", "3600"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_images_upload_json_output(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")
    respx.post(f"{SERVER}/v1/images").mock(return_value=httpx.Response(201, json=_image_dict()))
    result = runner.invoke(app, ["images", "upload", str(img_file), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == IMAGE_ID


def test_images_upload_missing_file(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "upload", str(tmp_path / "nonexistent.png")])
    assert result.exit_code != 0


def test_images_upload_requires_auth(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")
    result = runner.invoke(app, ["images", "upload", str(img_file)])
    assert result.exit_code != 0


@respx.mock
def test_images_upload_file_too_large_surfaces_message(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A 413 file_too_large from the server is surfaced cleanly with a non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")
    respx.post(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            413,
            json={
                "error": "file_too_large",
                "message": "image file exceeds the maximum allowed size",
            },
        )
    )
    result = runner.invoke(app, ["images", "upload", str(img_file)])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "exceeds the maximum allowed size" in str(result.exception)


@respx.mock
def test_images_upload_dimensions_exceeded_surfaces_message(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A 413 image_dimensions_exceeded from the server is surfaced cleanly with a non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "huge.png"
    img_file.write_bytes(b"\x89PNG")
    respx.post(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            413,
            json={
                "error": "image_dimensions_exceeded",
                "message": "image resolution exceeds the maximum allowed pixels",
            },
        )
    )
    result = runner.invoke(app, ["images", "upload", str(img_file)])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "exceeds the maximum allowed pixels" in str(result.exception)


@respx.mock
def test_images_upload_unsupported_type_surfaces_message(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A 415 unsupported_image_type from the server is surfaced cleanly with a non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "doc.svg"
    img_file.write_bytes(b"<svg></svg>")
    respx.post(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            415,
            json={
                "error": "unsupported_image_type",
                "message": "image must be a PNG, JPEG, WebP, or GIF",
            },
        )
    )
    result = runner.invoke(app, ["images", "upload", str(img_file)])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "PNG, JPEG, WebP, or GIF" in str(result.exception)


@respx.mock
def test_images_upload_public_content_rejected_surfaces_message(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A 422 image_content_rejected on a public upload is surfaced with a non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")
    respx.post(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            422,
            json={
                "error": "image_content_rejected",
                "message": "the image was rejected because it contains disallowed content",
            },
        )
    )
    result = runner.invoke(app, ["images", "upload", str(img_file), "--visibility", "public"])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "disallowed content" in str(result.exception)


@respx.mock
def test_images_upload_public_screening_unavailable_surfaces_message(
    tmp_path: Path, tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A 503 image_screening_unavailable on a public upload is surfaced with a non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")
    respx.post(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            503,
            json={
                "error": "image_screening_unavailable",
                "message": (
                    "the image could not be screened for disallowed content; try again shortly"
                ),
            },
        )
    )
    result = runner.invoke(app, ["images", "upload", str(img_file), "--visibility", "public"])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "try again shortly" in str(result.exception)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@respx.mock
def test_images_list_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            200,
            json={"items": [_image_dict()], "next_cursor": "cur1"},
        )
    )
    result = runner.invoke(app, ["images", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "items" in data
    assert data["next_cursor"] == "cur1"
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == IMAGE_ID


@respx.mock
def test_images_list_table(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(
            200,
            json={"items": [_image_dict()], "next_cursor": None},
        )
    )
    result = runner.invoke(app, ["images", "list", "--table"])
    assert result.exit_code == 0, result.output
    # Rich truncates long IDs in narrow terminals; check the prefix instead.
    assert "img_01" in result.output
    assert "private" in result.output


@respx.mock
def test_images_list_empty(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/images").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
    )
    result = runner.invoke(app, ["images", "list", "--table"])
    assert result.exit_code == 0, result.output
    assert "No images" in result.output


@respx.mock
def test_images_list_forwards_source_and_visibility(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("source") == "upload"
        assert request.url.params.get("visibility") == "public"
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    respx.get(f"{SERVER}/v1/images").mock(side_effect=check)
    result = runner.invoke(
        app,
        ["images", "list", "--source", "upload", "--visibility", "public", "--json"],
    )
    assert result.exit_code == 0, result.output


@respx.mock
def test_images_list_all_follows_cursor(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    img2 = dict(_image_dict())
    img2["id"] = "img_02bbbbbbbbbbbbbbbbbbbbbbbbbb"
    route = respx.get(f"{SERVER}/v1/images").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_image_dict()], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [img2], "next_cursor": None}),
        ]
    )
    result = runner.invoke(app, ["images", "list", "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 2
    data = json.loads(result.output)
    assert len(data["items"]) == 2


def test_images_list_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "list"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@respx.mock
def test_images_get_prints_details(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/images/{IMAGE_ID}").mock(
        return_value=httpx.Response(200, json=_image_dict())
    )
    result = runner.invoke(app, ["images", "get", IMAGE_ID])
    assert result.exit_code == 0, result.output
    assert IMAGE_ID in result.output
    assert "private" in result.output


@respx.mock
def test_images_get_json(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.get(f"{SERVER}/v1/images/{IMAGE_ID}").mock(
        return_value=httpx.Response(200, json=_image_dict())
    )
    result = runner.invoke(app, ["images", "get", IMAGE_ID, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == IMAGE_ID


def test_images_get_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "get", IMAGE_ID])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@respx.mock
def test_images_update_visibility(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["visibility"] == "public"
        return httpx.Response(200, json=_image_dict(visibility="public"))

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "update", IMAGE_ID, "--visibility", "public"])
    assert result.exit_code == 0, result.output
    assert "public" in result.output


@respx.mock
def test_images_update_ttl(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["ttl_seconds"] == 7200
        return httpx.Response(200, json=_image_dict(expires_at="2026-06-16T02:00:00+00:00"))

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "update", IMAGE_ID, "--ttl", "7200"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_images_update_permanent(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("permanent") is True
        return httpx.Response(200, json=_image_dict())

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "update", IMAGE_ID, "--permanent"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_images_update_public_content_rejected_surfaces_message(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """A 422 image_content_rejected on a private-to-public flip is surfaced with a non-zero exit."""
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(
        return_value=httpx.Response(
            422,
            json={
                "error": "image_content_rejected",
                "message": "the image was rejected because it contains disallowed content",
            },
        )
    )
    result = runner.invoke(app, ["images", "update", IMAGE_ID, "--visibility", "public"])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "disallowed content" in str(result.exception)


def test_images_update_ttl_and_permanent_rejected(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """--ttl and --permanent together must be rejected before any HTTP call."""
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "update", IMAGE_ID, "--ttl", "3600", "--permanent"])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "mutually exclusive" in str(result.exception).lower()


def test_images_update_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "update", IMAGE_ID, "--visibility", "public"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@respx.mock
def test_images_delete_auto_approves_when_not_tty(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    """CliRunner stdin is not a TTY, so confirm auto-approves."""
    _setup_creds(monkeypatch, tmp_config_paths)
    route = respx.delete(f"{SERVER}/v1/images/{IMAGE_ID}").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["images", "delete", IMAGE_ID])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "Deleted" in result.output


def test_images_delete_human_decline_exits_zero(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    with mock.patch("goodeye_cli.commands.images.confirm_destructive", return_value=False):
        result = runner.invoke(app, ["images", "delete", IMAGE_ID])
    assert result.exit_code == 0, result.output
    assert "Cancelled" in result.output


@respx.mock
def test_images_delete_yes_flag_skips_confirm(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    respx.delete(f"{SERVER}/v1/images/{IMAGE_ID}").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["images", "delete", IMAGE_ID, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output


def test_images_delete_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "delete", IMAGE_ID])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# share / unshare
# ---------------------------------------------------------------------------


@respx.mock
def test_images_share_sets_visibility_public(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("visibility") == "public"
        return httpx.Response(200, json=_image_dict(visibility="public"))

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "share", IMAGE_ID])
    assert result.exit_code == 0, result.output
    assert "public" in result.output


@respx.mock
def test_images_unshare_sets_visibility_private(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("visibility") == "private"
        return httpx.Response(200, json=_image_dict(visibility="private"))

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "unshare", IMAGE_ID])
    assert result.exit_code == 0, result.output
    assert "private" in result.output


# ---------------------------------------------------------------------------
# set-ttl
# ---------------------------------------------------------------------------


@respx.mock
def test_images_set_ttl_integer_sends_ttl_seconds(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("ttl_seconds") == 86400
        assert "permanent" not in body
        return httpx.Response(200, json=_image_dict(expires_at="2026-06-17T00:00:00+00:00"))

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "set-ttl", IMAGE_ID, "86400"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_images_set_ttl_permanent_sends_permanent_true(
    tmp_config_paths: ConfigPaths, monkeypatch
) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)

    def check(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("permanent") is True
        assert "ttl_seconds" not in body
        return httpx.Response(200, json=_image_dict())

    respx.patch(f"{SERVER}/v1/images/{IMAGE_ID}").mock(side_effect=check)
    result = runner.invoke(app, ["images", "set-ttl", IMAGE_ID, "permanent"])
    assert result.exit_code == 0, result.output
    assert "never" in result.output


def test_images_set_ttl_invalid_value_rejected(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "set-ttl", IMAGE_ID, "notanumber"])
    assert result.exit_code != 0
    assert result.exception is not None


def test_images_set_ttl_zero_rejected(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "set-ttl", IMAGE_ID, "0"])
    assert result.exit_code != 0
    assert result.exception is not None


def test_images_set_ttl_requires_auth(tmp_config_paths: ConfigPaths, monkeypatch) -> None:
    _setup_no_creds(monkeypatch, tmp_config_paths)
    result = runner.invoke(app, ["images", "set-ttl", IMAGE_ID, "3600"])
    assert result.exit_code != 0
