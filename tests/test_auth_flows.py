"""Tests for device-code login flow."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx

from goodeye_cli.auth_flows import device_code_login
from goodeye_cli.errors import GoodeyeError, InvalidCredentials

SERVER = "https://example.test"
DEVICE_URI = "https://api.workos.com/user_management/authorize/device"
TOKEN_URI = "https://api.workos.com/user_management/authenticate"


class FakeClock:
    """Simple monotonic clock that advances on every ``sleep`` call."""

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


@respx.mock
def test_device_code_login_happy_path_after_pending() -> None:
    respx.post(DEVICE_URI).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dev_code",
                "user_code": "ABCD-1234",
                "verification_uri": "https://workos.com/device",
                "verification_uri_complete": "https://workos.com/device?user_code=ABCD-1234",
                "interval": 1,
                "expires_in": 600,
            },
        )
    )

    def token_response(count: Iterator[int]) -> httpx.Response:
        n = next(count)
        if n < 2:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(200, json={"access_token": "jwt_tok"})

    counter = iter(range(100))
    respx.post(TOKEN_URI).mock(side_effect=lambda request: token_response(counter))

    respx.post(f"{SERVER}/v1/auth/exchange").mock(
        return_value=httpx.Response(
            200,
            json={"api_key": "good_live_EXAMPLE", "key_id": "key_01"},
        )
    )

    clock = FakeClock()
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    api_key = device_code_login(
        SERVER,
        workos_client_id="client_X",
        workos_device_authorization_uri=DEVICE_URI,
        workos_token_uri=TOKEN_URI,
        hostname="laptop",
        open_browser=fake_open,
        sleep=clock.sleep,
        clock=clock.monotonic,
    )
    assert api_key == "good_live_EXAMPLE"
    assert opened == ["https://workos.com/device?user_code=ABCD-1234"]


@respx.mock
def test_device_code_login_handles_slow_down() -> None:
    respx.post(DEVICE_URI).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dc",
                "user_code": "A-B",
                "verification_uri": "https://workos.com/device",
                "verification_uri_complete": "https://workos.com/device?user_code=A-B",
                "interval": 1,
                "expires_in": 600,
            },
        )
    )

    responses = iter(
        [
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(200, json={"access_token": "jwt_tok"}),
        ]
    )
    respx.post(TOKEN_URI).mock(side_effect=lambda request: next(responses))
    respx.post(f"{SERVER}/v1/auth/exchange").mock(
        return_value=httpx.Response(200, json={"api_key": "good_live_EXAMPLE", "key_id": "k"})
    )

    clock = FakeClock()
    api_key = device_code_login(
        SERVER,
        workos_client_id="client_X",
        workos_device_authorization_uri=DEVICE_URI,
        workos_token_uri=TOKEN_URI,
        open_browser=lambda _url: True,
        sleep=clock.sleep,
        clock=clock.monotonic,
    )
    assert api_key == "good_live_EXAMPLE"


@respx.mock
def test_device_code_login_fatal_error() -> None:
    respx.post(DEVICE_URI).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dc",
                "user_code": "A",
                "verification_uri": "v",
                "verification_uri_complete": "v",
                "interval": 1,
                "expires_in": 60,
            },
        )
    )
    respx.post(TOKEN_URI).mock(
        return_value=httpx.Response(
            400, json={"error": "access_denied", "error_description": "user declined"}
        )
    )
    with pytest.raises(InvalidCredentials) as exc_info:
        device_code_login(
            SERVER,
            workos_client_id="client_X",
            workos_device_authorization_uri=DEVICE_URI,
            workos_token_uri=TOKEN_URI,
            open_browser=lambda _u: True,
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )
    assert exc_info.value.slug == "access_denied"


@respx.mock
def test_device_code_login_timeout() -> None:
    respx.post(DEVICE_URI).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dc",
                "user_code": "A",
                "verification_uri": "v",
                "verification_uri_complete": "v",
                "interval": 1,
                "expires_in": 2,
            },
        )
    )
    respx.post(TOKEN_URI).mock(
        return_value=httpx.Response(400, json={"error": "authorization_pending"})
    )
    clock = FakeClock()
    with pytest.raises(GoodeyeError) as exc_info:
        device_code_login(
            SERVER,
            workos_client_id="client_X",
            workos_device_authorization_uri=DEVICE_URI,
            workos_token_uri=TOKEN_URI,
            open_browser=lambda _u: True,
            sleep=clock.sleep,
            clock=clock.monotonic,
            max_wait_s=3,
        )
    assert exc_info.value.slug == "auth_required"
