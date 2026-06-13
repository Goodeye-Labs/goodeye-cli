"""Tests for multi-file workflow wire models."""

from __future__ import annotations

from goodeye_cli.wire import (
    ClientConfig,
    TemplatePublishResult,
    WorkflowDetail,
    WorkflowFileEntry,
    WorkflowSaveResult,
)


def test_workflow_detail_parses_files_manifest() -> None:
    payload = {
        "id": "wf_1",
        "name": "example",
        "version": 1,
        "body": "runbook body",
        "description": "desc",
        "safety_verification_status": "clean",
        "files": [
            {
                "path": "scripts/run.sh",
                "sha256": "abc123",
                "size_bytes": 512,
                "executable": True,
                "content_kind": "text",
                "purpose": "entrypoint",
                "fetchable_over_mcp": True,
                "execution_gated": False,
                "safety_verification_status": "clean",
            },
            {
                "path": "README.md",
                "sha256": "def456",
                "size_bytes": 128,
                "executable": False,
                "content_kind": "text",
                "purpose": None,
                "fetchable_over_mcp": True,
                "execution_gated": False,
                "safety_verification_status": None,
            },
        ],
    }
    detail = WorkflowDetail.model_validate(payload)
    assert detail.safety_verification_status == "clean"
    assert len(detail.files) == 2
    assert detail.files[0].path == "scripts/run.sh"
    assert detail.files[0].executable is True
    assert detail.files[0].sha256 == "abc123"
    assert detail.files[0].size_bytes == 512
    assert detail.files[0].purpose == "entrypoint"
    assert detail.files[0].safety_verification_status == "clean"
    assert detail.files[1].path == "README.md"
    assert detail.files[1].executable is False
    assert detail.files[1].purpose is None


def test_workflow_detail_without_files_uses_defaults() -> None:
    """Older server responses without `files` or `safety_verification_status` still parse."""
    payload = {
        "id": "wf_old",
        "name": "legacy",
        "version": 1,
        "body": "body",
        "description": "old workflow",
    }
    detail = WorkflowDetail.model_validate(payload)
    assert detail.files == []
    assert detail.safety_verification_status is None


def test_workflow_file_entry_defaults() -> None:
    entry = WorkflowFileEntry.model_validate({"path": "some/file.txt"})
    assert entry.path == "some/file.txt"
    assert entry.sha256 is None
    assert entry.size_bytes == 0
    assert entry.executable is False
    assert entry.content_kind == "text"
    assert entry.purpose is None
    assert entry.fetchable_over_mcp is True
    assert entry.execution_gated is False
    assert entry.safety_verification_status is None


def test_client_config_parses_ignore_defaults() -> None:
    payload = {
        "workos_client_id": "client_abc",
        "workos_device_authorization_uri": "https://auth.example.com/device",
        "workos_token_uri": "https://auth.example.com/token",
        "ignore_defaults": ["*.pyc", "__pycache__/", ".env"],
    }
    config = ClientConfig.model_validate(payload)
    assert config.ignore_defaults == ["*.pyc", "__pycache__/", ".env"]


def test_client_config_ignore_defaults_empty_when_absent() -> None:
    """Older server responses without `ignore_defaults` still parse with empty default."""
    payload = {
        "workos_client_id": "client_abc",
        "workos_device_authorization_uri": "https://auth.example.com/device",
        "workos_token_uri": "https://auth.example.com/token",
    }
    config = ClientConfig.model_validate(payload)
    assert config.ignore_defaults == []


def test_workflow_detail_ignores_extra_unknown_fields() -> None:
    """Extra unknown fields from a newer server are silently discarded."""
    payload = {
        "id": "wf_future",
        "name": "future",
        "version": 1,
        "body": "body",
        "description": "desc",
        "totally_new_field": "something",
        "files": [],
    }
    detail = WorkflowDetail.model_validate(payload)
    assert detail.name == "future"
    assert detail.files == []
    assert not hasattr(detail, "totally_new_field")


def test_workflow_save_result_parses_authoring_notes() -> None:
    result = WorkflowSaveResult.model_validate(
        {
            "workflow_id": "wf_1",
            "version": 2,
            "name": "demo",
            "version_token": "tok",
            "authoring_notes": [
                "An image referenced in demo/README.md was not found in the workflow files.",
            ],
        }
    )
    assert result.authoring_notes == [
        "An image referenced in demo/README.md was not found in the workflow files.",
    ]


def test_workflow_save_result_defaults_authoring_notes_empty() -> None:
    """An older server with no authoring_notes field still parses (defaults to [])."""
    result = WorkflowSaveResult.model_validate(
        {
            "workflow_id": "wf_1",
            "version": 1,
            "name": "demo",
            "version_token": "tok",
        }
    )
    assert result.authoring_notes == []


def test_template_publish_result_parses_authoring_notes() -> None:
    result = TemplatePublishResult.model_validate(
        {
            "template_id": "tpl_1",
            "version": 3,
            "publishing_handle": "h",
            "authoring_notes": ["A video host in demo/README.md is not on the allowlist."],
        }
    )
    assert result.authoring_notes == [
        "A video host in demo/README.md is not on the allowlist.",
    ]


def test_template_publish_result_defaults_authoring_notes_empty() -> None:
    result = TemplatePublishResult.model_validate(
        {
            "template_id": "tpl_1",
            "version": 1,
            "publishing_handle": "h",
        }
    )
    assert result.authoring_notes == []
