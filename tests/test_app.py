"""Tests for the top-level error backstop in ``app.main()``.

The backstop lives in ``main()``, not in the Typer ``app`` callable, so the
Typer ``CliRunner`` (which invokes ``app`` directly) cannot exercise it. These
tests replace the module-level ``app`` with a function that raises and call
``main()`` itself.
"""

from __future__ import annotations

import httpx
import pytest

import goodeye_cli.app as app_module
from goodeye_cli.errors import NetworkError


def test_main_renders_network_error_humanely(monkeypatch, capsys) -> None:
    def boom() -> None:
        raise NetworkError(
            slug="network_error",
            message="Could not reach Goodeye. Check your connection and try again.",
            hint="server: https://api.goodeye.dev",
        )

    monkeypatch.setattr(app_module, "app", boom)
    with pytest.raises(SystemExit) as exc_info:
        app_module.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "network_error" in err
    assert "Could not reach Goodeye." in err
    assert "Traceback" not in err


def test_main_backstops_unexpected_exception(monkeypatch, capsys) -> None:
    def boom() -> None:
        raise ValueError("kaboom")

    monkeypatch.delenv("GOODEYE_DEBUG", raising=False)
    monkeypatch.setattr(app_module, "app", boom)
    with pytest.raises(SystemExit) as exc_info:
        app_module.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert "ValueError" in err
    assert "GOODEYE_DEBUG=1" in err
    assert "Traceback" not in err


def test_main_debug_env_reraises_for_traceback(monkeypatch) -> None:
    def boom() -> None:
        raise ValueError("kaboom")

    monkeypatch.setenv("GOODEYE_DEBUG", "1")
    monkeypatch.setattr(app_module, "app", boom)
    with pytest.raises(ValueError, match="kaboom"):
        app_module.main()


def test_main_debug_env_surfaces_cause_for_network_error(monkeypatch, capsys) -> None:
    # The whole fix is about network errors; when debugging "could not reach
    # Goodeye", the underlying transport reason must be visible.
    def boom() -> None:
        try:
            raise httpx.ConnectError("dns boom")
        except httpx.ConnectError as exc:
            raise NetworkError(
                slug="network_error",
                message="Could not reach Goodeye.",
                hint="server: https://api.goodeye.dev",
            ) from exc

    monkeypatch.setenv("GOODEYE_DEBUG", "1")
    monkeypatch.setattr(app_module, "app", boom)
    with pytest.raises(SystemExit) as exc_info:
        app_module.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "network_error" in err
    assert "debug: ConnectError: dns boom" in err
