"""Gitignore-style exclude rules for skill directory uploads.

When pushing a skill directory, some files should not be included in the
bundle: build artefacts, caches, VCS data, and so on.  This module builds
an :class:`IgnoreMatcher` from two sources of patterns:

1. A list of *defaults* supplied by the server (``ClientConfig.ignore_defaults``).
   When the server list is unavailable (offline or not yet fetched), the
   baked-in :data:`DEFAULT_IGNORE_PATTERNS` constant is used instead.
2. A per-directory ``.goodeyeignore`` file that layers additional patterns on
   top of the defaults using the same gitignore wildmatch syntax.  A ``!``
   negation line in ``.goodeyeignore`` can re-include a path that was excluded
   by the defaults.

The ``.goodeyeignore`` file itself is never excluded; it travels with the
bundle so that ignore rules are preserved across push/pull round-trips.
"""

from __future__ import annotations

from pathlib import Path

import pathspec

# ---------------------------------------------------------------------------
# Baked-in defaults
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_PATTERNS: list[str] = [
    ".git/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".venv/",
    "venv/",
    ".DS_Store",
    "dist/",
    "build/",
    "*.egg-info/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    "*.log",
]


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


class IgnoreMatcher:
    """Decides whether a relative file path should be excluded from an upload.

    Construct via :func:`build_ignore_spec`; do not instantiate directly.
    """

    def __init__(self, spec: pathspec.GitIgnoreSpec) -> None:
        self._spec = spec

    def is_ignored(self, rel_posix_path: str) -> bool:
        """Return ``True`` if *rel_posix_path* should be excluded from the bundle.

        The path must be relative to the skill directory and use forward
        slashes (POSIX style).  The special file ``.goodeyeignore`` is always
        considered *not* ignored regardless of the active patterns.
        """
        if rel_posix_path == ".goodeyeignore":
            return False
        return self._spec.match_file(rel_posix_path)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_ignore_spec(
    skill_dir: Path,
    defaults: list[str] | None,
) -> IgnoreMatcher:
    """Build an :class:`IgnoreMatcher` for *skill_dir*.

    :param skill_dir: Root directory of the skill being pushed.
    :param defaults: Pattern list from the server's client configuration.
        Pass ``None`` or an empty list to fall back to the baked-in
        :data:`DEFAULT_IGNORE_PATTERNS`.
    :returns: An :class:`IgnoreMatcher` whose :meth:`~IgnoreMatcher.is_ignored`
        method accepts POSIX-style paths relative to *skill_dir*.

    Pattern ordering follows gitignore semantics: defaults come first, then
    any lines from ``.goodeyeignore``.  A later ``!negation`` line can
    re-include a path that an earlier pattern excluded.
    """
    effective_defaults = defaults if defaults else DEFAULT_IGNORE_PATTERNS

    all_lines: list[str] = list(effective_defaults)

    goodeyeignore = skill_dir / ".goodeyeignore"
    if goodeyeignore.is_file():
        content = goodeyeignore.read_text(encoding="utf-8")
        all_lines.extend(content.splitlines())

    spec = pathspec.GitIgnoreSpec.from_lines(all_lines)
    return IgnoreMatcher(spec)
