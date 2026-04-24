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
    visibility: str
    current_version: int
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None
    owner_user_id: str | None = None


class WorkflowList(_WireBase):
    items: list[WorkflowSummary]
    next_cursor: str | None = None


class WorkflowDetail(_WireBase):
    id: str
    name: str
    visibility: str
    version: int
    body: str
    description: str = ""
    outcome: str = ""
    tags: list[str] = Field(default_factory=list)
    owner_user_id: str | None = None
    updated_at: datetime | None = None


class WorkflowSaveResult(_WireBase):
    workflow_id: str
    version: int
    name: str
    visibility: str


class WorkflowVisibilityResult(_WireBase):
    workflow_id: str
    name: str
    visibility: str


class WorkflowDeleteResult(_WireBase):
    workflow_id: str
    name: str
    deleted: bool


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
