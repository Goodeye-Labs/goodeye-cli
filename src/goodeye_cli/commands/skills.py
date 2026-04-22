"""`goodeye skills ...` subcommand group."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.wire import SkillDetail

app = typer.Typer(help="Browse and manage skills.", no_args_is_help=True)


def _client(*, require_auth: bool) -> GoodeyeClient:
    api_key = get_api_key()
    if require_auth and not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required.",
            hint="Run `goodeye login` or set GOODEYE_API_KEY.",
        )
    return GoodeyeClient(get_server(), api_key=api_key)


@app.command("list")
def list_cmd(
    filter_: str = typer.Option(
        "all",
        "--filter",
        "-f",
        help="Scope filter: all, public, or mine.",
        case_sensitive=False,
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    search: str | None = typer.Option(None, "--search", "-s", help="Search query."),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List skills visible to the caller. Auto-follows pagination."""
    console = Console()
    items: list[Any] = []
    with _client(require_auth=False) as client:
        cursor: str | None = None
        while True:
            page = client.list_skills(
                filter_=filter_.lower() if filter_ else None,
                tag=tag,
                search=search,
                cursor=cursor,
            )
            items.extend(page.items)
            cursor = page.next_cursor
            if not cursor:
                break

    if json_output:
        typer.echo("[" + ",".join(i.model_dump_json() for i in items) + "]")
        return

    table = Table(title=f"Skills ({filter_})")
    table.add_column("ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Visibility")
    table.add_column("Version", justify="right")
    table.add_column("Description")
    for item in items:
        table.add_row(
            item.id, item.name, item.visibility, str(item.current_version), item.description
        )
    if not items:
        console.print("[dim]No skills matched.[/dim]")
    else:
        console.print(table)


@app.command("get")
def get_cmd(
    id_or_name: str = typer.Argument(..., help="Skill ID (ULID) or name."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned version."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write body to this path instead of stdout."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Return the JSON envelope (default is raw markdown)."
    ),
) -> None:
    """Fetch a skill. Emits raw markdown by default; use ``--json`` for the envelope."""
    console = Console(stderr=True)
    with _client(require_auth=False) as client:
        result = client.get_skill(id_or_name, version=version, accept_markdown=not json_output)

    if json_output:
        assert isinstance(result, SkillDetail)
        text = result.model_dump_json(indent=2)
    else:
        assert isinstance(result, str)
        text = result

    if output is not None:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output}")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


def _parse_front_matter(source: str) -> tuple[dict[str, Any], str]:
    """Extract a YAML front-matter block from a markdown source.

    Front-matter is recognised when the file begins with ``---`` on its own line
    and a matching terminator ``---`` appears later. Everything between is parsed
    as YAML. Everything after the terminator is the body.

    Returns ``({}, source)`` if no front-matter is present.
    """
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return {}, source
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            yaml_text = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            if body.startswith("\n"):
                body = body[1:]
            parsed = yaml.safe_load(yaml_text) or {}
            if not isinstance(parsed, dict):
                raise ValidationFailed(
                    slug="validation_error",
                    message="YAML front-matter must be a mapping.",
                )
            return parsed, body
    return {}, source


@app.command("publish")
def publish(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Markdown file to upload."),
    skill_id: str | None = typer.Option(
        None, "--id", help="Skill ID (ULID) to append a new version to; run `goodeye skills list` to find it."
    ),
    public: bool = typer.Option(False, "--public", help="Mark as public. Default is private."),
    name_override: str | None = typer.Option(
        None, "--name", help="Override the `name` from front-matter."
    ),
) -> None:
    """Upload a skill from a markdown file with YAML front-matter.

    The front-matter follows the Claude Code skills convention:

    \b
    ---
    name: incident-postmortem
    description: Draft a postmortem from an incident transcript. Use when ...
    # Optional:
    # visibility: public
    # tags: [sre, postmortem]
    # manifest: { outcome: ..., kpi: {...}, ... }  # full verifier block
    ---

    Only ``name`` and ``description`` are required. Everything else is optional.
    ``--public`` overrides the front-matter ``visibility`` when provided.
    """
    console = Console()
    source = file.read_text(encoding="utf-8")
    front_matter, _stripped_body = _parse_front_matter(source)
    # Server stores the full markdown (including front-matter) so the skill
    # round-trips as a drop-in Claude Code SKILL.md.
    body = source

    effective_name: str | None = (
        name_override or front_matter.get("name") or front_matter.get("slug")
    )
    if not isinstance(effective_name, str) or not effective_name:
        raise ValidationFailed(
            slug="validation_error",
            message="Missing `name`. Add `name:` to the front-matter or pass --name.",
        )

    description = front_matter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValidationFailed(
            slug="validation_error",
            message="Missing `description`. Add `description:` to the front-matter.",
        )

    manifest_raw = front_matter.get("manifest")
    if manifest_raw is not None and not isinstance(manifest_raw, dict):
        raise ValidationFailed(
            slug="validation_error",
            message="`manifest` in front-matter must be a mapping.",
        )
    manifest: dict[str, Any] | None = dict(manifest_raw) if manifest_raw else None

    tags_raw = front_matter.get("tags")
    if tags_raw is None:
        tags: list[str] = []
    elif isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw]
    else:
        raise ValidationFailed(
            slug="validation_error",
            message="`tags` in front-matter must be a list of strings.",
        )

    visibility = (
        "public"
        if public
        else (
            str(front_matter.get("visibility"))
            if front_matter.get("visibility") in ("public", "private")
            else "private"
        )
    )

    with _client(require_auth=True) as client:
        result = client.save_skill(
            name=effective_name,
            description=description,
            body=body,
            manifest=manifest,
            tags=tags,
            visibility=visibility,
            skill_id=skill_id,
        )

    console.print(
        f"[green]Saved[/green] {result.name} v{result.version} "
        f"(skill_id={result.skill_id}, visibility={result.visibility})"
    )


@app.command("set-visibility")
def set_visibility(
    skill_id: str = typer.Argument(..., help="Skill ID (ULID); run `goodeye skills list` to find it. Names are not accepted here (unlike `skills get`)."),
    visibility: str = typer.Argument(..., help="`private` or `public`."),
) -> None:
    """Change a skill's visibility."""
    console = Console()
    visibility_norm = visibility.lower()
    if visibility_norm not in ("public", "private"):
        raise ValidationFailed(
            slug="validation_error",
            message="visibility must be 'public' or 'private'.",
        )
    with _client(require_auth=True) as client:
        result = client.set_skill_visibility(skill_id, visibility_norm)
    console.print(f"[green]Updated[/green] {result.skill_id} -> visibility={result.visibility}")


@app.command("delete")
def delete(
    skill_id: str = typer.Argument(..., help="Skill ID (ULID) to delete; run `goodeye skills list` to find it. Names are not accepted here (unlike `skills get`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Soft-delete a skill."""
    console = Console()
    if not yes:
        confirm = typer.confirm(f"Delete skill {skill_id}?", default=False)
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(code=1)
    with _client(require_auth=True) as client:
        result = client.delete_skill(skill_id)
    if result.deleted:
        console.print(f"[green]Deleted[/green] {result.skill_id}")
    else:
        console.print(f"[yellow]Not deleted[/yellow] {result.skill_id}")


__all__ = [
    "_parse_front_matter",
    "app",
    "delete",
    "get_cmd",
    "list_cmd",
    "publish",
    "set_visibility",
]
