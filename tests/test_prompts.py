"""Tests for the destructive-action prompt helper.

Confirms agents/CI invocations get headless behavior automatically: when
stdin is not a TTY, ``confirm_destructive`` short-circuits to ``True``
without ever calling ``typer.confirm``. Humans at a TTY still see the
prompt unless they pass ``--yes``.
"""

from __future__ import annotations

from unittest import mock

from goodeye_cli.commands.prompts import confirm_destructive


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
