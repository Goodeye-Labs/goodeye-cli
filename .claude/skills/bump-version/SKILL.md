---
name: bump-version
description: Version bump and optional PyPI release for goodeye-cli. Use when bumping the version, cutting a release, or pushing a git tag to trigger PyPI publish.
---

# goodeye-cli Version Bump

Two phases: (1) bump commit, always; (2) tag + push, only on explicit instruction.

## Step 1: Determine new version

User override always wins. Accept any of:
- Bump type: `major`, `minor`, `patch`
- Explicit target: `bump to 1.0.0`
- Pre-release (PEP 440): `0.9.0a1`, `0.9.0rc1`

Without override, inspect commits since the last tag:

```sh
git describe --tags --abbrev=0       # last tag, e.g. v0.8.0
git log <last-tag>..HEAD --oneline   # commits to classify
```

SemVer rules:
- major: breaking change, removed/renamed command, incompatible flag change
- minor: new subcommand, new flag, new user-visible feature
- patch: bug fix, internal refactor, docs, CI, dependency update, chore

Bias toward patch over minor, minor over major when ambiguous.

## Step 2: Edit files

`pyproject.toml`: one line change:
```toml
version = "X.Y.Z"
```

`uv.lock`: run from repo root after editing pyproject.toml:
```sh
uv lock
```
Updates only the self-referential `[[package]] name = "goodeye"` entry. No dependency upgrades occur.

## Step 3: Commit

Stage exactly these two files:
```sh
git add pyproject.toml uv.lock
```

Commit message (no body):
```
chore: bump version to X.Y.Z
```

## Step 4: Tag and push (explicit instruction only)

Only proceed if the user explicitly says to tag, push, release, or publish (e.g. "tag it", "push the tag", "ship it to PyPI").

Verify the tag doesn't already exist, then:
```sh
git tag vX.Y.Z
git push origin vX.Y.Z
```

This triggers `publish.yml` -> builds wheel -> publishes to PyPI via OIDC trusted publishing. No manual credentials needed. Monitor at github.com/Goodeye-Labs/goodeye-cli/actions.
