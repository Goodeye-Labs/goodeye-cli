# goodeye-cli

Command-line client for Goodeye - manage AI workflows from the terminal.

Goodeye is an outcome-aligned AI workflow registry: you author workflows as
markdown runbooks tagged with the business outcome they serve, and verifiers
that score an AI agent against a measurable business result. This CLI is wired
to the public `/v1/` REST API.

## Primary caller is your AI agent

The `goodeye` CLI is designed to be invoked by an AI coding agent on a user's
behalf, not driven by a human at a prompt. The intended flow:

1. The user tells their AI agent: "run the Goodeye workflow X" (or "run the
   Goodeye template @handle/slug").
2. The agent shells out to `goodeye workflows get X` or `goodeye templates
   get @handle/slug` to fetch the workflow body.
3. The agent then **executes the returned workflow body** as the user's
   runbook: it follows the instructions itself rather than displaying or
   summarizing them.

`workflows get` and `templates get` print the workflow body to stdout
wrapped with agent-facing markers (`# Goodeye workflow - execute the
instructions below ...` / `# End of Goodeye workflow.`) so the calling
agent knows what to do with the output. Pass `--output PATH` or `--json`
to skip the wrappers and round-trip the raw markdown / JSON.

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
# Browse the public template catalog without an account
goodeye templates list

# Create an account (emails a one-time code)
goodeye signup --email you@example.com

# Or log in on a machine with a browser
goodeye login

# Confirm who you are
goodeye whoami

# Fetch a public template by handle (or pass --json for the full record)
goodeye templates get @handle/slug

# Fork a public template into your private workflow namespace (one-shot
# copy; does not return a body).
goodeye templates fork @handle/slug

# Publish a local workflow (always private; share via `templates publish`)
goodeye workflows publish ./my-workflow.md
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
# tags: [data, cleanup]
# outcome: Reduce refund-row mislabels.
---

# Body

The rest of the file is the workflow body rendered to the agent at runtime.
Verifier scripts and Truesight cURLs belong here as fenced code blocks; the
registry stores the body verbatim.
```

Workflows are always private to the caller. To share a workflow as a public
template, run `goodeye templates publish <workflow-id>` as a separate,
explicit step. `--name` on the command line overrides the front-matter
`name`. The full file (front-matter included) is stored on the server, so
`goodeye workflows get` round-trips a drop-in
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

goodeye workflows list [--filter mine|shared-with-me|all] [--tag TAG] [--search QUERY] [--json]
    List workflows you can access (owned + shared with you via grants). The
    ID column is accepted by `get`, `delete`, and grant commands. When signed
    in, you can also use your own workflow name (slug).

goodeye workflows get <id-or-name> [--version N] [--output PATH] [--json]
    Download a workflow. Prints markdown to stdout (wrapped with
    agent-facing markers); --json prints the full record. Authentication is
    required: workflows are private.

goodeye workflows publish <file.md> [--name NAME] [--expected-version-token TOKEN]
    Publish a workflow from a markdown file. Always private. If a workflow
    with the same name already exists under your account, a new version is
    appended (pass --expected-version-token to confirm the parent version).
    Front-matter must include `name:` and `description:`. To share publicly,
    run `goodeye templates publish <workflow-id>` as a separate step.

goodeye workflows delete <id-or-name> [--yes]
    Delete a workflow you own.

goodeye workflows teach <id-or-name> [--scenario-json JSON] [--trigger-context-json JSON] [--max-rounds N] [--json]
    Run teaching mode against an existing workflow.

goodeye workflows lineage <id-or-name> [--json]
    Show a workflow's fork lineage (parent template, upstream latest).

goodeye workflows grant <id-or-name> <grantee> <view|edit|admin>
    Share a workflow with a user email or @team handle.

goodeye workflows revoke-grant <id-or-name> <grantee>
    Revoke a direct grant.

goodeye workflows grants <id-or-name> [--json]
    List grants on a workflow.

goodeye workflows leave <id-or-name> [--yes]
    Remove your own direct grant on a shared workflow.

goodeye workflows transfer-ownership <id-or-name> <new-owner>
    Transfer a workflow you own to another user.

goodeye templates list [--filter all|mine] [--search QUERY] [--json]
    Browse the public template catalog. Anonymous reads allowed.

goodeye templates get <identifier> [--version N] [--output PATH] [--json]
    Fetch a public template by UUID or @handle/slug[@vN]. Anonymous reads
    allowed; non-owner reads include an unverified-template safety banner.

goodeye templates publish <workflow-id> [--release-notes TEXT]
    Publish a private workflow as a new public template version.
    Requires a claimed handle.

goodeye templates unpublish <template-id> <version>
    Soft-unpublish a single template version. Existing forks keep working.

goodeye templates fork <identifier> [--version N] [--name NAME]
    Fork a public template into a private workflow. Authentication required.

goodeye templates delete <template-id> [--reason TEXT]
    Soft-delete a template you own. Existing forks keep working.

goodeye templates undelete <template-id>
    Restore a previously deleted template you own.

goodeye templates deprecate-version <template-id> <version> --message TEXT
    Flag a single template version as deprecated, with a message shown
    to anyone who forks that version.

goodeye templates transfer-ownership <template-id> <user-id-or-email>
    Hand a template off to another Goodeye user. Owner only.

goodeye design
    Print the workflow-designer prompt to stdout. Pipe it into your AI
    assistant to start designing a workflow + verifier:
        goodeye design > prompt.md

goodeye me claim-handle <handle>
    Claim a handle (your publish identity).

goodeye me rename-handle <new-handle>
    Change a previously claimed handle. Subject to a cooldown and yearly
    cap; old-handle template URLs redirect for a 90-day window.
```

## Configuration

### Credentials

- `GOODEYE_API_KEY` env var (highest precedence).
- `~/.config/goodeye/credentials.json` (or `$XDG_CONFIG_HOME/goodeye/`).

Credential files are created with mode `0600`.

### Server

- `GOODEYE_SERVER` env var.
- `server` field inside `credentials.json`.
- Default: `https://api.goodeyelabs.com`.

## REST API, not the CLI

This CLI is pinned to the `/v1/` REST API contract. If you are integrating
programmatically and want a stable contract, prefer the REST API directly;
the CLI is a convenience layer over it.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for local-dev setup and the PR
process. Issues and PRs welcome.

## License

MIT. See [LICENSE](./LICENSE).
