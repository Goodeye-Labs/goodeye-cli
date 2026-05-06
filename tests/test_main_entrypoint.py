"""Tests for the installed console-script entrypoint."""

from __future__ import annotations

import importlib
import sys

import pytest

from goodeye_cli.errors import RateLimited


def test_console_script_entrypoint_uses_structured_error_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import goodeye_cli.app as app_mod

    def boom() -> None:
        raise RateLimited(slug="rate_limited", message="slow down", status_code=429)

    monkeypatch.setattr(app_mod, "app", boom)
    sys.modules.pop("goodeye_cli.__main__", None)
    entrypoint = importlib.import_module("goodeye_cli.__main__")

    with pytest.raises(SystemExit) as excinfo:
        entrypoint.main()

    assert excinfo.value.code == 1
    assert "rate_limited" in capsys.readouterr().err
