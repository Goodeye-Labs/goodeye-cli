"""HTTP client wrapper around the Goodeye REST API.

Sync-first (the CLI is sync), built on ``httpx.Client``. Injects ``Authorization``
when an API key is present, sends a ``User-Agent`` that identifies the CLI
version, and translates non-2xx responses into typed ``errors.GoodeyeError``
subclasses.

The client also speaks directly to WorkOS for the device authorization + token
poll endpoints because those aren't proxied by the Goodeye server.
"""

from __future__ import annotations

from typing import Any

import httpx

from goodeye_cli import __version__
from goodeye_cli.errors import GoodeyeError, error_from_body
from goodeye_cli.wire import (
    ApiKeyCreated,
    ApiKeyList,
    ClaimHandleResult,
    ClientConfig,
    DeviceAuthResponse,
    ExchangeResult,
    MeResponse,
    SignupVerifyResult,
    TeamCreated,
    TeamDeleteResult,
    TeamList,
    TeamMember,
    TemplateDetail,
    TemplateForkResult,
    TemplateList,
    TemplatePublishResult,
    TemplateUnpublishResult,
    WorkflowDeleteResult,
    WorkflowDetail,
    WorkflowLineage,
    WorkflowList,
    WorkflowSaveResult,
)


def _user_agent() -> str:
    return f"goodeye-cli/{__version__}"


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    body: dict[str, Any] | None
    try:
        parsed = response.json()
        body = parsed if isinstance(parsed, dict) else None
    except (ValueError, httpx.DecodingError):
        body = None
    raise error_from_body(response.status_code, body)


class GoodeyeClient:
    """Sync HTTP client for the Goodeye REST API.

    Use as a context manager to ensure the underlying connection pool is closed::

        with GoodeyeClient(server="https://mcp.goodeyelabs.com") as client:
            me = client.get_me()
    """

    def __init__(
        self,
        server: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.api_key = api_key
        headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(
            base_url=self.server,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    # ----- context manager plumbing -----
    def __enter__(self) -> GoodeyeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ----- low-level helpers -----
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if accept is not None:
            headers["Accept"] = accept
        response = self._http.request(
            method,
            path,
            json=json_body,
            params=params,
            headers=headers or None,
        )
        _raise_for_status(response)
        return response

    # ----- client config / auth -----
    def get_client_config(self) -> ClientConfig:
        response = self._request("GET", "/.well-known/goodeye-client-config")
        return ClientConfig.model_validate(response.json())

    def signup(self, email: str) -> None:
        self._request("POST", "/v1/signup", json_body={"email": email})

    def signup_verify(self, email: str, code: str) -> SignupVerifyResult:
        response = self._request(
            "POST", "/v1/signup/verify", json_body={"email": email, "code": code}
        )
        return SignupVerifyResult.model_validate(response.json())

    def login(self, email: str) -> None:
        self._request("POST", "/v1/login", json_body={"email": email})

    def login_verify(self, email: str, code: str) -> SignupVerifyResult:
        response = self._request(
            "POST", "/v1/login/verify", json_body={"email": email, "code": code}
        )
        return SignupVerifyResult.model_validate(response.json())

    def exchange(self, hostname: str | None = None) -> ExchangeResult:
        body: dict[str, Any] = {}
        if hostname:
            body["hostname"] = hostname
        response = self._request("POST", "/v1/auth/exchange", json_body=body)
        return ExchangeResult.model_validate(response.json())

    # ----- me / api keys -----
    def get_me(self) -> MeResponse:
        response = self._request("GET", "/v1/me")
        return MeResponse.model_validate(response.json())

    def claim_handle(self, handle: str) -> ClaimHandleResult:
        response = self._request("PATCH", "/v1/me", json_body={"handle": handle})
        return ClaimHandleResult.model_validate(response.json())

    def create_api_key(self, name: str) -> ApiKeyCreated:
        response = self._request("POST", "/v1/api-keys", json_body={"name": name})
        return ApiKeyCreated.model_validate(response.json())

    def list_api_keys(self, limit: int = 50, cursor: str | None = None) -> ApiKeyList:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = self._request("GET", "/v1/api-keys", params=params)
        return ApiKeyList.model_validate(response.json())

    def revoke_api_key(self, key_id: str) -> None:
        self._request("DELETE", f"/v1/api-keys/{key_id}")

    # ----- workflows -----
    def list_workflows(
        self,
        filter_: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> WorkflowList:
        params: dict[str, Any] = {"limit": limit}
        if filter_:
            params["filter"] = filter_
        if tag:
            params["tag"] = tag
        if search:
            params["search"] = search
        if cursor:
            params["cursor"] = cursor
        response = self._request("GET", "/v1/workflows", params=params)
        return WorkflowList.model_validate(response.json())

    def get_workflow(
        self,
        id_or_slug: str,
        *,
        version: int | None = None,
        accept_markdown: bool = False,
    ) -> WorkflowDetail | str:
        """Fetch a workflow. Returns a ``WorkflowDetail`` for JSON responses or a raw
        markdown string when ``accept_markdown=True``.
        """
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        accept = "text/markdown" if accept_markdown else "application/json"
        response = self._request("GET", f"/v1/workflows/{id_or_slug}", params=params, accept=accept)
        if accept_markdown:
            return response.text
        return WorkflowDetail.model_validate(response.json())

    def save_workflow(
        self,
        *,
        name: str,
        description: str,
        body: str,
        outcome: str | None = None,
        tags: list[str] | None = None,
    ) -> WorkflowSaveResult:
        """POST /v1/workflows with the flat ``save_workflow`` payload.

        Workflows are always private to the caller. Public sharing is a
        separate explicit step (``publish_template_version``). ``outcome``
        and ``tags`` are top-level discovery facets surfaced by
        ``list_workflows``.
        """
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "body": body,
        }
        if outcome:
            payload["outcome"] = outcome
        if tags:
            payload["tags"] = list(tags)
        response = self._request("POST", "/v1/workflows", json_body=payload)
        return WorkflowSaveResult.model_validate(response.json())

    def delete_workflow(self, workflow_id: str) -> WorkflowDeleteResult:
        response = self._request("DELETE", f"/v1/workflows/{workflow_id}")
        return WorkflowDeleteResult.model_validate(response.json())

    def lookup_workflow_lineage(self, id_or_slug: str) -> WorkflowLineage:
        response = self._request("GET", f"/v1/workflows/{id_or_slug}/lineage")
        return WorkflowLineage.model_validate(response.json())

    # ----- templates -----
    def list_templates(
        self,
        *,
        filter_: str | None = None,
        search: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TemplateList:
        params: dict[str, Any] = {"limit": limit}
        if filter_:
            params["filter"] = filter_
        if search:
            params["search"] = search
        if cursor:
            params["cursor"] = cursor
        response = self._request("GET", "/v1/templates", params=params)
        return TemplateList.model_validate(response.json())

    def get_template(
        self,
        identifier: str,
        *,
        version: int | None = None,
        accept_markdown: bool = False,
    ) -> TemplateDetail | str:
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        accept = "text/markdown" if accept_markdown else "application/json"
        response = self._request("GET", f"/v1/templates/{identifier}", params=params, accept=accept)
        if accept_markdown:
            return response.text
        return TemplateDetail.model_validate(response.json())

    def publish_template_version(
        self, workflow_id: str, *, release_notes: str | None = None
    ) -> TemplatePublishResult:
        body: dict[str, Any] = {"workflow_id": workflow_id}
        if release_notes is not None:
            body["release_notes"] = release_notes
        response = self._request("POST", "/v1/templates", json_body=body)
        return TemplatePublishResult.model_validate(response.json())

    def unpublish_template_version(self, template_id: str, version: int) -> TemplateUnpublishResult:
        response = self._request("DELETE", f"/v1/templates/{template_id}/versions/{version}")
        return TemplateUnpublishResult.model_validate(response.json())

    def fork_template(
        self,
        identifier: str,
        *,
        version: int | None = None,
        name: str | None = None,
    ) -> TemplateForkResult:
        body: dict[str, Any] = {"identifier": identifier}
        if version is not None:
            body["version"] = version
        if name is not None:
            body["name"] = name
        response = self._request("POST", "/v1/templates/fork", json_body=body)
        return TemplateForkResult.model_validate(response.json())

    # ----- teams -----
    def create_team(self, handle: str) -> TeamCreated:
        response = self._request("POST", "/v1/teams", json_body={"handle": handle})
        return TeamCreated.model_validate(response.json())

    def list_teams(self, *, filter_: str = "all") -> TeamList:
        response = self._request("GET", "/v1/teams", params={"filter": filter_})
        return TeamList.model_validate(response.json())

    def delete_team(self, team_id: str) -> TeamDeleteResult:
        response = self._request("DELETE", f"/v1/teams/{team_id}")
        return TeamDeleteResult.model_validate(response.json())

    def list_team_members(self, team_id: str) -> list[TeamMember]:
        response = self._request("GET", f"/v1/teams/{team_id}/members")
        return [TeamMember.model_validate(row) for row in response.json()]

    def add_team_member(self, team_id: str, user_id_or_email: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/teams/{team_id}/members",
            json_body={"user_id_or_email": user_id_or_email},
        )
        return response.json()

    def remove_team_member(self, team_id: str, user_id: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/v1/teams/{team_id}/members/{user_id}")
        return response.json()

    def transfer_team_ownership(self, team_id: str, new_owner_user_id: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/teams/{team_id}/transfer-ownership",
            json_body={"new_owner_user_id": new_owner_user_id},
        )
        return response.json()

    def get_design_prompt(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/design/workflow-prompt")
        data = response.json()
        if not isinstance(data, dict):
            raise GoodeyeError(
                slug="internal_error",
                message="Unexpected response from /v1/design/workflow-prompt.",
            )
        return data


def request_device_authorization(
    device_authorization_uri: str,
    client_id: str,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> DeviceAuthResponse:
    """Issue the initial device-authorization request to WorkOS."""
    with httpx.Client(
        timeout=timeout, transport=transport, headers={"User-Agent": _user_agent()}
    ) as http:
        response = http.post(device_authorization_uri, data={"client_id": client_id})
    if response.is_error:
        body: dict[str, Any] | None
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else None
        except (ValueError, httpx.DecodingError):
            body = None
        raise error_from_body(response.status_code, body)
    return DeviceAuthResponse.model_validate(response.json())


def poll_device_token(
    token_uri: str,
    client_id: str,
    device_code: str,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Single poll against the WorkOS token endpoint.

    Returns ``(status_code, parsed_json)``. Callers inspect status + body to
    decide whether to continue polling (``authorization_pending`` / ``slow_down``)
    or stop (``200`` success, any other error).
    """
    with httpx.Client(
        timeout=timeout, transport=transport, headers={"User-Agent": _user_agent()}
    ) as http:
        response = http.post(
            token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
        )
    try:
        parsed = response.json()
        body: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
    except (ValueError, httpx.DecodingError):
        body = {}
    return response.status_code, body
