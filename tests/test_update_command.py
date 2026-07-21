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
from goodeye_cli.update import (
    PYPI_JSON_URL,
    UpdateCheckResult,
    blocked_upgrade_hint,
    detect_install_method,
    read_installed_version,
    run_update,
)


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
    monkeypatch.setattr(cmd_update_module, "read_installed_version", lambda **_: "0.2.0")

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
    monkeypatch.setattr(cmd_update_module, "read_installed_version", lambda **_: "0.2.0")

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
    monkeypatch.setattr(cmd_update_module, "read_installed_version", lambda **_: "0.2.0")

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


def _stub_available_check(current: str, latest: str) -> Any:
    return lambda **_: UpdateCheckResult(
        current_version=current,
        latest_version=latest,
        update_available=True,
    )


def _arrange_update(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: str,
    latest: str,
    method: str,
    installed_after: str | None,
) -> None:
    monkeypatch.setattr(
        cmd_update_module, "check_for_update", _stub_available_check(current, latest)
    )
    monkeypatch.setattr(cmd_update_module, "detect_install_method", lambda: method)
    monkeypatch.setattr(cmd_update_module, "run_update", lambda _method, **_kw: None)
    monkeypatch.setattr(cmd_update_module, "read_installed_version", lambda **_kw: installed_after)


def test_update_pinned_no_op_does_not_claim_success(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    # uv refused the upgrade (exact version pin): installed version is unchanged.
    _arrange_update(
        monkeypatch,
        current="0.24.0",
        latest="0.25.0",
        method="uv_tool",
        installed_after="0.24.0",
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Updated goodeye" not in result.output
    assert "still 0.24.0" in result.output
    assert "pinned" in result.output.lower()
    assert "uv tool install --force goodeye@latest" in result.output


def test_update_real_upgrade_reports_actually_installed_version_not_target(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    # The version that actually lands differs from the intended target; the
    # success line must reflect what was installed, not the target we aimed at.
    _arrange_update(
        monkeypatch,
        current="0.24.0",
        latest="0.25.0",
        method="uv_tool",
        installed_after="0.26.0",
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Updated goodeye from 0.24.0 to 0.26.0" in result.output
    # The success line reports what was installed, not the intended target. (The
    # earlier "Updating ... to 0.25.0" intent line naming the target is fine.)
    assert "Updated goodeye from 0.24.0 to 0.25.0" not in result.output


def test_update_real_upgrade_reports_installed_version(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    _arrange_update(
        monkeypatch,
        current="0.24.0",
        latest="0.25.0",
        method="uv_tool",
        installed_after="0.25.0",
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Updated goodeye from 0.24.0 to 0.25.0" in result.output


def test_update_unverifiable_outcome_does_not_claim_success(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    # The version probe could not read the installed version afterward.
    _arrange_update(
        monkeypatch,
        current="0.24.0",
        latest="0.25.0",
        method="uv_tool",
        installed_after=None,
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Updated goodeye" not in result.output
    assert "goodeye --version" in result.output


def test_blocked_upgrade_hint_uv_tool_mentions_pin_and_force_reinstall() -> None:
    hint = blocked_upgrade_hint("uv_tool")

    assert "pinned" in hint.lower()
    assert "uv tool install --force goodeye@latest" in hint


def test_blocked_upgrade_hint_pipx_force_reinstall() -> None:
    hint = blocked_upgrade_hint("pipx")

    assert "pipx install --force goodeye" in hint


def test_blocked_upgrade_hint_pip_force_reinstall() -> None:
    hint = blocked_upgrade_hint("pip", python_executable="/fake/py")

    assert "/fake/py" in hint
    assert "--force-reinstall" in hint


def test_read_installed_version_parses_stdout() -> None:
    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        assert argv[0] == "/fake/py"
        return CompletedProcess(argv, 0, stdout="0.25.0\n", stderr="")

    assert read_installed_version(python_executable="/fake/py", run_cmd=fake_run_cmd) == "0.25.0"


def test_read_installed_version_none_on_nonzero_exit() -> None:
    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        return CompletedProcess(argv, 1, stdout="", stderr="boom")

    assert read_installed_version(python_executable="/fake/py", run_cmd=fake_run_cmd) is None


def test_read_installed_version_none_on_empty_output() -> None:
    def fake_run_cmd(argv: list[str]) -> CompletedProcess[str]:
        return CompletedProcess(argv, 0, stdout="  \n", stderr="")

    assert read_installed_version(python_executable="/fake/py", run_cmd=fake_run_cmd) is None


def test_read_installed_version_none_on_oserror() -> None:
    def boom(_argv: list[str]) -> CompletedProcess[str]:
        raise FileNotFoundError("no such python")

    assert read_installed_version(python_executable="/fake/py", run_cmd=boom) is None
