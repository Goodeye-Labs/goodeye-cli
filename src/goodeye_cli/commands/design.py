"""`goodeye design` command."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console

from goodeye_cli.client import GoodeyeClient
from goodeye_cli.config import get_api_key, get_server
from goodeye_cli.errors import AuthRequired


def _render_prompt_pack(payload: dict[str, Any]) -> str | None:
    """Collapse a `design_workflow` payload into pipe-ready markdown.

    The MCP tool returns ``{"skill_md": <SKILL.md>, "references": {path: body}}``.
    Emit the SKILL.md first, then each reference file as a sub-section so the
    assistant on the other end of the pipe sees the full pack in order.
    """
    skill_md = payload.get("skill_md")
    if not isinstance(skill_md, str):
        return None
    out: list[str] = [skill_md.rstrip() + "\n"]
    references = payload.get("references")
    if isinstance(references, dict) and references:
        out.append("\n---\n\n# Reference files\n")
        for path in sorted(references):
            body = references[path]
            if not isinstance(body, str):
                continue
            out.append(f"\n## {path}\n\n{body.rstrip()}\n")
    return "".join(out)


def design(
    json_output: bool = typer.Option(False, "--json", help="Print the full response as JSON."),
) -> None:
    """Print the workflow-designer prompt to stdout.

    Pipe it into your AI assistant to start designing a skill + verifier:

    \b
        goodeye design > prompt.md
    """
    console = Console(stderr=True)
    server = get_server()
    api_key = get_api_key()
    if not api_key:
        raise AuthRequired(
            slug="auth_required",
            message="Authentication required to fetch the design prompt.",
            hint="Run `goodeye login`.",
        )
    with GoodeyeClient(server, api_key=api_key) as client:
        payload = client.get_design_prompt()

    if json_output:
        typer.echo(json.dumps(payload))
        return

    rendered = _render_prompt_pack(payload)
    if rendered is not None:
        typer.echo(rendered)
        return
    # Legacy fallbacks for future payload shapes.
    for key in ("prompt", "body", "content", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            typer.echo(value)
            return
    console.print("[yellow]Unrecognized design payload; emitting JSON.[/yellow]")
    typer.echo(json.dumps(payload, indent=2))
