"""CLI-level exceptions that mirror the server's error contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class GoodeyeError(Exception):
    """Base class for structured errors surfaced by the CLI.

    Attributes:
        slug: Short machine-readable slug (e.g. ``auth_required``). Mirrors the server's
            ``{error, message, hint}`` contract.
        message: Human-readable message.
        hint: Optional suggested next step for the user.
        status_code: HTTP status code associated with the error, when the error
            originated from a server response.
        exit_code: Process exit code. Defaults to 1; subclasses may override
            to let scripts distinguish error categories.
    """

    exit_code: ClassVar[int] = 1

    slug: str
    message: str
    hint: str | None = None
    status_code: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = self.message
        if self.hint:
            base = f"{base}\nHint: {self.hint}"
        return base


class AuthRequired(GoodeyeError):
    """Caller is anonymous and the operation requires auth."""


class InvalidCredentials(GoodeyeError):
    """Credentials were provided but rejected."""


class Forbidden(GoodeyeError):
    """Authenticated caller is not allowed to perform this operation."""


class NotFound(GoodeyeError):
    """Target resource does not exist (or is existence-masked)."""


class ValidationFailed(GoodeyeError):
    """Request body or arguments were rejected."""


class RateLimited(GoodeyeError):
    """Caller hit a rate limit. ``hint`` may contain the Retry-After hint."""


class Conflict(GoodeyeError):
    """State conflict (e.g. duplicate name)."""


class ServerError(GoodeyeError):
    """Generic 5xx or unknown error from the server."""


class NetworkError(GoodeyeError):
    """The server could not be reached at all (offline, DNS, proxy, timeout).

    Raised when the underlying transport fails before any HTTP response is
    received, so there is no status code or server error body to translate.
    Distinct from ``httpx``'s own transport exceptions: those are caught at
    the request boundary and re-raised as this humane, exit-coded error.
    """


class AccountSuspended(GoodeyeError):
    """Caller's account has been suspended (HTTP 403, slug=account_suspended)."""


class BudgetExhausted(GoodeyeError):
    """Caller's monthly credit budget has been exhausted (HTTP 402, slug=budget_exhausted)."""


class AnonymousDailyCapReached(GoodeyeError):
    """Shared daily cap on anonymous usage was reached (HTTP 402, slug=anonymous_daily_cap).

    Distinct from BudgetExhausted: there is no account here, and the platform's
    daily limit on total anonymous usage is reached. Sign in to use your own credits.
    """


class SafetyVerificationFailed(GoodeyeError):
    """Publish blocked: the hard-block safety rubric rejected the template body.

    Exit code 2 so callers can distinguish "template rejected" from other errors.
    The judge's reasoning is in ``message``; the run id is in ``extras["verifier_run_id"]``.
    """

    exit_code: ClassVar[int] = 2


class SafetyVerificationUnavailable(GoodeyeError):
    """Publish failed: the safety verifier runtime was unreachable or errored.

    Exit code 3 so callers can distinguish "judge is down" from "template rejected".
    """

    exit_code: ClassVar[int] = 3


_SLUG_MAP: dict[str, type[GoodeyeError]] = {
    "auth_required": AuthRequired,
    "invalid_credentials": InvalidCredentials,
    "forbidden": Forbidden,
    "not_found": NotFound,
    "validation_error": ValidationFailed,
    "rate_limited": RateLimited,
    "conflict": Conflict,
    "handle_already_claimed": Conflict,
    "internal_error": ServerError,
    "account_suspended": AccountSuspended,
    "budget_exhausted": BudgetExhausted,
    "anonymous_daily_cap": AnonymousDailyCapReached,
    "safety_verification_failed": SafetyVerificationFailed,
    "safety_verification_unavailable": SafetyVerificationUnavailable,
    "referral_code_not_found": NotFound,
    "self_referral": Conflict,
    "already_referred": Conflict,
    "referral_not_eligible": Conflict,
    # hosted images
    "image_not_found": NotFound,
    "file_too_large": ValidationFailed,
    "unsupported_image_type": ValidationFailed,
    "quota_exceeded": ValidationFailed,
}


def error_from_body(
    status_code: int,
    body: dict[str, object] | None,
) -> GoodeyeError:
    """Build a structured CLI error from a non-2xx server response body."""
    slug: str
    message: str
    hint: str | None

    if not body or not isinstance(body.get("error"), str):
        slug = "internal_error" if status_code >= 500 else "validation_error"
        message = f"Server returned HTTP {status_code}."
        hint = None
        extras: dict[str, Any] = {}
    else:
        slug = str(body["error"])
        raw_message = body.get("message") or body.get("reasoning")
        message = str(raw_message) if isinstance(raw_message, str) else f"HTTP {status_code}"
        raw_hint = body.get("hint")
        hint = str(raw_hint) if isinstance(raw_hint, str) else None
        extras = {
            key: value for key, value in body.items() if key not in {"error", "message", "hint"}
        }

    cls = _SLUG_MAP.get(slug, ServerError)
    return cls(slug=slug, message=message, hint=hint, status_code=status_code, extras=extras)
