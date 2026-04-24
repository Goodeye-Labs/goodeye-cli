# goodeye-cli

Command-line client for Goodeye - manage AI workflow workflows from the terminal.

Goodeye is an outcome-aligned AI workflow registry: you author workflows as
markdown runbooks tagged with the business outcome they serve, and verifiers
that score an AI agent against a measurable business result. This CLI is wired
to the public `/v1/` REST API.

## Install

Requires Python 3.12+.

```sh
uv tool install goodeye
# or
pipx install goodeye
# or
pip install goodeye
```

Once installed, the `goodeye` command is available on your `PATH`.

## Quickstart

```sh
# Browse the public registry without an account
goodeye workflows list --filter public

# Create an account (emails a one-time code)
goodeye signup --email you@example.com

# Or log in on a machine with a browser
goodeye login

# Confirm who you are
goodeye whoami

# Fetch a public workflow as markdown (copy `id` from the JSON; without a login,
# `get` resolves public workflows by UUID only, not by name)
goodeye workflows list --filter public --json
goodeye workflows get <id-from-above> > example-workflow.md

# Publish a local workflow
goodeye workflows publish ./my-workflow.md --public
```

### Workflow files

`goodeye workflows publish` reads a markdown file with YAML front-matter that
follows the Claude Code skills convention. Only `name` and `description` are
required:

```markdown
---
name: my-workflow
description: One sentence on what this workflow does and when to use it.
# Optional discovery facets:
# visibility: public        # overridden by --public
# tags: [data, cleanup]
# outcome: Reduce refund-row mislabels.
---

# Body

The rest of the file is the workflow body rendered to the agent at runtime.
Verifier scripts and Truesight cURLs belong here as fenced code blocks; the
registry stores the body verbatim.
```

`--public` on the command line overrides `visibility`. `--name` on the command
line overrides the front-matter `name`. The full file (front-matter included)
is stored on the server, so `goodeye workflows get` round-trips a drop-in
`~/.claude/skills/<name>/SKILL.md`.

Pre-cleanup files that nest `outcome` / `tags` under a `manifest:` block are
still accepted: those two keys are promoted to the top level and a deprecation
warning lists any other manifest keys (`kpi`, `programmatic_verifiers`, etc.)
that the server no longer stores. Move verifier scripts and cURLs into the body
when you next edit such a file.

## Command reference

```
goodeye login [--email EMAIL]
    Sign in on this machine. Opens the browser by default; with --email,
    signs in via a one-time code sent to your inbox.

goodeye signup --email EMAIL
    Create a Goodeye account. A one-time code is sent to your email.

goodeye logout
    Sign out on this machine by removing saved credentials. The key stays
    valid on the server; use `goodeye auth revoke-key` to disable it.

goodeye whoami
    Show who you're signed in as.

goodeye auth create-key --name NAME [--copy]
    Create a new API key. The secret is shown once - save it somewhere safe.

goodeye auth list-keys
    List your API keys. Secrets are never shown.

goodeye auth revoke-key <key-id>
    Revoke an API key. The key stops working immediately. <key-id> is the
    ID shown by `auth list-keys`.

goodeye workflows list [--filter all|public|mine] [--tag TAG] [--search QUERY] [--json]
    List workflows you can access. The ID column is accepted by `get`, `delete`,
    and `set-visibility`. When signed in, you can also use your own workflow
    name (slug) with those commands.

goodeye workflows get <id-or-name> [--version N] [--output PATH] [--json]
    Download a workflow. Prints markdown to stdout; --json prints the full
    record. When you are not signed in, use the workflow's UUID from `list`
    (name/slug resolution is for your own workflows once authenticated).

goodeye workflows publish <file.md> [--public] [--name NAME]
    Publish a workflow from a markdown file. If a workflow with the same name
    already exists under your account, a new version is appended. Front-matter
    must include `name:` and `description:`.

goodeye workflows set-visibility <id-or-name> <private|public>
    Change a workflow's visibility.

goodeye workflows delete <id-or-name> [--yes]
    Delete a workflow you own.

goodeye design
    Print the workflow-designer prompt to stdout. Pipe it into your AI
    assistant to start designing a workflow + verifier:
        goodeye design > prompt.md
```

## Configuration

### Credentials

- `GOODEYE_API_KEY` env var (highest precedence).
- `~/.config/goodeye/credentials.json` (or `$XDG_CONFIG_HOME/goodeye/`).

Credential files are created with mode `0600`.

### Server

- `GOODEYE_SERVER` env var.
- `server` field inside `credentials.json`.
- Default: `https://mcp.goodeyelabs.com`.

## REST API, not the CLI

This CLI is pinned to the `/v1/` REST API contract. If you are integrating
programmatically and want a stable contract, prefer the REST API directly;
the CLI is a convenience layer over it.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for local-dev setup and the PR
process. Issues and PRs welcome.

## License

MIT. See [LICENSE](./LICENSE).
