<!-- markdownlint-disable -->
![Infrahub Logo](https://assets-global.website-files.com/657aff4a26dd8afbab24944b/657b0e0678f7fd35ce130776_Logo%20INFRAHUB.svg)
<!-- markdownlint-restore -->

# Infrahub Sync

[Infrahub](https://github.com/opsmill/infrahub) by [OpsMill](https://opsmill.com) acts as a central hub to manage the data, templates and playbooks that powers your infrastructure. At its heart, Infrahub is built on 3 fundamental pillars:

- **A Flexible Schema**: A model of the infrastructure and the relation between the objects in the model, that's easily extensible.
- **Version Control**: Natively integrated into the graph database which opens up some new capabilities like branching, diffing, and merging data directly in the database.
- **Unified Storage**: By combining a graph database and git, Infrahub stores data and code needed to manage the infrastructure.

## Introduction

Infrahub Sync is a versatile Python package that synchronizes data between a source and a destination system. It builds on the robust capabilities of `diffsync` to offer flexible and efficient data synchronization across different platforms, including Netbox, Nautobot, and Infrahub. This package features a Typer-based CLI for ease of use, supporting operations such as listing available sync projects, generating diffs, and executing sync processes.

For comprehensive documentation on using Infrahub Sync, visit the [official Infrahub Sync documentation](https://docs.infrahub.app/sync/)

## Publishing a Release

This section documents how to publish new releases of `infrahub-sync` to PyPI.

### Overview

The project uses an automated release system powered by GitHub Actions. There are three ways to publish a release:

1. **Automated Release** (recommended for regular releases)
2. **Manual GitHub Release** (for controlled releases)
3. **Manual Workflow Dispatch** (for emergency or custom releases)

### Prerequisites

Before publishing, ensure:

- You have write access to the repository
- The `PYPI_TOKEN` secret is configured in repository settings
- The `GH_INFRAHUB_BOT_TOKEN` secret is configured (for automated releases)

### Method 1: Automated Release (Recommended)

This is the standard release flow. Releases are triggered automatically when PRs are merged to `main` or `stable` branches.

#### Step 1: Label Your Pull Requests

Apply appropriate labels to PRs before merging. Labels determine the version bump:

| Label                                                                  | Version Bump           | Use When                     |
| ---------------------------------------------------------------------- | ---------------------- | ---------------------------- |
| `changes/major`, `type/breaking-change`                                | Major (1.0.0 → 2.0.0)  | Breaking API changes         |
| `changes/minor`, `type/feature`, `type/refactoring`                    | Minor (1.0.0 → 1.1.0)  | New features, refactoring    |
| `changes/patch`, `type/bug`, `type/housekeeping`, `type/documentation` | Patch (1.0.0 → 1.0.1)  | Bug fixes, docs, maintenance |

Auto-labeling rules are configured in `.github/release-drafter.yml` but require a separate
workflow trigger to activate. For now, apply labels manually:

| PR Title Pattern                         | Recommended Label   |
| ---------------------------------------- | ------------------- |
| Contains `fix`                           | `type/bug`          |
| Contains `enhance`, `improve`, `feature` | `type/feature`      |
| Contains `chore`                         | `ci/skip-changelog` |
| Contains `deprecat`                      | `type/deprecated`   |

#### Step 2: Merge to Main

Merge your labeled PR to the `main` branch. The automation will:

1. Calculate the next version based on PR labels
2. Update `pyproject.toml` with the new version (and regenerate `poetry.lock`)
3. Commit changes as `chore(release): v{VERSION} [skip ci]`
4. Create/update a draft GitHub Release with auto-generated release notes

#### Step 3: Publish the GitHub Release

1. Navigate to the repository's **Releases** page
2. Find the draft release created by Release Drafter
3. Review the auto-generated release notes
4. Edit if needed (add context, highlights, migration notes)
5. Click **Publish release**

Publishing the release triggers the PyPI upload automatically.

### Method 2: Manual GitHub Release

Use this method when you want full control over the release timing and notes.

#### Step 1: Update the Version

Update the version in `pyproject.toml`:

```bash
poetry version <major|minor|patch|X.Y.Z>
poetry lock
```

Commit and push the changes:

```bash
git add pyproject.toml poetry.lock
git commit -m "chore(release): v$(poetry version -s)"
git push origin main
```

#### Step 2: Create a GitHub Release

1. Go to **Releases** → **Draft a new release**
2. Click **Choose a tag** and create a new tag matching your version (e.g., `1.6.0`)
3. Set the target to `main` branch
4. Add a release title (e.g., `1.6.0`)
5. Write release notes describing the changes
6. Click **Publish release**

This triggers the `trigger-release.yml` workflow, which publishes to PyPI.

### Method 3: Manual Workflow Dispatch

Use this for emergency releases or when you need to bypass the standard flow.

#### Via GitHub UI

1. Go to **Actions** → **Publish Infrahub Sync Package**
2. Click **Run workflow**
3. Configure the inputs:
   - `version`: The version string (e.g., `1.6.0`) - optional, for labeling
   - `publish`: Set to `true` to publish to PyPI (default: `false`)
   - `runs-on`: OS for the runner (default: `ubuntu-22.04`)
4. Click **Run workflow**

#### Via GitHub CLI

```bash
gh workflow run workflow-publish.yml \
  --field version="1.6.0" \
  --field publish=true
```

**Important:** When using workflow dispatch, ensure `pyproject.toml` already has the correct version, as this method builds from the current code state.

### Release Notes

Release notes are auto-generated based on merged PRs and their labels:

| Category             | Labels                                              |
| -------------------- | --------------------------------------------------- |
| Breaking Changes     | `changes/major`                                     |
| Minor Changes        | `changes/minor`, `type/feature`, `type/refactoring` |
| Patch & Bug Fixes    | `type/bug`, `changes/patch`                         |
| Documentation Change | `type/documentation`                                |

PRs with these labels are excluded from release notes:

- `ci/skip-changelog`
- `type/duplicate`

### Verifying a Release

After publishing:

1. **Check PyPI**: Visit [pypi.org/project/infrahub-sync](https://pypi.org/project/infrahub-sync/) to confirm the new version is available
2. **Check GitHub Actions**: Ensure the publish workflow completed successfully
3. **Test Installation**:

   ```bash
   pip install infrahub-sync==<new-version>
   infrahub-sync --version
   ```

### Troubleshooting

#### Release workflow skipped

The automated release is skipped when:

- The commit author is `opsmill-bot` with a `chore` prefix (prevents recursive releases)
- No version bump is detected (no labeled PRs since last release)
- Changes are only in the `docs/` directory

#### PyPI upload failed

Common causes:

- `PYPI_TOKEN` secret is missing or invalid
- Version already exists on PyPI (versions cannot be overwritten)
- Network issues during upload

To retry, use the manual workflow dispatch method.

#### Version not bumped correctly

Ensure PRs have appropriate labels before merging. If labels are missing, the version drafter may not calculate a new version.

### Workflow Files Reference

| Workflow                       | Type                           | Purpose                                                                  |
| ------------------------------ | ------------------------------ | ------------------------------------------------------------------------ |
| `trigger-push-stable.yml`      | Push to `main`/`stable`        | Calculates version, bumps `pyproject.toml`, triggers release draft       |
| `workflow-release-drafter.yml` | Reusable (`workflow_call`)     | Creates/updates GitHub Release draft; invoked by `trigger-release.yml`   |
| `trigger-release.yml`          | GitHub Release published       | Orchestrates release: invokes release drafter and publish workflows      |
| `workflow-publish.yml`         | Reusable (`workflow_dispatch`) | Builds and publishes package to PyPI; invoked by `trigger-release.yml`   |
