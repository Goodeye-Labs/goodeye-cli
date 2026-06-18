"""Shared output helpers for list/search command rendering."""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol

import typer
from pydantic import BaseModel

OutputMode = Literal["json", "table"]

_MISSING = object()


class Page(Protocol):
    items: list[Any]
    next_cursor: str | None


def resolve_output_mode(*, json_output: bool, table_output: bool) -> OutputMode:
    """Resolve explicit flags or choose a TTY-aware default."""
    if json_output and table_output:
        raise typer.BadParameter("Use either --json or --table, not both.")
    if json_output:
        return "json"
    if table_output:
        return "table"
    return "table" if sys.stdout.isatty() else "json"


def to_jsonable(value: Any) -> Any:
    """Convert Pydantic models recursively into JSON-compatible values."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def compact_json(value: Any) -> str:
    """Serialize a value as compact single-line JSON."""
    return json.dumps(to_jsonable(value), separators=(",", ":"))


def echo_json(value: Any) -> None:
    typer.echo(compact_json(value))


def items_envelope(
    items: Sequence[Any],
    *,
    next_cursor: str | None | object = _MISSING,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"items": list(items)}
    if next_cursor is not _MISSING:
        payload["next_cursor"] = next_cursor
    return payload


def fetch_pages(
    fetch_page: Callable[[str | None], Page],
    *,
    cursor: str | None,
    all_pages: bool,
) -> tuple[list[Any], str | None]:
    """Fetch one page by default, or follow cursors when requested."""
    from goodeye_cli.errors import ServerError

    items: list[Any] = []
    next_cursor = cursor
    seen_cursors: set[str] = set()
    while True:
        page = fetch_page(next_cursor)
        items.extend(page.items)
        next_cursor = page.next_cursor
        if not all_pages or next_cursor is None:
            return items, next_cursor
        if next_cursor in seen_cursors:
            raise ServerError(
                slug="internal_error",
                message=f"Server returned a repeated pagination cursor: {next_cursor!r}",
                hint="This is a server bug. Try the request again or contact support.",
            )
        seen_cursors.add(next_cursor)


def next_page_hint(
    command: Sequence[str],
    *,
    next_cursor: str,
    limit: int,
    options: Sequence[tuple[str, str | None]] = (),
    bool_flags: Sequence[str] = (),
    table: bool = True,
) -> str:
    parts = list(command)
    for flag, value in options:
        if value is not None and value != "":
            parts.extend([flag, value])
    parts.extend(bool_flags)
    if table:
        parts.append("--table")
    parts.extend(["--limit", str(limit), "--cursor", next_cursor])
    return f"Next page: {shlex.join(parts)}"
