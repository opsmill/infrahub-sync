# Releasing Infrahub Sync

Maintainer-only runbook for publishing new releases of `infrahub-sync` to [PyPI](https://pypi.org/project/infrahub-sync/).

## Overview

The project uses an automated release system powered by GitHub Actions. There are three ways to publish a release:

1. **Automated release** (recommended for regular releases)
2. **Manual GitHub release** (for controlled releases)
3. **Manual workflow dispatch** (for emergency or custom releases)

## Prerequisites

Before publishing, ensure:

- You have write access to the repository
- The `PYPI_TOKEN` secret is configured in repository settings
- The `GH_INFRAHUB_BOT_TOKEN` secret is configured (for automated releases)

## Method 1: Automated release (recommended)

This is the standard release flow. Nothing about a release is edited by hand: a push to `main` prepares a release pull request, and merging that pull request publishes the release.

### Step 1: Label your pull requests

Apply appropriate labels to PRs before merging. Labels determine the version bump:

| Label | Version Bump | Use When |
|-------|--------------|----------|
| `changes/major`, `type/breaking-change` | Major (1.0.0 → 2.0.0) | Breaking API changes |
| `changes/minor`, `type/feature`, `type/refactoring` | Minor (1.0.0 → 1.1.0) | New features, refactoring |
| `changes/patch`, `type/bug`, `type/housekeeping`, `type/documentation` | Patch (1.0.0 → 1.0.1) | Bug fixes, docs, maintenance |

The bump labels live in `.github/version-drafter.yml`. Apply them manually:

| PR Title Pattern | Recommended Label |
|------------------|-------------------|
| Contains `fix` | `type/bug` |
| Contains `enhance`, `improve`, `feature` | `type/feature` |
| Contains `chore` | `ci/skip-changelog` |
| Contains `deprecat` | `type/deprecated` |

Every pull request into `main` must also carry a news fragment under `changelog/` — `changelog-check.yml` enforces this. See the Changelog section of [AGENTS.md](AGENTS.md).

### Step 2: Merge to main

Merge your labeled PR to the `main` branch. `trigger-push-stable.yml` then:

1. Calculates the next version from the labels on PRs merged since the last release
2. Updates `pyproject.toml` with the new version and regenerates `uv.lock`
3. Assembles `CHANGELOG.md` from the news fragments with towncrier, consuming them
4. Opens a `chore(release): {VERSION}` pull request from `release/{VERSION}` into `main`

If there are no news fragments, no release pull request is prepared — the run reports this and stops. Add a fragment and push again.

### Step 3: Review and merge the release pull request

The release pull request is the reviewable artefact. Read the assembled `CHANGELOG.md` section in its diff — that is what users will read — and merge when it is right.

Merging it triggers `release-publish.yml`, which:

1. Reads the version back out of `pyproject.toml`
2. Creates the bare tag (for example `2.0.2`, not `v2.0.2`)
3. Publishes a GitHub Release whose body is the changelog section

Publishing that Release triggers `trigger-release.yml` and the PyPI upload.

A release is only ever cut by merging a `release/*` pull request: `release-publish.yml` refuses to tag a commit that is not one, so a hand-edited version on `main` cannot publish anything on its own.

## Method 2: Manual GitHub release

Use this method when you want full control over the release timing and notes.

This is an escape hatch and bypasses the changelog: fragments under `changelog/` are left unconsumed and `CHANGELOG.md` is not updated, so the release notes are whatever you type into the GitHub UI. Prefer Method 1. Note also that `main` is protected — the version bump still has to go through a pull request.

### Step 1: Update the version

Update the version in `pyproject.toml`:

```bash
# Edit pyproject.toml and update the version field
uv lock
```

Commit the changes on a branch and merge them through a pull request:

```bash
git switch -c chore/release-X.Y.Z
git add pyproject.toml uv.lock
git commit -m "chore(release): X.Y.Z"
git push origin chore/release-X.Y.Z
```

### Step 2: Create a GitHub release

1. Go to **Releases** → **Draft a new release**
2. Click **Choose a tag** and create a new tag matching your version (for example, `1.6.0`)
3. Set the target to `main` branch
4. Add a release title (for example, `1.6.0`)
5. Write release notes describing the changes
6. Click **Publish release**

This triggers the `trigger-release.yml` workflow, which publishes to PyPI.

## Method 3: Manual workflow dispatch

Use this for emergency releases or when you need to bypass the standard flow.

### Using the GitHub UI

1. Go to **Actions** → **Publish Infrahub Sync Package**
2. Click **Run workflow**
3. Configure the inputs:
   - `version`: The version string (for example, `1.6.0`) - optional, for labeling
   - `publish`: Set to `true` to publish to PyPI (default: `false`)
   - `runs-on`: OS for the runner (default: `ubuntu-22.04`)
4. Click **Run workflow**

### Using the GitHub CLI

```bash
gh workflow run workflow-publish.yml \
  --field version="1.6.0" \
  --field publish=true
```

**Important:** When using workflow dispatch, ensure `pyproject.toml` already has the correct version, as this method builds from the current code state.

## Release notes

Release notes are written by contributors, not generated from pull-request titles. Each pull request adds a news fragment under `changelog/`; towncrier assembles them into `CHANGELOG.md` when the release pull request is prepared, and `release-publish.yml` uses that section verbatim as the GitHub Release body.

The seven fragment types come from `[tool.towncrier]` in `pyproject.toml`: `security`, `removed`, `deprecated`, `added`, `changed`, `fixed`, `housekeeping`. See the Changelog section of [AGENTS.md](AGENTS.md) for how to add one.

Labels no longer shape the release notes — they only decide the version bump (see Step 1 above).

## Verifying a release

After publishing:

1. **Check PyPI**: Visit [pypi.org/project/infrahub-sync](https://pypi.org/project/infrahub-sync/) to confirm the new version is available
2. **Check GitHub Actions**: Ensure the publish workflow completed successfully
3. **Test Installation**:

```bash
pip install infrahub-sync==<new-version>
infrahub-sync --version
```

## Troubleshooting

### No release pull request was opened

Preparing a release is skipped when:

- The push is the merge of a `release/*` pull request (prevents recursive releases)
- The commit author is `opsmill-bot` with a `chore` prefix (same, for the older scheme)
- No version bump is detected (no labeled PRs since last release)
- Changes are only in the `docs/` directory
- There are no news fragments under `changelog/` — there is nothing to release

The last case is the common one after a run of `ci/skip-changelog` pull requests. Add a fragment (`housekeeping` is fine) and push again.

### The release pull request failed to prepare

`trigger-push-stable.yml` fails rather than skips when a news fragment exists but is empty or whitespace-only — it would otherwise render as a changelog heading with nothing under it. The error names the file: fill it in or delete it.

### The PyPI upload failed

Common causes:

- `PYPI_TOKEN` secret is missing or invalid
- Version already exists on PyPI (versions cannot be overwritten)
- Network issues during upload

To retry, use the manual workflow dispatch method.

### Version not bumped correctly

Ensure PRs have appropriate labels before merging. If labels are missing, the version drafter may not calculate a new version.

## Workflow files reference

| Workflow | Type | Purpose |
|----------|------|---------|
| `changelog-check.yml` | PR into `main` | Requires a news fragment on every pull request |
| `trigger-push-stable.yml` | Push to `main` | Calculates version, bumps `pyproject.toml`, assembles the changelog, opens the release pull request |
| `release-publish.yml` | Push to `main` | Tags and publishes the GitHub Release when a `release/*` pull request lands |
| `trigger-release.yml` | GitHub Release published | Invokes the publish workflow |
| `workflow-publish.yml` | Reusable (`workflow_dispatch`) | Builds and publishes package to PyPI; invoked by `trigger-release.yml` |
