# goodeye

[![PyPI version](https://img.shields.io/pypi/v/goodeye)](https://pypi.org/project/goodeye/) [![Python versions](https://img.shields.io/pypi/pyversions/goodeye)](https://pypi.org/project/goodeye/) [![License](https://img.shields.io/pypi/l/goodeye)](https://github.com/Goodeye-Labs/goodeye-cli/blob/main/LICENSE)

Goodeye makes an AI agent meet your standard before you ever see the output, even on work too subjective for a test.

`goodeye` is the command-line surface for Goodeye. The same capability reaches your agent over an MCP server and a REST API too, so it works wherever your agent runs. This CLI talks to the public `/v1` REST API.

## How it works

You capture the work that moves an outcome as a markdown runbook, called a workflow, and pair it with checks, called verifiers, that score an agent's output against the standard you set. The agent runs the workflow, the verifiers judge the result, and the agent revises until the output clears your bar. Workflows stay private to you until you choose to share one publicly as a template.

A verifier can be deterministic (format, schema, tests, numeric bounds) or an LLM judge for the calls no test can make, like tone or image quality. The full picture is at https://goodeye.dev/docs/overview.

## Your AI agent is the primary caller

The CLI is built to be driven by an AI coding agent on your behalf, though every command also works when you run it yourself. The intended loop: you ask your agent to run a Goodeye workflow, it fetches the body with `goodeye workflows get <name>` (or `goodeye templates get @handle/slug`), and it executes those instructions as your runbook instead of printing or summarizing them. The `get` commands wrap the body in agent-facing markers so the calling agent knows to run it. Pass `--output PATH` or `--json` when you want the raw content instead.

## Install

Requires Python 3.12 or later.

```sh
uv tool install goodeye
# or: pipx install goodeye
# or: pip install goodeye
```

The `goodeye` command is then on your `PATH`. Run `goodeye update` to upgrade to the latest release, or `goodeye update --check` to see whether one is available.

## Quickstart

### Run a public template, no account needed

Browsing, fetching, and running public templates need no sign-in.

```sh
goodeye templates list
goodeye templates get @randalolson/high-signal-chart-workflow
```

`templates get` prints the workflow body, and your agent follows it: it finds a dataset, renders a chart, and runs the template's pinned verifier, revising until the chart passes. That verifier run is the step your agent makes, drawing on a small per-network credit grant:

```sh
# what your agent runs against the finished chart:
goodeye verifiers run 89dcc843-d056-44d9-ae34-ebcff4903885 \
  --version 1 --media-url '<public-https-chart-url>' --anonymous
```

### Author your own workflow

```sh
goodeye login                          # or: goodeye register --email you@example.com
goodeye me claim-handle your-handle    # one time, required before you publish a template

goodeye design                         # prints the designer prompt; pipe it to your agent to draft a workflow and its verifiers

# save the draft from your agent's output (stdin keeps a stray file out of the working directory):
goodeye workflows publish - \
  --name my-workflow \
  --description "One sentence on what this does and when to use it." \
  --outcome "The result this workflow moves" <<'EOF'
# Body

The workflow body your agent will execute.
EOF

goodeye workflows get my-workflow      # fetch it back for an agent to run
goodeye templates publish my-workflow  # share it publicly as a template
```

## What the CLI can do

These command areas cover the whole surface. Run `goodeye --help` or `goodeye <area> --help` for the exact commands and flags, and see the full reference at https://goodeye.dev/docs/cli.

| Area | What it does | Docs |
|---|---|---|
| `workflows` | Find, save, version, run, share, and improve your private workflows, including teach, optimize, audit, and local file sync | https://goodeye.dev/docs/workflows |
| `templates` | Browse the public catalog, publish a workflow as a template, and fork one to customize | https://goodeye.dev/docs/templates |
| `verifiers` | Deploy and run the LLM-judge checks a workflow calls to grade agent output | https://goodeye.dev/docs/verifiers |
| `image-generators` | Deploy reusable image generators and generate images a workflow can call | https://goodeye.dev/docs/image-generators |
| `images` | Upload and manage hosted images with stable URLs | https://goodeye.dev/docs/images |
| `teams` | Create teams and manage membership so a workflow can be shared with a group | https://goodeye.dev/docs/teams |
| `referrals` | View your referral code and redeem someone else's | https://goodeye.dev/docs/referrals |
| `invitations` | Accept, decline, or cancel team and ownership-transfer invitations | https://goodeye.dev/docs/cli |
| `auth`, `me`, `usage` | Manage API keys, claim your handle, and check credit usage | https://goodeye.dev/docs/cli |
| `design` | Print the workflow-designer prompt for your agent | https://goodeye.dev/docs/cli |

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

The CLI is a convenience layer over the public `/v1` REST API. For a stable programmatic contract, call that REST API directly (https://goodeye.dev/docs/rest-api). To drive Goodeye from an MCP client, connect to `mcp.goodeye.dev/mcp` (https://goodeye.dev/docs/mcp). Your workflows and their verifiers reach your agent on every surface.

## Documentation

- Overview and mental model: https://goodeye.dev/docs/overview
- Getting started: https://goodeye.dev/docs/getting-started
- CLI reference, every command and flag: https://goodeye.dev/docs/cli
- Workflows: https://goodeye.dev/docs/workflows
- Verifiers: https://goodeye.dev/docs/verifiers
- Templates: https://goodeye.dev/docs/templates
- Public template catalog: https://goodeye.dev

## Contributing

See [CONTRIBUTING.md](https://github.com/Goodeye-Labs/goodeye-cli/blob/main/CONTRIBUTING.md) for local dev setup and the pull-request process. Issues and pull requests are welcome.

## License

MIT. See [LICENSE](https://github.com/Goodeye-Labs/goodeye-cli/blob/main/LICENSE).
