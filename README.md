# goodeye

[![PyPI version](https://img.shields.io/pypi/v/goodeye)](https://pypi.org/project/goodeye/) [![Python versions](https://img.shields.io/pypi/pyversions/goodeye)](https://pypi.org/project/goodeye/) [![License](https://img.shields.io/pypi/l/goodeye)](https://github.com/Goodeye-Labs/goodeye-cli/blob/main/LICENSE)

Goodeye is a private home for the skills your AI follows and the verifiers its work must pass.

`goodeye` is the command-line surface for Goodeye. The same capability reaches your agent over an MCP server and a REST API too, so it works wherever your agent runs. This CLI talks to the public `/v1` REST API.

## How it works

A skill is a markdown runbook your agent follows. A verifier is a check its output has to clear. Goodeye keeps both in one account you own, and three things follow from that.

**Private by default.** Nothing is public until you publish a template, which is a separate step you take on purpose. To share without going public, grant a named user or team access, and the verifiers the skill references go with it at the same version. Revoking works the same way.

**Always in sync.** `goodeye skills sync` mirrors your hosted skills into the directories your tools already read, so Claude Code, Codex, Cursor, and anything else reading skill files from disk run the same current version. Edit a skill once and every machine picks it up on the next pull.

**Verifiers, hosted.** A verifier can be deterministic (format, schema, tests, numeric bounds) or an LLM judge for the calls no test can make, like tone or image quality. Deploy a semantic verifier once, and every skill that references it runs that exact version, on your laptop, in CI, or on the machine of someone you granted it to.

The full picture is at https://goodeye.dev/docs/overview.

## Your AI agent is the primary caller

The CLI is built to be driven by an AI coding agent on your behalf, though every command also works when you run it yourself. The intended loop: you ask your agent to run a Goodeye skill, it fetches the body with `goodeye skills get <name>` (or `goodeye templates get @handle/slug`), and it executes those instructions as your runbook instead of printing or summarizing them. The `get` commands wrap the body in agent-facing markers so the calling agent knows to run it. Pass `--output PATH` or `--json` when you want the raw content instead.

## Install

Requires Python 3.12 or later.

```sh
uv tool install goodeye
# or: pipx install goodeye
# or: pip install goodeye
```

The `goodeye` command is then on your `PATH`. Run `goodeye update` to upgrade to the latest release, or `goodeye update --check` to see whether one is available.

## Quickstart

### Bring a skill you already have

A skill file on disk is a directory holding a `SKILL.md` plus optional siblings, which is exactly what `publish` expects. Importing one is a single command.

```sh
goodeye login                                     # or: goodeye register --email you@example.com
goodeye skills publish ~/.claude/skills/my-skill  # also ~/.agents/skills, ~/.cursor/skills, or any path
goodeye skills list
```

It lands as a private hosted skill. Nothing is public until you publish a template, which is a separate step.

### Sync it to every machine and agent

Point Goodeye at the directories your tools read, then pull:

```sh
goodeye skills sync target add --preset claude    # ~/.claude/skills
goodeye skills sync target add --preset agents    # ~/.agents/skills
goodeye skills sync target add --preset cursor    # ~/.cursor/skills
goodeye skills sync
```

Run the same commands on your other machines and they all read the current version. `goodeye skills sync auto on` keeps that up to date on its own; it only pulls new and updated skills, never overwrites local edits, and reports a conflict rather than clobbering it.

The skill you just published came from a directory that is now a target, so a copy of it is already there. Goodeye did not write that copy, so the first pull reports it as modified and leaves it alone. The hosted version is what you published, so adopt it once with `goodeye skills sync pull --force <slug>`; from then on it is tracked like everything else.

### Share it

```sh
goodeye skills grant my-skill @teammate view
goodeye skills grants my-skill
```

Their agent now runs your skill, and any semantic verifiers it references travel with the grant at the same version. Improve the skill and they get the improvement on their next pull.

### Author a skill from scratch

```sh
goodeye design                         # prints the designer prompt; pipe it to your agent to draft a skill and its verifiers

# save the draft from your agent's output (stdin keeps a stray file out of the working directory):
goodeye skills publish - \
  --name my-skill \
  --description "One sentence on what this does and when to use it." <<'EOF'
# Body

The skill body your agent will execute.
EOF

goodeye skills get my-skill            # fetch it back for an agent to run
```

### Run a public template, no account needed

Browsing, fetching, and running public templates need no sign-in.

```sh
goodeye templates list
goodeye templates get @randalolson/high-signal-chart-workflow
```

`templates get` prints the template body (the skill), and your agent follows it: it finds a dataset, renders a chart, and runs the template's pinned verifier, revising until the chart passes. That verifier run is the step your agent makes, drawing on a small per-network credit grant:

```sh
# what your agent runs against the finished chart:
goodeye verifiers run 89dcc843-d056-44d9-ae34-ebcff4903885 \
  --version 1 --media-url '<public-https-chart-url>' --anonymous
```

Fork it into a skill of your own with `goodeye templates fork @randalolson/high-signal-chart-workflow`. To publish one of your own, claim a handle first with `goodeye me claim-handle your-handle`, then run `goodeye templates publish my-skill`.

## What the CLI can do

These command areas cover the whole surface. Run `goodeye --help` or `goodeye <area> --help` for the exact commands and flags, and see the full reference at https://goodeye.dev/docs/cli.

| Area | What it does | Docs |
|---|---|---|
| `skills` | Find, save, version, run, share, and improve your private skills, including teach, optimize, audit, and local file sync | https://goodeye.dev/docs/skills |
| `templates` | Browse the public catalog, publish a skill as a template, and fork one to customize | https://goodeye.dev/docs/templates |
| `verifiers` | Deploy and run the LLM-judge checks a skill calls to grade agent output | https://goodeye.dev/docs/verifiers |
| `image-generators` | Deploy reusable image generators and generate images a skill can call | https://goodeye.dev/docs/image-generators |
| `images` | Upload and manage hosted images with stable URLs | https://goodeye.dev/docs/images |
| `teams` | Create teams and manage membership so a skill can be shared with a group | https://goodeye.dev/docs/teams |
| `referrals` | View your referral code and redeem someone else's | https://goodeye.dev/docs/referrals |
| `invitations` | Accept, decline, or cancel team and ownership-transfer invitations | https://goodeye.dev/docs/cli |
| `billing` | Upgrade to or cancel Pro (`plan upgrade` / `plan cancel`), open the billing portal, buy a one-time credit top-up, or manage automatic top-ups (`auto-topup show` / `set` / `off`) | https://goodeye.dev/docs/cli |
| `auth`, `me`, `usage` | Manage API keys, claim your handle, and check credit usage | https://goodeye.dev/docs/cli |
| `design` | Print the skill-designer prompt for your agent | https://goodeye.dev/docs/cli |

Session commands sit at the top level: `login`, `register`, `logout`, `whoami`, and `update`.

## Authentication and configuration

Sign in interactively with `goodeye login` or `goodeye register`. Both open a browser device-code flow and save your credentials on success. For agents and automation without a browser, use the email-code flow:

```sh
goodeye register --email you@example.com
goodeye register-verify --email you@example.com --code 123456
# existing account: goodeye login, then goodeye login-verify
```

`goodeye whoami` shows who you are, and `goodeye logout` removes the local credentials. Agents and scripts can authenticate with an API key instead: create one with `goodeye auth create-key --name my-agent` and pass it as the `GOODEYE_API_KEY` environment variable.

The CLI reads credentials from `GOODEYE_API_KEY` first, then from `~/.config/goodeye/credentials.json` (written with `0600` permissions). It targets `https://api.goodeye.dev` by default; override that with `GOODEYE_SERVER`. Full authentication details are at https://goodeye.dev/docs/cli.

## CLI, MCP, or REST

The CLI is a convenience layer over the public `/v1` REST API. For a stable programmatic contract, call that REST API directly (https://goodeye.dev/docs/rest-api). To drive Goodeye from an MCP client, connect to `mcp.goodeye.dev/mcp` (https://goodeye.dev/docs/mcp). Your skills and their verifiers reach your agent on every surface.

## Documentation

- Overview and mental model: https://goodeye.dev/docs/overview
- Getting started: https://goodeye.dev/docs/getting-started
- CLI reference, every command and flag: https://goodeye.dev/docs/cli
- Skills: https://goodeye.dev/docs/skills
- Verifiers: https://goodeye.dev/docs/verifiers
- Templates: https://goodeye.dev/docs/templates
- Public template catalog: https://goodeye.dev

## Contributing

See [CONTRIBUTING.md](https://github.com/Goodeye-Labs/goodeye-cli/blob/main/CONTRIBUTING.md) for local dev setup and the pull-request process. Issues and pull requests are welcome.

## License

MIT. See [LICENSE](https://github.com/Goodeye-Labs/goodeye-cli/blob/main/LICENSE).
