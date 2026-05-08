"""CLI-level exceptions that mirror the server's error contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    """

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


class AccountSuspended(GoodeyeError):
    """Caller's account has been suspended (HTTP 403, slug=account_suspended)."""


class BudgetExhausted(GoodeyeError):
    """Caller's monthly credit budget has been exhausted (HTTP 402, slug=budget_exhausted)."""


_SLUG_MAP: dict[str, type[GoodeyeError]] = {
    "auth_required": AuthRequired,
    "invalid_credentials": InvalidCredentials,
    "forbidden": Forbidden,
    "not_found": NotFound,
    "validation_error": ValidationFailed,
    "anonymous_limit_exceeded": RateLimited,
    "rate_limited": RateLimited,
    "conflict": Conflict,
    "handle_already_claimed": Conflict,
    "internal_error": ServerError,
    "account_suspended": AccountSuspended,
    "budget_exhausted": BudgetExhausted,
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
        raw_message = body.get("message")
        message = str(raw_message) if isinstance(raw_message, str) else f"HTTP {status_code}"
        raw_hint = body.get("hint")
        hint = str(raw_hint) if isinstance(raw_hint, str) else None
        extras = {
            key: value for key, value in body.items() if key not in {"error", "message", "hint"}
        }

    cls = _SLUG_MAP.get(slug, ServerError)
    return cls(slug=slug, message=message, hint=hint, status_code=status_code, extras=extras)
