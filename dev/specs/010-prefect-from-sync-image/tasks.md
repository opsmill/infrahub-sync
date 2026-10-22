# Tasks: Compose stacks run the Prefect server from the Sync image

**Input**: Design documents from `dev/specs/010-prefect-from-sync-image/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: included. Every functional requirement in the spec names its test, so each user
story phase starts with its tests. Write them first and check that they fail before the
implementation tasks.

**Organization**: tasks are grouped by user story (US1–US5 in [spec.md](spec.md)) so each story
can be built and checked on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1–US5)
- Paths are relative to the repository root.

## Note on the bucket step and spec 013

Spec 013 later changes the `sync-bootstrap` bucket step so that it checks whether the bucket
exists before it creates it. This feature does not change the bucket step: `converge_bucket`
in `infrahub_sync/service/bootstrap.py:79-90` already creates the bucket over the S3 API and
treats an existing bucket as success, which meets FR-012 (research R3). Tasks in this file
MUST NOT:

- change the signature or the return value of `converge_bucket`;
- add tests that require `create_bucket` to be the first S3 call, or that fail when a
  `head_bucket` call comes first;
- add bucket logic to Compose files (for example an `mc` or `aws` container).

The bucket tests in this feature (T020) assert the outcome only: the bucket exists after the
bootstrap on an empty Silo instance, and a second bootstrap succeeds without changing it.

---

## Phase 1: Setup (shared prerequisites)

**Purpose**: settle the two facts the plan left open, and add the dependencies.

- [ ] T001 Verify that `pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7` runs on linux/amd64 (research R3 ran it on arm64 only). On an amd64 host or a GitHub `ubuntu-latest` runner: run the repair step (`chown -R 65532:65532 /data` in the same image on a new named volume), start `server /data` as `65532:65532` with `MINIO_BROWSER=off`, run `silo healthcheck ready` inside the container, and create a bucket twice with a plain S3 client to see `BucketAlreadyOwnedByYou` on the second call. Record the commands, the host architecture and the results under R3 in `dev/specs/010-prefect-from-sync-image/research.md`. If any step fails, stop and report before Phase 3.
- [ ] T002 Write the first-start measurement procedure for FR-008 in `dev/specs/010-prefect-from-sync-image/research.md` (new section R11): run `time docker compose up -d --wait --wait-timeout 420` on the root `docker-compose.yml` twice, once with every image already pulled and once after `docker image rm` of every image the file names and `docker volume prune` of its volumes, on an amd64 GitHub runner and on an arm64 laptop. Record the current baseline (Prefect from `prefecthq/prefect`) now; T024 records the times for this feature with the same procedure.
- [ ] T003 Add the optional extra `testcontainers` (`infrahub-testcontainers` and `testcontainers>=4.8,<4.9`, Python 3.11 and later) and add both to the `dev` extra in `pyproject.toml`; run `uv lock` and check in `uv.lock` that `prefect` and `prefect-client` are both 3.8.6 and `fastapi` stays 0.141.1 (research R4). Commit `pyproject.toml` and `uv.lock`.

---

## Phase 2: Foundational (blocks every user story)

**Purpose**: the Prefect server factory that all three stacks start.

- [ ] T004 [P] Write unit tests for the factory in `tests/service/test_prefect_server.py`: `create_prefect_app()` returns a FastAPI application; `/api/health` answers 200 with an in-memory SQLite database URL (`PREFECT_API_DATABASE_CONNECTION_URL=sqlite+aiosqlite:///:memory:`); with `PREFECT_UI_ENABLED=false` no UI route is mounted. Skip the module on Python 3.10, as the other `tests/service` tests do.
- [ ] T005 Create `infrahub_sync/service/prefect_server.py` with a typed, documented `create_prefect_app() -> FastAPI` that returns `prefect.server.api.server.create_app()` and nothing else (research R1; Infrahub's version is `infrahub/backend/infrahub/prefect_server/app.py:32-47`, without its `/infrahub` router and start-up lock). Make T004 pass.
- [ ] T006 [P] Write a contract test helper `tests/compose/stacks.py` that returns the resolved `docker compose config --format json` of a given Compose file and profile list, and the list of image references, for use by the contract tests in US1–US5. Skip when Docker is not installed, as `tests/dev_stack/test_compose_valid.py:49` does.

**Checkpoint**: `uv run invoke tests.tests-unit` passes; the factory starts with
`uvicorn --factory infrahub_sync.service.prefect_server:create_prefect_app`.

---

## Phase 3: User Story 1 – Customer team deploys Sync from the root Compose file (P1) MVP

**Goal**: the root file runs `prefect-server` from the Sync image with the UI off and no
Prefect port, runs the object store from Silo, and pulls three images.

**Independent test**: [quickstart.md](quickstart.md#user-story-1-deploy-the-root-file).

### Tests for User Story 1

- [ ] T007 [P] [US1] Contract test in `tests/compose/test_compose_contract.py`: no image reference in the root file contains `prefecthq/prefect` (FR-001, SC-004), and `prefect-server.image` equals `sync-api.image` and `sync-worker.image` (FR-002).
- [ ] T008 [P] [US1] Contract test in `tests/compose/test_compose_contract.py`: `prefect-server.command` is the uvicorn factory command and its health check calls `http://127.0.0.1:4200/api/health` with `python -c "import httpx; ..."` (FR-001, [contracts/compose-services.md](contracts/compose-services.md)).
- [ ] T009 [P] [US1] Contract test in `tests/compose/test_compose_contract.py`: in the resolved environment, only `prefect-server` and `db-bootstrap` hold `INFRAHUB_SYNC_PREFECT_PASSWORD` or a Prefect database URL, and `cli` holds no `PREFECT_*` credential (FR-003).
- [ ] T010 [P] [US1] Contract test in `tests/compose/test_compose_contract.py`: `sync-api` is the only service with `ports`; `prefect-server` has `PREFECT_UI_ENABLED == "false"` and a `PREFECT_SERVER_UI_API_URL` on `127.0.0.1`; `prefect-server` is `read_only` with the Sync tmpfs mounts (FR-006, SC-003).
- [ ] T011 [P] [US1] Contract test in a new `tests/compose/test_object_store_pin.py`, parametrized over every Compose file in the repository: `object-store` and `object-store-init` use `pgsty/silo:RELEASE.<date>@sha256:<64 hex>`, no file references `cgr.dev/chainguard/minio` or `minio/minio`, the health check is `["CMD", "silo", "healthcheck", "ready"]`, and `object-store.user` is `65532:65532` (FR-011, FR-013).
- [ ] T012 [P] [US1] Contract test in `tests/compose/test_compose_contract.py`: the root file names exactly three distinct images (PostgreSQL, Silo, Sync) (SC-001).
- [ ] T013 [P] [US1] Update the existing digest test `test_every_image_other_than_sync_is_named_by_digest` (`tests/compose/test_compose_contract.py:416`) and `test_prefect_keeps_no_state_of_its_own` (`:184`) so they describe `prefect-server` as a Sync-image service, not a pinned third-party image.

### Implementation for User Story 1

- [ ] T014 [US1] In `docker-compose.yml`, change `prefect-server` (lines 150-187) to the Sync image `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-…}`, the command `uvicorn --host 0.0.0.0 --port 4200 --factory infrahub_sync.service.prefect_server:create_prefect_app`, `read_only: true` with `tmpfs: *sync-scratch`, the `httpx` health check, `PREFECT_UI_ENABLED: "false"`, the existing `PREFECT_SERVER_UI_API_URL`, and no `ports` entry. Keep the database URL and `PREFECT_SERVER_API_AUTH_STRING`. Make T007, T008 and T010 pass.
- [ ] T015 [US1] In `docker-compose.yml`, remove every Prefect database credential from services other than `prefect-server` and `db-bootstrap`, and every `PREFECT_*` credential from `cli` (lines 296-317). Make T009 pass.
- [ ] T016 [US1] In `docker-compose.yml`, change `object-store-init` (lines 102-118) and `object-store` (lines 120-148) to the Silo pin from T001, replace the `wget` health check with `["CMD", "silo", "healthcheck", "ready"]`, keep `user: "65532:65532"`, `command: ["server", "/data"]` and `MINIO_BROWSER: "off"`, and replace the "Temporary Chainguard build" comment with the Silo source and licence. Make T011 and T012 pass for the root file.
- [ ] T017 [US1] Change the release tooling that rewrites image pins, `tasks/release.py` (`release.update-docker-compose`, `release.validate-docker-compose`), so it treats `prefect-server` as a Sync-image service and the Silo pin as a third-party digest pin; update its tests.
- [ ] T018 [US1] Update the Compose lifecycle fixtures in `tests/compose/conftest.py` and `tests/compose/fixture-override.yaml` for the new `prefect-server` (no published port, Sync image built by CI) so `pytest -m compose tests/compose` still starts the stack.
- [ ] T019 [US1] Run `pytest -m compose tests/compose -x` against an image built from the checkout (`infrahub-sync:compose-test`) and fix failures until every service reaches healthy.
- [ ] T020 [US1] Extend `tests/compose/test_bootstrap_idempotence.py` so that, against an empty Silo volume, the first bootstrap leaves the bucket present and a second bootstrap succeeds without changing it (FR-012). Assert the outcome only, as the note on spec 013 above requires.
- [ ] T021 [P] [US1] In `docs/docs/compose-deployment.mdx`, name the Silo image, its AGPLv3 licence and a link to [github.com/pgsty/silo](https://github.com/pgsty/silo) (FR-014), and state that the default deployment publishes only the Sync API port and serves no Prefect UI.
- [ ] T022 [P] [US1] Add a news fragment `changelog/+prefect-from-sync-image.changed.md`: the Prefect server runs from the Sync image; the object store runs from Silo; there is no in-place update from `3.0.0a5`, so run `docker compose down -v` first, which removes the Prefect work pool, deployments and run history, the saved plans and run files, and the `infrahub_sync` database; `INFRAHUB_SYNC_PREFECT_PORT` has no effect without the override snippet (FR-009, D-14).
- [ ] T023 [US1] Add a documentation test in `tests/compose/test_documentation.py` that `docs/docs/compose-deployment.mdx` names the Silo image, `AGPL`, the source link, and the command `docker compose up -d --wait --wait-timeout 420` (FR-008, FR-014).
- [ ] T024 [US1] Re-measure the first-start times with the T002 procedure on the root file from this story, record them under R11 in `research.md`, and write them, with the `--wait-timeout 420` command, in `docs/docs/compose-deployment.mdx` (FR-008).

**Checkpoint**: User Story 1 passes its contract tests and the compose suite; quickstart US1
checks pass.

---

## Phase 4: User Story 2 – Engineer runs the whole development stack in containers (P1)

**Goal**: one development Compose file with profiles, `dev.start`, `dev.restart`, the
stale-image label, and one name per task.

**Independent test**: [quickstart.md](quickstart.md#user-story-2-everything-in-containers).

### Tests for User Story 2

- [ ] T025 [P] [US2] In a new `tests/dev_stack/test_profiles.py`: `docker compose -f development/docker-compose.yml config --profiles` lists `infrahub` and `sync`; without profiles only `postgres`, `object-store-init`, `object-store` and `prefect-server` resolve; `prefect-server` publishes `127.0.0.1:4230:4200` with the UI on and `PREFECT_UI_STATIC_DIRECTORY` under `/tmp/infrahub-sync/`; every Sync-image service mounts `infrahub_sync/` and `opsmill_prefect_extras/` read-only (FR-015, FR-019).
- [ ] T026 [P] [US2] In `tests/dev_stack/test_profiles.py`: `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml` and `development/preview.env` do not exist (FR-015).
- [ ] T027 [P] [US2] In a new `tests/dev_stack/test_build_inputs.py` (no Docker): the build-inputs hash changes when `Dockerfile`, `pyproject.toml` or `uv.lock` change and does not change when a file under `infrahub_sync/` changes; the rebuild decision is "build" for a missing image, a missing label or a different label, and "start" for an equal label (FR-018, research R5). Mock `docker image inspect`.
- [ ] T028 [P] [US2] In a new `tests/test_task_inventory.py`: walk the Invoke collection in `tasks/__init__.py`; the only top-level tasks are `format` and `lint`; every name in [contracts/invoke-tasks.md](contracts/invoke-tasks.md) exists; no removed name exists; no name appears twice (FR-016, FR-017, SC-005).
- [ ] T029 [P] [US2] Update `tests/dev_stack/test_compose_valid.py`, `test_loopback_publication.py`, `test_infrahub_network.py` and `test_netbox_network.py` to the new file name `development/docker-compose.yml` and the `infrahub` profile.

### Implementation for User Story 2

- [ ] T030 [US2] Add the build argument `BUILD_INPUTS` and the label `org.opsmill.infrahub-sync.build-inputs="${BUILD_INPUTS}"` to `Dockerfile` (next to the OCI labels at lines 76-85), defaulting to an empty value so release builds are unchanged.
- [ ] T031 [US2] Create `development/docker-compose.yml` from `development/docker-compose.dev.yml` and the preview services: core services without a profile (`postgres`, `object-store-init`, `object-store` on Silo as in T016, `prefect-server` from `infrahub-sync:dev` with the uvicorn command, UI on, `PREFECT_UI_STATIC_DIRECTORY=/tmp/infrahub-sync/prefect-ui`, `127.0.0.1:4230:4200`); `sync-bootstrap`, `sync-api` (`127.0.0.1:8030:8000`) and `sync-worker` under `profiles: ["sync"]` with read-only mounts of `../infrahub_sync` and `../opsmill_prefect_extras` over the installed copies; `include:` of `docker-compose.infrahub.yml`; every value that `development/preview.env` holds as a `${NAME:-default}` interpolation, following the table in [data-model.md](data-model.md#development-settings). Make T025 pass.
- [ ] T032 [US2] Add `profiles: ["infrahub"]` to every service in `development/docker-compose.infrahub.yml` (lines 209-329) and check that `netbox.*` and the benchmark runner, which also use this file, pass the profile.
- [ ] T033 [US2] Delete `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml` and `development/preview.env`. Make T026 pass.
- [ ] T034 [US2] In `tasks/dev.py`, add `dev_settings()` that runs `docker compose -f development/docker-compose.yml --profile sync --profile infrahub config --format json` and returns the project name, published host ports and bind address of `sync-api`, `prefect-server` and `infrahub-server`, and `INFRAHUB_INITIAL_ADMIN_TOKEN` (research R6); add `build_inputs_hash()` and the rebuild decision used by T027.
- [ ] T035 [US2] In `tasks/dev.py`, implement `dev.build` (passes `BUILD_INPUTS`), `dev.start [--infrahub-network]` (rebuild when stale, `up --detach --wait --wait-timeout 420` with `sync` and `infrahub` profiles, or attach to the named network and drop `infrahub`), `dev.restart` (recreate the Sync containers), `dev.stop`, `dev.destroy` (remove containers and volumes, keep the image), `dev.status`, `dev.logs [--name] [--lines]`, `dev.seed` and `dev.smoke`, moving the seed and smoke logic from `tasks/preview.py:587-618`. Read `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` as `tasks/dev.py:37-48` does today. Make T027 pass.
- [ ] T036 [US2] In `tasks/__init__.py`, add `dev` as a namespace; remove the top-level `docusaurus`, `generate-doc`, `tests-unit`, `tests-integration`, `tests-all`, `check-310`, `build`, `start` and `destroy`; keep the top-level `format` and `lint` unchanged (`tasks/__init__.py:40-50`); remove the `preview` collection. Move `check-310` to `tasks/tests.py` as `tests.check-310` and add `tests.all` there (unit and integration).
- [ ] T037 [US2] In `tasks/netbox.py`, rename `netbox.up` and `netbox.down` to `netbox.start` and `netbox.destroy`, and point `DEV_STACK_PROJECT` and the worker attachment at the new development file and project name. Make T028 pass.
- [ ] T038 [US2] In `tasks/bench.py`, replace `preview.load_preview_env()`, `preview.preview_urls()` and `preview._compose` (lines 97-104, 126, 171, 236) with `dev.dev_settings()` and the `dev` compose helper, and replace `ROOT / "compose.yaml"` (lines 192, 216, 220, 290) with `development/docker-compose.yml`; update `tests/test_benchmark_runner.py` (line 904 and the settings fixtures).
- [ ] T039 [US2] Delete `tasks/preview.py` and move or delete `tests/preview/` (keep tests that still describe `dev.smoke` behaviour, renamed under `tests/dev_stack/`).
- [ ] T040 [US2] Run the quickstart US2 checks: `uv run invoke dev.start`, `dev.smoke`, a code change plus `dev.restart`, and a stale `uv.lock` plus `dev.start`.

**Checkpoint**: User Stories 1 and 2 work on their own.

---

## Phase 5: User Story 3 – Engineer runs the Sync API and worker on the host (P1)

**Goal**: `dev.deps`, `dev.api`, `dev.worker` in the foreground, and a `dev.smoke` that names a
missing process.

**Independent test**: [quickstart.md](quickstart.md#user-story-3-sync-api-and-worker-on-the-host).

### Tests for User Story 3

- [ ] T041 [P] [US3] In a new `tests/dev_stack/test_deps_mode.py` (no Docker; mock `context.run`): `dev.deps` starts only core and `infrahub` profiles, prints the URLs from `dev_settings()` and returns; `dev.api` and `dev.worker` run their command with `pty=True` in the foreground and write no PID file (FR-021).
- [ ] T042 [P] [US3] In `tests/dev_stack/test_deps_mode.py`: in dependencies mode, `dev.smoke` fails with a message naming `dev.api` when the API URL does not answer and `dev.worker` when no worker is registered with the work pool (FR-022).

### Implementation for User Story 3

- [ ] T043 [US3] In `development/docker-compose.yml`, publish the object store on a loopback port (the value that was `PREVIEW_MINIO_PORT`) so host processes reach it; keep `postgres` on `127.0.0.1:5440`.
- [ ] T044 [US3] In `tasks/dev.py`, implement `dev.deps [--infrahub-network]` (rebuild when stale, start core and `infrahub`, print URLs), `dev.api` and `dev.worker` (foreground host processes with the environment built from `dev_settings()`: database URL on `127.0.0.1:5440`, object store on its loopback port, `PREFECT_API_URL=http://127.0.0.1:4230/api`, Sync API on `127.0.0.1:8030`), and the missing-process check in `dev.smoke`. Make T041 and T042 pass.
- [ ] T045 [US3] Run the quickstart US3 checks, including `dev.smoke` with `dev.worker` stopped.

**Checkpoint**: both development modes reach a passing `dev.smoke`.

---

## Phase 6: User Story 4 – OpsMill engineer opens the Prefect UI on a customer deployment (P2)

**Goal**: a documented override snippet that turns the UI on, on the loopback address only.

**Independent test**: [quickstart.md](quickstart.md#user-story-4-open-the-prefect-ui-on-a-deployment).

### Tests for User Story 4

- [ ] T046 [P] [US4] In `tests/compose/test_compose_contract.py`: `docker compose -f docker-compose.yml -f docs/docs/snippets/prefect-ui.override.yml config` shows `PREFECT_UI_ENABLED == "true"`, `PREFECT_UI_STATIC_DIRECTORY` under `/tmp/infrahub-sync/`, and exactly one `prefect-server` port with `host_ip: 127.0.0.1` (FR-007).
- [ ] T047 [P] [US4] In `tests/compose/test_documentation.py`: `docs/docs/compose-deployment.mdx` includes the snippet file's content unchanged and has a section on merging it into an existing `docker-compose.override.yml`.

### Implementation for User Story 4

- [ ] T048 [US4] Create the snippet `docs/docs/snippets/prefect-ui.override.yml` (values in [data-model.md](data-model.md#override-snippet)) and include it in `docs/docs/compose-deployment.mdx` with steps to open the UI, the credential prompt, how to merge with an existing override file, and how to close the port again. Make T046 and T047 pass.
- [ ] T049 [US4] Add a note to `dev/adr/0016-the-compose-deployment-is-one-tag-pinned-file.md` on the override snippet and on the root file shipped as package data (FR-010).
- [ ] T050 [US4] Run the quickstart US4 checks on a started root deployment.

---

## Phase 7: User Story 5 – CI and demo repositories run end-to-end tests (P2)

**Goal**: `infrahub_sync.testcontainers.TestInfrahubSyncDocker`, with the root file as package
data, used by the CI end-to-end tier.

**Independent test**: [quickstart.md](quickstart.md#user-story-5-end-to-end-test-class).

### Tests for User Story 5

- [ ] T051 [P] [US5] In a new `tests/testcontainers/test_packaging.py` (no Docker): `infrahub_sync/testcontainers/docker-compose.yml` is byte-equal to `docker-compose.yml`; a built wheel (`uv build --wheel`) contains it (FR-023).
- [ ] T052 [P] [US5] In `tests/testcontainers/test_image_selection.py` (no Docker): the default image is `registry.opsmill.io/opsmill/infrahub-sync:<importlib.metadata.version("infrahub-sync")>`; `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` override it (FR-023, [contracts/testcontainers-api.md](contracts/testcontainers-api.md)).
- [ ] T053 [P] [US5] In `tests/test_release_pin.py`: read `uv.lock` and assert that the `prefect` version equals the `prefect-client` version, and that `prefect-client` is pinned by the locked `infrahub-testcontainers` (FR-024, SC-007).
- [ ] T054 [P] [US5] In `tests/test_linter_tasks.py` or a new `tests/test_unit_tier.py`: the `tests.tests-unit` command line contains `-p no:pytest-infrahub-performance-test` and excludes the `compose` marker (FR-025).

### Implementation for User Story 5

- [ ] T055 [US5] Create `infrahub_sync/testcontainers/__init__.py` (exports `TestInfrahubSyncDocker`; raises `ImportError` naming the `testcontainers` extra when `infrahub_testcontainers` is missing) and `infrahub_sync/testcontainers/helpers.py` with `TestInfrahubSyncDocker(TestInfrahubDocker)` and class-scoped fixtures `infrahub_sync_image`, `infrahub_sync_compose` and `sync_api_url`: write a `.env` with test-only credentials, start the packaged root file after `infrahub_app`, connect `sync-worker` to the Infrahub project's `<project>_default` network, collect logs on failure, and stop both projects with volumes (research R8, `infrahub/python_testcontainers/infrahub_testcontainers/helpers.py:21-133`). Make T052 pass.
- [ ] T056 [US5] Add the packaged copy `infrahub_sync/testcontainers/docker-compose.yml` and a check in `tasks/release.py` (`release.update-docker-compose`) that rewrites both files together; or use `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml` to copy the root file at build time. Choose one and make T051 pass.
- [ ] T057 [US5] In `tasks/tests.py`, add `-p no:pytest-infrahub-performance-test` to the unit run (line 22 and the command at line 56). Make T054 pass.
- [ ] T058 [US5] Write an end-to-end test class in `tests/compose/test_testcontainers_fixture.py` (marker `compose`) that inherits `TestInfrahubSyncDocker`, checks that the worker reaches `infrahub-server`, and runs a plan and an apply of a small configuration package.
- [ ] T059 [US5] In `.github/workflows/workflow-nightly-e2e.yml`, make the `compose-suite` job (lines 189-250) install the `testcontainers` extra, set `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` to the image it builds, and run T058 with the rest of `tests/compose`.
- [ ] T060 [P] [US5] Document `infrahub_sync.testcontainers` as experimental in `docs/docs/development/guidelines/testing-tiers.md`, with the image variables and an example class.

---

## Phase 8: Polish and cross-cutting concerns

- [ ] T061 [P] Write a search test in `tests/test_task_names_in_docs.py` over `.github/`, `docs/` and `AGENTS.md` that finds none of the removed names (`invoke docusaurus`, `generate-doc`, `tests-unit`, `tests-integration`, `tests-all`, `check-310` without `tests.`, `preview.`, `netbox.up`, `netbox.down`) (FR-026).
- [ ] T062 Update `.github/scripts/nightly_e2e.py` (lines 70-87, 112-258) and the cleanup steps of `.github/workflows/workflow-nightly-e2e.yml` (lines 158, 166) to `dev.start`, `dev.seed`, `dev.destroy`, `netbox.start`, `netbox.destroy`, and to the new development file for the digest check.
- [ ] T063 [P] Change `invoke docusaurus` to `invoke docs.docusaurus` in `.github/workflows/workflow-linter.yml:184`.
- [ ] T064 [P] Update `AGENTS.md`: the Invoke tasks list, `preview` mentions, and the development stack section; keep `uv run invoke format` and `uv run invoke lint`.
- [ ] T065 [P] Rewrite `docs/docs/development-stack.mdx` for both modes, `--infrahub-network`, `dev.restart`, and the stale-image rebuild, so an engineer with a fresh checkout reaches `dev.smoke` from it alone (FR-027, SC-006); fold or delete `docs/docs/development/guides/preview-stack.md` and update `docs/sidebars.ts` and `tests/test_development_stack_docs.py`.
- [ ] T066 [P] Replace `check-310` with `tests.check-310` in `docs/docs/development/guidelines/testing-tiers.md` (lines 38, 539), and update `docs/docs/development/knowledge/repository-tour.md` for the new modules and the removed files.
- [ ] T067 [P] Amend `dev/adr/0019-prefect-is-an-internal-component-of-sync.md` with D-3: the Prefect API will later be reachable only through the Sync API (one process or a proxy, not decided); keeping the Prefect database password out of `sync-api` is defence in depth.
- [ ] T068 Run `uv run invoke format`, `uv run invoke lint`, `uv run invoke tests.tests-unit`, `uv run ty check .`, and the CLI sanity commands from `AGENTS.md`; fix every finding.
- [ ] T069 Run every section of [quickstart.md](quickstart.md) once more on a clean machine state and record any difference in this file.

---

## Dependencies and execution order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies. T001 must pass before T016 uses the pin.
- **Foundational (Phase 2)**: after T003; blocks every user story.
- **US1 (Phase 3)**: after Phase 2. T024 (measurement) runs last in the phase, after T019
  passes, and uses the T002 procedure.
- **US2 (Phase 4)**: after Phase 2; T031 reuses the Silo service shape from T016.
- **US3 (Phase 5)**: after US2 (it extends `development/docker-compose.yml` and `tasks/dev.py`).
- **US4 (Phase 6)**: after US1 (it overrides the root file).
- **US5 (Phase 7)**: after US1 (it packages the root file) and T003.
- **Polish (Phase 8)**: after the stories it documents; T062 after US2.

### Within each story

Tests first and failing, then Compose and task changes, then the manual quickstart check.

### Parallel opportunities

- Setup: T001 and T002 in parallel (different machines and files).
- Foundational: T004 and T006 in parallel.
- US1 tests T007–T013 in parallel (separate test functions; coordinate edits to
  `tests/compose/test_compose_contract.py`); T021 and T022 in parallel with T014–T020.
- US2 tests T025–T029 in parallel.
- After Phase 2, US1 and US2 can start in parallel; US4 and US5 can run in parallel once US1
  is done.
- Polish: T061 and T063–T067 in parallel.

## Parallel example: User Story 1

```text
T007 contract: no prefecthq/prefect, same image
T009 contract: Prefect password holders
T011 contract: Silo pin in every Compose file
T021 docs: Silo licence in compose-deployment.mdx
T022 changelog fragment
```

## Implementation strategy

### MVP

Phases 1 and 2, then User Story 1. The customer deployment then runs the Prefect server from
the Sync image with the UI closed and Silo as the object store. Stop and check the US1
quickstart before going on.

### Incremental delivery

1. US1: root deployment (MVP).
2. US2 and US3: the development stack in both modes, task names, benchmark runner.
3. US4: Prefect UI override snippet.
4. US5: `TestInfrahubSyncDocker` and the CI end-to-end tier.
5. Polish: workflows, documentation, ADR 0019, full lint and test run.
