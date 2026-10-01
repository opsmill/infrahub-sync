# Tasks: Publish the Sync image to the OpsMill registry

**Input**: Design documents from `specs/007-harbor-image-publishing/` (the `specs` symlink points to `dev/specs/`).

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md).

**Tests**: These are included. The constitution (Principle V) requires tests with each
change, and the plan names its test files. Write each test task before the
implementation task it covers, and confirm it fails first.

**Sibling references** (copy their structure, not their repository-specific names):
- `../infrahub-mcp/.github/workflows/ci-docker-image.yml`
- `../infrahub-mcp/.github/workflows/workflow-publish.yml` (the `docker_meta` and
  `publish_docker_image` jobs)
- `../infrahub/tasks/release.py` (`update_docker_compose` and
  `validate_docker_compose`)
- `../infrahub/docker-compose.yml` (the image line format)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: US1–US5, from spec.md.

## Story order

The spec ranks US1 (P1), then US2 and US3 (P2), then US4 and US5 (P3). Execution
order differs in one place: **US5 runs before US4**. US4 replaces `tasks/release.py`,
and the old module has importers (`tasks/image.py`, `tasks/compose.py`,
`tests/release/`, `tests/compose/lifecycle.py`, `tests/test_workflow_contracts.py`)
that US5 deletes first.

---

## Phase 1: Setup

**Purpose**: Record a baseline and prepare the shared path filter.

- [ ] T001 Record the baseline size of the image machinery with `wc -l .github/workflows/workflow-image.yml .github/workflows/workflow-candidate.yml tasks/image.py tasks/release.py tasks/compose.py` (expect 3195 lines in total). Append the figures to a "Baseline" section at the end of `specs/007-harbor-image-publishing/research.md`. SC-004 is measured against this number.
- [ ] T002 Add an `image_inputs` filter to `.github/file-filters.yml`. It matches `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `infrahub_sync/**`, `opsmill_prefect_extras/**`, `docker-compose.yml`, `tests/image/**`, `.github/workflows/ci-docker-image.yml`, `.github/workflows/trigger-pr-develop.yml` and `.github/file-filters.yml`. Leave the existing `image_files`, `compose_files`, `image_all` and `qualify_all` filters in place for now (T031 removes them).

---

## Phase 2: Foundational (the reusable image workflow and the smoke test)

**Purpose**: Build `ci-docker-image.yml` and the smoke test it runs. US1, US2 and US3
all call this workflow.

**⚠️ CRITICAL**: No user-story phase can start until this phase is done.

- [ ] T003 [P] Reduce `tests/image/conftest.py` to the smoke fixtures:
  - Keep `image_ref`, which reads `INFRAHUB_SYNC_IMAGE_REF`, along with `docker`, `run_in_image`, `api_environment`, `started_container`, `wait_for_api` and the constants they use.
  - Delete the `image_layout` fixture, `IMAGE_LAYOUT_ENV`, and every helper used only by the OCI-layout or archive tests.
  - `api_environment` must start its throwaway PostgreSQL on whatever architecture the host runs. Do not hard-code amd64.
- [ ] T004 [P] Retarget `tests/image/test_image_artifact.py` so it needs only `INFRAHUB_SYNC_IMAGE_REF` and a running Docker daemon, with no OCI layout and no digest record.
  - **Keep these checks**:
    - the CLI answers `infrahub-sync --help`;
    - the default command serves the API, polled at `GET /version` with a 60-second bound;
    - the worker, bootstrap and python command forms;
    - the non-root UID/GID and the read-only root;
    - the source-provenance check.
  - **Add** a test that `org.opencontainers.image.source`, `org.opencontainers.image.version` and `org.opencontainers.image.revision` labels are present and non-empty (FR-007).
  - **Add** a test that the API container's logs contain none of the bearer-token, database or S3 secret values that `api_environment` injected (Principle VI).
- [ ] T005 [P] Keep `tests/image/test_version_policy.py`, because `test_image_artifact.py` imports `RUNTIME_PYTHON_FLOOR` from it. Also keep `tests/image/test_build_context.py`, which checks `.dockerignore`. Make sure neither imports `tasks.image` or `tasks.release`, and fix any that do.
- [ ] T006 Create `.github/workflows/ci-docker-image.yml` per [contracts/ci-docker-image.md](contracts/ci-docker-image.md), copying the structure of `../infrahub-mcp/.github/workflows/ci-docker-image.yml`:
  - **Triggers and inputs**: `workflow_call` and `workflow_dispatch`, each with the inputs `publish`, `version`, `ref`, `tags`, `labels` and `platforms`.
  - **Permissions**: `contents: read`, `id-token: write`, `packages: write`.
  - **Dockerfile**: the root `Dockerfile`, not infrahub-mcp's `development/Dockerfile`.
  - **`build` job**: a matrix with linux/amd64 on `ubuntu-24.04` and linux/arm64 on `ubuntu-24.04-arm`, skipping any platform not listed in `inputs.platforms`. Its steps are:
    - check out `inputs.ref`;
    - set up Buildx;
    - `build-push-action` with `load: true`, tag `infrahub-sync:smoke`, and `labels: inputs.labels`;
    - `astral-sh/setup-uv`, then `uv sync --frozen --extra dev`;
    - `uv run pytest -m docker tests/image/test_image_artifact.py`, with `INFRAHUB_SYNC_IMAGE_REF=infrahub-sync:smoke`;
    - **only if `inputs.publish`**: log in to `${{ vars.HARBOR_HOST }}`, then run `build-push-action` again (it hits the warm cache) with `provenance: false` and `outputs: type=image,name=${{ vars.HARBOR_HOST }}/${{ github.repository }},push-by-digest=true,name-canonical=true,push=true`;
    - export and upload the `digests-amd64` / `digests-arm64` artifacts.
  - **`merge`, `sign` and `sbom` jobs**: copy them from infrahub-mcp, all with `if: inputs.publish`. Wrap each `cosign sign` and `cosign attest` call in a bounded retry (three attempts with backoff), as `../infrahub/.github/workflows/ci-docker-image.yml` does.
  - **Pinning**: pin every action by full commit SHA, with a version comment.
  - **Secrets**: never `echo` them. Only steps guarded by `publish` may reference `secrets.*`.
- [ ] T007 Add a guard as the first step of the `build` job in `.github/workflows/ci-docker-image.yml`. When `inputs.publish` is true and `inputs.tags` has no non-blank line, it fails with "publishing needs at least one tag" (spec edge case).
- [ ] T008 Add contract tests for `ci-docker-image.yml` in `tests/test_workflow_contracts.py`, under a new `ci-docker-image` section. Parse the YAML with the existing helpers in that file and assert:
  - the input names and defaults match the contract;
  - the `build` runners are `ubuntu-24.04` and `ubuntu-24.04-arm`;
  - the smoke step comes before any login or push step;
  - every step that references `secrets.` has an `if` mentioning `inputs.publish`;
  - `merge`, `sign` and `sbom` each have `if: inputs.publish`;
  - `merge` `needs: build`;
  - every `uses:` is pinned to a 40-hex SHA.

**Checkpoint**: Dispatching `ci-docker-image.yml` with `publish: false` builds and
smoke-tests both platforms (quickstart §2 and the first half of §4).

---

## Phase 3: User Story 1 — pull a released image from the OpsMill registry (Priority: P1) 🎯 MVP

**Goal**: every release pushes `registry.opsmill.io/opsmill/infrahub-sync:<version>`. A
stable release also moves `latest`, and a pre-release never does. The image is
signed and has its SBOMs attached.

**Independent Test**: quickstart §5. After a release, pull `<version>` on amd64 and
arm64 and run `infrahub-sync --help`. Verify the signature and both attestations,
and check where `latest` points for a pre-release and for a stable release.

- [ ] T009 [P] [US1] Add contract tests in `tests/test_workflow_contracts.py` covering the release path:
  - **`release-publish.yml`** computes `is_prerelease` with `packaging.version.Version` (`is_prerelease or is_devrelease`). It passes `--prerelease --latest=false` to `gh release create` when that is true, and `--latest` otherwise.
  - **`trigger-release.yml`** passes `prerelease: ${{ github.event.release.prerelease }}` to `workflow-publish.yml`.
  - **`workflow-publish.yml`**:
    - guards `uv publish` with `if: inputs.publish`;
    - has a `docker_meta` job whose metadata tags are `type=raw,value=${{ inputs.version }}`, with `latest` enabled only when not a pre-release and the tag equals `releases/latest`;
    - has a `publish_docker_image` job that calls `./.github/workflows/ci-docker-image.yml` with `publish: ${{ inputs.publish }}` and `secrets: inherit`.
- [ ] T010 [US1] In `.github/workflows/release-publish.yml`, add a step before "Create the tag and the GitHub Release":
  - It runs `uv run --no-project --with packaging python -c` and prints `prerelease=true|false` from `Version(v).is_prerelease or Version(v).is_devrelease`, where `v` is the version already read from `pyproject.toml`.
  - Pass `--prerelease --latest=false` to `gh release create` when that output is true, and `--latest` otherwise.
  - Keep the bare-version tag (no `v`) and the existing guards.
- [ ] T011 [US1] In `.github/workflows/trigger-release.yml`, pass `prerelease: ${{ github.event.release.prerelease }}` and `secrets: inherit` to `workflow-publish.yml`. Keep `publish: true` and `version: ${{ github.ref_name }}`.
- [ ] T012 [US1] Rewrite `.github/workflows/workflow-publish.yml`:
  - **Inputs**: add a `prerelease` boolean input (default false) to both `workflow_call` and `workflow_dispatch`, and make `uv publish` conditional on `inputs.publish` (bug fix: today it ignores that input).
  - **`docker_meta` job**: adapt infrahub-mcp's (needs the PyPI job). It sets `ref=${{ github.sha }}`, and works out `latest=true` only when `inputs.prerelease` is false and `gh api repos/${{ github.repository }}/releases/latest --jq .tag_name` equals `inputs.version`. It then runs `docker/metadata-action` with:
    - `images: ${{ vars.HARBOR_HOST }}/${{ github.repository }}`;
    - `tags: type=raw,value=${{ inputs.version }}`;
    - `flavor: latest=<computed>`;
    - labels `org.opencontainers.image.source`, `org.opencontainers.image.version` and `org.opencontainers.image.revision=${{ github.sha }}`.
  - **`publish_docker_image` job**: calls `ci-docker-image.yml` with `publish`, `version`, `ref`, `tags` and `labels` from `docker_meta`, and `secrets: inherit`.
- [ ] T013 [US1] Write the image page `docs/docs/container-image.mdx` from scratch for the registry flow:
  - where the image lives (`registry.opsmill.io/opsmill/infrahub-sync`);
  - the tag rules (version on every release, `latest` on stable releases only);
  - supported platforms;
  - `docker login registry.opsmill.io` for invited testers until 3.0.0 (FR-015);
  - `docker pull` and the command forms table, which stays;
  - how to check the signature and SBOM with `cosign verify` and `cosign verify-attestation`, using the identity flags in quickstart §4;
  - how to build locally with `docker build -t infrahub-sync:local .`.

  Remove every `invoke image.*`, OCI-layout, archive and canary section.
- [ ] T014 [P] [US1] In `docs/docs/installation.mdx`, replace the `private-candidate-*.tar.gz` release-package section (around lines 18–90) with a short "Run the container image" section that links to `container-image.mdx` and `compose-deployment.mdx`.

**Checkpoint**: The release path is wired from end to end. It can be checked with a
`workflow_dispatch` of `workflow-publish.yml` using `publish: true`,
`version: <test>` and `prerelease: true`.

---

## Phase 4: User Story 2 — publish an image on demand (Priority: P2)

**Goal**: maintainers dispatch `ci-docker-image.yml` with any ref, tags and platforms.

**Independent Test**: quickstart §4. A dispatch with `publish: true` pushes exactly
the listed tags, signed and with SBOMs attached. With `publish: false` nothing is
pushed. A single platform builds only that one. An empty tag list fails.

- [ ] T015 [P] [US2] In `tests/test_workflow_contracts.py`, assert that `ci-docker-image.yml` declares `workflow_dispatch` with the same six inputs as `workflow_call`, and that `run-name` (or the concurrency group) includes `inputs.ref`.
- [ ] T016 [US2] In `.github/workflows/ci-docker-image.yml`:
  - Add a `run-name: "Image ${{ inputs.ref }} (publish=${{ inputs.publish }})"`.
  - Add a concurrency group `${{ github.workflow }}-${{ inputs.ref }}` with `cancel-in-progress: true`.
  - In both `inputs` blocks, give `tags` a description saying it is newline-separated full references.
- [ ] T017 [P] [US2] Write `docs/docs/develop/guides/publishing-an-image.md`, a maintainer guide covering:
  - when to dispatch, for example a pre-release image for testers;
  - the input values, with a worked example for `registry.opsmill.io/opsmill/infrahub-sync:dispatch-test`;
  - how to verify the result (quickstart §4 commands);
  - why a dispatch never moves `latest` unless it is listed.

  Add the guide to `docs/sidebars.ts`, next to the develop guides.

**Checkpoint**: quickstart §4 passes.

---

## Phase 5: User Story 3 — pull requests prove the image builds (Priority: P2)

**Goal**: a PR that touches image inputs builds and smoke-tests both platforms
without credentials, and the required check **Full qualification** reports that
result.

**Independent Test**: quickstart §3.

- [ ] T018 [P] [US3] Rewrite the required-check contracts in `tests/test_workflow_contracts.py`:
  - Keep `REQUIRED_JOB = "qualification-required"` and assert its `name` is `Full qualification`.
  - Assert it `needs: ["image-changes", "image"]`, runs `if: always()`, and fails unless `needs.image.result` is `success` or `skipped`.
  - Assert the `image` job calls `./.github/workflows/ci-docker-image.yml` with `publish: false`, references no `secrets`, has no `actions: write`, and is gated on the `image_inputs` filter.
  - Delete the tier-guard tests that test the `qualify` label and the `qualification` decision job.
- [ ] T019 [US3] Rewrite the `image` and `qualification-required` jobs in `.github/workflows/trigger-pr-develop.yml`:
  - **Delete** the `qualification` tier-decision job and the `labeled`/`unlabeled` triggers that only served the `qualify` label.
  - **Add** an `image-changes` job using `opsmill/paths-filter` (already used in this repo; reuse its pinned SHA) with `filters: .github/file-filters.yml`, which outputs `image_inputs`.
  - **`image` job**: `needs: image-changes`, `if: needs.image-changes.outputs.image_inputs == 'true'`, and `uses: ./.github/workflows/ci-docker-image.yml` with:
    - `publish: false`;
    - `ref: ${{ github.event.pull_request.head.sha || github.sha }}`;
    - `tags: infrahub-sync:pr`;
    - `labels` carrying source, version from `pyproject.toml`, and revision.

    Its permissions are just `contents: read`.
  - **`qualification-required`** keeps its id and `name: "Full qualification"`, with `needs: ["image-changes", "image"]` and `if: always()`. It fails unless the `image-changes` result is `success` and the `image` result is `success` or `skipped`. Each failure message names the job that failed.
  - **Leave** `linter`, `uv-checker` and `tests` unchanged.
- [ ] T020 [P] [US3] Update the "Full qualification" section of `docs/docs/contributing.mdx` (around lines 185–200). The check is now the image build and smoke test on both platforms, which runs when image inputs change and passes when they don't. The `qualify` label is gone.
- [ ] T021 [P] [US3] Update `docs/docs/develop/guidelines/testing-tiers.md` to describe the remaining tiers: unit, `-m docker` smoke, and the opt-in `-m compose`. Remove the full-qualification-tier, clean-host and candidate tiers.

**Checkpoint**: quickstart §3 passes on a test PR.

---

## Phase 6: User Story 5 — the GitHub-artifact image machinery is gone (Priority: P3)

**Goal**: remove the candidate workflow, the old image gate, the tester packet,
archive handoff, clean-host suite and their tasks, tests and docs. US4 removes
`deploy/compose/` and the old Compose files.

**Independent Test**: quickstart §8 (the parts that aren't Compose-related). Unit
tests still collect and pass.

- [ ] T022 [US5] Delete `.github/workflows/workflow-candidate.yml` and `.github/workflows/workflow-image.yml`. Search `.github/` for any remaining caller (`grep -rn 'workflow-image\|workflow-candidate' .github`) and remove it.
- [ ] T023 [US5] Delete `tasks/image.py` and `tasks/compose.py`. In `tasks/__init__.py`, remove the `image` and `compose` collection registrations, and the `release` registration for now (T035 re-adds it). Move `ZERO_SKIP_OPTION`, which `tests/compose/conftest.py` imports from `tasks.compose`, into `tests/compose/conftest.py` as a local constant with the same value.
- [ ] T024 [US5] Delete the contents of `tasks/release.py` (the 1012-line candidate, kit, qualify and packet module), leaving an empty module docstring, ready for T035.
- [ ] T025 [P] [US5] Delete the image tests that serve the removed machinery:
  - `tests/image/canary.py`
  - `tests/image/test_candidate_reuse.py`
  - `tests/image/test_docker_archive.py`
  - `tests/image/test_oci_layout.py`
  - `tests/image/test_warm_builder.py`
  - `tests/image/test_image_containment.py`
  - `tests/image/test_vulnerability_policy.py`
  - `tests/image/test_image_documentation.py`

  Also delete `vulnerability-waivers.yml` if it exists at the repository root. Open `tests/image/test_image_build_inputs.py`: keep it if it checks only `Dockerfile` or `.dockerignore` inputs, and delete it if it reads `.image/` or `tasks.image`.
- [ ] T026 [P] [US5] Delete `tests/release/` (the whole directory).
- [ ] T027 [P] [US5] Delete the Compose qualification tests:
  - `tests/compose/clean_host/` (the whole directory);
  - every `tests/compose/test_clean_host_*.py` file;
  - `tests/compose/test_qualification.py`.
- [ ] T028 [US5] In `tests/test_workflow_contracts.py`, delete the tests and constants for these removed contracts: clean-host, packet, candidate, handoff, kit, `workflow-image.yml` and `workflow-candidate.yml` (roughly lines 583–2225). Remove the `tasks.release` import at line 28. Keep the US1, US2 and US3 contracts added above.
- [ ] T029 [P] [US5] Delete `examples/tester_packet/`, `docs/docs/develop/guides/building-a-tester-packet.md` and `docs/docs/develop/guides/qualifying-an-internal-candidate.md`. Remove their entries from `docs/sidebars.ts` (around lines 120–121) and their links from `docs/docs/develop/guides/index.md`.
- [ ] T030 [P] [US5] In `.gitignore`, remove the `.image/` and `.release/` entries (around lines 32–34). In `pyproject.toml`, rewrite the pytest marker help text (around lines 241–243) so `docker` reads "image smoke tests; needs Docker and `INFRAHUB_SYNC_IMAGE_REF`" and `compose` reads "Compose deployment lifecycle; needs Docker". Drop the mentions of `invoke image.*` and `compose.lifecycle`.
- [ ] T031 [US5] In `.github/file-filters.yml`, delete the `image_files`, `compose_files`, `image_all` and `qualify_all` filters, leaving `image_inputs`. Run `grep -rn 'image_all\|qualify_all\|image_files\|compose_files' .github` and expect no matches.
- [ ] T032 [US5] Run `uv run pytest -q --co` and `uv run pytest -q -m "not docker and not compose"`. Fix any import error left by T022–T031. The Compose suite may be collect-only broken until US4 if it imports `tasks.image` or `tasks.release`. In that case, delete only the import and the `write_candidate_binding` helper in `tests/compose/lifecycle.py` now, and note it for T041.

**Checkpoint**: there is no candidate, packet, archive or kit code left, and the unit
suite is green.

---

## Phase 7: User Story 4 — the Compose deployment uses the registry image (Priority: P3)

**Goal**: a self-contained root `docker-compose.yml` pulls the release's image by
version. The wrapper and `deploy/compose/` are gone. The dev stack moves to
`development/`.

**Independent Test**: quickstart §6 and §7.

### Tests for User Story 4

- [ ] T033 [P] [US4] Write `tests/test_release_pin.py`, with parametrized unit tests per [contracts/release-pin-tasks.md](contracts/release-pin-tasks.md). Each test runs on a `tmp_path` copy of a small compose fixture string and checks one of:
  - the version is rewritten on every Sync image line;
  - a second run is idempotent (no diff);
  - a pre-release version is accepted (`3.0.0a6`);
  - an invalid version fails;
  - a file with no Sync image line fails;
  - validate passes on a match;
  - validate fails on a mismatch and lists the offending lines;
  - a third-party image line (`postgres:16-alpine@sha256:…`) is untouched.
- [ ] T034 [P] [US4] Add a test to `tests/test_workflow_contracts.py`:
  - `trigger-push-stable.yml` runs `invoke release.update-docker-compose --version "${VERSION}"` after the "Update lock file" step;
  - its release-PR `git add` includes `docker-compose.yml`;
  - `release-publish.yml` runs `invoke release.validate-docker-compose` before `gh release create`.

### Implementation for User Story 4

- [ ] T035 [US4] Implement `tasks/release.py` per [contracts/release-pin-tasks.md](contracts/release-pin-tasks.md), modelled on `../infrahub/tasks/release.py`:
  - **Tasks**: two typed tasks, `update_docker_compose(context, version)` and `validate_docker_compose(context, version)`, exposed as `release.update-docker-compose` and `release.validate-docker-compose`.
  - **Regex**: match the `${VERSION:-<old>}` default only on lines containing `registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-`, and rewrite with plain text replacement so comments are preserved.
  - **Validation**: check the version with `packaging.version.Version`.
  - **Output**: use `structlog`, not `print`.
  - **Collections**: register a `release` collection in `tasks/__init__.py`.

  Make T033 pass.
- [ ] T036 [US4] Create the root `docker-compose.yml` from `deploy/compose/compose.yaml`, following [contracts/docker-compose-env.md](contracts/docker-compose-env.md):
  - **Sync images**: every Sync service (`sync-bootstrap`, `sync-api`, `sync-worker`, `cli`) uses `"${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<current pyproject version>}"`. Remove `pull_policy` and the instance labels (`INFRAHUB_SYNC_INSTANCE`).
  - **Bootstrap script**: inline `deploy/compose/bootstrap/databases.sh` as a top-level `configs: db-bootstrap-script: content: |` block, mounted at `/usr/local/bin/databases.sh` in `db-bootstrap`. Keep its `psql -v` bound-variable quoting.
  - **Admin password**: replace the `postgres-admin-password` secret file with `POSTGRES_PASSWORD: "${INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD:?...}"`. Give `db-bootstrap` `PGPASSWORD` from the same variable.
  - **Defaults**: inline every `deploy/compose/defaults.conf` value as `${VAR:-default}`.
  - **Connection strings**: compose `INFRAHUB_SYNC_DATABASE_URL` and `INFRAHUB_SYNC_PREFECT_DATABASE_URL` from the role, database and password variables, as overridable defaults.
  - **Required variables**: keep `:?message` on every credential, each naming the variable.
  - **Header**: add a short comment block with the three-step operator flow (fetch the file, write `.env`, `docker compose up -d`) and the Compose ≥ 2.24 minimum.
- [ ] T037 [US4] Run `uv run invoke release.validate-docker-compose --version "$(uv version --short)"` against the new root `docker-compose.yml`, and expect exit 0. Then run `docker compose -f docker-compose.yml config --quiet` with a throwaway `.env` that sets every required variable. Expect exit 0, and expect a clear error naming the variable when one is unset.
- [ ] T038 [US4] In `.github/workflows/trigger-push-stable.yml`, add a step "Pin the Compose image to the release" right after "Update lock file". It runs `uv run --no-sync invoke release.update-docker-compose --version "${VERSION}"`. Add `docker-compose.yml` to the release PR's `git add` list (around line 243).
- [ ] T039 [US4] In `.github/workflows/release-publish.yml`, add a step "Refuse a Compose file pinned to another version" before the tag step. It runs `uv run --no-sync invoke release.validate-docker-compose --version "${VERSION}"`, after the install step it needs. Make T034 pass.
- [ ] T040 [US4] Move the root `compose.yaml` to `development/docker-compose.dev.yml` with `git mv`:
  - Change `x-sync-build.context` to `..`.
  - Keep `name: infrahub-sync-dev`.
  - Fix the header comment: it no longer describes `deploy/compose/` and now points at the root `docker-compose.yml`.
  - Update `tasks/dev.py` and `tasks/netbox.py` to pass `-f development/docker-compose.dev.yml` on every `docker compose` call, and to update their comments and messages (`tasks/dev.py` lines 20 and 31; `tasks/netbox.py` lines 18 and 109).
  - Update `tests/dev_stack/test_compose_valid.py` (the path at line 25, plus its docstring) and `tests/dev_stack/test_netbox_network.py` (docstring).
- [ ] T041 [US4] Retarget the Compose lifecycle suite at the root `docker-compose.yml`:
  - **`tests/compose/conftest.py`**:
    - set `COMPOSE_FILE` to `REPO_ROOT / "docker-compose.yml"`;
    - delete `BUNDLE`, `DEFAULTS_FILE`, `INSTANCE_LABEL`, `BINDING_FILE` and `write_binding`;
    - write a per-test `.env` with random credentials;
    - set `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` from the image under test. They are read from the environment variables of the same names, and the test fails with a clear message if they are unset;
    - replace `start_command` with `docker compose -f … --env-file … up -d --wait`.
  - **`tests/compose/lifecycle.py`**: delete `write_candidate_binding` and the wrapper calls.
  - **`tests/compose/test_lifecycle.py`**: replace each `init`, `start`, `status`, `stop` and `reset` wrapper call (around lines 213, 548 and 720) with the equivalent `docker compose` command.
- [ ] T042 [US4] Rename `tests/compose/test_bundle_contract.py` to `tests/compose/test_compose_contract.py`, and change its assertions:
  - every Sync service uses the `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-X}` form;
  - every credential uses `:?`;
  - no service mounts a host path;
  - the bootstrap script comes from `configs`.

  Update these files for the new file and the missing wrapper, deleting tests that assert wrapper-only behaviour:
  - `tests/compose/test_compose_minimum.py` (assert Compose 2.24);
  - `tests/compose/test_configuration_free_startup.py`;
  - `tests/compose/test_container_cli.py` (the CLI runs through `docker compose run --rm cli …`);
  - `tests/compose/test_redaction.py`;
  - `tests/compose/test_source_credentials.py`;
  - `tests/compose/test_documentation.py` (lines 104–113 and 359).
- [ ] T043 [US4] Delete `tests/compose/test_preflight.py` and `deploy/compose/` (the whole directory: the wrapper, `OPERATING.md`, `defaults.conf`, `bootstrap/`, `configuration/` and `skills/`). Before deleting `deploy/compose/skills/*/SKILL.md`, carry their durable operator guidance (configuration registration, deployment steps) into `docs/docs/compose-deployment.mdx` (T045).
- [ ] T044 [US4] Run the opt-in suite locally: `docker build -t infrahub-sync:compose-test .`, then `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -x`. Fix any failure. In `.github/workflows/workflow-nightly-e2e.yml`, add (or adjust) a job that builds the image locally and runs the same command, so the suite keeps running nightly.
- [ ] T045 [US4] Rewrite `docs/docs/compose-deployment.mdx` for the single-file flow:
  - **Prerequisites**: Docker Compose 2.24 or later; registry login until 3.0.0.
  - **Getting the file**: fetch `docker-compose.yml` from the release tag.
  - **The `.env` file**: a sanitized example listing every required and optional variable from the contract, with placeholder values only.
  - **Running it**: `docker compose up -d --wait`, then the CLI through `docker compose run --rm cli`.
  - **Choosing an image**: overriding `VERSION` and `INFRAHUB_SYNC_DOCKER_IMAGE`.
  - **Wrapper equivalents**: a table mapping each removed wrapper command (`init`, `preflight`, `start`, `status`, `logs`, `stop`, `restart`, `reset`, `cli`) to its `docker compose` equivalent. `init` becomes "write `.env`", and `reset` becomes `down -v` with a data-loss warning.

  Rewrite `docs/docs/quickstart-compose.mdx` to the three-step flow.
- [ ] T046 [P] [US4] Rewrite the operations pages to use `docker compose` commands instead of `./infrahub-sync-compose`, and drop the `image.bind` and `docker load` content:
  - `docs/docs/operations/compose-troubleshooting.mdx`
  - `docs/docs/operations/day-2-operations.mdx`
  - `docs/docs/operations/supported-platforms-and-limits.mdx` (add arm64; Compose 2.24)
- [ ] T047 [P] [US4] Rewrite the tutorials' deployment steps for the same flow:
  - `docs/docs/tutorials/netbox-to-existing-infrahub.mdx`
  - `docs/docs/tutorials/nautobot-to-existing-infrahub.mdx`
  - `docs/docs/tutorials/netbox-demo-to-infrahub.mdx`
- [ ] T048 [P] [US4] Update the remaining references to the old files and paths:
  - `docs/docs/development-stack.mdx`: the dev stack now lives at `development/docker-compose.dev.yml`, run with `uv run invoke start`;
  - `docs/docs/custom-certificates.mdx` (line 33);
  - `docs/docs/adapters/choosing-an-adapter.mdx` (line 42);
  - `docs/docs/develop/knowledge/repository-tour.md` (line 211);
  - `README.md` (line 71, plus the `infrahub-sync:dev` mention).

**Checkpoint**: quickstart §6 and §7 pass. `test ! -e compose.yaml && test ! -d deploy/compose` succeeds.

---

## Phase 8: Polish and cross-cutting concerns

- [ ] T049 [P] Remove the candidate, packet and wrapper prose in `AGENTS.md` and `.github/copilot-instructions.md` (around line 185). Describe the image as published to `registry.opsmill.io/opsmill/infrahub-sync` by `ci-docker-image.yml`. Keep the "Required Development Workflow" and "Approval checklist" blocks verbatim, as AGENTS.md requires.
- [ ] T050 [P] Check the wording of `docs/docs/orchestration.mdx` and `docs/docs/develop/knowledge/orchestration-prefect.md` (one or two candidate or bundle mentions each), and replace any stale reference.
- [ ] T051 [P] Add the news fragment `changelog/+harbor-image-publishing.changed.md` with `uv run --extra dev towncrier create -c "…" +harbor-image-publishing.changed.md`. It says:
  - the Sync image is now published to `registry.opsmill.io/opsmill/infrahub-sync` for linux/amd64 and linux/arm64, signed, with SBOMs attached;
  - the Compose deployment is the root `docker-compose.yml`;
  - the `infrahub-sync-compose` wrapper and release bundle are removed;
  - pulls need a registry login until 3.0.0.
- [ ] T052 Run the SC-006 removal check in quickstart §8 and expect no matches. Then run `wc -l .github/workflows/ci-docker-image.yml tasks/release.py` and record the result next to the T001 baseline in `research.md`. The reduction must be at least 75% (SC-004).
- [ ] T053 Run the constitution's quality gates:
  - `uv sync --extra dev --extra prefect --extra service`
  - `uv run invoke format`
  - `uv run invoke lint` (rumdl, ruff, pylint, yamllint, ty)
  - `uv run ty check .`
  - `uv run pytest -q -m "not docker and not compose"`
  - `uv run invoke docs.generate`

  Fix every finding in touched files.
- [ ] T054 Run the CLI sanity checks: `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help` and `uv run infrahub-sync runs --help`. Then run the full local validation in quickstart §1, §2 and §7.
- [ ] T055 Hand the maintainers the plan's external rollout actions in "Rollout and external actions": create the private Harbor project, confirm the `HARBOR_*` org settings are shared, and flip the project to public at 3.0.0. Put them in the PR description draft at `specs/007-harbor-image-publishing/pr-notes.md`. Do not open a PR.

---

## Dependencies and execution order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup. Blocks US1, US2 and US3.
- **US1 (Phase 3)**: depends on Foundational.
- **US2 (Phase 4)**: depends on Foundational, and touches `ci-docker-image.yml` after T006 and T007. It is independent of US1.
- **US3 (Phase 5)**: depends on Foundational (T002, T006). It is independent of US1 and US2.
- **US5 (Phase 6)**: depends on US3, because T022 deletes `workflow-image.yml`, which `trigger-pr-develop.yml` calls until T019.
- **US4 (Phase 7)**: depends on US5, because T035 reuses `tasks/release.py` once T024 has emptied it. It also depends on US1 (T039 edits `release-publish.yml` after T010).
- **Polish (Phase 8)**: depends on everything.

### Within each story

- Tests before implementation (T008 before the final form of T006; T009 before T010–T012; T018 before T019; T033 and T034 before T035–T039).
- `docker-compose.yml` (T036) before the pin wiring (T038, T039), and both before the suite retarget (T041 and T042), docs last.

### Parallel opportunities

- Phase 2: T003, T004 and T005 run in parallel, then T006 to T008 in sequence.
- US1: T009 and T014 run alongside T010–T012 (different files).
- US2 and US3 can run in parallel once Phase 2 is done. T017, T020 and T021 are docs-only.
- US5: T025, T026, T027, T029 and T030 run in parallel after T022–T024.
- US4: T033 and T034 run in parallel; so do T046, T047 and T048.
- Polish: T049, T050 and T051 run in parallel.

## Parallel examples

```text
# Phase 2, together:
T003 tests/image/conftest.py   T004 tests/image/test_image_artifact.py   T005 tests/image/test_version_policy.py

# After Phase 2, two developers:
Dev A: US1 (T009–T014)       Dev B: US3 (T018–T021), then US2 (T015–T017)

# US5 deletions, together:
T025 tests/image/*   T026 tests/release/   T027 tests/compose/clean_host/   T029 examples/ + guides   T030 .gitignore + pyproject
```

## Implementation strategy

### MVP (US1)

1. Phases 1 and 2 bring the reusable workflow and smoke test.
2. Phase 3 publishes releases to Harbor.
3. **Stop and validate** with a `workflow-publish.yml` dispatch (`prerelease: true`, a throwaway version). That proves the push, signing, SBOMs, and that `latest` stays put. The Harbor project must exist first (T055 rollout item 1).

### Incremental delivery (one commit or PR slice per step, matching research's "Implementation order")

1. Phase 2 + US3: the PR gate moves to the new workflow, and the old workflows are still on disk.
2. US1 + US2: release and dispatch publishing.
3. US5: removal only, no new behaviour.
4. US4: the Compose replacement and dev-stack move.
5. Polish: docs, changelog, gates.

## Notes

- Never edit `CHANGELOG.md`, bump versions or create tags by hand (AGENTS.md
  "Changelog").
- Never open a PR or push without an explicit request.
- History stays as it is: `changelog/*`, `docs/docs/release-notes/**`,
  `docs/versioned_docs/**` and `dev/specs/archive/**` are not edited.
