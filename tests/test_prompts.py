"""Tests for the destructive-action prompt helper.

Confirms agents/CI invocations get headless behavior automatically: when
stdin is not a TTY, ``confirm_destructive`` short-circuits to ``True``
without ever calling ``typer.confirm``. Humans at a TTY still see the
prompt unless they pass ``--yes``.
"""

from __future__ import annotations

from unittest import mock

import pytest

from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.errors import ValidationFailed


def test_re_export_is_the_neutral_implementation() -> None:
    """The commands package re-exports the top-level helper, so engine modules
    can import it without reaching up into the commands package.
    """
    from goodeye_cli.prompts import confirm_destructive as neutral

    assert confirm_destructive is neutral


def test_yes_short_circuits_to_true_without_consulting_stdin() -> None:
    with mock.patch("goodeye_cli.prompts.typer.confirm") as confirm:
        assert confirm_destructive("delete?", yes=True) is True
    confirm.assert_not_called()


def test_non_tty_stdin_auto_approves() -> None:
    """The whole point: agents and CI never have a TTY, so the prompt
    must auto-approve rather than block on input.
    """
    with (
        mock.patch("goodeye_cli.prompts.sys.stdin") as stdin,
        mock.patch("goodeye_cli.prompts.typer.confirm") as confirm,
    ):
        stdin.isatty.return_value = False
        assert confirm_destructive("delete?", yes=False) is True
    confirm.assert_not_called()


def test_tty_stdin_defers_to_typer_confirm() -> None:
    with (
        mock.patch("goodeye_cli.prompts.sys.stdin") as stdin,
        mock.patch("goodeye_cli.prompts.typer.confirm") as confirm,
    ):
        stdin.isatty.return_value = True
        confirm.return_value = False
        assert confirm_destructive("delete?", yes=False) is False
    confirm.assert_called_once_with("delete?", default=False)


def test_permanent_delete_noninteractive_without_yes_aborts() -> None:
    """Permanent, irreversible deletes must not auto-approve when headless:
    a piped/agent/CI caller has to pass --yes to erase data.
    """
    with (
        mock.patch("goodeye_cli.prompts.sys.stdin") as stdin,
        mock.patch("goodeye_cli.prompts.typer.confirm") as confirm,
    ):
        stdin.isatty.return_value = False
        with pytest.raises(ValidationFailed) as excinfo:
            confirm_destructive("delete?", yes=False, require_explicit_yes_when_noninteractive=True)
    confirm.assert_not_called()
    assert "--yes" in excinfo.value.message


def test_permanent_delete_yes_short_circuits_even_when_noninteractive() -> None:
    with (
        mock.patch("goodeye_cli.prompts.sys.stdin") as stdin,
        mock.patch("goodeye_cli.prompts.typer.confirm") as confirm,
    ):
        stdin.isatty.return_value = False
        assert (
            confirm_destructive("delete?", yes=True, require_explicit_yes_when_noninteractive=True)
            is True
        )
    confirm.assert_not_called()


def test_permanent_delete_interactive_tty_still_prompts() -> None:
    """The stricter flag only changes headless behavior; a real terminal
    user keeps the normal confirmation prompt.
    """
    with (
        mock.patch("goodeye_cli.prompts.sys.stdin") as stdin,
        mock.patch("goodeye_cli.prompts.typer.confirm") as confirm,
    ):
        stdin.isatty.return_value = True
        confirm.return_value = True
        assert (
            confirm_destructive("delete?", yes=False, require_explicit_yes_when_noninteractive=True)
            is True
        )
    confirm.assert_called_once_with("delete?", default=False)
