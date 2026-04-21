# Contributing

Thanks for your interest in goodeye-cli.

## Dev setup

Prerequisite: [uv](https://github.com/astral-sh/uv) (Python package manager).

```sh
git clone https://github.com/Goodeye-Labs/goodeye-cli.git
cd goodeye-cli
uv sync --group dev
uv run pre-commit install
```

## Run the same checks CI runs

```sh
uv sync --group dev --frozen
uv run pre-commit run --all-files
uv run pytest
```

## Project layout

- `src/goodeye_cli/` - CLI package
  - `app.py` - Typer app root
  - `commands/` - one module per subcommand group
  - `client.py` - sync HTTP client around the Goodeye REST API
  - `auth_flows.py` - device-code and magic-auth flows
  - `config.py` - credentials + client config file I/O
  - `wire.py` - pydantic wire models for REST responses
  - `errors.py` - structured CLI errors (mirror server slugs)
- `tests/` - pytest suite. Network is mocked with `respx`; no live calls.

## Conventions

- Python 3.12+, ruff for lint + format, pyright for types.
- Line length 100.
- No em dashes in prose, comments, or docs; use `-`, `:`, or rephrase.
- Never commit secrets. Tests should mock WorkOS and the Goodeye server.
- Use obvious placeholders in example tokens (e.g. `good_live_EXAMPLE_...`).

## PR process

1. Branch from `main`.
2. Keep changes scoped; one logical change per PR.
3. Ensure `pre-commit` and `pytest` pass locally.
4. Open a PR against `main`. CI runs ruff + pyright + pytest.
5. A maintainer will review.

## Reporting security issues

Please do not open public issues for security reports. Email the maintainers
or use GitHub's private vulnerability reporting feature on this repository.
