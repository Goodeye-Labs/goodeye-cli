"""Browser-assisted and headless auth flows.

Two flows are implemented:

* **Device code flow** (``device_code_login``): used for ``goodeye login`` with no
  email. Requests a user_code from WorkOS, opens the verification URL in the
  default browser, polls until the user approves, then exchanges the resulting
  WorkOS JWT for a Goodeye API key via ``POST /v1/auth/exchange``.

* **Magic-auth flow** (``magic_auth_flow``): used for ``goodeye login --email``
  and ``goodeye signup --email``. Posts the email to ``/v1/{intent}``, prompts
  for the emailed code, and posts to ``/v1/{intent}/verify`` to retrieve the
  initial API key.

The flows are deliberately injection-friendly: every external dependency
(``httpx`` transport, browser opener, code prompt, clock) can be overridden so
the flows can be unit-tested with ``respx`` without any real I/O.
"""

from __future__ import annotations

import contextlib
import time
import webbrowser
from collections.abc import Callable

from rich.console import Console

from goodeye_cli.client import (
    GoodeyeClient,
    poll_device_token,
    request_device_authorization,
)
from goodeye_cli.errors import GoodeyeError, InvalidCredentials


def device_code_login(
    server: str,
    workos_client_id: str,
    workos_device_authorization_uri: str,
    workos_token_uri: str,
    *,
    hostname: str | None = None,
    console: Console | None = None,
    open_browser: Callable[[str], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_wait_s: float | None = None,
) -> str:
    """Run the device-code flow and return the minted API key.

    Args:
        server: Goodeye server base URL (used for ``/v1/auth/exchange``).
        workos_client_id: The WorkOS application client id, fetched from
            ``/.well-known/goodeye-client-config``.
        workos_device_authorization_uri: WorkOS device-authorization endpoint URL.
        workos_token_uri: WorkOS token endpoint URL.
        hostname: Optional host label to embed in the minted key's name.
        console: Rich console for UX output. A default is created when None.
        open_browser: Overridable function to open the verification URL.
            Returns False if no browser could be opened.
        sleep: Overridable sleep for tests.
        clock: Overridable monotonic clock for tests.
        max_wait_s: Optional hard cap on total poll duration. Defaults to the
            ``expires_in`` returned by WorkOS.

    Returns:
        The newly minted Goodeye API key.
    """
    out = console or Console()
    opener = open_browser or webbrowser.open

    auth = request_device_authorization(workos_device_authorization_uri, workos_client_id)
    out.print(
        f"\nVisit this URL to approve the sign-in:\n  [bold]{auth.verification_uri_complete}[/bold]"
    )
    out.print(f"User code: [bold]{auth.user_code}[/bold]\n")
    # Best-effort browser open; failure is non-fatal because the URL is already printed.
    with contextlib.suppress(Exception):
        opener(auth.verification_uri_complete)

    deadline = clock() + (max_wait_s if max_wait_s is not None else auth.expires_in)
    interval = max(1, int(auth.interval))

    access_token: str | None = None
    while clock() < deadline:
        status, body = poll_device_token(workos_token_uri, workos_client_id, auth.device_code)
        if status == 200 and isinstance(body.get("access_token"), str):
            access_token = str(body["access_token"])
            break
        # WorkOS/OAuth pending-states: continue polling. Everything else: fail fast.
        error = body.get("error") if isinstance(body, dict) else None
        if status == 400 and error in ("authorization_pending", "slow_down"):
            if error == "slow_down":
                interval += 5
            sleep(interval)
            continue
        description = body.get("error_description") if isinstance(body, dict) else None
        message = description if isinstance(description, str) else "Device authorization failed."
        raise InvalidCredentials(
            slug=str(error) if isinstance(error, str) else "invalid_credentials",
            message=message,
            status_code=status,
        )

    if access_token is None:
        raise GoodeyeError(
            slug="auth_required",
            message="Timed out waiting for device approval.",
            hint="Re-run `goodeye login` and complete approval in the browser.",
        )

    with GoodeyeClient(server, api_key=access_token) as client:
        result = client.exchange(hostname=hostname)
    return result.api_key


def magic_auth_flow(
    server: str,
    email: str,
    *,
    intent: str = "login",
    prompt_code: Callable[[], str] | None = None,
    console: Console | None = None,
) -> str:
    """Run the magic-auth flow and return the newly minted API key.

    Args:
        server: Goodeye server base URL.
        email: The user's email address.
        intent: ``"login"`` or ``"signup"``. Both call the same underlying WorkOS
            magic-auth endpoints but are kept separate for logging/legibility.
        prompt_code: Callable that returns the 6-digit code the user received.
            Defaults to a Rich input prompt.
        console: Rich console for UX output.

    Returns:
        The newly minted Goodeye API key.
    """
    if intent not in ("login", "signup"):
        raise ValueError(f"intent must be 'login' or 'signup', got {intent!r}")

    out = console or Console()

    def _default_prompt() -> str:
        return out.input("Enter the 6-digit code sent to your email: ").strip()

    prompt = prompt_code or _default_prompt

    with GoodeyeClient(server) as client:
        if intent == "signup":
            client.signup(email)
        else:
            client.login(email)
        out.print(f"A sign-in code was sent to [bold]{email}[/bold].")
        code = prompt()
        if not code:
            raise InvalidCredentials(
                slug="invalid_credentials",
                message="No code provided.",
                hint="Check your email and re-run the command.",
            )
        result = (
            client.signup_verify(email, code)
            if intent == "signup"
            else client.login_verify(email, code)
        )
    return result.api_key
