# Implementation notes: spec 013

## Baseline (T001, T002)

Starting commit: `bad4e0b3` on branch `013-compose-images`. No code or test changed for these
two tasks.

### T001: offline unit tier

- Environment: Python 3.11.8, full development profile
  (`uv sync --extra dev --extra prefect --extra service`).
- Command: `uv run invoke tests.tests-unit`
- Started: 2026-10-15T19:07:12Z
- Result: 5670 passed, 4 skipped, 203 deselected, 4 warnings in 138.49s. Exit code 0.

### T002: compose lifecycle tier

- Environment: Docker 29.4.0 on OrbStack (aarch64), Docker Compose 5.1.2.
- Image: `infrahub-sync:compose-test` (image ID `a5eb800a0a73`), built with
  `docker build -t infrahub-sync:compose-test .` at the starting commit.
- Command:
  `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -n 0`
- Started: 2026-10-15T20:43:18Z
- Result: 37 passed, 0 failed, 0 skipped, 216 deselected in 519.42s (8 min 39 s). Exit code 0.
- Failing tests: none.
- Scope: one local run on arm64 only. It does not measure the failure rate that T004 measures
  with ten repeated runs on amd64 and arm64.

## T004 measurement run

This section records the dispatch only. The results and their classification against C1 to C5
are written later in `plan.md`, section "Lifecycle failure causes".

- Branch: `ci/compose-repeat-measure`, created from `origin/feature/v3-develop` at
  `8dc921b9bbf3d948b59bbf40c322d40255d738ab`. It holds one commit that changes only
  `.github/workflows/workflow-nightly-e2e.yml`, with the same content as the T003 commit
  `c422cede` on `013-compose-images`.
- Branch head: `9438b62f4b61bf0dd447e2be09c25fe06d6c85f2`.
- Commit under test: `8dc921b9bbf3d948b59bbf40c322d40255d738ab`, the head of
  `feature/v3-develop`. The workflow refuses a `sha` input that is not merged into
  `feature/v3-develop`, so the measurement branch head cannot be the `sha` input. The workflow
  definition comes from the measurement branch (`--ref`); the tests and the image come from the
  commit under test, which holds no reliability change.
- Dispatch command:
  `gh workflow run workflow-nightly-e2e.yml --ref ci/compose-repeat-measure -f sha=8dc921b9bbf3d948b59bbf40c322d40255d738ab -f compose_repeat=10`
- Run: 38806056523, <https://github.com/opsmill/infrahub-sync/actions/runs/38806056523>
- Dispatched: 2026-10-15T20:57:02Z. Run created: 2026-10-15T20:57:04Z.
- Legs: "Compose deployment suite against a locally built image (amd64)" on `ubuntu-22.04` and
  "Compose deployment suite against a locally built image (arm64)" on `ubuntu-24.04-arm`.
- The dispatch also runs the job "Integration, preview, saved plan, and local NetBox example".
  The workflow has no input to skip it, and its result is not part of this measurement.
- Status at hand-back (2026-10-15T20:58:00Z): run in progress; both legs had passed the commit
  check and the image build and were running the repeated suite step.

## User Story 1 (T014 to T024)

- T018: no release tag of version 3 or later ships a root `docker-compose.yml` (checked
  with `git tag` and `git ls-remote --tags origin` on 2026-10-15). Until one exists,
  `tests/compose/test_prefect_server_upgrade.py` reads the previous file from the parent of
  the newest commit that removed `prefecthq/prefect` from `docker-compose.yml`.
  `INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF` overrides the reference. The test skips only when
  neither source exists; under `--compose-zero-skip` that skip fails the nightly job.
- T022: `tasks/release.py` counts no fixed number of Sync image lines.
  `uv run invoke release.validate-docker-compose --version 3.0.0a5` reports `sync_lines=5`.
- T024: with a local `docker build`, three image tests fail because the build passes no OCI
  version and revision labels: `test_the_image_carries_every_required_oci_label[...version]`,
  `[...revision]` and `test_the_image_carries_the_source_provenance_of_the_commit_it_was_built_from`.
  The publishing workflow passes these labels. No file of this phase changes them.
