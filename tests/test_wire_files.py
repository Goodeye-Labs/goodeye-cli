"""Tests for multi-file workflow wire models."""

from __future__ import annotations

from goodeye_cli.wire import (
    ClientConfig,
    TemplateDetail,
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
    # Absent on older servers / templates with no verifiers.
    assert result.verifier_exposure_notice is None


def test_template_publish_result_parses_verifier_exposure_notice() -> None:
    notice = "Publishing makes this template's verifier definitions public."
    result = TemplatePublishResult.model_validate(
        {
            "template_id": "tpl_1",
            "version": 2,
            "publishing_handle": "h",
            "verifier_exposure_notice": notice,
        }
    )
    assert result.verifier_exposure_notice == notice


def test_workflow_detail_parses_image_generators_and_archived_at() -> None:
    payload = {
        "id": "wf_ig",
        "name": "img-gen",
        "version": 1,
        "body": "body",
        "description": "desc",
        "image_generators": [
            {"name": "hero", "generator_ref": "system:image-standard"},
            {"name": "banner", "generator_ref": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee@2"},
        ],
        "archived_at": "2026-06-14T00:00:00Z",
    }
    detail = WorkflowDetail.model_validate(payload)
    assert detail.image_generators[0].name == "hero"
    assert detail.image_generators[0].generator_ref == "system:image-standard"
    assert detail.image_generators[1].generator_ref.endswith("@2")
    assert detail.archived_at is not None


def test_workflow_detail_defaults_image_generators_empty() -> None:
    """An older server without `image_generators` / `archived_at` still parses."""
    detail = WorkflowDetail.model_validate(
        {"id": "wf_old", "name": "legacy", "version": 1, "body": "b", "description": "d"}
    )
    assert detail.image_generators == []
    assert detail.archived_at is None


def test_template_detail_parses_image_generator_snapshots_files_and_archived_at() -> None:
    payload = {
        "id": "tpl_ig",
        "slug": "img-gen",
        "name": "img-gen",
        "handle": "h",
        "owner_user_id": "u1",
        "version": 1,
        "body": "body",
        "publishing_handle": "h",
        "archived_at": "2026-06-14T00:00:00Z",
        "image_generator_snapshots": [
            {"name": "hero", "system_name": "image-standard"},
            {
                "name": "banner",
                "generator_id": "gen-123",
                "generator_version": 3,
                "provider": "fal",
                "model": "some/model",
                "generation_contract": "text_image",
                "default_params": {"steps": 30},
                "config_hash": "abc",
            },
        ],
        "files": [{"path": "demo/preview.png", "sha256": "abc", "size_bytes": 10}],
    }
    detail = TemplateDetail.model_validate(payload)
    assert detail.archived_at is not None
    assert detail.image_generator_snapshots[0].name == "hero"
    assert detail.image_generator_snapshots[0].system_name == "image-standard"
    assert detail.image_generator_snapshots[1].generator_id == "gen-123"
    assert detail.image_generator_snapshots[1].generator_version == 3
    assert detail.image_generator_snapshots[1].model == "some/model"
    assert detail.image_generator_snapshots[1].default_params == {"steps": 30}
    # A system-tier binding carries neither field.
    assert detail.image_generator_snapshots[0].generator_id is None
    assert detail.image_generator_snapshots[0].default_params == {}
    assert detail.files[0].path == "demo/preview.png"


def test_template_detail_defaults_new_fields_empty() -> None:
    """An older server without the new fields still parses with empty defaults."""
    detail = TemplateDetail.model_validate(
        {
            "id": "tpl_old",
            "slug": "legacy",
            "name": "legacy",
            "handle": "h",
            "owner_user_id": "u1",
            "version": 1,
            "body": "b",
            "publishing_handle": "h",
        }
    )
    assert detail.archived_at is None
    assert detail.image_generator_snapshots == []
    assert detail.files == []
