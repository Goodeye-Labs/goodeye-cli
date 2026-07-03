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
from urllib.parse import unquote

import httpx

from goodeye_cli import __version__
from goodeye_cli.config import get_request_timeout_seconds
from goodeye_cli.errors import GoodeyeError, NetworkError, error_from_body
from goodeye_cli.wire import (
    ApiKeyCreated,
    ApiKeyList,
    AuthVerifyResult,
    AutoTopUpResult,
    CheckoutResult,
    ClaimHandleResult,
    ClientConfig,
    CreditPurchaseResult,
    DeviceAuthResponse,
    ExchangeResult,
    ImageDetail,
    ImageGenerationRunResult,
    ImageGeneratorDeleteResult,
    ImageGeneratorDeployResult,
    ImageGeneratorDetail,
    ImageGeneratorList,
    ImageGeneratorRevokeResult,
    ImageList,
    InvitationAcceptResult,
    InvitationCancelResult,
    InvitationDeclineResult,
    InvitationList,
    MeResponse,
    PortalResult,
    RedeemResponse,
    ReferralStatusResponse,
    RenameHandleResult,
    SafetyCheckResult,
    SubscriptionCancelResult,
    TeamCreated,
    TeamDeleteResult,
    TeamList,
    TeamMember,
    TemplateArchiveResult,
    TemplateDeleteResult,
    TemplateDeleteVersionResult,
    TemplateDeprecateVersionResult,
    TemplateDetail,
    TemplateForkResult,
    TemplateList,
    TemplatePublishResult,
    TemplateSearchResponse,
    TemplateUnarchiveResult,
    TemplateUnpublishResult,
    UsageResponse,
    VerifierDeleteResult,
    VerifierDeployResult,
    VerifierList,
    VerifierRevokeResult,
    VerifierRunResult,
    VerifierVersionDetail,
    WorkflowArchiveResult,
    WorkflowAuditResult,
    WorkflowDeleteResult,
    WorkflowDeleteVersionResult,
    WorkflowDetail,
    WorkflowGrantList,
    WorkflowGrantResult,
    WorkflowGrantRevokeResult,
    WorkflowLeaveResult,
    WorkflowLineage,
    WorkflowList,
    WorkflowOptimizeResult,
    WorkflowSaveResult,
    WorkflowSearchResponse,
    WorkflowTeachResult,
    WorkflowUnarchiveResult,
)


def _user_agent() -> str:
    return f"goodeye-cli/{__version__}"


def _network_error(server: str, exc: httpx.RequestError) -> NetworkError:
    """Translate a raw transport failure into a humane, exit-coded error.

    ``httpx.RequestError`` is the base for every transport-level failure
    (connect refused, DNS, proxy, read/connect timeout). They are raised
    before any response exists, so ``_raise_for_status`` never sees them and
    callers would otherwise get a multi-frame ``httpx`` traceback.
    """
    return NetworkError(
        slug="network_error",
        message="Could not reach Goodeye. Check your connection and try again.",
        hint=f"server: {server}",
    )


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

        with GoodeyeClient(server="https://api.goodeye.dev") as client:
            me = client.get_me()
    """

    def __init__(
        self,
        server: str,
        *,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.api_key = api_key
        # Omit default ``Accept`` here; :meth:`_request` sets it per call so httpx
        # does not merge ``application/json`` with ``text/markdown`` on GETs.
        headers = {"User-Agent": _user_agent()}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(
            base_url=self.server,
            headers=headers,
            timeout=get_request_timeout_seconds() if timeout is None else timeout,
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
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        accept: str | None = None,
        follow_redirects: bool = False,
        authenticated: bool = True,
    ) -> httpx.Response:
        merged: dict[str, str] = dict(self._http.headers)
        if not authenticated:
            for header_name in list(merged):
                if header_name.lower() == "authorization":
                    del merged[header_name]
        accept_value = "application/json" if accept is None else accept
        merged["accept"] = accept_value
        merged.pop("Accept", None)
        request = self._http.build_request(
            method,
            path,
            json=json_body,
            params=params,
            headers=merged,
        )
        if not authenticated:
            # Client default headers are merged into the request; omitting the key
            # from ``merged`` alone does not drop ``Authorization`` (see httpx
            # ``Client._merge_headers``). Remove any Authorization after merge.
            for header_name in list(request.headers.keys()):
                if header_name.lower() == "authorization":
                    del request.headers[header_name]
        try:
            response = self._http.send(request, follow_redirects=follow_redirects)
        except httpx.RequestError as exc:
            raise _network_error(self.server, exc) from exc
        _raise_for_status(response)
        return response

    def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        files: dict[str, Any],
        data: dict[str, str],
    ) -> httpx.Response:
        """Send a multipart/form-data request (used for file uploads)."""
        headers = dict(self._http.headers)
        # Let httpx set Content-Type with the boundary.
        headers.pop("content-type", None)
        headers.pop("Content-Type", None)
        headers["accept"] = "application/json"
        headers.pop("Accept", None)
        try:
            response = self._http.request(
                method,
                path,
                files=files,
                data=data,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise _network_error(self.server, exc) from exc
        _raise_for_status(response)
        return response

    # ----- client config / auth -----
    def get_client_config(self) -> ClientConfig:
        response = self._request("GET", "/.well-known/goodeye-client-config")
        return ClientConfig.model_validate(response.json())

    def register(self, email: str) -> None:
        self._request("POST", "/v1/register", json_body={"email": email})

    def register_verify(self, email: str, code: str) -> AuthVerifyResult:
        response = self._request(
            "POST", "/v1/register/verify", json_body={"email": email, "code": code}
        )
        return AuthVerifyResult.model_validate(response.json())

    def login(self, email: str) -> None:
        self._request("POST", "/v1/login", json_body={"email": email})

    def login_verify(self, email: str, code: str) -> AuthVerifyResult:
        response = self._request(
            "POST", "/v1/login/verify", json_body={"email": email, "code": code}
        )
        return AuthVerifyResult.model_validate(response.json())

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

    def get_usage(self) -> UsageResponse:
        response = self._request("GET", "/v1/me/usage")
        return UsageResponse.model_validate(response.json())

    def get_referral_status(self) -> ReferralStatusResponse:
        response = self._request("GET", "/v1/referrals/me")
        return ReferralStatusResponse.model_validate(response.json())

    def redeem_referral_code(self, code: str) -> RedeemResponse:
        response = self._request("POST", "/v1/referrals/redeem", json_body={"code": code})
        return RedeemResponse.model_validate(response.json())

    def claim_handle(self, handle: str) -> ClaimHandleResult:
        response = self._request("PATCH", "/v1/me", json_body={"handle": handle})
        return ClaimHandleResult.model_validate(response.json())

    def rename_handle(self, handle: str) -> RenameHandleResult:
        response = self._request("POST", "/v1/me/rename-handle", json_body={"handle": handle})
        return RenameHandleResult.model_validate(response.json())

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

    # ----- billing / Pro subscription -----
    def start_checkout(self) -> CheckoutResult:
        """POST /v1/billing/checkout: begin a Pro subscription purchase.

        Returns a Stripe-hosted checkout link. Raises ``already_subscribed``
        when the caller already holds an active subscription, or
        ``billing_not_enabled`` when self-service billing is off.
        """
        response = self._request("POST", "/v1/billing/checkout", json_body={})
        return CheckoutResult.model_validate(response.json())

    def cancel_subscription(self) -> SubscriptionCancelResult:
        """POST /v1/billing/subscription/cancel: cancel at period end.

        Pro access continues through the current paid period; this does not
        revoke access immediately. Raises ``no_active_subscription`` when
        there is nothing to cancel, or ``billing_not_enabled`` when
        self-service billing is off.
        """
        response = self._request("POST", "/v1/billing/subscription/cancel", json_body={})
        return SubscriptionCancelResult.model_validate(response.json())

    def open_billing_portal(self) -> PortalResult:
        """POST /v1/billing/portal: a Stripe-hosted portal link.

        Raises ``billing_not_enabled`` when self-service billing is off.
        """
        response = self._request("POST", "/v1/billing/portal", json_body={})
        return PortalResult.model_validate(response.json())

    def purchase_credits(
        self, amount_usd: int, *, idempotency_key: str | None = None
    ) -> CreditPurchaseResult:
        """POST /v1/billing/credits/purchase: buy a one-time credit top-up.

        Charges the default payment method on file and returns a ``charged``
        result with the new balance when one exists, or a
        ``checkout_required`` result with a hosted checkout link otherwise.
        Raises ``invalid_amount`` when the requested amount is outside the
        allowed range, or ``billing_not_enabled`` when self-service billing
        is off. ``idempotency_key`` should be a fresh value per call so a
        transport-level retry cannot charge the card twice.
        """
        body: dict[str, Any] = {"amount_usd": amount_usd}
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        response = self._request("POST", "/v1/billing/credits/purchase", json_body=body)
        return CreditPurchaseResult.model_validate(response.json())

    def get_auto_top_up(self) -> AutoTopUpResult:
        """GET /v1/billing/auto-top-up: your automatic credit top-up configuration.

        Returns the config block plus this month's spend toward it. A caller
        who has never configured automatic top-ups gets a disabled default
        block, not an error.
        """
        response = self._request("GET", "/v1/billing/auto-top-up")
        return AutoTopUpResult.model_validate(response.json())

    def configure_auto_top_up(
        self,
        *,
        amount_usd: int,
        threshold_usd: int | None = None,
        monthly_cap_usd: int | None = None,
    ) -> AutoTopUpResult:
        """PUT /v1/billing/auto-top-up: turn on automatic top-ups and set their terms.

        Requires a default payment method already on file (a manual credit
        purchase saves one). Returns the resolved config block: the server
        defaults ``threshold_usd`` to ``amount_usd`` and ``monthly_cap_usd``
        to four times ``amount_usd`` when either is omitted, and echoes the
        resolved values back. Re-running this also clears any previously
        failed automatic top-up state, acting as a retry. Raises
        ``no_payment_method`` when no default payment method is on file, or
        ``invalid_amount`` when the amount, threshold, or monthly cap falls
        outside the allowed range.
        """
        body: dict[str, Any] = {"amount_usd": amount_usd}
        if threshold_usd is not None:
            body["threshold_usd"] = threshold_usd
        if monthly_cap_usd is not None:
            body["monthly_cap_usd"] = monthly_cap_usd
        response = self._request("PUT", "/v1/billing/auto-top-up", json_body=body)
        return AutoTopUpResult.model_validate(response.json())

    def disable_auto_top_up(self) -> AutoTopUpResult:
        """DELETE /v1/billing/auto-top-up: turn off automatic credit top-ups.

        Returns the config block with ``enabled`` false. Leaves the
        previously configured amount, threshold, and monthly cap in place for
        a later re-enable.
        """
        response = self._request("DELETE", "/v1/billing/auto-top-up")
        return AutoTopUpResult.model_validate(response.json())

    # ----- workflows -----
    def list_workflows(
        self,
        filter_: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_archived: bool = False,
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
        if include_archived:
            params["include_archived"] = "true"
        response = self._request("GET", "/v1/workflows", params=params)
        return WorkflowList.model_validate(response.json())

    def search_workflows(
        self,
        *,
        query: str,
        filter_: str = "all",
        tag: str | None = None,
        limit: int = 5,
    ) -> WorkflowSearchResponse:
        body: dict[str, Any] = {
            "query": query,
            "filter": filter_,
            "limit": limit,
        }
        if tag is not None:
            body["tag"] = tag
        response = self._request("POST", "/v1/workflows/search", json_body=body)
        return WorkflowSearchResponse.model_validate(response.json())

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
        expected_version_token: str | None = None,
        source: str | None = None,
        verifiers: list[dict[str, Any]] | None = None,
        image_generators: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> WorkflowSaveResult:
        """POST /v1/workflows with the flat ``save_workflow`` payload.

        Workflows are always private to the caller. Public sharing is a
        separate explicit step (``publish_template_version``). ``outcome``
        and ``tags`` are top-level discovery facets surfaced by
        ``list_workflows``. ``source`` is an optional provenance marker
        (e.g. 'manual' or 'teach').

        ``files`` controls the workflow file tree. Omit it (``None``) to carry
        the existing tree forward unchanged. Pass an empty list to clear the
        tree. Pass a non-empty list to replace the tree with the supplied files.

        ``image_generators`` binds image generators to the workflow, each entry
        ``{"name", "generator_ref"}``. Omit it (``None``) to carry the existing
        bindings forward; pass an empty list to clear them.
        """
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "body": body,
            "expected_version_token": expected_version_token,
        }
        if outcome:
            payload["outcome"] = outcome
        if tags:
            payload["tags"] = list(tags)
        if source is not None:
            payload["source"] = source
        if verifiers is not None:
            payload["verifiers"] = list(verifiers)
        if image_generators is not None:
            payload["image_generators"] = list(image_generators)
        if files is not None:
            payload["files"] = list(files)
        response = self._request("POST", "/v1/workflows", json_body=payload)
        return WorkflowSaveResult.model_validate(response.json())

    def archive_workflow(self, workflow_id: str) -> WorkflowArchiveResult:
        response = self._request("POST", f"/v1/workflows/{workflow_id}/archive", json_body={})
        return WorkflowArchiveResult.model_validate(response.json())

    def unarchive_workflow(self, workflow_id: str) -> WorkflowUnarchiveResult:
        response = self._request("POST", f"/v1/workflows/{workflow_id}/unarchive", json_body={})
        return WorkflowUnarchiveResult.model_validate(response.json())

    def delete_workflow(self, workflow_id: str) -> WorkflowDeleteResult:
        response = self._request("DELETE", f"/v1/workflows/{workflow_id}")
        return WorkflowDeleteResult.model_validate(response.json())

    def delete_workflow_version(
        self, workflow_id: str, version: int
    ) -> WorkflowDeleteVersionResult:
        response = self._request("DELETE", f"/v1/workflows/{workflow_id}/versions/{version}")
        return WorkflowDeleteVersionResult.model_validate(response.json())

    def grant_workflow(
        self,
        workflow_id: str,
        grantee_email_or_at_team_handle: str,
        role: str,
        include_history: bool = False,
    ) -> WorkflowGrantResult:
        response = self._request(
            "POST",
            f"/v1/workflows/{workflow_id}/grants",
            json_body={
                "grantee_email_or_at_team_handle": grantee_email_or_at_team_handle,
                "role": role,
                "include_history": include_history,
            },
        )
        return WorkflowGrantResult.model_validate(response.json())

    def revoke_workflow_grant(
        self, workflow_id: str, grantee_email_or_at_team_handle: str
    ) -> WorkflowGrantRevokeResult:
        response = self._request(
            "DELETE",
            f"/v1/workflows/{workflow_id}/grants",
            json_body={"grantee_email_or_at_team_handle": grantee_email_or_at_team_handle},
        )
        return WorkflowGrantRevokeResult.model_validate(response.json())

    def list_workflow_grants(self, workflow_id: str) -> WorkflowGrantList:
        response = self._request("GET", f"/v1/workflows/{workflow_id}/grants")
        return WorkflowGrantList.model_validate(response.json())

    def leave_shared_workflow(self, workflow_id: str) -> WorkflowLeaveResult:
        response = self._request("POST", f"/v1/workflows/{workflow_id}/leave")
        return WorkflowLeaveResult.model_validate(response.json())

    def transfer_workflow_ownership(
        self, workflow_id: str, new_owner_user_id_or_email: str
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/workflows/{workflow_id}/transfer-ownership",
            json_body={"new_owner_user_id_or_email": new_owner_user_id_or_email},
        )
        return response.json()

    def lookup_workflow_lineage(self, id_or_slug: str) -> WorkflowLineage:
        response = self._request("GET", f"/v1/workflows/{id_or_slug}/lineage")
        return WorkflowLineage.model_validate(response.json())

    def teach_workflow(self, workflow_id: str) -> WorkflowTeachResult:
        response = self._request("POST", f"/v1/workflows/{workflow_id}/teach")
        return WorkflowTeachResult.model_validate(response.json())

    def optimize_workflow(
        self, workflow_id: str, *, max_iterations: int | None = None
    ) -> WorkflowOptimizeResult:
        """POST /v1/workflows/{id}/optimize with optional max_iterations.

        Returns the SKILL pack (skill_md + references), the workflow id, and
        the validated iteration cap. The server defaults max_iterations to 20
        and accepts values from 1 to 1000.
        """
        params: dict[str, Any] = {}
        if max_iterations is not None:
            params["max_iterations"] = max_iterations
        response = self._request(
            "POST",
            f"/v1/workflows/{workflow_id}/optimize",
            params=params or None,
        )
        return WorkflowOptimizeResult.model_validate(response.json())

    def optimize_description(
        self, workflow_id: str, *, max_iterations: int | None = None
    ) -> WorkflowOptimizeResult:
        """POST /v1/workflows/{id}/optimize-description with optional max_iterations.

        Returns the SKILL pack (skill_md + references), the workflow id, and
        the validated iteration cap for the description-tuning loop. The server
        defaults max_iterations to 10 and accepts values from 1 to 1000. The
        response shape matches the optimize pack, so it reuses
        WorkflowOptimizeResult.
        """
        params: dict[str, Any] = {}
        if max_iterations is not None:
            params["max_iterations"] = max_iterations
        response = self._request(
            "POST",
            f"/v1/workflows/{workflow_id}/optimize-description",
            params=params or None,
        )
        return WorkflowOptimizeResult.model_validate(response.json())

    def audit_workflow(self, workflow_id: str | None = None) -> WorkflowAuditResult:
        """Fetch the audit SKILL pack.

        With a workflow_id, POST /v1/workflows/{id}/audit returns the pack plus
        the workflow id. Without one, GET /v1/audit/workflow-prompt returns the
        pack for auditing a local skill that is not on Goodeye yet.
        """
        if workflow_id is not None:
            response = self._request("POST", f"/v1/workflows/{workflow_id}/audit")
        else:
            response = self._request("GET", "/v1/audit/workflow-prompt")
        return WorkflowAuditResult.model_validate(response.json())

    def check_workflow_safety(
        self, workflow_id: str, *, version: int | None = None
    ) -> SafetyCheckResult:
        """POST /v1/workflows/{workflow_id}/safety-check.

        Runs the block and advisory safety verifiers against the workflow
        body and returns a combined verdict. Auth required.
        """
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        response = self._request(
            "POST",
            f"/v1/workflows/{workflow_id}/safety-check",
            params=params or None,
        )
        return SafetyCheckResult.model_validate(response.json())

    def get_workflow_file(self, workflow_id: str, path: str) -> dict[str, Any]:
        """GET /v1/workflows/{workflow_id}/files?path=<path>.

        Returns the file envelope for a single file. The envelope contains
        ``path``, ``content_kind``, ``size_bytes``, ``executable``, ``purpose``,
        ``safety_verification_status``, ``execution_gated``, and exactly one of
        ``content`` (text), ``content_base64`` (small binary), or ``error``.
        """
        response = self._request(
            "GET",
            f"/v1/workflows/{workflow_id}/files",
            params={"path": path},
        )
        return response.json()

    def get_workflow_files(self, workflow_id: str, paths: list[str]) -> dict[str, Any]:
        """GET /v1/workflows/{workflow_id}/files?paths=A&paths=B (batch).

        Returns ``{"files": [<envelope>, ...]}``. Envelopes are returned in
        lexicographic path order. An envelope may carry ``error`` instead of
        content if the path was not found or exceeds the inline size cap.
        """
        response = self._request(
            "GET",
            f"/v1/workflows/{workflow_id}/files",
            params=[("paths", p) for p in paths],
        )
        return response.json()

    def get_workflow_file_raw(
        self, workflow_id: str, path: str, *, sha256: str | None = None
    ) -> bytes:
        """GET /v1/workflows/{workflow_id}/files?path=<path>&format=raw.

        Returns the file's raw bytes (the response body). Unlike the JSON
        ``get_workflow_file`` route, raw serving applies no inline binary
        ceiling, so this is the only way to retrieve a binary larger than the
        server's inline limit (the ``binary_too_large_for_inline`` case) -- the
        sync pull uses it to mirror over-ceiling demo assets to disk.

        Pass ``sha256`` to content-address the fetch: the file is resolved by
        path and then confirmed to match the address, so a republished or
        removed file no longer resolves at a stale address (returns not found).
        """
        params: dict[str, str] = {"path": path, "format": "raw"}
        if sha256 is not None:
            params["sha256"] = sha256
        response = self._request(
            "GET",
            f"/v1/workflows/{workflow_id}/files",
            params=params,
        )
        return response.content

    # ----- templates -----
    def list_templates(
        self,
        *,
        filter_: str | None = None,
        search: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_archived: bool = False,
    ) -> TemplateList:
        params: dict[str, Any] = {"limit": limit}
        if filter_:
            params["filter"] = filter_
        if search:
            params["search"] = search
        if cursor:
            params["cursor"] = cursor
        if include_archived:
            params["include_archived"] = "true"
        response = self._request("GET", "/v1/templates", params=params)
        return TemplateList.model_validate(response.json())

    def search_templates(
        self,
        *,
        query: str,
        filter_: str = "all",
        limit: int = 5,
    ) -> TemplateSearchResponse:
        response = self._request(
            "POST",
            "/v1/templates/search",
            json_body={"query": query, "filter": filter_, "limit": limit},
        )
        return TemplateSearchResponse.model_validate(response.json())

    def get_template(
        self,
        identifier: str,
        *,
        version: int | None = None,
        accept_markdown: bool = False,
    ) -> TemplateDetail | str:
        result, _redirect = self.get_template_with_redirect(
            identifier, version=version, accept_markdown=accept_markdown
        )
        return result

    def get_template_with_redirect(
        self,
        identifier: str,
        *,
        version: int | None = None,
        accept_markdown: bool = False,
    ) -> tuple[TemplateDetail | str, str | None]:
        """Like ``get_template`` but also returns the final identifier when the
        server issued a redirect (e.g. the publishing handle was renamed).

        Returns ``(result, final_identifier)``. ``final_identifier`` is ``None``
        when no redirect occurred. The CLI uses this to print a one-line
        stderr note when a handle URL has moved.
        """
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        accept = "text/markdown" if accept_markdown else "application/json"
        response = self._request(
            "GET",
            f"/v1/templates/{identifier}",
            params=params,
            accept=accept,
            follow_redirects=True,
        )
        final_identifier: str | None = None
        if response.history:
            # The final URL path looks like /v1/templates/<identifier>; pull the
            # last segment back out so we can show it to the user.
            final_path = response.url.path
            prefix = "/v1/templates/"
            if final_path.startswith(prefix):
                final_identifier = unquote(final_path[len(prefix) :])
        result: TemplateDetail | str
        if accept_markdown:
            result = response.text
        else:
            result = TemplateDetail.model_validate(response.json())
        return result, final_identifier

    def get_template_file_raw(
        self, identifier: str, path: str, *, sha256: str | None = None
    ) -> bytes:
        """GET /v1/templates/{identifier}/files?path=<path>&format=raw.

        Returns the file's raw bytes (the response body), for pulling a
        template's demo asset (or any attached file) verbatim. ``identifier``
        accepts the same forms as the other template methods (UUID,
        ``@handle/slug``, or ``@handle/slug@vN``). Redirects are followed so a
        renamed publishing handle still resolves.

        Pass ``sha256`` to content-address the fetch: the file is resolved by
        path and then confirmed to match the address, so a republished or
        removed file no longer resolves at a stale address (returns not found).
        """
        params: dict[str, str] = {"path": path, "format": "raw"}
        if sha256 is not None:
            params["sha256"] = sha256
        response = self._request(
            "GET",
            f"/v1/templates/{identifier}/files",
            params=params,
            follow_redirects=True,
        )
        return response.content

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

    def archive_template(
        self, template_id: str, *, archive_reason: str | None = None
    ) -> TemplateArchiveResult:
        body: dict[str, Any] = {}
        if archive_reason is not None:
            body["archive_reason"] = archive_reason
        response = self._request(
            "POST",
            f"/v1/templates/{template_id}/archive",
            json_body=body if body else None,
        )
        return TemplateArchiveResult.model_validate(response.json())

    def unarchive_template(self, template_id: str) -> TemplateUnarchiveResult:
        response = self._request("POST", f"/v1/templates/{template_id}/unarchive", json_body={})
        return TemplateUnarchiveResult.model_validate(response.json())

    def delete_template(self, template_id: str) -> TemplateDeleteResult:
        response = self._request("DELETE", f"/v1/templates/{template_id}")
        return TemplateDeleteResult.model_validate(response.json())

    def delete_template_version(
        self, template_id: str, version: int
    ) -> TemplateDeleteVersionResult:
        response = self._request(
            "DELETE", f"/v1/templates/{template_id}/versions/{version}/permanent"
        )
        return TemplateDeleteVersionResult.model_validate(response.json())

    def deprecate_template_version(
        self, template_id: str, version: int, *, message: str
    ) -> TemplateDeprecateVersionResult:
        response = self._request(
            "POST",
            f"/v1/templates/{template_id}/versions/{version}/deprecate",
            json_body={"message": message},
        )
        return TemplateDeprecateVersionResult.model_validate(response.json())

    def transfer_template_ownership(
        self, template_id: str, new_owner_user_id_or_email: str
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/templates/{template_id}/transfer-ownership",
            json_body={"new_owner_user_id_or_email": new_owner_user_id_or_email},
        )
        return response.json()

    def check_template_safety(
        self,
        template_id: str,
        *,
        version: int | None = None,
        anonymous: bool = False,
    ) -> SafetyCheckResult:
        """POST /v1/templates/{template_id}/safety-check.

        Runs the block and advisory safety verifiers against the template
        body. Auth is optional: ``anonymous=True`` omits ``Authorization``
        even when the client was constructed with an API key (public preview
        path; anonymous spend is billed against the per-IP-hash grant).
        """
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        response = self._request(
            "POST",
            f"/v1/templates/{template_id}/safety-check",
            params=params or None,
            authenticated=not anonymous,
        )
        return SafetyCheckResult.model_validate(response.json())

    # ----- teams -----
    def create_team(self, handle: str) -> TeamCreated:
        response = self._request("POST", "/v1/teams", json_body={"handle": handle})
        return TeamCreated.model_validate(response.json())

    def list_teams(self, *, filter_: str = "all") -> TeamList:
        response = self._request("GET", "/v1/teams", params={"filter": filter_})
        return TeamList.model_validate(response.json())

    def delete_team(self, team: str) -> TeamDeleteResult:
        response = self._request("DELETE", f"/v1/teams/{team}")
        return TeamDeleteResult.model_validate(response.json())

    def list_team_members(self, team: str) -> list[TeamMember]:
        response = self._request("GET", f"/v1/teams/{team}/members")
        payload = response.json()
        rows = payload.get("items", []) if isinstance(payload, dict) else payload
        return [TeamMember.model_validate(row) for row in rows]

    def add_team_member(self, team: str, user: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/teams/{team}/members",
            json_body={"user_identifier": user},
        )
        return response.json()

    def remove_team_member(self, team: str, user: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/v1/teams/{team}/members/{user}")
        return response.json()

    def transfer_team_ownership(self, team: str, new_owner: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/teams/{team}/transfer-ownership",
            json_body={"new_owner_user_identifier": new_owner},
        )
        return response.json()

    # ----- verifiers -----
    def deploy_verifier(self, payload: dict[str, Any]) -> VerifierDeployResult:
        response = self._request("POST", "/v1/verifiers", json_body=payload)
        return VerifierDeployResult.model_validate(response.json())

    def list_verifiers(self, limit: int = 50, cursor: str | None = None) -> VerifierList:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = self._request("GET", "/v1/verifiers", params=params)
        return VerifierList.model_validate(response.json())

    def get_verifier(
        self, verifier_id: str, *, version: int | None = None
    ) -> VerifierVersionDetail:
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        response = self._request("GET", f"/v1/verifiers/{verifier_id}", params=params)
        return VerifierVersionDetail.model_validate(response.json())

    def run_verifier(
        self,
        verifier_id: str,
        *,
        inputs: dict[str, str],
        media_url: str | None = None,
        version: int | None = None,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        workflow_ref: str | None = None,
        run_id: str | None = None,
        anonymous: bool = False,
    ) -> VerifierRunResult:
        """POST /v1/verifiers/{verifier_id}/runs.

        ``anonymous=True`` omits ``Authorization`` even when the client was
        constructed with an API key (public preview path; rate limited).
        ``verifier_id`` must be a UUID or ``system:<name>`` for anonymous runs;
        the CLI command resolves caller-owned names to UUIDs via
        ``get_verifier`` before calling this method.
        """
        body: dict[str, Any] = {"inputs": inputs}
        if media_url is not None:
            body["media_url"] = media_url
        if version is not None:
            body["version"] = version
        if workflow_id is not None:
            body["workflow_id"] = workflow_id
        if workflow_version is not None:
            body["workflow_version"] = workflow_version
        if workflow_ref is not None:
            body["workflow_ref"] = workflow_ref
        if run_id is not None:
            body["run_id"] = run_id
        response = self._request(
            "POST",
            f"/v1/verifiers/{verifier_id}/runs",
            json_body=body,
            authenticated=not anonymous,
        )
        return VerifierRunResult.model_validate(response.json())

    def revoke_verifier(self, verifier_id: str) -> VerifierRevokeResult:
        response = self._request("DELETE", f"/v1/verifiers/{verifier_id}")
        return VerifierRevokeResult.model_validate(response.json())

    def delete_verifier(self, verifier_id: str) -> VerifierDeleteResult:
        response = self._request("DELETE", f"/v1/verifiers/{verifier_id}/permanent")
        return VerifierDeleteResult.model_validate(response.json())

    # ----- image generators -----

    def deploy_image_generator(
        self,
        *,
        name: str,
        description: str,
        provider: str = "fal",
        model: str,
        generation_contract: str,
        default_params: dict[str, Any] | None = None,
        expected_version_token: str | None = None,
    ) -> ImageGeneratorDeployResult:
        """POST /v1/image-generators to create or re-version an image generator."""
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "provider": provider,
            "model": model,
            "generation_contract": generation_contract,
        }
        if default_params is not None:
            payload["default_params"] = default_params
        if expected_version_token is not None:
            payload["expected_version_token"] = expected_version_token
        response = self._request("POST", "/v1/image-generators", json_body=payload)
        return ImageGeneratorDeployResult.model_validate(response.json())

    def list_image_generators(
        self, limit: int = 50, cursor: str | None = None
    ) -> ImageGeneratorList:
        """GET /v1/image-generators with cursor-based pagination."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = self._request("GET", "/v1/image-generators", params=params)
        return ImageGeneratorList.model_validate(response.json())

    def get_image_generator(
        self, generator_id: str, *, version: int | None = None
    ) -> ImageGeneratorDetail:
        """GET /v1/image-generators/{generator_id} for a UUID."""
        params: dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        response = self._request("GET", f"/v1/image-generators/{generator_id}", params=params)
        return ImageGeneratorDetail.model_validate(response.json())

    def generate_image(
        self,
        generator_path_id: str,
        *,
        prompt: str,
        model: str | None = None,
        reference_image_url: str | None = None,
        num_images: int = 1,
        seed: int | None = None,
        param_overrides: dict[str, Any] | None = None,
        version: int | None = None,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        workflow_ref: str | None = None,
        run_id: str | None = None,
        visibility: str = "public",
        anonymous: bool = False,
    ) -> ImageGenerationRunResult:
        """POST /v1/image-generators/{generator_path_id}/runs.

        ``generator_path_id`` is the path segment resolved by the caller:
        - a deployed generator UUID (when ``--generator`` is supplied)
        - ``system:image-standard`` as a placeholder when ``--model`` is
          supplied in the body (the server ignores the path when ``model``
          is set) or when neither flag is given (server resolves the default
          tier).

        ``anonymous=True`` omits ``Authorization`` even when the client was
        constructed with an API key.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if model is not None:
            body["model"] = model
        if reference_image_url is not None:
            body["reference_image_url"] = reference_image_url
        body["num_images"] = num_images
        if seed is not None:
            body["seed"] = seed
        if param_overrides is not None:
            body["param_overrides"] = param_overrides
        if version is not None:
            body["version"] = version
        if workflow_id is not None:
            body["workflow_id"] = workflow_id
        if workflow_version is not None:
            body["workflow_version"] = workflow_version
        if workflow_ref is not None:
            body["workflow_ref"] = workflow_ref
        if run_id is not None:
            body["run_id"] = run_id
        body["visibility"] = visibility
        response = self._request(
            "POST",
            f"/v1/image-generators/{generator_path_id}/runs",
            json_body=body,
            authenticated=not anonymous,
        )
        return ImageGenerationRunResult.model_validate(response.json())

    def revoke_image_generator(self, generator_id: str) -> ImageGeneratorRevokeResult:
        """DELETE /v1/image-generators/{generator_id}."""
        response = self._request("DELETE", f"/v1/image-generators/{generator_id}")
        return ImageGeneratorRevokeResult.model_validate(response.json())

    def delete_image_generator(self, generator_id: str) -> ImageGeneratorDeleteResult:
        """DELETE /v1/image-generators/{generator_id}/permanent."""
        response = self._request("DELETE", f"/v1/image-generators/{generator_id}/permanent")
        return ImageGeneratorDeleteResult.model_validate(response.json())

    # ----- invitations -----
    def list_invitations(
        self,
        *,
        filter_: str | None = None,
        state: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> InvitationList:
        params: dict[str, Any] = {}
        if filter_:
            params["filter"] = filter_
        if state:
            params["state"] = state
        if limit is not None:
            params["limit"] = limit
        if cursor:
            params["cursor"] = cursor
        response = self._request("GET", "/v1/invitations", params=params)
        return InvitationList.model_validate(response.json())

    def accept_invitation(self, invitation_id: str) -> InvitationAcceptResult:
        response = self._request("POST", f"/v1/invitations/{invitation_id}/accept", json_body={})
        return InvitationAcceptResult.model_validate(response.json())

    def decline_invitation(self, invitation_id: str) -> InvitationDeclineResult:
        response = self._request("POST", f"/v1/invitations/{invitation_id}/decline", json_body={})
        return InvitationDeclineResult.model_validate(response.json())

    def cancel_invitation(self, invitation_id: str) -> InvitationCancelResult:
        response = self._request("POST", f"/v1/invitations/{invitation_id}/cancel", json_body={})
        return InvitationCancelResult.model_validate(response.json())

    def get_design_prompt(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/design/workflow-prompt")
        data = response.json()
        if not isinstance(data, dict):
            raise GoodeyeError(
                slug="internal_error",
                message="Unexpected response from /v1/design/workflow-prompt.",
            )
        return data

    # ----- hosted images -----

    def upload_image(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        *,
        visibility: str = "private",
        ttl_seconds: int | None = None,
    ) -> ImageDetail:
        """POST /v1/images (multipart/form-data) to upload a hosted image."""
        data: dict[str, str] = {"visibility": visibility}
        if ttl_seconds is not None:
            data["ttl_seconds"] = str(ttl_seconds)
        files = {"file": (filename, file_bytes, content_type)}
        response = self._request_multipart("POST", "/v1/images", files=files, data=data)
        return ImageDetail.model_validate(response.json())

    def list_images(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
        source: str | None = None,
        visibility: str | None = None,
    ) -> ImageList:
        """GET /v1/images with cursor-based pagination."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if source:
            params["source"] = source
        if visibility:
            params["visibility"] = visibility
        response = self._request("GET", "/v1/images", params=params)
        return ImageList.model_validate(response.json())

    def get_image(self, image_id: str) -> ImageDetail:
        """GET /v1/images/{id}."""
        response = self._request("GET", f"/v1/images/{image_id}")
        return ImageDetail.model_validate(response.json())

    def update_image(
        self,
        image_id: str,
        *,
        visibility: str | None = None,
        ttl_seconds: int | None = None,
        permanent: bool | None = None,
        rotate_view_secret: bool | None = None,
    ) -> ImageDetail:
        """PATCH /v1/images/{id}."""
        body: dict[str, Any] = {}
        if visibility is not None:
            body["visibility"] = visibility
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if permanent is not None:
            body["permanent"] = permanent
        if rotate_view_secret is not None:
            body["rotate_view_secret"] = rotate_view_secret
        response = self._request("PATCH", f"/v1/images/{image_id}", json_body=body)
        return ImageDetail.model_validate(response.json())

    def delete_image(self, image_id: str) -> None:
        """DELETE /v1/images/{id}. Returns 204 with no body."""
        self._request("DELETE", f"/v1/images/{image_id}")


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
        try:
            response = http.post(device_authorization_uri, data={"client_id": client_id})
        except httpx.RequestError as exc:
            raise _network_error(device_authorization_uri, exc) from exc
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
        try:
            response = http.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                },
            )
        except httpx.RequestError as exc:
            raise _network_error(token_uri, exc) from exc
    try:
        parsed = response.json()
        body: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
    except (ValueError, httpx.DecodingError):
        body = {}
    return response.status_code, body
