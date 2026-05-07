"""Tests for CLI update checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import GoodeyeError
from goodeye_cli.update import (
    PYPI_JSON_URL,
    UPDATE_CHECK_INTERVAL_SECONDS,
    check_for_update,
    check_for_update_background,
    fetch_latest_pypi_version,
    is_update_available,
    load_update_cache,
    save_update_cache,
)


@respx.mock
def test_fetch_latest_pypi_version_parses_info_version() -> None:
    route = respx.get(PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"info": {"version": "0.7.2"}})
    )

    assert fetch_latest_pypi_version() == "0.7.2"
    assert route.called


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"info": {}}),
        httpx.Response(200, json={"info": {"version": 702}}),
        httpx.Response(500, json={"message": "nope"}),
    ],
)
@respx.mock
def test_fetch_latest_pypi_version_raises_for_invalid_response(response: httpx.Response) -> None:
    respx.get(PYPI_JSON_URL).mock(return_value=response)

    with pytest.raises(GoodeyeError, match="PyPI"):
        fetch_latest_pypi_version()


@respx.mock
def test_check_for_update_surfaces_pypi_failures() -> None:
    respx.get(PYPI_JSON_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GoodeyeError, match="PyPI"):
        check_for_update(current_version="0.7.1")


def test_is_update_available_compares_versions() -> None:
    assert is_update_available("0.7.1", "0.7.2") is True
    assert is_update_available("0.7.1", "0.7.1") is False
    assert is_update_available("0.7.2", "0.7.1") is False


def test_is_update_available_stable_local_ignores_remote_prerelease() -> None:
    assert is_update_available("0.7.1", "0.7.2rc1") is False


def test_is_update_available_prerelease_local_accepts_newer_prerelease() -> None:
    assert is_update_available("0.7.2b1", "0.7.2rc1") is True


def test_update_cache_roundtrips(
    tmp_config_paths: ConfigPaths,
) -> None:
    checked_at = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)

    path = save_update_cache("0.7.2", paths=tmp_config_paths, checked_at=checked_at)

    assert path == tmp_config_paths.update_check_file
    assert load_update_cache(tmp_config_paths) == {
        "checked_at": checked_at.isoformat(),
        "latest_version": "0.7.2",
        "pypi_url": PYPI_JSON_URL,
    }


@respx.mock(assert_all_called=False)
def test_background_check_uses_fresh_cache_without_hitting_pypi(
    tmp_config_paths: ConfigPaths,
) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    save_update_cache("0.7.2", paths=tmp_config_paths, checked_at=now - timedelta(minutes=5))
    route = respx.get(PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"info": {"version": "0.7.3"}})
    )

    result = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        now=now,
    )

    assert result is not None
    assert result.latest_version == "0.7.2"
    assert result.update_available is True
    assert route.called is False


@respx.mock
def test_background_check_refreshes_stale_cache_and_writes_result(
    tmp_config_paths: ConfigPaths,
) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    stale_checked_at = now - timedelta(seconds=UPDATE_CHECK_INTERVAL_SECONDS + 1)
    save_update_cache("0.7.2", paths=tmp_config_paths, checked_at=stale_checked_at)
    respx.get(PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"info": {"version": "0.7.3"}})
    )

    result = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        now=now,
    )

    assert result is not None
    assert result.latest_version == "0.7.3"
    assert result.update_available is True
    assert load_update_cache(tmp_config_paths) == {
        "checked_at": now.isoformat(),
        "latest_version": "0.7.3",
        "pypi_url": PYPI_JSON_URL,
    }


def test_background_check_returns_none_on_network_failure(
    tmp_config_paths: ConfigPaths,
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    result = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        transport=httpx.MockTransport(fail),
    )

    assert result is None


def test_background_check_returns_none_on_parse_failure(
    tmp_config_paths: ConfigPaths,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    result = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        transport=httpx.MockTransport(respond),
    )

    assert result is None


def test_background_check_writes_failure_marker_and_skips_pypi_until_stale(
    tmp_config_paths: ConfigPaths,
) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    calls = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(fail)

    first = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        now=now,
        transport=transport,
    )
    assert first is None
    assert calls == 1
    assert load_update_cache(tmp_config_paths) == {
        "checked_at": now.isoformat(),
        "latest_version": None,
        "pypi_url": PYPI_JSON_URL,
    }

    second = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        now=now + timedelta(minutes=5),
        transport=transport,
    )
    assert second is None
    assert calls == 1, "fresh failure marker should suppress repeat PyPI calls"

    third = check_for_update_background(
        current_version="0.7.1",
        paths=tmp_config_paths,
        now=now + timedelta(seconds=UPDATE_CHECK_INTERVAL_SECONDS + 1),
        transport=transport,
    )
    assert third is None
    assert calls == 2, "stale failure marker should allow another PyPI attempt"


def test_background_check_returns_none_on_cache_write_failure(tmp_path: Path) -> None:
    config_dir = tmp_path / "not-a-directory"
    config_dir.write_text("blocking file", encoding="utf-8")
    paths = ConfigPaths(
        config_dir=config_dir,
        credentials_file=config_dir / "credentials.json",
        config_file=config_dir / "config.json",
        update_check_file=config_dir / "update-check.json",
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"info": {"version": "0.7.2"}})

    result = check_for_update_background(
        current_version="0.7.1",
        paths=paths,
        transport=httpx.MockTransport(respond),
    )

    assert result is None
