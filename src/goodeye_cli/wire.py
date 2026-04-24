"""Pydantic wire models for Goodeye REST responses.

Only the shapes the CLI reads. No domain models from the server are mirrored;
these are deliberately minimal and permissive so minor additive server changes
do not break old CLI releases.
"""

from __future__ import annotations

from datetime import datetime

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
    is_ephemeral: bool = False


class WorkflowList(_WireBase):
    items: list[WorkflowSummary]
    next_cursor: str | None = None


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
    is_ephemeral: bool = False


class WorkflowSaveResult(_WireBase):
    workflow_id: str
    version: int
    name: str


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
    is_ephemeral: bool = False


class SignupVerifyResult(_WireBase):
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
    email: str
    handle: str | None = None
    role: str


class TeamDeleteResult(_WireBase):
    team_id: str
    deleted: bool
