"""Tests for gitignore-style exclude rules used when uploading skill directories."""

from __future__ import annotations

from pathlib import Path

from goodeye_cli.ignore import DEFAULT_IGNORE_PATTERNS, build_ignore_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(tmp_path: Path, goodeyeignore: str | None = None, defaults: list[str] | None = None):
    """Build an IgnoreMatcher for tmp_path, optionally writing a .goodeyeignore."""
    if goodeyeignore is not None:
        (tmp_path / ".goodeyeignore").write_text(goodeyeignore)
    return build_ignore_spec(tmp_path, defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_ignores_node_modules_and_git(tmp_path: Path) -> None:
    """Baked-in defaults exclude common VCS / build artefacts."""
    spec = _spec(tmp_path, defaults=None)  # no .goodeyeignore, no server defaults

    assert spec.is_ignored("node_modules/x.js")
    assert spec.is_ignored(".git/config")
    assert spec.is_ignored("__pycache__/y.pyc")
    assert spec.is_ignored("foo.pyc")

    # Normal content file must NOT be ignored
    assert not spec.is_ignored("references/r.md")


def test_goodeyeignore_overlays_defaults(tmp_path: Path) -> None:
    """Lines in .goodeyeignore add patterns on top of defaults; negations re-include."""
    goodeyeignore = "*.tmp\n!build/keep.txt\n"
    spec = _spec(tmp_path, goodeyeignore=goodeyeignore, defaults=None)

    # Extra pattern from .goodeyeignore
    assert spec.is_ignored("scratch.tmp")

    # build/ is in DEFAULT_IGNORE_PATTERNS; negation should re-include the specific file
    assert not spec.is_ignored("build/keep.txt")

    # Other files under build/ remain ignored
    assert spec.is_ignored("build/other.o")


def test_goodeyeignore_is_itself_not_ignored(tmp_path: Path) -> None:
    """The .goodeyeignore file itself is never excluded, even if a pattern would match."""
    # Add a wildcard that would normally match everything
    goodeyeignore = ".*\n"
    spec = _spec(tmp_path, goodeyeignore=goodeyeignore, defaults=None)

    # Normally a dot-file pattern would match .goodeyeignore, but it must be protected
    assert not spec.is_ignored(".goodeyeignore")


def test_offline_uses_baked_defaults(tmp_path: Path) -> None:
    """Passing defaults=None or defaults=[] both fall through to the baked-in list."""
    spec_none = _spec(tmp_path, defaults=None)
    spec_empty = _spec(tmp_path, defaults=[])

    for spec in (spec_none, spec_empty):
        assert spec.is_ignored("node_modules/x")
        assert not spec.is_ignored("SKILL.md")

    # The baked-in fallback is the canonical default set.
    assert ".git/" in DEFAULT_IGNORE_PATTERNS
    assert "node_modules/" in DEFAULT_IGNORE_PATTERNS
    assert len(DEFAULT_IGNORE_PATTERNS) == 15
