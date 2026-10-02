---
title: "CI workflows"
---

## CI workflows

> Part of: Develop > Guidelines | Related: [Image publishing](../knowledge/image-publishing.md)

<!-- Extracted from specs/007-harbor-image-publishing on 2026-10-01 -->

Rules for writing or changing GitHub Actions workflows under `.github/workflows/`. These rules
come from review findings on the image publishing workflows. Each one prevents a failure that
a passing run would otherwise hide.

### Shell steps

- **Start every multi-line `run:` script with `set -euo pipefail`.** The default `bash -e` has
  no `pipefail`, so a failing command piped into `jq` exits 0 and the step records an empty
  value.
- **Validate values that later steps depend on.** Check digests, versions and tags against an
  explicit pattern, and fail with `::error::` naming the value. Don't let an empty value travel
  to the next job.
- **Expand globs with `shopt -s nullglob`, and fail on zero matches.** Don't let an unmatched
  pattern pass through literally.
- **Treat only the expected status as the "absent" case.** For an API lookup that can
  legitimately miss, treat only HTTP 404 that way. A rate limit, a 5xx or a token error must
  fail, not read as "nothing there".

### Inputs and secrets

- **Pass workflow inputs and other free text to shell through `env:`, never by interpolating
  `${{ }}` into the script.**
- **Reference a secret only in a step or job whose `if` is a positive publish condition**, for
  example `inputs.publish`. Pull-request callers pass no secrets, so fork PRs behave like
  internal ones.
- **Fail early when a required registry variable is empty.** For example, an empty
  `HARBOR_HOST` makes `docker/login-action` default to Docker Hub with the inherited
  credentials.
- **Grant the least permissions.** A caller must grant every permission any job of the called
  workflow declares, skipped jobs included. Don't grant permissions that nothing uses, such as
  `packages: write` for a non-GitHub registry.

### Pinning and concurrency

- **Pin every `uses:` to a full 40-character commit SHA**, with a version comment. Reuse pins
  already present in this repository or in `infrahub` before resolving new ones.
- **Never let anything cancel a run that publishes.** A cancelled run can leave a pushed tag
  unsigned. In a workflow with a boolean `publish` input, set
  `cancel-in-progress: ${{ !inputs.publish }}` and put publishing runs in their own group, for
  example by ending the group with `${{ inputs.publish && 'publish' || 'build' }}`. A run that
  arrives with `cancel-in-progress: true` cancels every run in progress in its group, so a
  shared group lets a build-only run cancel a publishing one.

### Testing workflows

- **Cover workflow behaviour in `tests/test_workflow_contracts.py`.**
- **Prefer running the real step script with stub commands** (`gh`, `docker`, `uv`, `git`)
  over asserting on its text. Use the file's `_run_step` and `_run_with_stub` helpers.
- **Check guards for their meaning, not by substring.** A check that an `if` mentions
  `inputs.publish` also passes for `!inputs.publish`.
- **Make an all-skipped test run a failure in CI.** Use `--compose-zero-skip` for the Compose
  suite, and `pytest.fail` instead of skip when `GITHUB_ACTIONS` is set.
