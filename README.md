# goodeye-cli

Public CLI for [Goodeye](https://mcp.goodeyelabs.com) - manage AI workflow
skills from the terminal.

Goodeye is an outcome-aligned AI workflow registry: you author skills (markdown
bodies + manifests) and verifiers that score an AI agent against a measurable
business outcome. This CLI is the command-line companion to the Goodeye server,
wired to the public `/v1/` REST API.

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

# Push a local skill
goodeye skills push ./my-skill.md --public
```

### Skill files

`goodeye skills push` reads a markdown file with optional YAML front-matter:

```markdown
---
slug: my-skill
visibility: private
manifest:
  title: My skill
  tags: [data, cleanup]
---

# Body

The rest of the file is the skill body rendered to the agent at runtime.
```

`--public` on the command line overrides `visibility`. `--slug` on the command
line overrides the front-matter `slug`.

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

goodeye auth revoke-key <id>
    Revokes (soft-deletes) a key.

goodeye skills list [--filter all|public|own] [--tag TAG] [--search QUERY] [--json]
    Paginated listing; auto-follows cursor.

goodeye skills get <id-or-slug> [--version N] [--output PATH] [--json]
    Emits raw markdown by default; --json returns the envelope.

goodeye skills push <file.md> [--id ID] [--public] [--slug SLUG]
    Creates or appends a skill version.

goodeye skills set-visibility <id> <private|public>
goodeye skills delete <id> [--yes]

goodeye design
    Prints the workflow-designer prompt pack to stdout.
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

This CLI is pinned to the `/v1/` REST API contract defined by the Goodeye
server. If you are integrating programmatically and want a stable contract,
prefer the REST API directly. The CLI is a convenience layer over it.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for local-dev setup and the PR
process. Issues and PRs welcome.

## License

MIT. See [LICENSE](./LICENSE).
