"""Tests for output.fetch_pages helper."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from goodeye_cli.errors import ServerError
from goodeye_cli.output import fetch_pages


class _Page:
    def __init__(self, items: list[Any], next_cursor: str | None) -> None:
        self.items = items
        self.next_cursor = next_cursor


def test_fetch_pages_single_page() -> None:
    """Default (all_pages=False) returns exactly the first page."""
    page = _Page(items=[1, 2], next_cursor="cursor-a")
    fetch_fn = MagicMock(return_value=page)
    items, cursor = fetch_pages(fetch_fn, cursor=None, all_pages=False)
    assert items == [1, 2]
    assert cursor == "cursor-a"
    fetch_fn.assert_called_once_with(None)


def test_fetch_pages_follows_cursors_to_end() -> None:
    """all_pages=True collects all items across pages."""
    pages = [
        _Page(items=[1, 2], next_cursor="cursor-b"),
        _Page(items=[3, 4], next_cursor=None),
    ]
    call_count = 0

    def fetch_fn(cursor: str | None) -> _Page:
        nonlocal call_count
        result = pages[call_count]
        call_count += 1
        return result

    items, cursor = fetch_pages(fetch_fn, cursor=None, all_pages=True)
    assert items == [1, 2, 3, 4]
    assert cursor is None


def test_fetch_pages_repeated_cursor_raises() -> None:
    """all_pages=True raises ServerError when the server returns a repeating cursor."""
    repeating = _Page(items=[1], next_cursor="cursor-loop")
    fetch_fn = MagicMock(return_value=repeating)
    with pytest.raises(ServerError) as exc_info:
        fetch_pages(fetch_fn, cursor=None, all_pages=True)
    err = exc_info.value
    assert err.slug == "internal_error"
    assert "cursor-loop" in err.message
