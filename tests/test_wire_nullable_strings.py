"""Tests that optional wire strings tolerate an explicit JSON null.

Optional server columns come back as null once a caller clears them. These
fields already default to "", so a null must read the same way rather than
failing validation and taking the whole response down with it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from goodeye_cli.wire import (
    TemplateList,
    TemplateSearchResponse,
    TemplateSummary,
    WorkflowDetail,
    WorkflowList,
    WorkflowSearchResponse,
    WorkflowSummary,
)


def _template_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "tmpl-1",
        "slug": "writing-humanizer",
        "name": "writing-humanizer",
        "handle": "example",
        "owner_user_id": "user-1",
        "latest_version": 10,
        "publishing_handle": "example",
    }
    row.update(overrides)
    return row


def _workflow_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {"id": "skill-1", "name": "example-skill", "current_version": 1}
    row.update(overrides)
    return row


@pytest.mark.parametrize("field", ["outcome", "description"])
def test_template_summary_accepts_null(field: str) -> None:
    summary = TemplateSummary.model_validate(_template_row(**{field: None}))
    assert getattr(summary, field) == ""


@pytest.mark.parametrize("field", ["outcome", "description"])
def test_workflow_summary_accepts_null(field: str) -> None:
    summary = WorkflowSummary.model_validate(_workflow_row(**{field: None}))
    assert getattr(summary, field) == ""


def test_null_outcome_does_not_fail_the_whole_template_list() -> None:
    """One cleared row must not take down the rows that parsed fine."""
    payload = {
        "items": [_template_row(id="tmpl-1"), _template_row(id="tmpl-2", outcome=None)],
        "next_cursor": None,
    }
    listing = TemplateList.model_validate(payload)
    assert [item.id for item in listing.items] == ["tmpl-1", "tmpl-2"]
    assert listing.items[1].outcome == ""


def test_null_outcome_does_not_fail_the_whole_workflow_list() -> None:
    payload = {"items": [_workflow_row(), _workflow_row(id="skill-2", outcome=None)]}
    listing = WorkflowList.model_validate(payload)
    assert listing.items[1].outcome == ""


def test_template_search_accepts_null_strings() -> None:
    payload = {
        "items": [
            {
                "id": "tmpl-1",
                "rank": 1,
                "match_reason": "name match",
                "slug": None,
                "name": None,
                "handle": None,
                "description": None,
                "outcome": None,
            }
        ],
        "query": "humanize",
        "limit": 10,
    }
    item = TemplateSearchResponse.model_validate(payload).items[0]
    assert (item.slug, item.name, item.handle, item.description, item.outcome) == ("",) * 5


def test_workflow_search_accepts_null_strings() -> None:
    payload = {
        "items": [{"id": "skill-1", "rank": 1, "match_reason": "m", "description": None}],
        "query": "humanize",
        "limit": 10,
    }
    assert WorkflowSearchResponse.model_validate(payload).items[0].description == ""


def test_workflow_detail_accepts_null_outcome() -> None:
    detail = WorkflowDetail.model_validate(
        {"id": "skill-1", "name": "n", "version": 1, "body": "b", "outcome": None}
    )
    assert detail.outcome == ""


def test_absent_and_present_values_are_unchanged() -> None:
    """The coercion must only touch null, leaving normal payloads alone."""
    absent = TemplateSummary.model_validate(_template_row())
    assert absent.outcome == ""
    present = TemplateSummary.model_validate(_template_row(outcome="Ship faster."))
    assert present.outcome == "Ship faster."


def test_wrong_types_are_still_rejected() -> None:
    """Tolerating null must not turn the field into an anything-goes field."""
    with pytest.raises(ValidationError):
        TemplateSummary.model_validate(_template_row(outcome=123))
