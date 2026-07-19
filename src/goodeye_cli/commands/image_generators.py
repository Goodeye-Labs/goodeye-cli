"""Typer commands for ``goodeye image-generators`` (deploy, list, show, generate, revoke)."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import (
    VERIFIER_REQUEST_TIMEOUT_SECONDS,
    get_api_key,
    get_request_timeout_seconds,
    get_server,
)
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)

# Default tier placeholder used when neither --generator nor --model is supplied.
# The server resolves ``system:image-standard`` to the default quality tier.
# This is also used as the path placeholder when --model is supplied in the body
# (the server ignores the path id when ``model`` is set in the body).
_DEFAULT_TIER = "system:image-standard"

app = typer.Typer(
    help=(
        "Manage image generators: owner-scoped, versioned image-generation "
        "configurations backed by a supported provider. Generators can be "
        "deployed once and referenced by UUID from skills. The `generate` "
        "command also accepts platform-managed quality tiers "
        "(e.g. `system:image-standard`) and direct model identifiers for "
        "authenticated one-off calls.\n\n"
        "Most commands require auth (`goodeye login` or GOODEYE_API_KEY). "
        "`generate` supports an `--anonymous` flag for public preview runs.\n\n"
        "Lifecycle: deploy -> show/list -> generate (many times) -> revoke."
    ),
    short_help="Deploy and run image generators.",
    no_args_is_help=True,
)


def _client(*, timeout_default: float | None = None) -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    timeout = (
        get_request_timeout_seconds(default=timeout_default)
        if timeout_default is not None
        else get_request_timeout_seconds()
    )
    return GoodeyeClient(get_server(), api_key=api_key, timeout=timeout)


def _client_for_generate(*, anonymous: bool, timeout_default: float | None = None) -> GoodeyeClient:
    """Build a client for ``image-generators generate``.

    Anonymous runs never attach stored credentials, even if configured.
    Authenticated runs require an API key on file.
    """
    timeout = (
        get_request_timeout_seconds(default=timeout_default)
        if timeout_default is not None
        else get_request_timeout_seconds()
    )
    if anonymous:
        return GoodeyeClient(get_server(), api_key=None, timeout=timeout)
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required (or pass --anonymous for a public preview).",
            hint="Run `goodeye login`, set GOODEYE_API_KEY, or use --anonymous.",
        )
    return GoodeyeClient(get_server(), api_key=api_key, timeout=timeout)


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"{label} must be valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed(
            slug="validation_error",
            message=f"{label} must be a JSON object.",
        )
    return parsed


@app.command("deploy")
def deploy(
    name: str = typer.Option(..., "--name", help="Generator name (lowercase, hyphens, max 128)."),
    description: str = typer.Option(
        ..., "--description", help="Short summary of what this generator produces."
    ),
    provider: str = typer.Option(
        "fal",
        "--provider",
        help="Generation provider backing this generator.",
    ),
    model: str = typer.Option(
        ...,
        "--model",
        help=(
            "Provider model identifier (e.g. a provider-org/model-name slug). "
            "Must resolve to a supported image model."
        ),
    ),
    generation_contract: str = typer.Option(
        ...,
        "--generation-contract",
        help=(
            "Input shape at generation time: ``text_to_image`` (prompt only) or "
            "``image_to_image`` (prompt plus a reference image URL)."
        ),
    ),
    params_json: str | None = typer.Option(
        None,
        "--params-json",
        help=(
            "JSON object of default generation parameters merged with per-call "
            "overrides at run time (e.g. image size, aspect ratio). "
            "Omit to use the provider model defaults."
        ),
    ),
    expected_version_token: str | None = typer.Option(
        None,
        "--expected-version-token",
        help=(
            "Required when re-deploying an existing generator. "
            "Get the current token from `goodeye image-generators list` "
            "or the previous deploy response. Omit for first deploy."
        ),
    ),
) -> None:
    """Deploy a reusable image generator your skills can call (new or new version).

    On success prints the ``generator_id``, ``version``, and the new
    ``version_token``. Persist the token: it's required for the next re-deploy.
    """
    default_params: dict[str, Any] | None = None
    if params_json is not None:
        default_params = _json_object(params_json, "--params-json")

    console = Console()
    with _client() as client:
        result = client.deploy_image_generator(
            name=name,
            description=description,
            provider=provider,
            model=model,
            generation_contract=generation_contract,
            default_params=default_params,
            expected_version_token=expected_version_token,
        )
    console.print(
        f"[green]Deployed[/green] {result.name} v{result.version} "
        f"(generator_id={result.generator_id}, version_token={result.version_token})"
    )


@app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
    limit: int = typer.Option(25, "--limit", "-l", min=1, help="Max results per page."),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow cursors and combine all pages."),
) -> None:
    """List active (non-revoked) image generators you own.

    Shows the current version and version token for each generator. Use
    `goodeye image-generators show <id>` for the full deploy-time config of a
    specific version.
    """
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client() as client:
        items, final_cursor = fetch_pages(
            lambda page_cursor: client.list_image_generators(limit=limit, cursor=page_cursor),
            cursor=cursor,
            all_pages=all_pages,
        )
    if mode == "json":
        echo_json(items_envelope(items, next_cursor=final_cursor))
        return
    table = Table(title="Image generators")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Version", justify="right")
    table.add_column("Status")
    table.add_column("Description")
    for item in items:
        table.add_row(
            item.generator_id,
            item.name,
            str(item.current_version),
            item.status,
            item.description,
        )
    if not items:
        console.print("[dim]No image generators.[/dim]")
    else:
        console.print(table)
    if final_cursor:
        hint = next_page_hint(
            ("goodeye", "image-generators", "list"),
            next_cursor=final_cursor,
            limit=limit,
        )
        console.print(f"[dim]{hint}[/dim]")


@app.command("show")
def show(
    generator_id: str = typer.Argument(
        ...,
        help="Image generator UUID.",
    ),
    version: int | None = typer.Option(
        None, "--version", "-v", help="Pin to a specific version; defaults to current."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Show one image generator version: model, contract, default parameters.

    Returns the full deploy-time payload plus ``config_hash`` for drift detection.
    Owner-only: a non-owned or revoked generator returns 404.
    """
    console = Console()
    with _client() as client:
        result = client.get_image_generator(generator_id, version=version)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(f"[bold]{result.name}[/bold] (v{result.version})  status={result.status}")
    console.print(f"generator_id: {result.generator_id}")
    console.print(f"description:  {result.description}")
    console.print(f"provider:     {result.provider}")
    console.print(f"model:        {result.model}")
    console.print(f"contract:     {result.generation_contract}")
    console.print(f"config_hash:  {result.config_hash}")
    if result.default_params:
        console.print(f"default_params: {json.dumps(result.default_params)}")


@app.command("generate")
def generate(
    prompt: str = typer.Option(
        ...,
        "--prompt",
        help="Text description of the image to generate.",
    ),
    generator: str | None = typer.Option(
        None,
        "--generator",
        help=(
            "Generator reference: a deployed generator UUID, "
            "``uuid@version`` to pin a version, or ``system:<tier>`` for a "
            "platform-managed quality tier. Mutually exclusive with --model."
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=(
            "Direct model identifier for an authenticated one-off generation. "
            "Not usable from published templates or with --anonymous. "
            "Mutually exclusive with --generator."
        ),
    ),
    reference_image_url: str | None = typer.Option(
        None,
        "--reference-image-url",
        help=(
            "Public HTTP or HTTPS URL of the reference image. Required for "
            "``image_to_image`` generators; rejected for ``text_to_image`` ones."
        ),
    ),
    num_images: int = typer.Option(
        1,
        "--num-images",
        min=1,
        max=4,
        help="Number of images to generate (1 to 4). Each image is billed separately.",
    ),
    seed: int | None = typer.Option(
        None,
        "--seed",
        help=(
            "Optional seed for reproducible output. Same prompt and seed "
            "produce consistent results across runs."
        ),
    ),
    visibility: str = typer.Option(
        "public",
        "--visibility",
        help=(
            "Access level for the hosted copy of each image: 'public' (default) "
            "gives a link that opens in any browser; 'private' gives a link only "
            "you can open and forward, while the plain URL stays locked. Applies "
            "to authenticated generations; anonymous generations are always public."
        ),
    ),
    params_json: str | None = typer.Option(
        None,
        "--params-json",
        help=(
            "JSON object of per-call parameter overrides merged on top of the "
            "generator's defaults (e.g. aspect ratio, image size)."
        ),
    ),
    version: int | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Pin to a specific deployed generator version.",
    ),
    anonymous: bool = typer.Option(
        False,
        "--anonymous",
        help=(
            "Do not send credentials (anonymous public preview; spend is gated "
            "by a small per-IP credit budget). Requires a system tier or a "
            "generator UUID that appears in a published template."
        ),
    ),
    skill_id: str | None = typer.Option(
        None,
        "--skill-id",
        help=(
            "Optional skill UUID stamped onto the run for provenance. "
            "Access-checked: a skill you cannot see returns 404."
        ),
    ),
    skill_version: int | None = typer.Option(
        None,
        "--skill-version",
        help="Skill version invoking this run; pair with --skill-id.",
    ),
    skill_ref: str | None = typer.Option(
        None,
        "--skill-ref",
        help="Free-form skill label (slug or name) for human-readable provenance.",
    ),
    workflow_id: str | None = typer.Option(
        None,
        "--workflow-id",
        hidden=True,
        help="Deprecated alias for --skill-id.",
    ),
    workflow_version: int | None = typer.Option(
        None,
        "--workflow-version",
        hidden=True,
        help="Deprecated alias for --skill-version.",
    ),
    workflow_ref: str | None = typer.Option(
        None,
        "--workflow-ref",
        hidden=True,
        help="Deprecated alias for --skill-ref.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Caller-supplied correlation ID stamped onto the run row.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Generate one or more images and print the URL(s) to stdout (one per line).

    Resolution order for the generation target (highest wins):
    - ``--generator UUID`` or ``--generator system:<tier>``: uses the deployed
      generator or system tier referenced in the path.
    - ``--model slug``: authenticated one-off using a direct model identifier.
      The body carries the model; the server ignores the path placeholder.
    - Neither: defaults to the platform standard quality tier.

    ``--generator`` and ``--model`` are mutually exclusive.

    Image URLs are printed to stdout (one per line) so the result can be
    piped. Cost and run metadata are printed to stderr or in JSON mode.

    Caller-shape errors (contract mismatch, mutual exclusion) return 400 with
    no run row written. Provider errors return 201 with ``status="error"``
    and the command exits 1.
    """
    if generator is not None and model is not None:
        raise ValidationFailed(
            slug="validation_error",
            message="--generator and --model are mutually exclusive: supply one or the other.",
        )

    param_overrides: dict[str, Any] | None = None
    if params_json is not None:
        param_overrides = _json_object(params_json, "--params-json")

    # Resolve the path id and body model for the /runs call.
    # - --generator present: use it as the path id; no model in body.
    # - --model present (no --generator): placeholder path; model goes in body.
    # - Neither: placeholder path; server resolves the default tier.
    if generator is not None:
        generator_path_id = generator
        effective_model: str | None = None
    else:
        generator_path_id = _DEFAULT_TIER
        effective_model = model  # may be None (default tier path)

    # --workflow-id/--workflow-version/--workflow-ref are deprecated hidden
    # aliases for --skill-id/--skill-version/--skill-ref; the canonical flag
    # wins when both are supplied. Announce the deprecation on stderr when a
    # caller still uses one, matching the `goodeye workflows` group notice.
    if workflow_id is not None or workflow_version is not None or workflow_ref is not None:
        typer.echo(
            "--workflow-id/--workflow-version/--workflow-ref are deprecated and "
            "will be removed on 2026-10-01; use --skill-id/--skill-version/--skill-ref.",
            err=True,
        )
    effective_skill_id = skill_id if skill_id is not None else workflow_id
    effective_skill_version = skill_version if skill_version is not None else workflow_version
    effective_skill_ref = skill_ref if skill_ref is not None else workflow_ref

    console_err = Console(stderr=True)
    with _client_for_generate(
        anonymous=anonymous, timeout_default=VERIFIER_REQUEST_TIMEOUT_SECONDS
    ) as client:
        result = client.generate_image(
            generator_path_id,
            prompt=prompt,
            model=effective_model,
            reference_image_url=reference_image_url,
            num_images=num_images,
            seed=seed,
            param_overrides=param_overrides,
            version=version,
            workflow_id=effective_skill_id,
            workflow_version=effective_skill_version,
            workflow_ref=effective_skill_ref,
            run_id=run_id,
            visibility=visibility,
            anonymous=anonymous,
        )

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        if result.status == "error":
            raise typer.Exit(code=1)
        return

    if result.status == "error":
        console_err.print(f"[bold red]ERROR[/bold red] {result.error_code}: {result.error_message}")
        raise typer.Exit(code=1)

    # Print URLs to stdout (one per line) for pipability.
    urls = (
        result.image_urls if result.image_urls else ([result.image_url] if result.image_url else [])
    )
    for url in urls:
        typer.echo(url)

    # Metadata to stderr.
    meta_parts = [f"run_id={result.run_id}", f"cost_usd={result.cost_usd}"]
    if result.duration_ms is not None:
        meta_parts.append(f"duration_ms={result.duration_ms}")
    console_err.print(f"[dim]{', '.join(meta_parts)}[/dim]")


@app.command("revoke")
def revoke(
    generator_id: str = typer.Argument(
        ...,
        help="Image generator UUID.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Revoke an image generator you own.

    Sets the generator to ``revoked`` and removes it from list/show/generate
    (subsequent calls return 404). Existing run rows are retained for audit.
    Irreversible: replace by deploying a fresh generator under a new name.

    Use `image-generators delete` to permanently erase the generator and all its data.
    """
    console = Console()
    if not confirm_destructive(f"Revoke image generator {generator_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client() as client:
        result = client.revoke_image_generator(generator_id)
    console.print(f"[green]Revoked[/green] {result.name}")


@app.command("delete")
def delete(
    generator_id: str = typer.Argument(
        ...,
        help="Image generator UUID.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Permanently and immediately delete an image generator you own.

    WARNING: This is permanent. The generator, all its versions (provider config,
    model, parameters), all run records, and all anonymous run records are removed
    from the live system at once. There is NO recovery path.

    Use `image-generators revoke` if you want a recoverable alternative that
    deactivates the generator while keeping the audit trail intact. Revoking is
    irreversible in the sense that the name cannot be reused, but the data is
    preserved.

    Serving gate: if any live published template version carries a snapshot
    that references this generator, deletion is refused (409). Unpublish the
    relevant template version(s) first, then retry.

    Encrypted backups age out within the platform's standard retention window
    (up to three months), so the content is not instantly erased from all
    systems everywhere, but it is no longer accessible through any product
    surface after this call.
    """
    console = Console()
    if not confirm_destructive(
        f"Permanently delete image generator {generator_id}? This cannot be undone.", yes=yes
    ):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client() as client:
        result = client.delete_image_generator(generator_id)
    if result.deleted:
        console.print(f"[green]Permanently deleted[/green] {result.name}")
    else:
        console.print(f"[yellow]Not deleted[/yellow] {result.name}")
