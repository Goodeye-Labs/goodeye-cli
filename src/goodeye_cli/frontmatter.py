"""YAML front-matter parsing and metadata coercion for workflow bodies.

A workflow body is markdown that may open with a ``---`` fenced YAML block
carrying discovery facets (``name``, ``description``, ``outcome``, ``tags``).
This module turns that block into a mapping plus the remaining body, and
coerces individual facets into the shapes the registry expects.

It deliberately depends only on ``yaml`` and the CLI error types so it can be
imported from both the command layer (publish) and the sync engine without an
import cycle through the command modules.
"""

from __future__ import annotations

from typing import Any

import yaml

from goodeye_cli.errors import ValidationFailed


def parse_front_matter(source: str) -> tuple[dict[str, Any], str]:
    """Extract a YAML front-matter block from a markdown source.

    Front-matter is recognised when the file begins with ``---`` on its own line
    and a matching terminator ``---`` appears later. Everything between is parsed
    as YAML. Everything after the terminator is the body.

    Returns ``({}, source)`` if no front-matter is present.
    """
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return {}, source
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            yaml_text = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            if body.startswith("\n"):
                body = body[1:]
            try:
                parsed = yaml.safe_load(yaml_text) or {}
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                problem = getattr(exc, "problem", None)
                if mark is not None and problem:
                    detail = f" (line {mark.line + 1}, column {mark.column + 1}): {problem}"
                else:
                    detail = "."
                raise ValidationFailed(
                    slug="validation_error",
                    message=f"Workflow front-matter is not valid YAML{detail}",
                ) from exc
            if not isinstance(parsed, dict):
                raise ValidationFailed(
                    slug="validation_error",
                    message="YAML front-matter must be a mapping.",
                )
            return parsed, body
    return {}, source


def coerce_required_text(raw: Any, *, field_name: str, missing_message: str) -> str:
    if raw is None:
        raise ValidationFailed(
            slug="validation_error",
            message=missing_message,
        )
    if isinstance(raw, str) and raw.strip():
        return raw
    raise ValidationFailed(
        slug="validation_error",
        message=f"`{field_name}` must be a non-empty string.",
    )


def coerce_outcome(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw
    raise ValidationFailed(
        slug="validation_error",
        message="`outcome` must be a non-empty string.",
    )


def coerce_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    raise ValidationFailed(
        slug="validation_error",
        message="`tags` must be a list of strings.",
    )


def extract_discovery_facets(front_matter: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Pull supported discovery facets from top-level front matter."""
    outcome = coerce_outcome(front_matter.get("outcome"))
    tags = coerce_tags(front_matter.get("tags"))
    return outcome, tags


__all__ = [
    "coerce_outcome",
    "coerce_required_text",
    "coerce_tags",
    "extract_discovery_facets",
    "parse_front_matter",
]
