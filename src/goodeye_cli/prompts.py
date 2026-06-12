"""Shared prompt helpers for destructive CLI actions.

The CLI is consumed by both humans at a TTY and AI agents driving it
headlessly. Interactive ``typer.confirm`` prompts hang an agent waiting on
stdin, so destructive actions auto-approve when stdin is not a TTY (the
standard ``git`` / ``gh`` / ``npm`` pattern). Humans at a real terminal
still see the safety prompt unless they pass ``--yes``.

This lives at the package top level (not under ``commands/``) so engine
modules like ``sync`` can import it without reaching up into the commands
package. ``commands/prompts.py`` re-exports it for existing call sites.
"""

from __future__ import annotations

import sys

import typer

from goodeye_cli.errors import ValidationFailed


def confirm_destructive(
    message: str,
    *,
    yes: bool,
    require_explicit_yes_when_noninteractive: bool = False,
) -> bool:
    """Return ``True`` when the destructive action should proceed.

    Skips the prompt when ``yes`` is set or when stdin is not a TTY so
    agents and CI invocations never block on input. At an interactive
    terminal, defers to ``typer.confirm`` with a no-default so a stray
    Enter does not approve.

    ``require_explicit_yes_when_noninteractive`` raises the safety bar for
    irreversible actions (permanent delete): when stdin is not a TTY the
    helper no longer auto-approves. Instead it raises so a piped, agent, or
    CI invocation must pass ``--yes`` to erase data, while ``--yes`` and
    interactive prompts behave exactly as before. Recoverable or idempotent
    actions (archive, unarchive, leave) leave this off and keep the standard
    auto-approve-when-headless behavior.
    """
    if yes:
        return True
    if not sys.stdin.isatty():
        if require_explicit_yes_when_noninteractive:
            raise ValidationFailed(
                slug="confirmation_required",
                message=(
                    "This action permanently deletes data and cannot be undone. "
                    "Re-run with --yes to confirm non-interactively."
                ),
            )
        return True
    return typer.confirm(message, default=False)
