"""Shared prompt helpers for destructive CLI commands.

The CLI is consumed by both humans at a TTY and AI agents driving it
headlessly. Interactive ``typer.confirm`` prompts hang an agent waiting on
stdin, so destructive actions auto-approve when stdin is not a TTY (the
standard ``git`` / ``gh`` / ``npm`` pattern). Humans at a real terminal
still see the safety prompt unless they pass ``--yes``.
"""

from __future__ import annotations

import sys

import typer


def confirm_destructive(message: str, *, yes: bool) -> bool:
    """Return ``True`` when the destructive action should proceed.

    Skips the prompt when ``yes`` is set or when stdin is not a TTY so
    agents and CI invocations never block on input. At an interactive
    terminal, defers to ``typer.confirm`` with a no-default so a stray
    Enter does not approve.
    """
    if yes or not sys.stdin.isatty():
        return True
    return typer.confirm(message, default=False)
