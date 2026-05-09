"""Pydantic wire models for Goodeye REST responses.

Only the shapes the CLI reads. No domain models from the server are mirrored;
these are deliberately minimal and permissive so minor additive server changes
do not break old CLI releases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _WireBase(BaseModel):
    """Shared config: ignore unknown fields for forward-compat."""

    model_config = ConfigDict(extra="ignore")


class ClientConfig(_WireBase):
    workos_client_id: str
    workos_device_authorization_uri: str
    workos_token_uri: str


class MeResponse(_WireBase):
    email: str
    handle: str | None = None
    handle_claimed_at: datetime | None = None


class ClaimHandleResult(_WireBase):
    handle: str
    claimed_at: datetime | None = None


class RenameHandleResult(_WireBase):
    handle: str
    claimed_at: datetime | None = None
    renamed: bool = True
    self_reclaim: bool = False


class ApiKey(_WireBase):
    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None = None


class ApiKeyCreated(_WireBase):
    id: str
    name: str
    key: str
    created_at: datetime


class ApiKeyList(_WireBase):
    items: list[ApiKey]
    next_cursor: str | None = None


class WorkflowSummary(_WireBase):
    id: str
    name: str
    current_version: int
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None
    owner_user_id: str | None = None
    parent_template_id: str | None = None
    parent_template_version: int | None = None
    effective_role: str | None = None
    version_token: str | None = None


class WorkflowList(_WireBase):
    items: list[WorkflowSummary]
    next_cursor: str | None = None


class WorkflowSearchItem(_WireBase):
    """One ranked row from POST /v1/workflows/search."""

    id: str
    rank: int
    match_reason: str
    slug: str | None = None
    name: str | None = None
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)


class WorkflowSearchResponse(_WireBase):
    items: list[WorkflowSearchItem]
    query: str
    limit: int
    search_mode: str = "llm"


class WorkflowVerifierRefWire(_WireBase):
    """Verifier binding on a workflow (publish payload or fork response)."""

    name: str
    verifier_id: str
    version: int | None = None
    role: str | None = None
    source_workflow_id: str | None = None


class WorkflowDetail(_WireBase):
    id: str
    name: str
    version: int
    body: str
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)
    owner_user_id: str | None = None
    updated_at: datetime | None = None
    parent_template_id: str | None = None
    parent_template_version: int | None = None
    effective_role: str | None = None
    version_token: str | None = None
    verifiers: list[WorkflowVerifierRefWire] = Field(default_factory=list)


class WorkflowSaveResult(_WireBase):
    workflow_id: str
    version: int
    name: str
    version_token: str
    verifiers: list[WorkflowVerifierRefWire] = Field(default_factory=list)


class SaveWorkflowInput(_WireBase):
    """Flat POST /v1/workflows body the CLI constructs (documentation + parity)."""

    name: str
    description: str
    body: str
    expected_version_token: str | None = None
    outcome: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    verifiers: list[WorkflowVerifierRefWire] = Field(default_factory=list)


class WorkflowGrantResult(_WireBase):
    workflow_id: str
    role: str


class WorkflowGrant(_WireBase):
    grantee_type: str
    grantee_identifier: str
    role: str
    granted_by: str
    granted_at: datetime | None = None
    is_via_team: bool = False


class WorkflowGrantList(_WireBase):
    items: list[WorkflowGrant]


class WorkflowGrantRevokeResult(_WireBase):
    workflow_id: str
    revoked: bool


class WorkflowLeaveResult(_WireBase):
    workflow_id: str
    removed_direct_grants: int


class WorkflowDeleteResult(_WireBase):
    workflow_id: str
    name: str
    deleted: bool


class WorkflowLineage(_WireBase):
    workflow_id: str
    parent_template_id: str | None = None
    parent_template_version: int | None = None
    upstream_latest_version: int | None = None
    is_upstream_unpublished: bool | None = None


class WorkflowTeachResult(_WireBase):
    workflow_id: str
    skill_md: str
    trigger_context_echo: dict[str, Any] | None = None


class TemplateSummary(_WireBase):
    id: str
    slug: str
    name: str
    handle: str
    owner_user_id: str
    latest_version: int
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)
    publishing_handle: str
    published_at: datetime | None = None


class TemplateList(_WireBase):
    items: list[TemplateSummary]
    next_cursor: str | None = None


class TemplateSearchItem(_WireBase):
    """One ranked row from POST /v1/templates/search."""

    id: str
    rank: int
    match_reason: str
    slug: str = ""
    name: str = ""
    handle: str = ""
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)


class TemplateSearchResponse(_WireBase):
    items: list[TemplateSearchItem]
    query: str
    limit: int
    search_mode: str = "llm"


class TemplateVerifierSnapshotWire(_WireBase):
    """Public metadata for a verifier attached to a template version."""

    name: str
    input_contract: str
    input_fields: list[str] = Field(default_factory=list)
    verifier_version: int | None = None
    config_hash: str | None = None


class TemplateDetail(_WireBase):
    id: str
    slug: str
    name: str
    handle: str
    owner_user_id: str
    version: int
    body: str
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)
    release_notes: str | None = None
    publishing_handle: str
    safety_verification_status: str = "unverified"
    published_at: datetime | None = None
    unpublished_at: datetime | None = None
    verifier_snapshots: list[TemplateVerifierSnapshotWire] = Field(default_factory=list)


class TemplatePublishResult(_WireBase):
    template_id: str
    version: int
    publishing_handle: str


class TemplateUnpublishResult(_WireBase):
    template_id: str
    version: int
    unpublished: bool


class TemplateForkResult(_WireBase):
    workflow_id: str
    slug: str
    name: str
    parent_template_id: str
    parent_template_version: int
    version_token: str | None = None
    redirected: bool = False
    redirected_from_handle: str | None = None
    redirected_to_handle: str | None = None
    deprecation_warning: str | None = None
    verifiers: list[WorkflowVerifierRefWire] = Field(default_factory=list)


class TemplateDeleteResult(_WireBase):
    template_id: str
    deleted: bool
    idempotent: bool = False


class TemplateUndeleteResult(_WireBase):
    template_id: str
    deleted: bool
    idempotent: bool = False


class TemplateDeprecateVersionResult(_WireBase):
    template_id: str
    version: int
    deprecated_at: datetime | None = None
    deprecation_message: str | None = None


class TemplateTransferOwnershipResult(_WireBase):
    template_id: str
    owner_user_id: str
    transferred: bool


class WorkflowTransferOwnershipResult(_WireBase):
    workflow_id: str
    owner_user_id: str
    transferred: bool


class AuthVerifyResult(_WireBase):
    api_key: str


class ExchangeResult(_WireBase):
    api_key: str
    key_id: str


class DeviceAuthResponse(_WireBase):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int = 5
    expires_in: int = 900


class DeviceTokenResponse(_WireBase):
    access_token: str
    token_type: str | None = None


class TeamCreated(_WireBase):
    team_id: str
    handle: str


class TeamSummary(_WireBase):
    team_id: str
    handle: str
    owner_user_id: str
    role: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TeamList(_WireBase):
    items: list[TeamSummary]


class TeamMember(_WireBase):
    user_id: str
    email: str | None = None
    handle: str | None = None
    role: str


class TeamDeleteResult(_WireBase):
    team_id: str
    deleted: bool


class VerifierSummary(_WireBase):
    verifier_id: str
    name: str
    description: str
    current_version: int
    status: str
    version_token: str
    updated_at: datetime | None = None
    role: str | None = None
    source_workflow_id: str | None = None


class VerifierList(_WireBase):
    items: list[VerifierSummary]
    next_cursor: str | None = None


class VerifierDeployResult(_WireBase):
    verifier_id: str
    version: int
    version_token: str
    name: str
    status: str
    input_contract: str
    config_hash: str


class VerifierVersionDetail(_WireBase):
    """One version of a verifier: full deploy-time config plus head metadata."""

    verifier_id: str
    name: str
    description: str
    version: int
    criterion: str
    input_contract: str
    input_fields: list[str] = Field(default_factory=list)
    few_shot_examples: list[dict[str, Any]] = Field(default_factory=list)
    judge_model_config: dict[str, Any] = Field(default_factory=dict)
    reasoning_field_description: str
    config_hash: str
    status: str


class VerifierRunResult(_WireBase):
    verifier_run_id: str | None = None
    anonymous_verifier_run_id: str | None = None
    remaining_anonymous_runs: int | None = None
    verifier_id: str
    version: int
    status: str
    passed: bool | None = None
    reasoning: str | None = None
    duration_ms: int | None = None
    created_at: str
    error_code: str | None = None
    error_message: str | None = None


class VerifierRevokeResult(_WireBase):
    verifier_id: str
    name: str
    revoked: bool


# ----- invitations -----


class InvitationSummary(_WireBase):
    id: str
    kind: str
    target_id: str
    target_label: str
    proposed_by_handle: str
    proposed_to_handle: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None


class InvitationList(_WireBase):
    items: list[InvitationSummary]
    next_cursor: str | None = None


class InvitationAcceptResult(_WireBase):
    """Shape of the action performed on accept (varies by invitation kind)."""

    model_config = ConfigDict(extra="allow")


class InvitationDeclineResult(_WireBase):
    invitation_id: str
    resolution: str


class InvitationCancelResult(_WireBase):
    invitation_id: str
    resolution: str


# ----- invite-envelope shapes for propose operations -----


class InvitationEnvelope(_WireBase):
    """Returned by propose operations when an invitation is created."""

    invitation_id: str
    kind: str
    expires_at: datetime
