# goodeye-cli

Command-line client for Goodeye - manage AI workflow skills from the terminal.

Goodeye is an outcome-aligned AI workflow registry: you author skills (markdown
bodies + manifests) and verifiers that score an AI agent against a measurable
business outcome. This CLI is wired to the public `/v1/` REST API.

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
goodeye skills list --filter public

# Create an account (emails a one-time code)
goodeye signup --email you@example.com

# Or log in on a machine with a browser
goodeye login

# Confirm who you are
goodeye whoami

# Fetch a public skill as markdown
goodeye skills get brand-voice > brand-voice.md

# Publish a local skill
goodeye skills publish ./my-skill.md --public
```

### Skill files

`goodeye skills publish` reads a markdown file with YAML front-matter that
follows the Claude Code skills convention. Only `name` and `description` are
required:

```markdown
---
name: my-skill
description: One sentence on what this skill does and when to use it.
# Optional:
# visibility: public        # overridden by --public
# tags: [data, cleanup]
# manifest:                 # optional verifier block
#   outcome: Reduce refund-row mislabels
#   kpi: { name: error_rate, definition: rows mislabeled / total }
---

# Body

The rest of the file is the skill body rendered to the agent at runtime.
```

`--public` on the command line overrides `visibility`. `--name` on the command
line overrides the front-matter `name`. The full file (front-matter included)
is stored on the server, so `goodeye skills get` round-trips a drop-in
`~/.claude/skills/<name>/SKILL.md`.

## Command reference

```
goodeye login [--email EMAIL]
    Without --email: opens the browser for WorkOS device-code approval.
    With --email: sends a one-time code to your inbox.

goodeye signup --email EMAIL
    Creates an account and mints your initial API key.

goodeye logout
    Deletes local credentials. Does not revoke the key server-side; see
    `goodeye auth list-keys` and `goodeye auth revoke-key`.

goodeye whoami
    Shows the current user identified by your credentials.

goodeye auth create-key --name NAME [--copy]
    Mints a new API key. The secret is printed once.

goodeye auth list-keys
    Table of your API keys (secrets are never returned).

goodeye auth revoke-key <key-id>
    Revokes (soft-deletes) a key. <key-id> is the ULID shown by `auth list-keys`.

goodeye skills list [--filter all|public|mine] [--tag TAG] [--search QUERY] [--json]
    Paginated listing; auto-follows cursor. The ID column in the output is what
    `delete`, `set-visibility`, and `publish --id` expect.

goodeye skills get <id-or-name> [--version N] [--output PATH] [--json]
    Accepts either the skill's ULID or its name. Emits raw markdown by default;
    --json returns the envelope.

goodeye skills publish <file.md> [--id SKILL-ID] [--public] [--name NAME]
    Creates a new skill or, when --id is given, appends a new version to the
    existing skill with that ULID. Front-matter must include `name:` and
    `description:`.

goodeye skills set-visibility <skill-id> <private|public>
    Change visibility of a skill. <skill-id> must be the ULID (not the name);
    run `skills list` to find it.

goodeye skills delete <skill-id> [--yes]
    Soft-delete a skill. <skill-id> must be the ULID (not the name);
    run `skills list` to find it.

goodeye design
    Prints the workflow-designer prompt pack to stdout. Save to a file and
    load it as context in your AI assistant session:
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
