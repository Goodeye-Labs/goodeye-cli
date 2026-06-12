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

### Updates

The CLI checks PyPI in the background (at most every four hours) and may print a short notice to **stderr** when a newer release exists. Notices are skipped in CI (`CI` set), for `--json` output, and for `goodeye update` itself so machine-readable stdout stays clean.

- `goodeye update --check`: show your installed version vs PyPI and whether an update is available.
- `goodeye update`: update automatically when the install method is recognized:
  - **uv tool:** runs `uv tool upgrade goodeye`
  - **pipx:** runs `pipx upgrade goodeye`
  - **pip:** runs `python -m pip install --upgrade goodeye` using the same interpreter as the CLI

Editable installs, unknown layouts, or missing `uv`/`pipx` on PATH are not auto-updated; the command prints the same manual install lines as above. After a successful update, run `goodeye --version` (or rerun your command) to confirm the new version.

## Quickstart

```sh
# Browse the public template catalog without an account
goodeye templates list

# Create an account (non-interactive: start, then verify with the emailed code)
goodeye register --email you@example.com
goodeye register-verify --email you@example.com --code 123456

# Or log in on a machine with a browser (interactive device-code flow)
goodeye login

# Confirm who you are
goodeye whoami

# Fetch a public template by handle (or pass --json for the full record)
goodeye templates get @handle/slug

# Fork a public template into your private workflow namespace (one-shot
# copy; does not return a body).
goodeye templates fork @handle/slug

# Save a generated workflow without creating a local file
goodeye workflows publish - \
  --name my-workflow \
  --description "One sentence on what this workflow does and when to use it." \
  --outcome "Reduce refund-row mislabels" \
  --tag data \
  --tag cleanup <<'EOF'
# Body

The rest of the workflow body goes here.
EOF
```

### Workflow input

For AI agents generating a workflow body, prefer stdin so no intermediate file is left in the user's working directory:

```sh
goodeye workflows publish - \
  --name my-workflow \
  --description "One sentence on what this workflow does and when to use it." \
  --outcome "Reduce refund-row mislabels" \
  --tag data \
  --tag cleanup <<'EOF'
# Body

The rest of the workflow body goes here.
EOF
```

Use a markdown file when you already have one or intentionally want a durable local copy:

```sh
goodeye workflows publish ./my-workflow.md
```

The markdown uses YAML front matter following the Goodeye workflow body
convention. `name`, `description`, and `outcome` are required; `tags` are
optional:

```markdown
---
name: my-workflow
description: One sentence on what this workflow does and when to use it.
outcome: Reduce refund-row mislabels.
# Optional discovery facet:
# tags: [data, cleanup]
---

# Body

The rest of the file is the workflow body rendered to the agent at runtime.
Inline checks (structural format/schema, functional tests/bounds) belong here
as fenced code blocks. LLM-judge checks are deployed separately as verifiers
(see "Verifiers" below) and referenced from the body by `verifier_id` or
`verifier_id@version`. The registry stores the body verbatim.
```

Workflows are always private to the caller. To share a workflow as a public
template, run `goodeye templates publish <workflow-uuid-or-name>` as a separate,
explicit step. `--name`, `--description`, `--outcome`, and `--tag` on the
command line override matching front-matter metadata. Goodeye stores the full
markdown body, including front matter when present, so `goodeye workflows get`
can round-trip the workflow body.

### Verifiers

A verifier is a single LLM-judge criterion ("does this output satisfy this
rule?") deployed to your account and runnable on demand. A workflow body can
call out to one by UUID (or `UUID@version`) to gate or score an agent's
output. Verifiers are private and owner-scoped; there is no public catalog.

Three input shapes are supported:

- `text`: judges text fields only.
- `text_image`: judges text fields together with one image.
- `image`: judges a single image with no text inputs.

Deploy a verifier from generated JSON with stdin:

```sh
goodeye verifiers deploy - <<'EOF'
{
  "name": "refund-claim-supported",
  "description": "Flag refund replies that lack a stated reason.",
  "criterion": "Return passed=true only when the reply text states a concrete reason for the refund (an order issue, defect, billing error, etc.). Generic apologies without a stated reason fail.",
  "input_contract": "text",
  "input_fields": ["reply_text"],
  "few_shot_examples": [
    {
      "inputs": {"reply_text": "Refunded $42.10 for the cracked mug per your photo."},
      "passed": true,
      "reasoning": "Reason given: cracked mug."
    },
    {
      "inputs": {"reply_text": "Sorry for the trouble! Refund issued."},
      "passed": false,
      "reasoning": "No reason stated."
    }
  ],
  "model_settings": {"model": "openai/gpt-5.4", "reasoning_effort": "medium"}
}
EOF
# Deployed refund-claim-supported v1 (verifier_id=..., version_token=...)
```

If you already have a durable config file, file input still works:

```sh
goodeye verifiers deploy ./refund-claim-supported.json
```

Re-deploying the same `name` appends a new version. The second deploy must
include `expected_version_token` from the previous response (or from
`goodeye verifiers list`); a token mismatch returns 409.

Run a verifier against one input:

```sh
goodeye verifiers run <verifier_id> \
    --inputs-json '{"reply_text": "Refunded for the bent shipment."}' \
    --workflow-id <workflow-uuid> --workflow-version 3
# PASS verifier_run_id=...
```

`--inputs-json` keys must match the deployed `input_fields` exactly (no
missing or extra). For `text_image` and `image` contracts, pass a public
HTTPS URL via `--media-url`. The optional `--workflow-id`,
`--workflow-version`, `--workflow-ref`, and `--run-id` flags stamp
provenance onto the persisted run row. The command exits 0 on a successful
judgment regardless of pass/fail; check the PASS/FAIL line or `--json`
output to gate downstream actions.

Inspect, list, or retire:

```sh
goodeye verifiers list
goodeye verifiers show <verifier_id> [--version N]
goodeye verifiers revoke <verifier_id>      # irreversible; deploy a fresh one to replace
```

### Login and registration

For humans, use the interactive browser login:

```bash
goodeye login
```

For AI agents, automation, or terminals where prompts are awkward, use the
non-interactive email-code flow:

```bash
goodeye register --email you@example.com
goodeye register-verify --email you@example.com --code 123456
```

Existing users can start and complete non-interactive login the same way:

```bash
goodeye login --email you@example.com
goodeye login-verify --email you@example.com --code 123456
```

Successful `register-verify`, `login-verify`, and interactive `login` all save
credentials to `~/.config/goodeye/credentials.json` so future commands stay
authenticated.

## Command reference

### Output modes and pagination

List and search commands are TTY-aware: when stdout is a terminal they print a
Rich table by default, and when stdout is redirected or captured they print
compact single-line JSON. Pass `--table` or `--json` to choose explicitly; the
flags are mutually exclusive. JSON list output is always wrapped in an object:
paginated lists print `{"items":[...],"next_cursor":...}`, while unpaginated
lists print `{"items":[...]}`. Search commands print the search response object
with `items`, `query`, `limit`, and `search_mode`.

Paginated list commands (`workflows list`, `templates list`, `verifiers list`,
and `auth list-keys`) fetch one page by default with `--limit 25`. Use
`--limit N` to change page size, `--cursor TOKEN` to start from a cursor, or
`--all` to follow cursors and combine all returned items. Table output shows a
short next-page command when more results are available. `teams list`, `teams
members`, and `workflows grants` are currently unpaginated and do not expose
`--limit`, `--cursor`, or `--all`.

```
goodeye login
    Interactive sign-in for humans: browser/device-code flow; saves
    credentials on success.

goodeye login --email EMAIL
    Non-interactive email-code login start for agents and automation. Does not
    save credentials until you run goodeye login-verify with the emailed code.

goodeye login-verify --email EMAIL --code CODE
    Complete non-interactive email login and save credentials locally.

goodeye register --email EMAIL
    Start non-interactive account registration (emails a code when eligible).

goodeye register-verify --email EMAIL --code CODE
    Complete registration and save credentials locally.

goodeye logout
    Sign out on this machine by removing saved credentials. The key stays
    valid on the server; use `goodeye auth revoke-key` to disable it.

goodeye whoami
    Show who you're signed in as.

goodeye auth create-key --name NAME [--copy]
    Create a new API key. The secret is shown once - save it somewhere safe.

goodeye auth list-keys [--limit N] [--cursor TOKEN] [--all] [--json|--table]
    List your API keys. Secrets are never shown. Fetches one page by default;
    --all follows cursors and returns a combined items envelope.

goodeye auth revoke-key <key-id-or-name>
    Revoke an API key. The key stops working immediately. The argument may
    be the ID shown by `auth list-keys` or a unique key name.

goodeye workflows list [--filter mine|shared-with-me|all] [--tag TAG] [--search QUERY] [--limit N] [--cursor TOKEN] [--all] [--include-archived] [--json|--table]
    List workflows you can access (owned + shared with you via grants). The
    ID column is accepted by `get`, `archive`, `delete`, and grant commands.
    When signed in, you can also use your own workflow name (slug). Fetches
    one page by default; --all follows cursors and returns a combined items
    envelope. Pass --include-archived to also list your own archived
    workflows; the table then adds an "Archived at" column (restore with
    `workflows unarchive`).

goodeye workflows search <query> [--filter mine|shared-with-me|all] [--tag TAG] [--limit N] [--json|--table]
    LLM-ranked natural-language search over workflows you can access.
    Use this when you remember roughly what a workflow does but not its
    name; use `list` for plain enumeration or tag filtering. Defaults to
    --limit 5.

goodeye workflows get <id-or-name> [--version N] [--output PATH] [--json]
    Download a workflow. Prints markdown to stdout (wrapped with
    agent-facing markers); --json prints the full record. Authentication is
    required: workflows are private.

goodeye workflows publish <file.md|-> [--name NAME] [--description TEXT] [--outcome TEXT] [--tag TAG] [--expected-version-token TOKEN]
    Publish a workflow from markdown. Use `-` to read markdown from stdin,
    which is preferred for generated agent output. File input is still useful
    for durable local files. Always private. If a workflow with the same name
    already exists under your account, a new version is appended (pass
    --expected-version-token to confirm the parent version). Metadata may come
    from flags, front matter, or both; flags override front matter. `name`,
    `description`, and `outcome` are required. Repeat --tag to set tags. To
    share publicly, run `goodeye templates publish <workflow-uuid-or-name>` as
    a separate step.

goodeye workflows archive <id-or-name> [--yes]
    Archive a workflow you own. Archiving hides it from list results and
    grants but keeps every version and file intact. Reversible with
    `workflows unarchive`. Prefer this over `delete` unless you truly want
    permanent removal.

goodeye workflows unarchive <id-or-name>
    Restore an archived workflow you own (the inverse of `archive`). It
    becomes visible again in list results and grants.

goodeye workflows delete <id-or-name> [--yes]
    Permanently and immediately delete a workflow you own: the workflow, all
    its versions, all attached files, and all access grants are removed at
    once. There is NO recovery path; use `archive` for a reversible
    alternative.

goodeye workflows delete-version <id-or-name> <version> [--yes]
    Permanently and immediately delete a single non-current workflow version
    and its attached files. The current (live) version cannot be removed this
    way; use `delete` for the whole workflow. There is NO recovery path.
    Surviving version numbers are not renumbered.

goodeye workflows teach <id-or-name> [--trigger-context JSON]
    Fetch the teach SKILL pack for an existing workflow. The pack is
    printed to stdout for the calling agent to follow; persist the
    refined workflow with `goodeye workflows publish - --name <name> --description <description> --outcome <outcome> --source teach --expected-version-token <captured token>`.

goodeye workflows lineage <id-or-name> [--json]
    Show a workflow's fork lineage (parent template, upstream latest).

goodeye workflows grant <id-or-name> <grantee> <view|edit|admin> [--include-history]
    Share a workflow with a user email or @team handle. By default the
    grantee sees the version current at share time and later; pass
    --include-history to share the workflow's full version history.

goodeye workflows revoke-grant <id-or-name> <grantee>
    Revoke a direct grant.

goodeye workflows grants <id-or-name> [--json|--table]
    List grants on a workflow. Currently unpaginated.

goodeye workflows leave <id-or-name> [--yes]
    Remove your own direct grant on a shared workflow.

goodeye workflows transfer-ownership <id-or-name> <new-owner>
    Transfer a workflow you own to another user.

goodeye workflows sync [--target DIR] [--force] [--yes] [--json|--table]
    Pull every configured target, then print where each workflow stands.
    Equivalent to running `sync pull` followed by `sync status`. --force and
    --yes apply to that pull; they are ignored when a subcommand is given.

goodeye workflows sync target add <DIR> [--scope owned|all|selected] [--only GLOB] [--json|--table]
goodeye workflows sync target add --preset claude|agents|cursor [--scope owned|all|selected] [--only GLOB]
    Add a local directory to mirror workflows into. Give a path or a --preset
    (claude, agents, or cursor), not both. --scope picks which workflows land
    here: owned (the default), all (everything you can access), or selected
    (only the slugs or globs given with repeated --only). Local-only step; it
    does not contact the registry.

goodeye workflows sync target list [--json|--table]
    List the configured local sync targets with their scope.

goodeye workflows sync target remove <DIR>
    Remove a configured local sync target by its directory.

goodeye workflows sync pull [SLUG...] [--target DIR] [--force] [--yes] [--json|--table]
    Pull registry workflows down to the configured directories, each written
    to <target>/<slug>/SKILL.md. Omit slugs to pull everything in scope. A
    locally edited file is preserved unless --force overwrites it. A workflow
    deleted on the registry has its local copy removed after a confirmation
    prompt (--yes skips it); this only ever touches local files.

goodeye workflows sync status [--target DIR] [--json|--table]
    Report drift between the registry and the local directories without
    fetching or writing anything: each workflow is classified clean, edited
    locally, behind the registry, conflicted, deleted upstream, or an
    untracked local directory, with the next step to reconcile it.

goodeye workflows sync push [SLUG...] [--target DIR] [--json|--table]
    Upload locally edited workflows back to the registry. Only files that
    differ from the last sync are sent, and each upload is checked against the
    registry's current version: if it moved since the last sync, that workflow
    is reported as a conflict and left untouched (reconcile with `sync pull`
    first). Omit slugs to push every locally edited workflow in scope.

goodeye templates list [--filter all|mine] [--search QUERY] [--limit N] [--cursor TOKEN] [--all] [--include-archived] [--json|--table]
    Browse the public template catalog. Anonymous reads allowed. Fetches one
    page by default; --all follows cursors and returns a combined items
    envelope. Pass --include-archived to also list your own archived
    templates (restore with `templates unarchive`); a template still appears
    only while at least one of its versions remains published.

goodeye templates search <query> [--filter all|mine] [--limit N] [--json|--table]
    LLM-ranked natural-language search over public templates. Defaults to
    --limit 5.

goodeye templates get <identifier> [--version N] [--output PATH] [--json]
    Fetch a public template by UUID or @handle/slug[@vN]. Anonymous reads
    allowed; non-owner reads include an unverified-template safety banner.

goodeye templates publish <workflow-uuid-or-name> [--release-notes TEXT]
    Publish a private workflow as a new public template version.
    Requires a claimed handle.

goodeye templates unpublish <template-ref> <version>
    Soft-unpublish a single template version. Existing forks keep working.
    <template-ref> is a template UUID or @handle/slug.

goodeye templates fork <identifier> [--version N] [--name NAME]
    Fork a public template into a private workflow. Authentication required.

goodeye templates archive <template-ref> [--reason TEXT] [--yes]
    Archive a template you own. Archiving hides it from the public catalog
    but keeps every version and fork lineage intact; existing forks keep
    working. Reversible with `templates unarchive`. <template-ref> is a
    template UUID or @handle/slug.

goodeye templates unarchive <template-ref>
    Restore an archived template you own (the inverse of `archive`). It
    becomes visible again in the public catalog.
    <template-ref> is a template UUID or @handle/slug.

goodeye templates delete <template-ref> [--yes]
    Permanently and immediately delete a template you own: the template, all
    its versions, all attached files, and all version verification records
    are removed at once. There is NO recovery path; use `archive` for a
    reversible alternative. A template that is not archived and still has a
    published version is refused (unpublish those versions or archive it
    first). Forks keep their own content; their parent pointer is severed and
    `workflows lineage` reports the source as permanently deleted.
    <template-ref> is a template UUID or @handle/slug.

goodeye templates delete-version <template-ref> <version> [--yes]
    Permanently and immediately delete a single template version, its files,
    and its verification records. The version must be unpublished first (use
    `templates unpublish`). There is NO recovery path. Surviving version
    numbers are not renumbered. <template-ref> is a template UUID or
    @handle/slug.

goodeye templates deprecate-version <template-ref> <version> --message TEXT
    Flag a single template version as deprecated, with a message shown
    to anyone who forks that version.
    <template-ref> is a template UUID or @handle/slug.

goodeye templates transfer-ownership <template-ref> <user-id-or-email-or-handle>
    Hand a template off to another Goodeye user. Owner only.
    <template-ref> is a template UUID or @handle/slug.

goodeye verifiers deploy <config.json|->
    Deploy a verifier from JSON config (or append a new version). Use `-` to
    read verifier config JSON from stdin, which is preferred for generated
    agent output. File input is still useful for durable local config files.
    Required fields: name, description, criterion, input_contract. input_fields
    required for text and text_image contracts; few_shot_examples and
    model_settings optional. expected_version_token is required when
    re-deploying an existing verifier (get it from `verifiers list`).

goodeye verifiers list [--limit N] [--cursor TOKEN] [--all] [--json|--table]
    List active verifiers you own with their current version and version token.
    Fetches one page by default; --all follows cursors and returns a combined
    items envelope.

goodeye verifiers show <verifier_id> [--version N] [--json]
    Show one verifier version: criterion, contract, calibration, config_hash.

goodeye verifiers run <verifier_id> [--inputs-json JSON] [--media-url URL] \
                                    [--version N] [--workflow-id UUID] \
                                    [--workflow-version N] [--workflow-ref TEXT] \
                                    [--run-id TEXT] [--anonymous] [--json]
    Run a verifier and print PASS/FAIL plus reasoning. <verifier_id> is a
    UUID, system:<name>, or your caller-owned name (resolved client-side
    via a single get_verifier call before the run). --anonymous skips
    credentials for the public-preview path; rate limited and requires a
    UUID or system:<name> (names cannot be resolved without auth).
    --inputs-json keys must match the version's input_fields exactly.
    --media-url is required for text_image and image contracts.

goodeye verifiers revoke <verifier_id> [--yes]
    Revoke a verifier you own. Irreversible; existing run rows are kept.

goodeye design
    Print the workflow-designer prompt to stdout. Pipe it into your AI
    assistant to start designing a workflow + verifier:
        goodeye design
    Only redirect to a file when you intentionally want a durable local prompt copy.

goodeye me claim-handle <handle>
    Claim a handle (your publish identity).

goodeye me rename-handle <new-handle>
    Change a previously claimed handle. Subject to a cooldown and yearly
    cap; old-handle template URLs redirect for a 90-day window.

goodeye teams list [--filter all|mine|member] [--json|--table]
    List teams visible to you. Currently unpaginated; JSON output is an
    items envelope.

goodeye teams members <team> [--json|--table]
    List members of a team. Currently unpaginated; JSON output is an
    items envelope.
```

## Configuration

### Credentials

- `GOODEYE_API_KEY` env var (highest precedence).
- `~/.config/goodeye/credentials.json` (or `$XDG_CONFIG_HOME/goodeye/`).

Credential files are created with mode `0600`.

### Server

- `GOODEYE_SERVER` env var.
- `server` field inside `credentials.json`.
- Default: `https://api.goodeye.dev`.

## REST API, not the CLI

This CLI is pinned to the `/v1/` REST API contract. If you are integrating
programmatically and want a stable contract, prefer the REST API directly;
the CLI is a convenience layer over it.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for local-dev setup and the PR
process. Issues and PRs welcome.

## License

MIT. See [LICENSE](./LICENSE).
