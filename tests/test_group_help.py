"""Guards on how command-group help is wired.

Passing ``help=`` to ``add_typer`` overrides the sub-app's own
``Typer(help=...)`` outright rather than merging with it. When both are set the
module-level text never renders anywhere, so the richer description that lives
next to the commands becomes dead code that reads as if it ships. Every group
had this at one point, including the only description of verifiers as
owner-scoped and versioned.

These tests pin the convention: each group owns its help in its own module, and
``app.py`` only mounts it.

Note on what these assert against. The composed click command reports the
*effective* help, which under a shadowing ``help=`` is the shadowing text
itself. Comparing rendered output to that is tautological and passes while the
bug is present. So the structural check reads Typer's own registration record,
and the render check compares against the module's declared text.
"""

from __future__ import annotations

import pytest
from typer.models import DefaultPlaceholder, TyperInfo
from typer.testing import CliRunner

from goodeye_cli.app import app

runner = CliRunner()

# Groups mounted a second time under another name to keep an old spelling
# working. The alias supplies its own help so the deprecation is visible, which
# is the one legitimate reason to pass help= to add_typer.
ALIAS_GROUPS = {"workflows", "subscription"}


def _groups() -> dict[str, TyperInfo]:
    return {str(info.name): info for info in app.registered_groups}


def _declared_help(info: TyperInfo) -> str:
    """The help the group's own module declares."""
    assert info.typer_instance is not None
    return (info.typer_instance.info.help or "").strip()


def test_groups_are_discovered() -> None:
    """Guard the guard: these tests are worthless against an empty set."""
    assert len(_groups()) >= 10


@pytest.mark.parametrize("name", sorted(_groups()))
def test_add_typer_does_not_shadow_module_help(name: str) -> None:
    """``app.py`` must not pass ``help=`` when the module declares its own."""
    info = _groups()[name]
    if name in ALIAS_GROUPS:
        pytest.skip("alias group: its own help marks the deprecation")
    assert isinstance(info.help, DefaultPlaceholder), (
        f"add_typer(name={name!r}) passes help=, which silently shadows the "
        f"module's own Typer(help=...). Delete it and let the module own the "
        f"text, using short_help for the one-liner in the parent listing."
    )


@pytest.mark.parametrize("name", sorted(_groups()))
def test_every_group_declares_help(name: str) -> None:
    assert _declared_help(_groups()[name]), f"group {name!r} declares no help"


@pytest.mark.parametrize("name", sorted(_groups()))
def test_group_help_renders_what_the_module_declares(name: str) -> None:
    """Invoking the real CLI proves the user sees the module's text."""
    info = _groups()[name]
    if name in ALIAS_GROUPS:
        pytest.skip("alias group: renders the alias help by design")

    result = runner.invoke(app, [name, "--help"])
    assert result.exit_code == 0

    # The renderer re-wraps to terminal width, so compare on the first
    # sentence with whitespace collapsed rather than the raw string.
    first_sentence = _declared_help(info).split(".")[0]
    rendered = " ".join(result.output.split())
    assert first_sentence in rendered, (
        f"`goodeye {name} --help` does not render its declared help; "
        f"a shadowing help= on add_typer is the usual cause"
    )


@pytest.mark.parametrize("name", sorted(_groups()))
def test_group_help_has_no_markdown_bold(name: str) -> None:
    """``rich_markup_mode`` is not ``markdown``, so ``**`` renders literally.

    Bold written as ``**text**`` reaches the user as visible asterisks. Use
    backticks, which the rest of the CLI's help already uses for literals.
    """
    assert "**" not in _declared_help(_groups()[name]), (
        f"group {name!r} uses markdown bold, which renders as literal asterisks"
    )


@pytest.mark.parametrize("name", sorted(_groups()))
def test_long_group_help_declares_a_short_help(name: str) -> None:
    """Long help needs an explicit ``short_help`` or the listing gets truncated.

    Without one, click derives the parent listing entry by cutting the help
    text and appending an ellipsis, which reads as a broken sentence.
    """
    info = _groups()[name]
    if len(_declared_help(info)) <= 120:
        return
    assert info.typer_instance is not None
    short = info.typer_instance.info.short_help
    assert short and not isinstance(short, DefaultPlaceholder), (
        f"group {name!r} has long help but no short_help, so the parent "
        f"listing will show a truncated sentence"
    )
