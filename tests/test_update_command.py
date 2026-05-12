"""Tests for ``goodeye update`` and install-method helpers."""

from __future__ import annotations

import json
from importlib.metadata import Distribution
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import respx
from typer.testing import CliRunner

import goodeye_cli.app as app_module
import goodeye_cli.commands.update as cmd_update_module
import goodeye_cli.update as update_module
from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths
from goodeye_cli.errors import GoodeyeError
from goodeye_cli.update import PYPI_JSON_URL, UpdateCheckResult, detect_install_method, run_update


def _fake_dist(*, direct_url: dict[str, Any] | None) -> SimpleNamespace:
    def read_text(name: str) -> str | None:
        if name != "direct_url.json":
            return None
        if direct_url is None:
            return None
        return json.dumps(direct_url)

    return SimpleNamespace(read_text=read_text)


def _venv_prefix(tmp_path: Any, name: str = "venv") -> tuple[Any, Any]:
    prefix = tmp_path / name
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    pyexe = bindir / "python"
    pyexe.write_text("", encoding="utf-8")
    return prefix, pyexe


@respx.mock
def test_goodeye_update_check_success(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    respx.get(PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"info": {"version": "99.0.0"}})
    )

    result = CliRunner().invoke(app, ["update", "--check"])

    assert result.exit_code == 0, result.output
    assert "Current version:" in result.output
    assert "Latest PyPI version: 99.0.0" in result.output
    assert "Update available:" in result.output


@respx.mock
def test_goodeye_update_check_failure_nonzero_exit(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    respx.get(PYPI_JSON_URL).mock(return_value=httpx.Response(500, json={}))

    result = CliRunner().invoke(app, ["update", "--check"])

    assert result.exit_code == 1
    assert "PyPI" in result.stderr or "HTTP" in result.stderr or "PyPI" in result.output


def test_goodeye_update_when_current_no_subprocess(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))

    def fake_check(**_kwargs: object) -> UpdateCheckResult:
        return UpdateCheckResult(
            current_version="1.0.0",
            latest_version="1.0.0",
            update_available=False,
        )

    ran: list[list[str]] = []

    def fake_run_update(method: str, **_kwargs: object) -> None:
        ran.append([method])

    monkeypatch.setattr(cmd_update_module, "check_for_update", fake_check)
    monkeypatch.setattr(cmd_update_module, "run_update", fake_run_update)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "up to date" in result.output.lower()
    assert ran == []


def test_goodeye_update_uv_tool_path(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setattr(
        cmd_update_module,
        "check_for_update",
        lambda **_: UpdateCheckResult(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        ),
    )
    monkeypatch.setattr(cmd_update_module, "detect_install_method", lambda: "uv_tool")

    captured: list[list[str]] = []

    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        captured.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        cmd_update_module,
        "run_update",
        lambda method, **kw: run_update(method, run_cmd=fake_run_cmd, which=lambda _: "/fake/uv"),
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Updated goodeye" in result.output
    assert captured == [["/fake/uv", "tool", "upgrade", "goodeye"]]


def test_goodeye_update_pipx_path(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setattr(
        cmd_update_module,
        "check_for_update",
        lambda **_: UpdateCheckResult(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        ),
    )
    monkeypatch.setattr(cmd_update_module, "detect_install_method", lambda: "pipx")

    captured: list[list[str]] = []

    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        captured.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        cmd_update_module,
        "run_update",
        lambda method, **kw: run_update(method, run_cmd=fake_run_cmd, which=lambda _: "/x/pipx"),
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert captured == [["/x/pipx", "upgrade", "goodeye"]]


def test_goodeye_update_pip_path(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setattr(
        cmd_update_module,
        "check_for_update",
        lambda **_: UpdateCheckResult(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        ),
    )
    monkeypatch.setattr(cmd_update_module, "detect_install_method", lambda: "pip")

    captured: list[list[str]] = []

    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        captured.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")

    py = "/tmp/fakepython"
    monkeypatch.setattr(
        cmd_update_module,
        "run_update",
        lambda method, **kw: run_update(method, run_cmd=fake_run_cmd, python_executable=py),
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert captured == [[py, "-m", "pip", "install", "--upgrade", "goodeye"]]


def test_goodeye_update_unsupported_install(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setattr(
        cmd_update_module,
        "check_for_update",
        lambda **_: UpdateCheckResult(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
        ),
    )
    monkeypatch.setattr(cmd_update_module, "detect_install_method", lambda: "unsupported")

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert isinstance(result.exception, GoodeyeError)
    assert result.exception.slug == "update_unsupported_install"


def test_run_update_subprocess_failure_includes_command_and_hint() -> None:
    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        return CompletedProcess(argv, 2, stdout=None, stderr=None)

    with pytest.raises(GoodeyeError) as excinfo:
        run_update("pip", run_cmd=fake_run_cmd, python_executable="/fake/py")

    err = excinfo.value
    assert err.slug == "update_command_failed"
    assert "exit 2" in err.message
    assert "pip install" in err.message or "-m pip" in err.message
    assert "above" in err.message
    assert err.hint is not None
    assert "uv tool upgrade" in err.hint


def test_run_update_oserror_from_subprocess() -> None:
    def boom(_argv: list[str]) -> CompletedProcess[str]:
        raise FileNotFoundError("no such file")

    with pytest.raises(GoodeyeError) as excinfo:
        run_update(
            "uv_tool",
            run_cmd=boom,
            which=lambda _: "/fake/uv",
        )

    assert excinfo.value.slug == "update_command_os_error"
    assert "/fake/uv" in excinfo.value.message
    assert excinfo.value.hint


def test_run_update_missing_uv_on_path() -> None:
    with pytest.raises(GoodeyeError) as excinfo:
        run_update(
            "uv_tool",
            which=lambda _: None,
            run_cmd=lambda _: CompletedProcess([], 0, "", ""),
        )

    assert excinfo.value.slug == "update_tool_missing"


def test_detect_install_method_uv_prefix(tmp_path: Any) -> None:
    prefix = tmp_path / "uv" / "tools" / "goodeye"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    pyexe = bindir / "python"
    pyexe.write_text("", encoding="utf-8")
    dist = _fake_dist(direct_url=None)
    assert (
        detect_install_method(
            sys_executable=str(pyexe),
            sys_prefix=str(prefix),
            distribution=cast(Distribution, dist),
        )
        == "uv_tool"
    )


def test_detect_install_method_pipx_prefix(tmp_path: Any) -> None:
    prefix = tmp_path / "pipx" / "venvs" / "goodeye"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    pyexe = bindir / "python"
    pyexe.write_text("", encoding="utf-8")
    dist = _fake_dist(direct_url=None)
    assert (
        detect_install_method(
            sys_executable=str(pyexe),
            sys_prefix=str(prefix),
            distribution=cast(Distribution, dist),
        )
        == "pipx"
    )


def test_detect_install_method_editable_direct_url_unsupported(tmp_path: Any) -> None:
    prefix, pyexe = _venv_prefix(tmp_path)
    dist = _fake_dist(direct_url={"url": "file:///src/goodeye", "dir_info": {"editable": True}})
    assert (
        detect_install_method(
            sys_executable=str(pyexe),
            sys_prefix=str(prefix),
            distribution=cast(Distribution, dist),
        )
        == "unsupported"
    )


def test_detect_install_method_pip_without_direct_url_legacy(tmp_path: Any) -> None:
    prefix, pyexe = _venv_prefix(tmp_path)
    dist = _fake_dist(direct_url=None)
    assert (
        detect_install_method(
            sys_executable=str(pyexe),
            sys_prefix=str(prefix),
            distribution=cast(Distribution, dist),
        )
        == "pip"
    )


def test_detect_install_method_pip_with_pypi_direct_url(tmp_path: Any) -> None:
    prefix, pyexe = _venv_prefix(tmp_path)
    dist = _fake_dist(
        direct_url={
            "url": "https://files.pythonhosted.org/packages/ab/goodeye-0.2.0-py3-none-any.whl",
            "archive_info": {"hash": "sha256:abc"},
        }
    )
    assert (
        detect_install_method(
            sys_executable=str(pyexe),
            sys_prefix=str(prefix),
            distribution=cast(Distribution, dist),
        )
        == "pip"
    )


def test_should_suppress_only_when_update_is_first_arg() -> None:
    assert update_module.should_suppress_background_notice(["update"], {}) is True
    assert update_module.should_suppress_background_notice(["templates", "update"], {}) is False


def test_update_invocation_suppresses_background_notice(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("CI", raising=False)

    monkeypatch.setattr(app_module, "_get_background_notice_args", lambda: ["update"])

    def fake_check(*_a: object, **_k: object) -> UpdateCheckResult | None:
        return UpdateCheckResult(
            current_version="0.7.1",
            latest_version="0.7.2",
            update_available=True,
        )

    monkeypatch.setattr(update_module, "check_for_update_background", fake_check)

    def fake_check_for_update(**_kwargs: object) -> UpdateCheckResult:
        return UpdateCheckResult(
            current_version="0.7.2",
            latest_version="0.7.2",
            update_available=False,
        )

    monkeypatch.setattr(cmd_update_module, "check_for_update", fake_check_for_update)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "goodeye 0.7.2 is available" not in result.stderr
    assert "goodeye 0.7.2 is available" not in result.stdout


def test_upgrade_alias_dispatches_to_update(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.setattr(
        cmd_update_module,
        "check_for_update",
        lambda **_: UpdateCheckResult(
            current_version="1.0.0",
            latest_version="1.0.0",
            update_available=False,
        ),
    )

    result = CliRunner().invoke(app, ["upgrade"])

    assert result.exit_code == 0, result.output
    assert "up to date" in result.output.lower()


def test_upgrade_alias_hidden_from_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "update" in result.output
    assert "upgrade" not in result.output
