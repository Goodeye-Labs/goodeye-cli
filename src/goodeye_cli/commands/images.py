"""Typer commands for ``goodeye images``.

Covers upload, list, get, update, delete, share, unshare, set-ttl, and
reset-link. Consumes the hosted-image REST endpoints under /v1/images.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import get_api_key, get_request_timeout_seconds, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)

app = typer.Typer(
    help=(
        "Upload and manage hosted images. Images can be private (the default) or "
        "public. Public images are reachable by URL without credentials. Expiry "
        "is controlled by --ttl at upload time or updated later with "
        "`goodeye images update` or `goodeye images set-ttl`.\n\n"
        "All commands require auth (`goodeye login` or GOODEYE_API_KEY)."
    ),
    short_help="Upload and manage hosted images.",
    no_args_is_help=True,
)


def _client() -> GoodeyeClient:
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key, timeout=get_request_timeout_seconds())


def _guess_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


@app.command("upload")
def upload(
    file: Path = typer.Argument(..., help="Local path to the image file to upload."),
    visibility: str = typer.Option(
        "private",
        "--visibility",
        help='Visibility of the uploaded image: "public" or "private" (default).',
    ),
    ttl: int | None = typer.Option(
        None,
        "--ttl",
        help=(
            "Lifetime of the image in seconds (must be > 0). Omit to keep the image indefinitely."
        ),
        min=1,
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Upload a local image file and receive a hosted URL.

    The image is stored privately by default. Pass --visibility public to make
    it accessible to anyone with the URL. Use --ttl to set an expiry in seconds.
    """
    if not file.exists():
        raise ValidationFailed(
            slug="validation_error",
            message=f"File not found: {file}",
        )
    if not file.is_file():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Path is not a file: {file}",
        )

    content_type = _guess_content_type(file)
    file_bytes = file.read_bytes()

    console = Console()
    with _client() as client:
        result = client.upload_image(
            file_bytes,
            file.name,
            content_type,
            visibility=visibility,
            ttl_seconds=ttl,
        )

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    console.print(f"[green]Uploaded[/green] {result.id}")
    console.print(f"url:        {result.url}")
    console.print(f"token:      {result.token}")
    console.print(f"visibility: {result.visibility}")
    if result.expires_at:
        console.print(f"expires_at: {result.expires_at.isoformat()}")
    if result.size_bytes is not None:
        console.print(f"size_bytes: {result.size_bytes}")
    if result.content_type:
        console.print(f"type:       {result.content_type}")


@app.command("list")
def list_cmd(
    source: str | None = typer.Option(
        None,
        "--source",
        help='Filter by origin: "upload" (directly uploaded) or "generated" (AI-generated).',
    ),
    visibility: str | None = typer.Option(
        None,
        "--visibility",
        help='Filter by visibility: "public" or "private".',
    ),
    limit: int = typer.Option(25, "--limit", "-l", min=1, help="Max results per page."),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow cursors and combine all pages."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """List your hosted images with optional source and visibility filters."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client() as client:
        items, final_cursor = fetch_pages(
            lambda page_cursor: client.list_images(
                limit=limit,
                cursor=page_cursor,
                source=source,
                visibility=visibility,
            ),
            cursor=cursor,
            all_pages=all_pages,
        )
    if mode == "json":
        echo_json(items_envelope(items, next_cursor=final_cursor))
        return

    table = Table(title="Images")
    table.add_column("ID")
    table.add_column("URL")
    table.add_column("Visibility")
    table.add_column("Source")
    table.add_column("Expires at")
    for item in items:
        table.add_row(
            item.id,
            item.url,
            item.visibility,
            item.source or "",
            item.expires_at.isoformat() if item.expires_at else "never",
        )
    if not items:
        console.print("[dim]No images.[/dim]")
    else:
        console.print(table)
    if final_cursor:
        hint_options: list[tuple[str, str | None]] = []
        if source:
            hint_options.append(("--source", source))
        if visibility:
            hint_options.append(("--visibility", visibility))
        hint = next_page_hint(
            ("goodeye", "images", "list"),
            next_cursor=final_cursor,
            limit=limit,
            options=hint_options,
        )
        console.print(f"[dim]{hint}[/dim]")


@app.command("get")
def get(
    image_id: str = typer.Argument(..., help="Image ID."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Show details for one hosted image."""
    console = Console()
    with _client() as client:
        result = client.get_image(image_id)

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    console.print(f"[bold]{result.id}[/bold]")
    console.print(f"url:        {result.url}")
    console.print(f"token:      {result.token}")
    console.print(f"visibility: {result.visibility}")
    if result.expires_at:
        console.print(f"expires_at: {result.expires_at.isoformat()}")
    else:
        console.print("expires_at: never")
    if result.size_bytes is not None:
        console.print(f"size_bytes: {result.size_bytes}")
    if result.content_type:
        console.print(f"type:       {result.content_type}")
    if result.source:
        console.print(f"source:     {result.source}")
    if result.visibility == "private":
        console.print(
            "[dim]This url is your private view link: open it or forward it to view the image. "
            "The plain url without the token stays locked.[/dim]"
        )


@app.command("update")
def update(
    image_id: str = typer.Argument(..., help="Image ID to update."),
    visibility: str | None = typer.Option(
        None,
        "--visibility",
        help='New visibility: "public" or "private".',
    ),
    ttl: int | None = typer.Option(
        None,
        "--ttl",
        help=(
            "New lifetime in seconds from now (must be > 0). Mutually exclusive with --permanent."
        ),
        min=1,
    ),
    permanent: bool = typer.Option(
        False,
        "--permanent",
        help="Clear the expiry and keep the image indefinitely. Mutually exclusive with --ttl.",
    ),
    rotate_view_secret: bool = typer.Option(
        False,
        "--rotate-view-secret",
        help=(
            "Issue a fresh private view link and revoke every link shared earlier "
            "for this image. Use to un-share a private image."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Update the visibility, expiry, or private view link of an image you own.

    Pass --ttl <seconds> to extend or shorten the lifetime, or --permanent to
    remove the expiry entirely. --ttl and --permanent are mutually exclusive.
    Pass --rotate-view-secret to issue a fresh private view link and revoke the
    links you shared earlier. For a private image, the printed url is the current
    view link.
    """
    if ttl is not None and permanent:
        raise ValidationFailed(
            slug="validation_error",
            message="--ttl and --permanent are mutually exclusive: use one or the other.",
        )

    console = Console()
    with _client() as client:
        result = client.update_image(
            image_id,
            visibility=visibility,
            ttl_seconds=ttl,
            permanent=permanent if permanent else None,
            rotate_view_secret=rotate_view_secret if rotate_view_secret else None,
        )

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    console.print(f"[green]Updated[/green] {result.id}")
    console.print(f"visibility: {result.visibility}")
    if result.expires_at:
        console.print(f"expires_at: {result.expires_at.isoformat()}")
    else:
        console.print("expires_at: never")
    if result.visibility == "private" and result.url:
        console.print(f"view link:  {result.url}")


@app.command("delete")
def delete(
    image_id: str = typer.Argument(..., help="Image ID to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Permanently delete a hosted image you own.

    The image and its URL are removed immediately. This cannot be undone.
    """
    console = Console()
    if not confirm_destructive(f"Delete image {image_id}? This cannot be undone.", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client() as client:
        client.delete_image(image_id)
    console.print(f"[green]Deleted[/green] {image_id}")


@app.command("share")
def share(
    image_id: str = typer.Argument(..., help="Image ID to make public."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Make an image public so anyone with the URL can view it."""
    console = Console()
    with _client() as client:
        result = client.update_image(image_id, visibility="public")

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    console.print(f"[green]Shared[/green] {result.id}  visibility=public")
    console.print(f"url: {result.url}")


@app.command("unshare")
def unshare(
    image_id: str = typer.Argument(..., help="Image ID to make private."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Restrict an image to private access (owner only).

    A private image still has a view link you can open or forward to people you
    choose; the printed url is that link, while the plain url stays locked.
    """
    console = Console()
    with _client() as client:
        result = client.update_image(image_id, visibility="private")

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    console.print(f"[green]Unshared[/green] {result.id}  visibility=private")
    if result.url:
        console.print(f"view link: {result.url}")


@app.command("set-ttl")
def set_ttl(
    image_id: str = typer.Argument(..., help="Image ID."),
    ttl_or_permanent: str = typer.Argument(
        ...,
        help=(
            'New expiry: a positive integer (seconds from now) or the word "permanent" '
            "to remove the expiry entirely."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Set or clear the expiry on an image.

    Pass a positive integer to set a new lifetime in seconds, or pass the word
    "permanent" to remove the expiry and keep the image indefinitely.
    """
    console = Console()

    ttl_seconds: int | None = None
    use_permanent: bool = False

    if ttl_or_permanent.lower() == "permanent":
        use_permanent = True
    else:
        try:
            ttl_seconds = int(ttl_or_permanent)
        except ValueError as exc:
            raise ValidationFailed(
                slug="validation_error",
                message=(
                    f'Invalid value: "{ttl_or_permanent}". '
                    'Pass a positive integer (seconds) or the word "permanent".'
                ),
            ) from exc
        if ttl_seconds <= 0:
            raise ValidationFailed(
                slug="validation_error",
                message="TTL must be a positive integer greater than 0.",
            )

    with _client() as client:
        result = client.update_image(
            image_id,
            ttl_seconds=ttl_seconds,
            permanent=True if use_permanent else None,
        )

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    if use_permanent:
        console.print(f"[green]Updated[/green] {result.id}  expires_at=never")
    else:
        console.print(
            f"[green]Updated[/green] {result.id}  "
            f"expires_at={result.expires_at.isoformat() if result.expires_at else 'unknown'}"
        )


@app.command("reset-link")
def reset_link(
    image_id: str = typer.Argument(..., help="Image ID."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Issue a fresh private view link and revoke the links you shared earlier.

    Rotates the image's view secret: every link you handed out before stops
    working, and the printed url is the new view link to open or share.
    """
    console = Console()
    with _client() as client:
        result = client.update_image(image_id, rotate_view_secret=True)

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    console.print(f"[green]Reset view link[/green] {result.id}")
    if result.url:
        console.print(f"view link: {result.url}")
