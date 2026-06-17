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
    ignore_defaults: list[str] = Field(default_factory=list)


class MeResponse(_WireBase):
    email: str
    handle: str | None = None
    handle_claimed_at: datetime | None = None


class UsageResponse(_WireBase):
    """Spendable-balance summary returned by GET /v1/me/usage.

    Money-shaped fields are strings to preserve exact decimal values across
    the wire (callers parse them with ``Decimal`` or ``float`` as needed).
    ``available_usd`` is what the caller can spend right now: monthly grant
    plus any one-off purchased credits and referral bonus credits, minus
    carried-over unpaid balance. ``referral_remaining_usd`` defaults to 0.00
    so responses from servers that predate the field still parse.
    """

    tier: str
    available_usd: str
    monthly_remaining_usd: str
    monthly_refill_usd: str
    monthly_refill_at: datetime
    purchased_remaining_usd: str
    referral_remaining_usd: str = "0.00"
    unpaid_balance_usd: str


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
    # ISO-8601 timestamp for archived rows, null for live rows. Only
    # populated for the caller's own archived workflows, surfaced when
    # `list_workflows` is called with `include_archived=true`.
    archived_at: datetime | None = None


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


class WorkflowImageGeneratorRefWire(_WireBase):
    """Image generator binding on a workflow.

    ``generator_ref`` is a system tier (``system:<tier>``), a deployed
    generator UUID, or a pinned ``<uuid>@<version>``. ``name`` is the local
    handle used in the workflow's generation steps.
    """

    name: str
    generator_ref: str


class WorkflowFileEntry(_WireBase):
    """One row in the file manifest returned by GET /v1/workflows/{id}."""

    path: str
    sha256: str | None = None
    size_bytes: int = 0
    executable: bool = False
    content_kind: str = "text"
    purpose: str | None = None
    fetchable_over_mcp: bool = True
    execution_gated: bool = False
    safety_verification_status: str | None = None


class FileEntryWire(_WireBase):
    """One file entry in the POST /v1/workflows `files` array.

    An inline entry carries exactly one content channel: ``content`` for
    verbatim UTF-8 text, or ``content_base64`` for base64-encoded bytes
    (binary siblings, or text whose bytes the CLI could not send through
    ``content``). A reference entry carries ``sha256`` instead so the
    server reuses an existing blob without a re-upload.
    """

    path: str
    executable: bool = False
    purpose: str | None = None
    content: str | None = None
    content_base64: str | None = None
    sha256: str | None = None


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
    image_generators: list[WorkflowImageGeneratorRefWire] = Field(default_factory=list)
    files: list[WorkflowFileEntry] = Field(default_factory=list)
    safety_verification_status: str | None = None
    # ISO-8601 timestamp for an archived workflow, null for a live one. Only
    # the owner can fetch an archived workflow, so this is null for grantees.
    archived_at: datetime | None = None


class WorkflowSaveResult(_WireBase):
    workflow_id: str
    version: int
    name: str
    version_token: str
    verifiers: list[WorkflowVerifierRefWire] = Field(default_factory=list)
    image_generators: list[WorkflowImageGeneratorRefWire] = Field(default_factory=list)
    # Advisory notes about a saved workflow's demo (e.g. a demo image that was
    # referenced but not attached, or a video host that will not embed).
    # Defaults to empty so responses from older servers still parse.
    authoring_notes: list[str] = Field(default_factory=list)


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
    image_generators: list[WorkflowImageGeneratorRefWire] = Field(default_factory=list)
    # None means omit from the payload (server carries the tree forward).
    # An empty list explicitly clears the file tree on the server.
    files: list[FileEntryWire] | None = None


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
    includes_full_history: bool = False
    shared_from_version: int | None = None


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


class WorkflowArchiveResult(_WireBase):
    workflow_id: str
    name: str
    archived: bool


class WorkflowUnarchiveResult(_WireBase):
    workflow_id: str
    name: str


class WorkflowDeleteVersionResult(_WireBase):
    workflow_id: str
    version: int
    deleted: bool


class WorkflowLineage(_WireBase):
    workflow_id: str
    parent_template_id: str | None = None
    parent_template_version: int | None = None
    upstream_latest_version: int | None = None
    is_upstream_unpublished: bool | None = None
    parent_source_status: str | None = None
    parent_permanently_deleted: bool | None = None
    parent_template_archived_at: str | None = None
    parent_template_archive_reason: str | None = None
    parent_version_deprecated_at: str | None = None
    parent_version_deprecation_message: str | None = None


class WorkflowTeachResult(_WireBase):
    workflow_id: str
    skill_md: str


class WorkflowOptimizeResult(_WireBase):
    workflow_id: str
    skill_md: str
    references: dict[str, str] = Field(default_factory=dict)
    max_iterations: int


class WorkflowAuditResult(_WireBase):
    skill_md: str
    references: dict[str, str] = Field(default_factory=dict)
    workflow_id: str | None = None


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
    safety_verification_status: str = "unverified"
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


class TemplateImageGeneratorSnapshotWire(_WireBase):
    """Public metadata for an image generator attached to a template version.

    A system-tier binding carries only ``name`` and ``system_name``; a deployed
    binding carries the pinned config fields. ``extra="ignore"`` (via the base)
    keeps the model forgiving across snapshot shapes.

    ``generator_id`` is present on a deployed binding for both owners and
    non-owners; ``default_params`` is returned to owners only.
    """

    name: str
    system_name: str | None = None
    generator_id: str | None = None
    generator_version: int | None = None
    provider: str | None = None
    model: str | None = None
    generation_contract: str | None = None
    default_params: dict[str, Any] = Field(default_factory=dict)
    config_hash: str | None = None


class TemplateSafetyVerification(_WireBase):
    status: str = "unverified"
    advisory_run_id: str | None = None
    advisory_reasoning: str | None = None


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
    safety_verification: TemplateSafetyVerification | None = None
    published_at: datetime | None = None
    unpublished_at: datetime | None = None
    # ISO-8601 timestamp for an archived template, null for a live one.
    archived_at: datetime | None = None
    verifier_snapshots: list[TemplateVerifierSnapshotWire] = Field(default_factory=list)
    image_generator_snapshots: list[TemplateImageGeneratorSnapshotWire] = Field(
        default_factory=list
    )
    files: list[WorkflowFileEntry] = Field(default_factory=list)


class TemplatePublishResult(_WireBase):
    template_id: str
    version: int
    publishing_handle: str
    safety_verification: TemplateSafetyVerification | None = None
    # Advisory notes about the published template's demo (e.g. a demo image that
    # was referenced but not attached, or a video host that will not embed).
    # Defaults to empty so responses from older servers still parse.
    authoring_notes: list[str] = Field(default_factory=list)


class SafetyCheckVerifierRun(_WireBase):
    """One side (block or advisory) of a `safety-check` response."""

    verifier_id: str | None = None
    verifier_version: int | None = None
    verifier_run_id: str | None = None
    verdict: str
    reasoning: str | None = None


class SafetyCheckResult(_WireBase):
    """Response shape for `POST /v1/{workflows,templates}/{id}/safety-check`."""

    resource_type: str
    resource_id: str
    resource_version: int
    status: str
    block: SafetyCheckVerifierRun
    advisory: SafetyCheckVerifierRun


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
    slug: str | None = None
    deleted: bool


class TemplateArchiveResult(_WireBase):
    template_id: str
    archived: bool


class TemplateUnarchiveResult(_WireBase):
    template_id: str


class TemplateDeleteVersionResult(_WireBase):
    template_id: str
    version: int
    deleted: bool


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


class VerifierDeleteResult(_WireBase):
    verifier_id: str
    name: str
    deleted: bool


# ----- invitations -----


class InvitationSummary(_WireBase):
    id: str
    kind: str
    target_id: str
    # target_label is None when the underlying team / workflow / template has
    # been deleted while the invitation is still pending; pending_invitations
    # has no FK on target_id, so the row outlives its target.
    target_label: str | None = None
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


# ----- image generators -----


class ImageGeneratorSummary(_WireBase):
    """One row from GET /v1/image-generators (list)."""

    generator_id: str
    name: str
    description: str = ""
    current_version: int
    version_token: str
    status: str
    scope: str = "user"
    created_at: str | None = None
    updated_at: str | None = None


class ImageGeneratorList(_WireBase):
    items: list[ImageGeneratorSummary]
    next_cursor: str | None = None


class ImageGeneratorDetail(_WireBase):
    """Full version detail from GET /v1/image-generators/{id} or POST /v1/image-generators."""

    generator_id: str
    name: str
    description: str = ""
    current_version: int
    version: int
    version_token: str
    status: str
    scope: str = "user"
    provider: str = "fal"
    model: str
    generation_contract: str
    default_params: dict[str, Any] = Field(default_factory=dict)
    config_hash: str
    created_at: str | None = None
    updated_at: str | None = None


class ImageGeneratorDeployResult(_WireBase):
    """Response from POST /v1/image-generators."""

    generator_id: str
    name: str
    description: str = ""
    current_version: int
    version: int
    version_token: str
    status: str
    scope: str = "user"
    provider: str = "fal"
    model: str
    generation_contract: str
    config_hash: str
    created_at: str | None = None


class ImageGeneratorRevokeResult(_WireBase):
    generator_id: str
    name: str
    revoked: bool


class ImageGeneratorDeleteResult(_WireBase):
    generator_id: str
    name: str
    deleted: bool


class ImageGenerationRunResult(_WireBase):
    """Response from POST /v1/image-generators/{id}/runs."""

    run_id: str
    model_tier_or_model: str
    image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    num_images: int = 1
    cost_usd: str = "0"
    duration_ms: int | None = None
    status: str
    created_at: str
    error_code: str | None = None
    error_message: str | None = None


# ----- referrals -----


class ReferralStatusResponse(_WireBase):
    """Response from GET /v1/referrals/me."""

    code: str
    instructions: str
    redeemed_count: int
    activated_count: int
    credits_earned_usd: str
    slots_remaining: int


class RedeemResponse(_WireBase):
    """Response from POST /v1/referrals/redeem."""

    status: str
    credits_granted_usd: str
    expires_at: datetime
    referrer_handle: str


# ----- hosted images -----


class ImageDetail(_WireBase):
    """One image record returned by GET /v1/images/{id} or POST /v1/images."""

    id: str
    token: str
    url: str
    visibility: str
    expires_at: datetime | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    source: str | None = None
    created_at: str | None = None


class ImageList(_WireBase):
    items: list[ImageDetail]
    next_cursor: str | None = None
