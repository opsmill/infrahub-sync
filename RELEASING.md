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

This is the standard release flow. Releases are triggered automatically when PRs are merged to `main` or `stable` branches.

### Step 1: Label your pull requests

Apply appropriate labels to PRs before merging. Labels determine the version bump:

| Label | Version Bump | Use When |
|-------|--------------|----------|
| `changes/major`, `type/breaking-change` | Major (1.0.0 → 2.0.0) | Breaking API changes |
| `changes/minor`, `type/feature`, `type/refactoring` | Minor (1.0.0 → 1.1.0) | New features, refactoring |
| `changes/patch`, `type/bug`, `type/housekeeping`, `type/documentation` | Patch (1.0.0 → 1.0.1) | Bug fixes, docs, maintenance |

Auto-labeling rules are configured in `.github/release-drafter.yml` but require a separate workflow trigger to activate. For now, apply labels manually:

| PR Title Pattern | Recommended Label |
|------------------|-------------------|
| Contains `fix` | `type/bug` |
| Contains `enhance`, `improve`, `feature` | `type/feature` |
| Contains `chore` | `ci/skip-changelog` |
| Contains `deprecat` | `type/deprecated` |

### Step 2: Merge to main

Merge your labeled PR to the `main` branch. The automation will:

1. Calculate the next version based on PR labels
2. Update `pyproject.toml` with the new version (and regenerate `uv.lock`)
3. Commit changes as `chore(release): v{VERSION} [skip ci]`
4. Create/update a draft GitHub Release with auto-generated release notes

### Step 3: Publish the GitHub release

1. Navigate to the repository's **Releases** page
2. Find the draft release created by Release Drafter
3. Review the auto-generated release notes
4. Edit if needed (add context, highlights, migration notes)
5. Click **Publish release**

Publishing the release triggers the PyPI upload automatically.

## Method 2: Manual GitHub release

Use this method when you want full control over the release timing and notes.

### Step 1: Update the version

Update the version in `pyproject.toml`:

```bash
# Edit pyproject.toml and update the version field
uv lock
```

Commit and push the changes:

```bash
git add pyproject.toml uv.lock
git commit -m "chore(release): vX.Y.Z"
git push origin main
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

Release notes are auto-generated based on merged PRs and their labels:

| Category | Labels |
|----------|--------|
| Breaking Changes | `changes/major` |
| Minor Changes | `changes/minor`, `type/feature`, `type/refactoring` |
| Patch & Bug Fixes | `type/bug`, `changes/patch` |
| Documentation Change | `type/documentation` |

PRs with these labels are excluded from release notes:

- `ci/skip-changelog`
- `type/duplicate`

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

### Release workflow skipped

The automated release is skipped when:

- The commit author is `opsmill-bot` with a `chore` prefix (prevents recursive releases)
- No version bump is detected (no labeled PRs since last release)
- Changes are only in the `docs/` directory

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
| `trigger-push-stable.yml` | Push to `main`/`stable` | Calculates version, bumps `pyproject.toml`, triggers release draft |
| `workflow-release-drafter.yml` | Reusable (`workflow_call`) | Creates/updates GitHub Release draft; invoked by `trigger-release.yml` |
| `trigger-release.yml` | GitHub Release published | Orchestrates release: invokes release drafter and publish workflows |
| `workflow-publish.yml` | Reusable (`workflow_dispatch`) | Builds and publishes package to PyPI; invoked by `trigger-release.yml` |
