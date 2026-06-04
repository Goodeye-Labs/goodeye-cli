"""Compatibility re-export of the destructive-action prompt helper.

The implementation moved to the package-top-level ``goodeye_cli.prompts`` so
engine modules (like ``sync``) can import it without the commands package
reaching back into itself. This module re-exports it so existing call sites
under ``commands/`` keep importing from here unchanged.
"""

from __future__ import annotations

from goodeye_cli.prompts import confirm_destructive

__all__ = ["confirm_destructive"]
