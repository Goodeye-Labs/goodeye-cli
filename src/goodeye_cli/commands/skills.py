"""`goodeye skills ...` subcommand group."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.table import Table

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.commands import workflows_sync
from goodeye_cli.commands.prompts import confirm_destructive
from goodeye_cli.config import get_api_key, get_config_paths, get_server
from goodeye_cli.errors import AuthRequired, ValidationFailed
from goodeye_cli.frontmatter import (
    coerce_outcome,
    coerce_required_text,
    coerce_tags,
    extract_discovery_facets,
    parse_front_matter,
)
from goodeye_cli.output import (
    echo_json,
    fetch_pages,
    items_envelope,
    next_page_hint,
    resolve_output_mode,
)
from goodeye_cli.wire import SafetyCheckResult, WorkflowDetail, WorkflowFilePatchResult

_log = logging.getLogger(__name__)

_WORKFLOW_VERIFIER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")

app = typer.Typer(
    help=(
        "Find, run, save, share, and sync your private skills. A skill is a "
        "markdown runbook your agent executes on your behalf. Skills are "
        "private to you: share one with a named user or team using `grant`, "
        "and the verifiers it references travel with it at the same version. "
        "Publishing a public template is a separate, explicit step "
        "(`goodeye templates publish`)."
        "\n\n"
        "Use `sync` to mirror your hosted skills into the local directories "
        "your agents read, so every machine runs the current version."
    ),
    short_help="Find, run, save, share, and sync your private skills.",
    no_args_is_help=True,
)
app.add_typer(workflows_sync.app, name="sync")


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
        help=(
            "Scope filter: all (mine + shared, the default), mine "
            "(skills you own), or shared-with-me (skills shared "
            "with you via grants)."
        ),
        case_sensitive=False,
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    search: str | None = typer.Option(None, "--search", "-s", help="Search query."),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
    limit: int = typer.Option(25, "--limit", "-l", min=1, help="Max results per page."),
    cursor: str | None = typer.Option(None, "--cursor", help="Start listing from this cursor."),
    all_pages: bool = typer.Option(False, "--all", help="Follow cursors and combine all pages."),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Also list your own archived skills (restore with `skills unarchive`).",
    ),
) -> None:
    """List skills you own plus those shared with you."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        items, final_cursor = fetch_pages(
            lambda page_cursor: client.list_workflows(
                filter_=filter_.lower() if filter_ else None,
                tag=tag,
                search=search,
                limit=limit,
                cursor=page_cursor,
                include_archived=include_archived,
            ),
            cursor=cursor,
            all_pages=all_pages,
        )

    if mode == "json":
        echo_json(items_envelope(items, next_cursor=final_cursor))
        return

    table = Table(title=f"Skills ({filter_})")
    table.add_column("ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Version", justify="right")
    table.add_column("Fork of", no_wrap=True)
    table.add_column("Description")
    # The archived-at column is only meaningful (and only carries values)
    # when the caller asked to include their archived skills.
    if include_archived:
        table.add_column("Archived at", no_wrap=True)
    for item in items:
        fork_cell = ""
        if item.parent_template_id:
            fork_cell = f"tpl {item.parent_template_id[:8]}...@v{item.parent_template_version}"
        cells = [item.id, item.name, str(item.current_version), fork_cell, item.description]
        if include_archived:
            cells.append(
                f"[yellow]{item.archived_at.isoformat()}[/yellow]"
                if item.archived_at is not None
                else "[dim]live[/dim]"
            )
        table.add_row(*cells)
    if not items:
        console.print("[dim]No skills matched.[/dim]")
    else:
        console.print(table)
    if final_cursor:
        hint = next_page_hint(
            ("goodeye", "skills", "list"),
            next_cursor=final_cursor,
            limit=limit,
            options=(
                ("--filter", filter_.lower() if filter_ else None),
                ("--tag", tag),
                ("--search", search),
            ),
            bool_flags=("--include-archived",) if include_archived else (),
        )
        console.print(f"[dim]{hint}[/dim]")


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Natural-language search query."),
    filter_: str = typer.Option(
        "all",
        "--filter",
        "-f",
        help="mine | all | shared-with-me",
        case_sensitive=False,
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Restrict candidates to this tag."),
    limit: int = typer.Option(5, "--limit", "-l", min=1, max=10, help="Max results (1-10)."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print results as a table."),
) -> None:
    """Find a skill by describing what it does, even if you don't know its name."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        result = client.search_workflows(
            query=query,
            filter_=filter_.lower(),
            tag=tag,
            limit=limit,
        )
    if mode == "json":
        echo_json(result)
        return

    table = Table(title="Skill search")
    table.add_column("Rank", justify="right")
    table.add_column("Slug")
    table.add_column("Match reason")
    for item in result.items:
        slug = item.slug or item.name or item.id
        table.add_row(str(item.rank), slug, item.match_reason)
    if not result.items:
        console.print("[dim]No matches.[/dim]")
    else:
        console.print(table)


@app.command("get")
def get_cmd(
    id_or_name: str = typer.Argument(..., help="Skill UUID or name."),
    version: int | None = typer.Option(None, "--version", "-v", help="Pinned version."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the skill body to this path instead of printing the runbook to stdout.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full skill record as JSON instead of markdown."
    ),
) -> None:
    """Fetch a skill for the calling AI agent to execute.

    The body is the user's skill: a markdown runbook the agent should
    follow on the user's behalf, not just display. Prints the markdown to
    stdout by default. With --output, writes the canonical skill body
    (front-matter first, without the run-guidance framing printed to stdout)
    so the saved file stays editable and can be re-published.
    """
    console = Console(stderr=True)
    # Stdout streams the server markdown verbatim, which carries the run-guidance
    # directive in-band. Writing to a file with --output instead saves the
    # canonical body so the export round-trips back through `publish`; that path
    # fetches the structured record rather than the runbook markdown.
    fetch_markdown = not json_output and output is None
    with _client(require_auth=True) as client:
        result = client.get_workflow(id_or_name, version=version, accept_markdown=fetch_markdown)

    if json_output:
        assert isinstance(result, WorkflowDetail)
        text = result.model_dump_json(indent=2)
    elif output is not None:
        assert isinstance(result, WorkflowDetail)
        text = result.body
    else:
        assert isinstance(result, str)
        text = result

    if output is not None:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output}")
    elif json_output:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


# Front-matter parsing and metadata coercion live in the neutral
# ``goodeye_cli.frontmatter`` module so the sync engine can reuse them without
# importing this command module (which would create a cycle). These private
# aliases keep the existing call sites and tests working unchanged.
_parse_front_matter = parse_front_matter
_coerce_required_text = coerce_required_text
_coerce_outcome = coerce_outcome
_coerce_tags = coerce_tags
_extract_discovery_facets = extract_discovery_facets


def _parse_workflow_verifier_flags(values: list[str]) -> list[dict[str, str]]:
    """Parse ``--verifier logical-name=verifier_uuid[@version]`` into API wire dicts.

    The optional ``@version`` suffix rides through untouched as part of
    ``verifier_id``; the server parses and stores the pin.
    """
    rows: list[dict[str, str]] = []
    for raw in values:
        if "=" not in raw:
            raise ValidationFailed(
                slug="validation_error",
                message=f"Each --verifier must be name=verifier_id (got {raw!r}).",
            )
        name, vid = raw.split("=", 1)
        name, vid = name.strip(), vid.strip()
        if not name or not vid:
            raise ValidationFailed(
                slug="validation_error",
                message=f"Each --verifier needs non-empty name and id (got {raw!r}).",
            )
        if not _WORKFLOW_VERIFIER_NAME_RE.fullmatch(name):
            raise ValidationFailed(
                slug="validation_error",
                message=("Each --verifier name must use lowercase letters, digits, and hyphens."),
            )
        rows.append({"name": name, "verifier_id": vid})
    return rows


def _parse_workflow_image_generator_flags(values: list[str]) -> list[dict[str, str]]:
    """Parse ``--image-generator logical-name=generator_ref`` into API wire dicts.

    ``generator_ref`` is a system tier (``system:<tier>``), a deployed generator
    UUID, or a pinned ``<uuid>@<version>``; it rides through untouched and the
    server validates it.
    """
    rows: list[dict[str, str]] = []
    for raw in values:
        if "=" not in raw:
            raise ValidationFailed(
                slug="validation_error",
                message=f"Each --image-generator must be name=generator_ref (got {raw!r}).",
            )
        name, ref = raw.split("=", 1)
        name, ref = name.strip(), ref.strip()
        if not name or not ref:
            raise ValidationFailed(
                slug="validation_error",
                message=f"Each --image-generator needs non-empty name and ref (got {raw!r}).",
            )
        if not _WORKFLOW_VERIFIER_NAME_RE.fullmatch(name):
            raise ValidationFailed(
                slug="validation_error",
                message=(
                    "Each --image-generator name must use lowercase letters, digits, and hyphens."
                ),
            )
        rows.append({"name": name, "generator_ref": ref})
    return rows


def _read_markdown_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Markdown file not found: {source}",
        )
    if not path.is_file():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Markdown path is not a file: {source}",
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not decode markdown file as UTF-8: {source}",
        ) from exc
    except OSError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not read markdown file: {source}",
        ) from exc


def _print_authoring_notes(notes: list[str]) -> None:
    """Print advisory demo authoring notes to stderr (one per line).

    Notes are advisory and never block. They go to stderr so machine-readable
    stdout (e.g. piped output) stays clean.
    """
    if not notes:
        return
    stderr = Console(stderr=True)
    for note in notes:
        stderr.print(note)


def _is_skill_directory(source: str) -> bool:
    """Return True when *source* points to a directory containing SKILL.md."""
    p = Path(source)
    return p.is_dir() and (p / "SKILL.md").is_file()


@app.command("publish")
def publish(
    file: str = typer.Argument(
        ...,
        help=(
            "Markdown skill file, a directory containing SKILL.md (uploads the full "
            "directory tree), or '-' to read markdown from stdin "
            "(preferred for generated agent output)."
        ),
    ),
    name_override: str | None = typer.Option(
        None, "--name", help="Override the `name` from front-matter."
    ),
    description_override: str | None = typer.Option(
        None, "--description", help="Override the `description` from front-matter."
    ),
    outcome_override: str | None = typer.Option(
        None,
        "--outcome",
        help="Override the `outcome` from front-matter. Optional discovery metadata.",
    ),
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help=("Skill discovery tag. Repeat to set multiple tags; overrides front-matter tags."),
        ),
    ] = None,
    expected_version_token: str | None = typer.Option(
        None,
        "--expected-version-token",
        help="Required token when updating an existing skill. Omit only for creates.",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help=(
            "Optional provenance marker: 'manual', 'teach', 'optimization', "
            "'description_optimization', or 'audit'. Defaults to NULL."
        ),
    ),
    verifier: Annotated[
        list[str] | None,
        typer.Option(
            "--verifier",
            help=(
                "Verifier binding: name=verifier_id (repeatable). Append "
                "@version (name=verifier_id@version) to pin a specific version; "
                "unpinned uses the verifier's current version when the skill "
                "is published as a template."
            ),
        ),
    ] = None,
    clear_verifiers: bool = typer.Option(
        False,
        "--clear-verifiers",
        help="Send an explicit empty verifier binding list, removing existing bindings.",
    ),
    image_generator: Annotated[
        list[str] | None,
        typer.Option(
            "--image-generator",
            help=(
                "Image generator binding: name=generator_ref (repeatable). "
                "generator_ref is a quality tier (system:<tier>), a deployed "
                "generator UUID, or uuid@version to pin a specific version."
            ),
        ),
    ] = None,
    clear_image_generators: bool = typer.Option(
        False,
        "--clear-image-generators",
        help="Send an explicit empty image generator binding list, removing existing bindings.",
    ),
    clear_files: bool = typer.Option(
        False,
        "--clear-files",
        help="Send an empty file tree, removing all sibling files from the skill.",
    ),
) -> None:
    """Save a new skill (or a new version) to your private registry.

    When FILE is a directory containing SKILL.md, the full directory tree is
    uploaded: SKILL.md becomes the skill body and all other non-ignored files
    are uploaded as sibling files.  When FILE is a single file or stdin, no
    file tree is sent (the server carries the existing tree forward).

    Metadata can be passed as flags, read from YAML front-matter, or both.
    When both are present, command-line flags win:

    \b
    goodeye skills publish - \\
      --name incident-postmortem \\
      --description "Draft a postmortem from an incident transcript." \\
      --outcome "Reduce mean-time-to-postmortem from days to hours." \\
      --tag sre --tag postmortem

    Front-matter follows the Goodeye skill body convention:

    \b
    ---
    name: incident-postmortem
    description: Draft a postmortem from an incident transcript. Use when ...
    outcome: Reduce mean-time-to-postmortem from days to hours.
    # Optional discovery facet:
    # tags: [sre, postmortem]
    ---

    ``name`` and ``description`` are required, either as flags or
    front-matter. ``outcome`` and tags are optional.

    Skills are always private to the caller. To share a skill as a
    public template, run ``goodeye templates publish <skill-uuid-or-name>`` as a
    separate, explicit step.

    Inline deterministic checks may live in the body as fenced code blocks.
    Deploy LLM-judge checks separately as verifiers and reference them by
    verifier_id or verifier_id@version.
    """
    from goodeye_cli.sync import build_files_payload

    console = Console()

    # Determine input mode: directory with SKILL.md, single file, or stdin.
    is_dir_mode = file != "-" and _is_skill_directory(file)
    if clear_files and is_dir_mode:
        raise ValidationFailed(
            slug="validation_error",
            message="--clear-files cannot be used with a directory input.",
            hint="Either pass a single file path to clear the tree, or omit --clear-files "
            "to upload the directory tree.",
        )

    skill_dir = Path(file)
    if is_dir_mode:
        markdown = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    else:
        markdown = _read_markdown_input(file)

    front_matter, _stripped_body = _parse_front_matter(markdown)
    # Server stores the full markdown (including front-matter) so skill
    # bodies round-trip through `goodeye skills get`.
    body = markdown

    effective_name = _coerce_required_text(
        name_override
        if name_override is not None
        else front_matter.get("name") or front_matter.get("slug"),
        field_name="name",
        missing_message="Missing `name`. Add `name:` to the front-matter or pass --name.",
    )

    description = _coerce_required_text(
        description_override
        if description_override is not None
        else front_matter.get("description"),
        field_name="description",
        missing_message=(
            "Missing `description`. Add `description:` to the front-matter or pass --description."
        ),
    )

    outcome, tags = _extract_discovery_facets(front_matter)
    if outcome_override is not None:
        outcome = _coerce_outcome(outcome_override)
    if tag:
        tags = list(tag)
    if clear_verifiers and verifier:
        raise ValidationFailed(
            slug="validation_error",
            message="Use either --clear-verifiers or --verifier, not both.",
        )
    verifiers = _parse_workflow_verifier_flags(list(verifier or []))
    verifier_payload: list[dict[str, str]] | None = [] if clear_verifiers else verifiers or None

    if clear_image_generators and image_generator:
        raise ValidationFailed(
            slug="validation_error",
            message="Use either --clear-image-generators or --image-generator, not both.",
        )
    image_generators = _parse_workflow_image_generator_flags(list(image_generator or []))
    image_generator_payload: list[dict[str, str]] | None = (
        [] if clear_image_generators else image_generators or None
    )

    # Build the files payload.
    # Directory mode: upload the full tree (everything is inline since there is
    # no recorded state to compare against).
    # Single-file / stdin: omit files entirely so the server carries the
    # existing tree forward.
    # --clear-files: send an empty list to wipe the tree.
    files_payload: list[dict] | None
    if clear_files:
        files_payload = []
    elif is_dir_mode:
        # Fetch ignore defaults from the server; fall back to baked-in list on
        # any network failure.
        ignore_defaults: list[str] | None = None
        try:
            with _client(require_auth=False) as anon_client:
                cfg = anon_client.get_client_config()
                ignore_defaults = cfg.ignore_defaults if cfg.ignore_defaults else None
        except Exception:
            # Offline fallback to the baked-in defaults is intentional; log the
            # swallowed error at debug level so a misconfiguration is diagnosable
            # without changing the fallback behavior.
            _log.debug("could not fetch ignore defaults from server config", exc_info=True)
        files_payload, _ = build_files_payload(skill_dir, None, ignore_defaults)
    else:
        files_payload = None

    with _client(require_auth=True) as client:
        result = client.save_workflow(
            name=effective_name,
            description=description,
            body=body,
            outcome=outcome,
            tags=tags,
            expected_version_token=expected_version_token,
            source=source,
            verifiers=verifier_payload,
            image_generators=image_generator_payload,
            files=files_payload,
        )

    console.print(
        f"[green]Saved[/green] {result.name} v{result.version} "
        f"(skill_id={result.workflow_id}, version_token={result.version_token})"
    )
    extra_notes = [result.next_step] if result.next_step else []
    _print_authoring_notes([*result.authoring_notes, *extra_notes])


def _read_file_bytes(source: Path) -> bytes:
    """Read the raw bytes of a local file named as file content."""
    if not source.exists():
        raise ValidationFailed(
            slug="validation_error",
            message=f"File not found: {source}",
        )
    if not source.is_file():
        raise ValidationFailed(
            slug="validation_error",
            message=f"Not a file: {source}",
        )
    try:
        return source.read_bytes()
    except OSError as exc:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not read file: {source}",
        ) from exc


def _read_stdin_bytes() -> bytes:
    """Read standard input as raw bytes so binary content survives the pipe."""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # pragma: no cover - only on a text-only stdin stub
        return sys.stdin.read().encode("utf-8")
    return buffer.read()


def _resolve_expected_version_token(
    client: GoodeyeClient, skill_id: str, supplied: str | None
) -> str:
    """Return the token to write against, reading the current one when unset.

    Resolving it here is not a weaker guard than demanding the flag: the server
    still rejects the write if another writer landed between this read and it,
    which is the only race a single command invocation can have. The flag stays
    for scripts that already hold a token and want to skip the round-trip.
    """
    if supplied:
        return supplied
    detail = client.get_workflow(skill_id)
    assert isinstance(detail, WorkflowDetail)
    if not detail.version_token:
        raise ValidationFailed(
            slug="validation_error",
            message=f"Could not read the current version token for {skill_id}.",
            hint="Pass --expected-version-token with the token from `goodeye skills get --json`.",
        )
    return detail.version_token


def _refresh_sync_index(
    result: WorkflowFilePatchResult,
    *,
    expected_version_token: str,
    path: str,
    content: bytes | None,
    executable: bool | None = None,
    purpose: str | None = None,
) -> None:
    """Bring the local mirror in line with the change that just landed.

    A tracked mirror is a directory on disk plus an index recording a hash per
    file and the version it is synced at. Leaving any of that stale after a
    single-path change makes the next `goodeye skills sync push` report a change
    that is not real, or send the old copy back and revert the one just made, so
    the command would work against itself.

    Only mirrors recorded at the version the change was written against are
    updated, and each one moves its directory, its recorded file state, and its
    sync point together onto the new version. A mirror sitting at any other
    version has a different base and is left for a pull. When no mirror matches
    there is nothing to update and the index is left untouched.

    ``content`` is the bytes just written, or None for a removal.

    Best-effort: the write already succeeded, so an unreadable index or an
    unwritable mirror is reported and moves on rather than failing the command.
    """
    from goodeye_cli import sync

    try:
        paths = get_config_paths()
        state = sync.load_sync_state(paths)
        if content is None:
            touched = sync.record_file_removed(
                state,
                result,
                expected_version_token=expected_version_token,
                path=path,
            )
        else:
            touched = sync.record_file_written(
                state,
                result,
                expected_version_token=expected_version_token,
                path=path,
                content=content,
                executable=executable,
                purpose=purpose,
            )
        if touched:
            sync.save_sync_state(state, paths)
    except Exception:
        _log.debug("could not refresh the local sync index", exc_info=True)
        Console(stderr=True).print(
            "[yellow]Note[/yellow] the local copy could not be updated; "
            "run `goodeye skills sync pull` to resync."
        )


def _print_file_change(result: WorkflowFilePatchResult) -> None:
    """Report what the change touched, and what it left alone.

    Paths and the skill name come back from the server, so they are escaped: a
    path containing square brackets would otherwise read as Rich markup.
    """
    console = Console()
    console.print(
        f"[green]Updated[/green] {rich_escape(result.name)} v{result.version} "
        f"(skill_id={result.workflow_id}, version_token={result.version_token})"
    )
    if result.changed:
        console.print(f"  changed: {rich_escape(', '.join(result.changed))}")
    if result.deleted:
        console.print(f"  deleted: {rich_escape(', '.join(result.deleted))}")
    console.print(f"  kept unchanged: {result.carried_forward} file(s)")
    _print_authoring_notes(result.authoring_notes)


@app.command("put-file")
def put_file(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    path: str = typer.Argument(
        ...,
        help=(
            "Path of the file inside the skill, relative and POSIX-style "
            "(e.g. references/rubric.md). Use SKILL.md to rewrite the runbook."
        ),
    ),
    from_file: Path | None = typer.Option(
        None, "--from-file", help="Read the new content for PATH from this local file."
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read the new content for PATH from standard input."
    ),
    executable: bool | None = typer.Option(
        None,
        "--executable/--no-executable",
        help=(
            "Mark the file executable when extracted, or clear that mark. "
            "Omit both to keep the file's current setting."
        ),
    ),
    purpose: str | None = typer.Option(
        None,
        "--purpose",
        help=(
            "Short label for the file's role in the skill. Omit to keep the file's current label."
        ),
    ),
    expected_version_token: str | None = typer.Option(
        None,
        "--expected-version-token",
        help=(
            "Token for the version you are changing, for scripts that already "
            "hold one. Omit to read the current token first."
        ),
    ),
) -> None:
    """Write one file in a hosted skill, keeping every other path unchanged.

    Only the path you name changes: the rest of the skill's files ride forward
    untouched, unlike `goodeye skills publish <dir>`, which replaces the whole
    tree so any path missing from the directory is deleted. Send the file's
    complete new content, not a patch or a diff.

    \b
    goodeye skills put-file my-skill references/rubric.md --from-file ./rubric.md
    cat rubric.md | goodeye skills put-file my-skill references/rubric.md --stdin

    The file's executable mark and role label keep their current values unless
    you set them, and the skill's description, outcome, tags, and verifier
    references carry forward untouched. This writes the next version of the
    skill and requires edit access.
    """
    if from_file is not None and stdin:
        raise ValidationFailed(
            slug="validation_error",
            message="Use either --from-file or --stdin, not both.",
        )
    if from_file is None and not stdin:
        raise ValidationFailed(
            slug="validation_error",
            message="No content given for the file.",
            hint="Pass --from-file <path> to read a local file, or --stdin to pipe it in.",
        )

    from goodeye_cli.sync import inline_content_field

    # Exactly one source is set by the checks above, so an unset --from-file
    # means the content is on stdin.
    raw = _read_stdin_bytes() if from_file is None else _read_file_bytes(from_file)
    entry: dict[str, object] = {"path": path, **inline_content_field(raw)}
    # An absent `executable` or `purpose` means "keep the current value" on this
    # route, so a flag the caller did not pass must be left off entirely rather
    # than sent as a default that would reset a setting they never mentioned.
    if executable is not None:
        entry["executable"] = executable
    if purpose is not None:
        entry["purpose"] = purpose

    with _client(require_auth=True) as client:
        token = _resolve_expected_version_token(client, skill_id, expected_version_token)
        result = client.patch_workflow_files(
            skill_id,
            expected_version_token=token,
            files=[entry],
        )

    _print_file_change(result)
    _refresh_sync_index(
        result,
        expected_version_token=token,
        path=path,
        content=raw,
        executable=executable,
        purpose=purpose,
    )


@app.command("rm-file")
def rm_file(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    path: str = typer.Argument(
        ...,
        help=(
            "Path of the file to remove from the skill, relative and "
            "POSIX-style (e.g. references/rubric.md)."
        ),
    ),
    expected_version_token: str | None = typer.Option(
        None,
        "--expected-version-token",
        help=(
            "Token for the version you are changing, for scripts that already "
            "hold one. Omit to read the current token first."
        ),
    ),
) -> None:
    """Remove one file from a hosted skill, keeping every other path unchanged.

    Only the path you name is removed: the rest of the skill's files ride
    forward untouched, unlike `goodeye skills publish <dir>`, which replaces the
    whole tree so any path missing from the directory is deleted. The path must
    already be in the skill, and the runbook (SKILL.md) cannot be removed.

    This writes the next version of the skill and requires edit access.
    """
    with _client(require_auth=True) as client:
        token = _resolve_expected_version_token(client, skill_id, expected_version_token)
        result = client.patch_workflow_files(
            skill_id,
            expected_version_token=token,
            delete_paths=[path],
        )

    _print_file_change(result)
    _refresh_sync_index(result, expected_version_token=token, path=path, content=None)


@app.command("lineage")
def lineage(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    json_output: bool = typer.Option(False, "--json", help="Print lineage as JSON."),
) -> None:
    """Show a skill's fork lineage."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.lookup_workflow_lineage(skill_id)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    # A permanently-deleted parent severs the FK, so the response carries
    # parent_template_id=None while still pinning the version the fork was
    # taken from. A skill that was never a fork has neither. Only the
    # latter is "not a fork"; gating on parent_template_id alone would
    # mislabel a deleted-parent fork and try to print "template None".
    if result.parent_template_id is None and result.parent_template_version is None:
        console.print("[dim]Not a fork (no parent template).[/dim]")
        return
    if result.parent_template_id is None:
        console.print(
            f"Forked from a template (now permanently deleted) "
            f"pinned to v{result.parent_template_version}."
        )
        console.print(
            "[red]Parent template permanently deleted[/red] "
            "(content no longer accessible through any product surface)."
        )
        return
    console.print(
        f"Forked from template {result.parent_template_id} "
        f"pinned to v{result.parent_template_version}; "
        f"upstream latest: v{result.upstream_latest_version}; "
        f"upstream unpublished at pinned version: {result.is_upstream_unpublished}."
    )
    source_status = result.parent_source_status or "unknown"
    if result.parent_permanently_deleted:
        console.print(
            "[red]Parent template permanently deleted[/red] "
            "(content no longer accessible through any product surface)."
        )
    elif source_status == "archived":
        archived_at = result.parent_template_archived_at or "unknown time"
        console.print(f"[yellow]Parent template archived[/yellow] at {archived_at}.")
    if result.parent_version_deprecated_at is not None:
        message = result.parent_version_deprecation_message or "no message"
        console.print(
            f"[yellow]Pinned version deprecated[/yellow] "
            f"at {result.parent_version_deprecated_at}: {message}."
        )


@app.command("archive")
def archive(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Hide a skill from your list without deleting it (reversible).

    Archiving hides the skill from list results and grants but keeps all
    versions and content intact. Use `skills unarchive` to restore.
    Use `skills delete` only when you want permanent, irreversible removal.
    """
    console = Console()
    if not confirm_destructive(f"Archive skill {skill_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.archive_workflow(skill_id)
    console.print(f"[green]Archived[/green] {result.name} (skill_id={result.workflow_id})")


@app.command("unarchive")
def unarchive(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
) -> None:
    """Restore an archived skill you own.

    The inverse of `skills archive`. The skill becomes visible again in
    list results and grants.
    """
    console = Console()
    with _client(require_auth=True) as client:
        result = client.unarchive_workflow(skill_id)
    console.print(f"[green]Unarchived[/green] skill {result.workflow_id}")


@app.command("delete")
def delete(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Permanently and immediately delete a skill you own.

    WARNING: This is permanent. The skill, all its versions, all attached
    files, and all access grants are removed from the live system at once.
    There is NO recovery path.

    Use `skills archive` if you want a reversible alternative. Archived
    skills can be restored at any time with `skills unarchive`.

    Encrypted backups age out within the platform's standard retention window
    (up to three months), so the content is not instantly erased from all
    systems everywhere, but it is no longer accessible through any product
    surface after this call.
    """
    console = Console()
    if not confirm_destructive(
        f"Permanently delete skill {skill_id}? This cannot be undone.",
        yes=yes,
    ):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.delete_workflow(skill_id)
    if result.deleted:
        console.print(f"[green]Permanently deleted[/green] {result.name}")
    else:
        console.print(f"[yellow]Not deleted[/yellow] {result.name}")


@app.command("delete-version")
def delete_version(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    version: int = typer.Argument(..., help="Version number to permanently delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Permanently and immediately delete a single non-current skill version.

    WARNING: This is permanent. The version row and all its attached files are
    removed from the live system at once. There is NO recovery path. The current
    (live) version cannot be deleted with this command; use `skills delete`
    to remove the entire skill including its current version.

    Use `skills archive` if you want a reversible alternative for the whole
    skill.

    Encrypted backups age out within the platform's standard retention window
    (up to three months), so the content is not instantly erased from all
    systems everywhere, but it is no longer accessible through any product
    surface after this call.

    Version numbers remain monotonic with a gap where the deleted version was.
    Surviving versions are not renumbered.
    """
    console = Console()
    if not confirm_destructive(
        f"Permanently delete skill {skill_id} version {version}? This cannot be undone.",
        yes=yes,
    ):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.delete_workflow_version(skill_id, version)
    if result.deleted:
        console.print(
            f"[green]Permanently deleted[/green] skill {result.workflow_id} v{result.version}"
        )
    else:
        console.print(f"[yellow]Not deleted[/yellow] skill {result.workflow_id} v{result.version}")


@app.command("grant")
def grant(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    grantee: str = typer.Argument(..., help="User or team UUID, email, or handle."),
    role: str = typer.Argument(..., help="Role to grant: view, edit, or admin."),
    include_history: bool = typer.Option(
        False,
        "--include-history",
        help=(
            "Share the skill's full version history"
            " (default: only the version current at share time and later)."
        ),
    ),
) -> None:
    """Grant skill access to a named user or team."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.grant_workflow(
            skill_id, grantee, role.lower(), include_history=include_history
        )
    history_note = " (full history)" if include_history else ""
    console.print(
        f"[green]Granted[/green] {result.role} on {result.workflow_id} to {grantee}{history_note}"
    )


@app.command("revoke-grant")
def revoke_grant(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    grantee: str = typer.Argument(..., help="User or team UUID, email, or handle."),
) -> None:
    """Revoke a direct skill grant."""
    console = Console()
    with _client(require_auth=True) as client:
        result = client.revoke_workflow_grant(skill_id, grantee)
    if result.revoked:
        console.print(f"[green]Revoked[/green] grant for {grantee}")
    else:
        console.print(f"[yellow]No direct grant found[/yellow] for {grantee}")


@app.command("grants")
def grants(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    json_output: bool = typer.Option(False, "--json", help="Print grants as JSON."),
    table_output: bool = typer.Option(False, "--table", help="Print grants as a table."),
) -> None:
    """List skill grants."""
    console = Console()
    mode = resolve_output_mode(json_output=json_output, table_output=table_output)
    with _client(require_auth=True) as client:
        result = client.list_workflow_grants(skill_id)
    if mode == "json":
        echo_json(result)
        return
    table = Table(title=f"Skill grants ({skill_id})")
    table.add_column("Grantee")
    table.add_column("Type")
    table.add_column("Role")
    table.add_column("Via team")
    table.add_column("History")
    for grant_row in result.items:
        if grant_row.includes_full_history:
            history_cell = "full"
        elif grant_row.shared_from_version is not None:
            history_cell = f"from v{grant_row.shared_from_version}"
        else:
            # Defensive fallback: a well-formed response always sets exactly one
            # of the two fields above, so this only triggers if the scope fields
            # are both absent (e.g. an older server). Label it as unknown rather
            # than a malformed version string.
            history_cell = "unknown"
        table.add_row(
            grant_row.grantee_identifier,
            grant_row.grantee_type,
            grant_row.role,
            "yes" if grant_row.is_via_team else "no",
            history_cell,
        )
    if not result.items:
        console.print("[dim]No grants.[/dim]")
    else:
        console.print(table)


@app.command("leave")
def leave(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove your personal access to a skill someone shared with you (team access stays)."""
    console = Console()
    if not confirm_destructive(f"Leave shared skill {skill_id}?", yes=yes):
        console.print("Cancelled.")
        raise typer.Exit(code=0)
    with _client(require_auth=True) as client:
        result = client.leave_shared_workflow(skill_id)
    console.print(
        f"[green]Left[/green] {result.workflow_id}; "
        f"removed {result.removed_direct_grants} direct grant(s)."
    )


def _split_version_suffix(value: str) -> tuple[str, int | None]:
    """Strip an optional trailing ``@N`` or ``@vN`` version suffix.

    Returns ``(identifier_without_suffix, version)``. ``@`` characters
    inside the identifier (e.g. ``@handle/slug``) are preserved because
    only the final segment after the last ``@`` is examined. A trailing
    ``@<garbage>`` (non-numeric) is treated as user error: the caller
    clearly meant to pin a version, so ``ValidationFailed`` is raised
    instead of silently passing the unparsed string through to the
    server (which would 404 with no useful signal).
    """
    if "@" not in value:
        return value, None
    head, _, tail = value.rpartition("@")
    if not head or not tail:
        return value, None
    candidate = tail[1:] if tail.startswith("v") else tail
    if not candidate.isdigit():
        raise ValidationFailed(
            slug="validation_error",
            message=(
                f"Version suffix '{tail}' is not a valid version number. "
                "Use @N or @vN (e.g. my-skill@3 or my-skill@v3)."
            ),
        )
    parsed = int(candidate)
    if parsed < 1:
        raise ValidationFailed(
            slug="validation_error",
            message=(
                f"Version suffix '{tail}' is not a valid version number. "
                "Use @N or @vN (e.g. my-skill@3 or my-skill@v3)."
            ),
        )
    return head, parsed


def _render_safety_check(result: SafetyCheckResult, console: Console) -> None:
    """Pretty-print a safety-check result, color-coded by status."""
    color = {
        "clean": "green",
        "flagged": "yellow",
        "blocked": "red",
        "error": "red",
    }.get(result.status, "white")
    console.print(
        f"[bold {color}]{result.status.upper()}[/bold {color}] "
        f"{result.resource_type} {result.resource_id} v{result.resource_version}"
    )
    if result.truncated:
        fields = rich_escape(", ".join(result.truncated_fields)) or "some fields"
        console.print(
            f"  [yellow]note[/yellow] {fields} exceeded the safety check input "
            "limit and were truncated for this scan; the authoritative verdict is "
            "the status recorded when the template was published."
        )
    for label, run in (("block", result.block), ("advisory", result.advisory)):
        verdict_color = {
            "pass": "green",
            "fail": "red",
            "error": "red",
        }.get(run.verdict, "white")
        verifier_id_text = run.verifier_id or "unknown"
        version_text = f" v{run.verifier_version}" if run.verifier_version is not None else ""
        run_id_text = f", verifier_run_id={run.verifier_run_id}" if run.verifier_run_id else ""
        console.print(
            f"  [{verdict_color}]{run.verdict}[/{verdict_color}] "
            f"{label} (verifier_id={verifier_id_text}{version_text}{run_id_text})"
        )
        if run.reasoning:
            # `reasoning` is server-controlled LLM-generated text. Escape so
            # tags like `[bold]` or `[/red]` are not interpreted as Rich markup.
            console.print(f"    {rich_escape(run.reasoning)}")


@app.command("check-safety")
def check_safety(
    skill_id: str = typer.Argument(
        ...,
        help=(
            "Skill UUID or slug, optionally pinned with ``@N`` or ``@vN`` "
            "(e.g. my-skill@3). The ``--version`` flag overrides any suffix."
        ),
    ),
    version: int | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Pin to a specific skill version; overrides any ``@N`` suffix.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Run the safety verifiers on a skill you can see.

    Auth required. Each call costs two metered verifier runs (one block,
    one advisory). The verdicts are not persisted onto the skill row;
    re-run to re-check. Returns ``status=clean`` when both verifiers pass,
    ``flagged`` when only the advisory fails, ``blocked`` when the block
    verifier fails, and ``error`` when the block verifier errors.
    """
    console = Console()
    parsed_id, parsed_version = _split_version_suffix(skill_id)
    effective_version = version if version is not None else parsed_version
    with _client(require_auth=True) as client:
        result = client.check_workflow_safety(parsed_id, version=effective_version)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    _render_safety_check(result, console)


@app.command("transfer-ownership")
def transfer_ownership(
    skill_id: str = typer.Argument(..., help="Skill UUID or name."),
    new_owner: str = typer.Argument(..., help="New owner UUID, email, or handle."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw response as JSON."),
) -> None:
    """Transfer a skill to another user. Returns an invitation the recipient must accept."""
    console = Console()
    with _client(require_auth=True) as client:
        body = client.transfer_workflow_ownership(skill_id, new_owner)
    if json_output:
        echo_json(body)
        return
    if "invitation_id" in body:
        console.print("Ownership transfer invited.")
        console.print(f"  id:         {body['invitation_id']}")
        console.print(f"  expires_at: {body.get('expires_at', '')}")
        console.print("")
        console.print(f"Recipient must run: goodeye invitations accept {body['invitation_id']}")
    else:
        console.print("No-op: target is already the owner.")


@app.command("teach")
def teach(
    skill_id: str = typer.Argument(..., help="Skill UUID or name to teach"),
) -> None:
    """Improve an existing skill by hand: teach it from examples and corrections you provide.

    The command returns the pack content; the agent (or you, working from
    a script) follows the pack to run the teach session and persist the
    result via `goodeye skills publish - --name <name>
    --description <description> --outcome <outcome> --source teach
    --expected-version-token <captured at stage 2>`.
    """
    stderr = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.teach_workflow(skill_id)
    stderr.print(f"[bold]skill_id:[/bold] {result.workflow_id}")
    # Write skill_md raw to stdout so markdown link syntax (`[text](url)`) is not
    # parsed as Rich markup, line wrapping is left to the caller, and piping into
    # an agent or file produces a clean SKILL.md.
    sys.stdout.write(result.skill_md)
    if not result.skill_md.endswith("\n"):
        sys.stdout.write("\n")


@app.command("optimize")
def optimize(
    skill_id: str = typer.Argument(..., help="Skill UUID or name to optimize"),
    max_iterations: int | None = typer.Option(
        None,
        "--max-iterations",
        help=(
            "Optimization loop budget. Defaults to 20 when omitted. "
            "Accepted range 1 to 1000; the upper bound is a safety cap, "
            "not a recommended setting (most runs converge well before that)."
        ),
        min=1,
        max=1000,
    ),
) -> None:
    """Automatically improve an existing skill: runs an optimization loop to tune it
    against its own verifier results.

    The command returns the pack content; the agent (or you, working from
    a script) follows the pack to run the optimization loop, then persists
    the final version via:

    \b
        goodeye skills publish - --name <name> --description <description>
            --outcome <outcome> --source optimization
            --expected-version-token <captured at stage 1>

    only after explicit user approval. The loop never auto-saves.
    """
    stderr = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.optimize_workflow(skill_id, max_iterations=max_iterations)
    stderr.print(f"[bold]skill_id:[/bold] {result.workflow_id}")
    stderr.print(f"[bold]max_iterations:[/bold] {result.max_iterations}")
    # Stream skill_md raw to stdout so markdown link syntax (`[text](url)`) is
    # not parsed as Rich markup. Reference files are appended as labeled
    # sub-sections so a piped agent sees the full pack in order, matching the
    # rendering used by `goodeye design`.
    sys.stdout.write(result.skill_md.rstrip() + "\n")
    if result.references:
        sys.stdout.write("\n---\n\n# Reference files\n")
        for path in sorted(result.references):
            body = result.references[path]
            sys.stdout.write(f"\n## {path}\n\n{body.rstrip()}\n")


@app.command("optimize-description")
def optimize_description(
    skill_id: str = typer.Argument(..., help="Skill UUID or name whose description to optimize"),
    max_iterations: int | None = typer.Option(
        None,
        "--max-iterations",
        help=(
            "Optimization loop budget. Defaults to 10 when omitted. "
            "Accepted range 1 to 1000; the upper bound is a safety cap, "
            "not a recommended setting (most runs converge well before that)."
        ),
        min=1,
        max=1000,
    ),
) -> None:
    """Tune an existing skill's trigger: sharpen its description so it fires at the right times.

    The pack guides an agent (or you, working from a script) through tuning
    the skill's description, the text that decides when the skill
    fires, for trigger accuracy. Only the description changes; the body,
    outcome, tags, and sibling files carry forward. After explicit user
    approval the final version is persisted via:

    \b
        goodeye skills publish - --name <name> --description <description>
            --outcome <outcome> --source description_optimization
            --expected-version-token <captured at stage 1>

    The loop never auto-saves.
    """
    stderr = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.optimize_description(skill_id, max_iterations=max_iterations)
    stderr.print(f"[bold]skill_id:[/bold] {result.workflow_id}")
    stderr.print(f"[bold]max_iterations:[/bold] {result.max_iterations}")
    # Stream skill_md raw to stdout so markdown link syntax (`[text](url)`) is
    # not parsed as Rich markup. Reference files are appended as labeled
    # sub-sections so a piped agent sees the full pack in order, matching the
    # rendering used by `goodeye skills optimize`.
    sys.stdout.write(result.skill_md.rstrip() + "\n")
    if result.references:
        sys.stdout.write("\n---\n\n# Reference files\n")
        for path in sorted(result.references):
            body = result.references[path]
            sys.stdout.write(f"\n## {path}\n\n{body.rstrip()}\n")


@app.command("audit")
def audit(
    skill_id: str | None = typer.Argument(
        None,
        help=(
            "Skill UUID, slug, or name to audit. Omit to audit a local "
            "skill file that is not yet saved to Goodeye."
        ),
    ),
) -> None:
    """Review an existing hosted skill against best practices (or a local skill file not
    yet on Goodeye) and fix what it flags.

    The command returns the pack content; the agent (or you, working from a
    script) follows the pack to run the audit, then applies the fixes you
    approve and syncs the result via:

    \b
        goodeye skills publish - --name <name> --description <description>
            --outcome <outcome> --source audit
            --expected-version-token <captured during the audit>

    Without a skill id, the pack audits a local skill file and recommends
    saving it to Goodeye.
    """
    stderr = Console(stderr=True)
    with _client(require_auth=True) as client:
        result = client.audit_workflow(skill_id)
    if result.workflow_id:
        stderr.print(f"[bold]skill_id:[/bold] {result.workflow_id}")
    # Stream skill_md raw to stdout so markdown link syntax is not parsed as
    # Rich markup. Reference files are appended as labeled sub-sections so a
    # piped agent sees the full pack in order, matching goodeye skills optimize.
    sys.stdout.write(result.skill_md.rstrip() + "\n")
    if result.references:
        sys.stdout.write("\n---\n\n# Reference files\n")
        for path in sorted(result.references):
            body = result.references[path]
            sys.stdout.write(f"\n## {path}\n\n{body.rstrip()}\n")


__all__ = [
    "_parse_front_matter",
    "app",
    "archive",
    "audit",
    "check_safety",
    "delete",
    "delete_version",
    "get_cmd",
    "grant",
    "grants",
    "leave",
    "lineage",
    "list_cmd",
    "optimize",
    "optimize_description",
    "publish",
    "put_file",
    "revoke_grant",
    "rm_file",
    "teach",
    "transfer_ownership",
    "unarchive",
]
