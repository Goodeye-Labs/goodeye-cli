"""Tests for the best-effort automatic-pull tail and its synchronous gate."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from typer.testing import CliRunner

import goodeye_cli.app as app_module
from goodeye_cli import background as background_sync
from goodeye_cli import sync
from goodeye_cli.app import app
from goodeye_cli.config import ConfigPaths

SERVER = "https://example.test"
DEFAULT_EMAIL = "owner@example.com"


def _enabled_config() -> sync.SyncConfig:
    return sync.SyncConfig(
        targets=[sync.SyncTarget(path="~/skills", scope="owned")],
        auto=sync.AutoConfig(enabled=True, interval_seconds=3600),
    )


# ----- synchronous gate -----


def test_gate_registers_for_eligible_invocation() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    assert (
        background_sync.should_run_auto_pull(
            ["logout"],
            {},
            _enabled_config(),
            sync.SyncState(),
            authenticated=True,
            now=now,
        )
        is True
    )


@pytest.mark.parametrize(
    "args,env",
    [
        (["whoami", "--json"], {}),
        (["--version"], {}),
        (["logout"], {"CI": "1"}),
        (["workflows", "sync", "pull"], {}),
    ],
)
def test_gate_skips_suppressed_invocations(args: list[str], env: dict[str, str]) -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    assert (
        background_sync.should_run_auto_pull(
            args, env, _enabled_config(), sync.SyncState(), authenticated=True, now=now
        )
        is False
    )


def test_gate_skips_when_unauthenticated() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    assert (
        background_sync.should_run_auto_pull(
            ["logout"], {}, _enabled_config(), sync.SyncState(), authenticated=False, now=now
        )
        is False
    )


def test_gate_skips_when_auto_disabled() -> None:
    """An explicit `auto off` stops the tail even with targets configured."""
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    config = sync.SyncConfig(
        targets=[sync.SyncTarget(path="~/skills", scope="owned")],
        auto=sync.AutoConfig(enabled=False, explicitly_set=True),
    )
    assert (
        background_sync.should_run_auto_pull(
            ["logout"], {}, config, sync.SyncState(), authenticated=True, now=now
        )
        is False
    )


def test_gate_runs_for_a_configured_target_with_no_stated_preference() -> None:
    """Having a target is enough; the user never has to opt in separately."""
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    config = sync.SyncConfig(targets=[sync.SyncTarget(path="~/skills", scope="owned")])
    assert config.auto.explicitly_set is False
    assert (
        background_sync.should_run_auto_pull(
            ["logout"], {}, config, sync.SyncState(), authenticated=True, now=now
        )
        is True
    )


def test_gate_skips_when_no_targets() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    config = sync.SyncConfig(auto=sync.AutoConfig(enabled=True))
    assert (
        background_sync.should_run_auto_pull(
            ["logout"], {}, config, sync.SyncState(), authenticated=True, now=now
        )
        is False
    )


def test_gate_skips_when_not_yet_due() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    state = sync.SyncState(last_auto_pull_at=now - timedelta(seconds=10))
    assert (
        background_sync.should_run_auto_pull(
            ["logout"], {}, _enabled_config(), state, authenticated=True, now=now
        )
        is False
    )


# ----- lock -----


def test_acquire_lock_succeeds_then_blocks_second(tmp_config_paths: ConfigPaths) -> None:
    lock = tmp_config_paths.sync_lock_file
    assert background_sync._acquire_lock(lock, now=time.time()) is True
    # A second acquire while the lock is held returns False.
    assert background_sync._acquire_lock(lock, now=time.time()) is False
    background_sync._release_lock(lock)
    # Released: acquirable again.
    assert background_sync._acquire_lock(lock, now=time.time()) is True
    background_sync._release_lock(lock)


def test_acquire_lock_steals_stale_lock(tmp_config_paths: ConfigPaths) -> None:
    lock = tmp_config_paths.sync_lock_file
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    # Pretend the lock is far older than the staleness bound.
    far_future = time.time() + background_sync.LOCK_STALE_SECONDS + 60
    assert background_sync._acquire_lock(lock, now=far_future) is True
    background_sync._release_lock(lock)


# ----- summary -----


def _item(action: sync.PullAction, slug: str = "wf") -> sync.PullItem:
    return sync.PullItem(slug=slug, target_path="~/skills", action=action)


def test_summary_none_when_nothing_changed() -> None:
    result = sync.PullResult(items=[_item("up-to-date"), _item("up-to-date", "b")])
    assert background_sync.format_auto_pull_summary(result) is None


def test_summary_reports_updates() -> None:
    result = sync.PullResult(
        items=[_item("pulled"), _item("pulled", "b"), _item("up-to-date", "c")]
    )
    summary = background_sync.format_auto_pull_summary(result)
    assert summary is not None
    assert "auto-synced" in summary
    assert "2 updated" in summary


def test_summary_surfaces_skipped_with_follow_up() -> None:
    result = sync.PullResult(items=[_item("pulled"), _item("skipped-modified", "b")])
    summary = background_sync.format_auto_pull_summary(result)
    assert summary is not None
    assert "1 skipped (local edits)" in summary
    assert "goodeye skills sync status" in summary


def test_summary_reports_gone_alongside_skipped() -> None:
    # Gone workflows must always be surfaced, even when skipped local edits also
    # exist; the gone count is never dropped because a skip is present.
    result = sync.PullResult(items=[_item("skipped-modified"), _item("deleted-on-server", "b")])
    summary = background_sync.format_auto_pull_summary(result)
    assert summary is not None
    assert "1 skipped (local edits)" in summary
    assert "1 gone from the registry" in summary
    assert "goodeye skills sync status" in summary


# ----- tail -----


def _me_route(email: str = DEFAULT_EMAIL) -> respx.Route:
    return respx.get(f"{SERVER}/v1/me").mock(
        return_value=httpx.Response(200, json={"email": email})
    )


def _list_response(items: list[dict], next_cursor: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"items": items, "next_cursor": next_cursor})


@respx.mock
def test_tail_claims_window_and_pulls(tmp_config_paths: ConfigPaths, tmp_path, capsys) -> None:
    _me_route()
    target_dir = tmp_path / "skills"
    config = sync.SyncConfig(
        targets=[sync.SyncTarget(path=str(target_dir), scope="owned")],
        auto=sync.AutoConfig(enabled=True, interval_seconds=3600),
    )
    sync.save_sync_config(config, tmp_config_paths)

    respx.get(f"{SERVER}/v1/skills").mock(
        return_value=_list_response(
            [{"id": "skl_a", "name": "alpha", "current_version": 1, "version_token": "t1"}]
        )
    )
    respx.get(f"{SERVER}/v1/skills/skl_a").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "skl_a",
                "name": "alpha",
                "version": 1,
                "body": "body",
                "version_token": "t1",
                "effective_role": "owner",
                "verifiers": [],
            },
        )
    )

    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    background_sync.run_auto_pull_tail(
        tmp_config_paths,
        server=SERVER,
        api_key="good_live_EXAMPLE",
        now=now,
    )

    # The window was claimed (stamped) and the workflow landed on disk.
    assert sync.load_sync_state(tmp_config_paths).last_auto_pull_at == now
    assert (target_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "body"
    err = capsys.readouterr().err
    assert "auto-synced" in err
    # The lock was released.
    assert not tmp_config_paths.sync_lock_file.exists()


def test_tail_swallows_failure_and_claims_window(tmp_config_paths: ConfigPaths, capsys) -> None:
    # No HTTP routes registered and a real (unreachable) server: the network call
    # fails, but the tail must not raise and the window is already claimed so it
    # waits out the next interval instead of retrying every command.
    config = sync.SyncConfig(
        targets=[sync.SyncTarget(path="~/skills", scope="owned")],
        auto=sync.AutoConfig(enabled=True),
    )
    sync.save_sync_config(config, tmp_config_paths)

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    # Must not raise.
    background_sync.run_auto_pull_tail(
        tmp_config_paths,
        server=SERVER,
        api_key="good_live_EXAMPLE",
        now=now,
        transport=httpx.MockTransport(fail),
    )
    assert sync.load_sync_state(tmp_config_paths).last_auto_pull_at == now
    assert not tmp_config_paths.sync_lock_file.exists()


def test_tail_skips_when_lock_held(tmp_config_paths: ConfigPaths) -> None:
    # When another process holds the lock, the tail does no work: it does not
    # even claim the window.
    config = sync.SyncConfig(
        targets=[sync.SyncTarget(path="~/skills", scope="owned")],
        auto=sync.AutoConfig(enabled=True),
    )
    sync.save_sync_config(config, tmp_config_paths)
    tmp_config_paths.sync_lock_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_config_paths.sync_lock_file.write_text("", encoding="utf-8")

    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    background_sync.run_auto_pull_tail(
        tmp_config_paths,
        server=SERVER,
        api_key="good_live_EXAMPLE",
        now=now,
    )
    # The window was never claimed because the lock was held.
    assert sync.load_sync_state(tmp_config_paths).last_auto_pull_at is None
    # The pre-existing (fresh) lock is untouched.
    assert tmp_config_paths.sync_lock_file.exists()


# ----- app-entry wiring -----


def _set_invocation_args(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(app_module, "_get_background_notice_args", lambda: args, raising=False)


def _silence_update_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_module.update_checks, "check_for_update_background", lambda *a, **k: None
    )


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_config_paths: ConfigPaths) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config_paths.config_dir.parent))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GOODEYE_API_KEY", "good_live_EXAMPLE")
    monkeypatch.setenv("GOODEYE_SERVER", SERVER)


def test_entry_registers_tail_for_eligible_invocation(
    tmp_config_paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _silence_update_notice(monkeypatch)
    _set_invocation_args(monkeypatch, ["logout"])
    sync.save_sync_config(_enabled_config(), tmp_config_paths)

    registered: list[object] = []
    monkeypatch.setattr(app_module.atexit, "register", registered.append)

    result = CliRunner().invoke(app, ["logout"])
    assert result.exit_code == 0, result.output
    assert registered == [background_sync.run_auto_pull_tail]


def test_entry_does_not_register_when_disabled(
    tmp_config_paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _silence_update_notice(monkeypatch)
    _set_invocation_args(monkeypatch, ["logout"])
    # No sync.json at all: auto disabled by default.

    registered: list[object] = []
    monkeypatch.setattr(app_module.atexit, "register", registered.append)

    result = CliRunner().invoke(app, ["logout"])
    assert result.exit_code == 0, result.output
    assert registered == []


@pytest.mark.parametrize(
    "args",
    [
        ["--version"],
        ["whoami", "--json"],
        ["workflows", "sync", "pull"],
    ],
)
def test_entry_does_not_register_for_suppressed_invocations(
    tmp_config_paths: ConfigPaths,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _silence_update_notice(monkeypatch)
    _set_invocation_args(monkeypatch, args)
    sync.save_sync_config(_enabled_config(), tmp_config_paths)

    registered: list[object] = []
    monkeypatch.setattr(app_module.atexit, "register", registered.append)

    CliRunner().invoke(app, args)
    assert registered == []


def test_entry_register_gate_never_raises_into_command(
    tmp_config_paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_env(monkeypatch, tmp_config_paths)
    _silence_update_notice(monkeypatch)
    _set_invocation_args(monkeypatch, ["logout"])
    sync.save_sync_config(_enabled_config(), tmp_config_paths)

    def boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(background_sync, "should_run_auto_pull", boom)

    result = CliRunner().invoke(app, ["logout"])
    # The broken gate is swallowed; the command exits cleanly.
    assert result.exit_code == 0, result.output
