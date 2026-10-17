---
description: "Task list for spec 013: Fewer images in the Compose deployment"
---

# Tasks: Fewer images in the Compose deployment

**Input**: design documents in `dev/specs/013-compose-images/`: [spec.md](spec.md),
[plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md)

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: the specification requires tests (FR-017, FR-018, SC-002, SC-004, SC-010, SC-013), so
each story writes its tests first and checks that they fail before the implementation task.

**Organization**: tasks are grouped by user story. Paths are relative to the repository root.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel with the other [P] tasks of the same phase (different files, no
  dependency on an unfinished task).
- **[Story]**: the user story of spec.md the task serves (US1 to US5).

## Commands used by the tasks

| Purpose | Command |
| --- | --- |
| Full development profile | `uv sync --extra dev --extra prefect --extra service` |
| Offline unit tier | `uv run invoke tests.tests-unit` |
| One offline test file | `uv run pytest <path> -n 0` |
| Image under test | `docker build -t infrahub-sync:compose-test .` |
| Image tier | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m docker tests/image -n 0` |
| Compose lifecycle tier | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -n 0` |
| Format and lint (required before the commit) | `uv run invoke format` then `uv run invoke lint` |
| Docs | `uv run invoke docs.generate` then `uv run invoke docs.docusaurus` |

Rule for every task (FR-035): no code, test, log line, error message or document added by this
specification prints a credential value. Errors name a setting or a file, never a value. No task
adds value masking or calls spec 009's masking helpers.

## Cross-spec dependencies on spec 009

File ownership follows plan.md, "Coordination with spec 009". The `tasks-009` agent confirmed
these rows on 2026-10-15 and records the same dependencies in spec 009's tasks.md. No 013 task
edits `infrahub_sync/failure_cause.py`, the redaction helpers in `infrahub_sync/execution.py`,
`infrahub_sync/configuration/credentials.py` or the secret-redaction guideline.

| Task | File | Owner | Dependency |
| --- | --- | --- | --- |
| T010 | `infrahub_sync/service/worker.py` | 009 | 013 adds only the `trust_store` import and one call at the start of `main()`. Whichever branch merges second rebases and keeps both changes. |
| T075 | `docs/docs/running-a-sync.mdx` | 009 | 013 changes only the sentence near line 235 that names the Prefect UI at `127.0.0.1:4200` on Compose. |
| T031, T045, T054 | `docs/docs/operations/compose-troubleshooting.mdx` | 013 | 009 appends one section, "A run failed: read its cause", at the end of the page. 013 edits only the existing rows and adds rows to the existing table. |
| T049 | `tests/compose/test_custom_ca.py` | 013 | Asserts failure type `tls_certificate_rejected` only once spec 009's `infrahub_sync/failure_cause.py` is merged. Before that, it asserts that the run fails. |
| T007, T039 | `infrahub_sync/service/bootstrap.py` | 013 | 009 does not edit it. Object store failures inside a run stay `internal_error` under 009; 013 adds no typed exception to 009's allowlist. |
| T078, T079, T080 | `dev/adr/` | each | 009 writes ADRs 0020 to 0022; 013 writes the 0019 amendment and ADR 0023. If 009 has not merged, keep the number 0023 anyway. |

---

## Phase 1: Setup

**Purpose**: confirm the starting point before any change.

- [X] T001 Install the full development profile and run `uv run invoke tests.tests-unit` at the starting commit; record the pass count in the pull request notes (no file change; files read: `pyproject.toml`, `uv.lock`)
- [X] T002 Build `infrahub-sync:compose-test` from `Dockerfile` and run the compose lifecycle tier once locally (`INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -n 0`), as described in `docs/docs/development/guidelines/testing-tiers.md` ("Compose (opt-in)"); keep the summary for T004

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: measure the lifecycle tier before any change (FR-026), and add the CA trust setup
that US3 (TLS to an external store) and US5 (custom CA) both need.

**Checkpoint**: no reliability fix (Phase 8) starts before T004 is complete. No US3 or US5 task
starts before T012 is complete.

### Lifecycle measurement (FR-026, measure before fixing)

- [X] T003 Add a `workflow_dispatch` input `compose_repeat` (integer, default `1`) and an architecture matrix to the `compose-suite` job in `.github/workflows/workflow-nightly-e2e.yml` (lines 189-260): legs `ubuntu-22.04` (linux/amd64) and `ubuntu-24.04-arm` (linux/arm64); run `uv run --no-sync pytest -m compose tests/compose --compose-zero-skip` `compose_repeat` times in a shell loop without `-x` across iterations, continue after a failed iteration, print `iteration N: pass|fail` per iteration, upload each iteration's diagnostics as an artifact named `compose-<arch>-<N>`, and fail the job when any iteration failed. Change no test file in this task.
- [X] T004 FR-026 first measurement, before any reliability change: with the user's approval, push a branch that holds only T003, dispatch `gh workflow run workflow-nightly-e2e.yml --ref <branch> -f compose_repeat=10`, and wait for both legs. For each failed iteration, read its diagnostics artifact and classify the cause against C1 to C5, or as a new cause C6, C7 and so on. Write the result in `dev/specs/013-compose-images/plan.md`, section "Lifecycle failure causes": fill the "Confirmed" column (confirmed with the run URL and iteration, or not observed), add a row per new cause with its evidence, and add a line "Measured on <date>: amd64 <passes>/10, arm64 <passes>/10, run <URL>". A compose lifecycle run is not a tier M measurement; do not call it one.

### Custom CA trust store (FR-024, FR-030), shared by US3 and US5

- [X] T005 [P] Write `tests/service/test_trust_store.py` for `install_custom_ca_bundle()` of `infrahub_sync/service/trust_store.py`, with certificates generated in `tmp_path` by the `cryptography` package already in the lock file: (a) absent directory and empty directory change no environment variable and write no file; (b) a directory with one `.pem` and one `.crt` writes `$TMPDIR/infrahub-sync-ca-bundle.pem` with mode `0600`, starting with the content of `certifi.where()`, followed by the two files in name order; files with other suffixes are ignored; (c) `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` and `INFRAHUB_TLS_CA_FILE` are set to that path; a variable that was already set keeps its value; (d) a file with a `-----BEGIN PRIVATE KEY-----` or `-----BEGIN RSA PRIVATE KEY-----` block raises an error whose text names the file and contains none of the file's bytes; (e) a file that `ssl.SSLContext.load_verify_locations` rejects raises an error that names the file and the `ssl` error class only; (f) the `structlog` record (captured with `structlog.testing.capture_logs`) holds only the certificate count and the bundle path
- [X] T006 Implement `infrahub_sync/service/trust_store.py`: `install_custom_ca_bundle(directory: Path | None = None, environ: MutableMapping[str, str] | None = None) -> Path | None`, reading `INFRAHUB_SYNC_CA_DIRECTORY` when `directory` is `None`, and following the rules of `dev/specs/013-compose-images/data-model.md`, "Custom CA directory and CA bundle", and research.md R8 steps 1 to 5. Raise a new `CustomCertificateError(ValueError)` with fixed text (`"CA certificate file <name> holds a private key"`, `"CA certificate file <name> cannot be loaded: <ssl error class>"`). Read only the PEM header lines to detect a private key. Typed, with docstrings, no `ty` override. Make T005 pass.
- [X] T007 Call `install_custom_ca_bundle()` as the first statement of `main()` in `infrahub_sync/service/bootstrap.py` (line 182), before any client is built; on `CustomCertificateError`, print the error text to standard error and return a non-zero code, in the same way `main()` reports its fixed failure families today (lines 182-189)
- [X] T008 [P] Call `install_custom_ca_bundle()` as the first statement of `main()` in `infrahub_sync/service/serve.py` (line 77), before uvicorn starts; on `CustomCertificateError`, write the error text to standard error and exit with code 1
- [X] T009 [P] Add tests that each entry point calls the trust store first and stops on `CustomCertificateError` without a traceback that holds file content: `tests/service/test_compose_bootstrap.py` (bootstrap), `tests/service/test_serve.py` or the existing test module of `serve.main()` (find it with `grep -rln "serve.main\|service.serve import" tests/service`)
- [X] T010 Call `install_custom_ca_bundle()` as the first statement of `main()` in `infrahub_sync/service/worker.py` (line 463), with the same error handling as T008. **Spec 009 owns this file**: add only the import and this call, change nothing else, and record in the pull request that the second branch to merge rebases. Add the matching test to the existing worker entry point test in `tests/service/` (find it with `grep -rln "worker.main\|service.worker import main" tests/service`).
- [X] T011 Write the contract tests for the CA mount first, in `tests/compose/test_compose_contract.py`: narrow `test_the_api_and_the_worker_share_no_mount_source` (line 131), `test_a_sync_service_mounts_nothing_at_all` (line 145), `test_only_postgresql_and_the_object_store_keep_a_named_volume` (line 171), `test_no_service_mounts_a_host_path` (line 210) and `test_no_sync_service_takes_a_filesystem_input` (line 661) to properties 4 and 5 of `dev/specs/013-compose-images/contracts/compose-services.md` (writable mounts only; the one exception is the read-only CA mount); add tests that the mount `/etc/infrahub-sync/ca` is read-only on exactly `sync-bootstrap`, `sync-api` and `sync-worker`, that `INFRAHUB_SYNC_CA_DIRECTORY=/etc/infrahub-sync/ca` reaches exactly those three, that with `INFRAHUB_SYNC_CA_CERTIFICATES` unset the source is the named volume `sync-ca-certificates`, and that with `INFRAHUB_SYNC_CA_CERTIFICATES=./ca-certificates` the source is a bind of that path
- [X] T012 In `docker-compose.yml`, add to `sync-bootstrap`, `sync-api` and `sync-worker` the volume `"${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro"` and the variable `INFRAHUB_SYNC_CA_DIRECTORY: "/etc/infrahub-sync/ca"`; declare the top-level volume `sync-ca-certificates`; rewrite the header comment (lines 19-25) so that it says the Sync services share no writable mount and that the CA directory is the one read-only mount. Make T011 pass with `uv run pytest tests/compose/test_compose_contract.py -n 0`.
- [X] T013 [P] In `development/docker-compose.dev.yml`, add the same CA mount and `INFRAHUB_SYNC_CA_DIRECTORY` to `sync-bootstrap`, `sync-api` and `sync-worker`, and declare the volume `sync-ca-certificates`

**Checkpoint**: `uv run invoke tests.tests-unit` passes; the trust store runs in all three entry points.

---

## Phase 3: User Story 1 - Deploy V3 with the Prefect server from the Sync image (Priority: P1) MVP

**Goal**: the root `docker-compose.yml` runs `prefect-server` from the Sync image through
`infrahub_sync.service.prefect_server`, read-only, holding only the Prefect database and API
credentials.

**Independent test**: start the root compose file with `infrahub-sync:compose-test`, plan and
apply a run, and check that no container uses a `prefecthq/prefect` image (quickstart.md
scenarios 2, 3 and 5).

### Tests for User Story 1 (write first, check that they fail)

- [X] T014 [P] [US1] Write `tests/service/test_prefect_server.py`: `create_prefect_server()` returns the result of `prefect.server.api.server.create_app(final=True)` (patch `create_app` and assert the call arguments); the returned application has exactly the routes of `create_app(final=True)` (compare route paths); `main()` calls `uvicorn.run` with `"infrahub_sync.service.prefect_server:create_prefect_server"`, `factory=True`, host from `PREFECT_SERVER_API_HOST`, port from `PREFECT_SERVER_API_PORT`, `timeout_keep_alive` from `PREFECT_SERVER_API_KEEPALIVE_TIMEOUT`, and the WebSocket ping values that `prefect server start` passes (`prefect/cli/_server_utils.py:341-352` in the installed Prefect 3.8.6)
- [X] T015 [P] [US1] Extend `tests/image/test_image_artifact.py`: the image sets `PREFECT_UI_STATIC_DIRECTORY=/var/lib/infrahub-sync/prefect/ui`; a container started with `python -m infrahub_sync.service.prefect_server`, `--read-only`, and tmpfs mounts owned by UID 10001 on `/var/lib/infrahub-sync`, `/var/lib/infrahub-sync/prefect` and `/tmp/infrahub-sync` answers `GET /api/health` with 200 and `GET /` with the UI page; a second container started with `uvicorn --factory infrahub_sync.service.prefect_server:create_prefect_server --host 0.0.0.0 --port 4200` answers `GET /api/health` with 200. Use the database setup that `tests/image/conftest.py` already provides, or a SQLite file under the `PREFECT_HOME` scratch root.
- [X] T016 [US1] In `tests/compose/test_compose_contract.py`, for the root file: add `prefect-server` to `ALL_SYNC_SERVICES` (line 46) and fix each test that iterates it so that `prefect-server` is checked for the Sync image reference, read-only root and scratch tmpfs; assert that no service image has the repository `prefecthq/prefect`; assert that the `prefect-server` command is `["python", "-m", "infrahub_sync.service.prefect_server"]`; update `test_prefect_keeps_no_state_of_its_own` (line 184) so that `prefect-server` mounts no volume and no bind; update `test_every_sync_service_names_the_registry_image_and_the_release_version` (line 454) and `sync_image_lines()` (line 432) for the fifth Sync image line; assert that the environment of `prefect-server` holds only `PREFECT_SERVER_DATABASE_CONNECTION_URL` and `PREFECT_SERVER_API_AUTH_STRING` among the credential settings of `dev/specs/013-compose-images/contracts/environment-variables.md`, "Receivers of each credential" (US1 scenario 4)
- [X] T017 [P] [US1] In `tests/compose/test_lifecycle.py`, add a check to the full plan-and-apply test (SC-002): the Prefect version that `sync-worker` imports (`python -c "import prefect; print(prefect.__version__)"` through `docker compose exec`) equals the `GET http://prefect-server:4200/api/admin/version` answer read from inside the Compose network; do not rely on a start message in the `prefect-server` log (the factory server logs nothing on a successful start)
- [X] T018 [P] [US1] Write `tests/compose/test_prefect_server_upgrade.py` (marker `compose`) for US1 scenario 3: start a project from the previous release's `docker-compose.yml` (read it with `git show <previous release tag>:docker-compose.yml` into `tmp_path`), register the example package, plan and apply one run; replace the file with the working tree's `docker-compose.yml` and run `up -d --wait` on the same project; assert that the work pool and the deployment still exist and that the earlier flow run is still listed, by reading `POST /api/work_pools/filter`, `/api/deployments/filter` and `/api/flow_runs/filter` from inside the network with the Prefect API credential

### Implementation for User Story 1

- [X] T019 [P] [US1] Create `infrahub_sync/service/prefect_server.py` with `create_prefect_server() -> FastAPI`, which returns `create_app(final=True)` and adds no route and no bootstrap step, and `main() -> None`, which calls `uvicorn.run` as described in T014; add `if __name__ == "__main__": main()`. Typed, with module and function docstrings. Make T014 pass.
- [X] T020 [P] [US1] Add `PREFECT_UI_STATIC_DIRECTORY=/var/lib/infrahub-sync/prefect/ui` to the runtime `ENV` block of `Dockerfile` (lines 101-104, next to `PREFECT_HOME`)
- [X] T021 [US1] In `docker-compose.yml`, change the `prefect-server` service: image `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release>}`, written exactly like the other Sync services so that `invoke release.update-docker-compose` and `release.validate-docker-compose` (`tasks/release.py:114,137`) update it; `command: ["python", "-m", "infrahub_sync.service.prefect_server"]`; `read_only: true`; `tmpfs: *sync-scratch`; no volume; keep its environment (lines 157-173) and its health check (lines 178-187) unchanged; leave `ports` for US2. Make T016 pass.
- [X] T022 [US1] Run `uv run invoke release.validate-docker-compose --version <current version>` and check that `tasks/release.py` counts the new `prefect-server` image line; if `validate_docker_compose` (line 137) expects a fixed number of Sync image lines, update that number in `tasks/release.py`
- [X] T023 [US1] Update the services table of `docs/docs/compose-deployment.mdx` (lines 50-59) so that `prefect-server` runs from the Sync image, and update "Change the image" in `docs/docs/operations/day-2-operations.mdx` (lines 58-62) to say that the Prefect server changes with `VERSION`, and that a custom Sync image must include the `service` extra
- [X] T024 [US1] Build `infrahub-sync:compose-test` and run the image tier for `tests/image/test_image_artifact.py` and the compose tier for `tests/compose/test_lifecycle.py` and `tests/compose/test_prefect_server_upgrade.py`; fix failures in the files of this phase

**Checkpoint**: US1 is complete: three images, `prefect-server` from the Sync image, plan and
apply pass.

---

## Phase 4: User Story 2 - Use the Sync UI, with the Prefect UI closed by default (Priority: P1)

**Goal**: only `sync-api` publishes a host port; an engineer opens the Prefect UI on
`127.0.0.1` with a `prefect-ui.yml` file named with `-f`.

**Independent test**: quickstart.md scenario 6.

### Tests for User Story 2

- [X] T025 [US2] In `tests/compose/test_compose_contract.py`, replace `test_only_the_api_and_prefect_publish_a_host_port` (line 304) with a test that `sync-api` is the only service with `ports`; assert that `PREFECT_SERVER_UI_API_URL` of `prefect-server` is `http://127.0.0.1:4200/api` by default and follows `INFRAHUB_SYNC_PREFECT_PORT`; add a test that writes the `prefect-ui.yml` snippet of `dev/specs/013-compose-images/contracts/ports.md` to `tmp_path`, renders `docker compose -f docker-compose.yml -f <tmp>/prefect-ui.yml config`, and asserts that `prefect-server` then publishes `127.0.0.1:4200` only, whatever `INFRAHUB_SYNC_BIND_ADDRESS` is
- [X] T026 [P] [US2] Add a test to `tests/compose/test_documentation.py` that `docs/docs/compose-deployment.mdx` contains the `prefect-ui.yml` snippet exactly as in `dev/specs/013-compose-images/contracts/ports.md`, and the open and close commands of that contract

### Implementation for User Story 2

- [X] T027 [US2] In `docker-compose.yml`, remove `ports` from `prefect-server` (lines 176-177) and set `PREFECT_SERVER_UI_API_URL: "http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}/api"`; update any comment that describes the Prefect port. Make T025 pass.
- [X] T028 [US2] Remove the fixed Prefect host ports from the compose tests, which now reach Prefect at `http://prefect-server:4200` inside the network: `PREFECT_PORT` in `tests/compose/lifecycle.py` (lines 28-33), the port 4231 in `tests/compose/test_lifecycle.py` (line 55), the port 4251 in `tests/compose/test_configuration_free_startup.py` (line 46), and any `INFRAHUB_SYNC_PREFECT_PORT` setting in `tests/compose/conftest.py`; this removes the Prefect half of candidate cause C1
- [X] T029 [US2] In `docs/docs/compose-deployment.mdx`: change the prerequisites (line 35) so that only the Sync API port must be free; rewrite the surfaces table (lines 303-308) and the Prefect UI paragraph (lines 359-363) as the opt-in of `contracts/ports.md`, with the snippet, the open and close commands, the `127.0.0.1` rule, and an SSH port forward for a remote engineer; list `INFRAHUB_SYNC_PREFECT_PORT` in the optional settings (lines 101-121) as a setting of the opt-in only; add upgrade notes that say `INFRAHUB_SYNC_PREFECT_PORT` has no effect without the opt-in. Make T026 pass.
- [X] T030 [P] [US2] In `docs/docs/quickstart-compose.mdx` (line 35), state that only the Sync API port must be free; remove any other mention of the Prefect host port
- [X] T031 [P] [US2] In `docs/docs/operations/compose-troubleshooting.mdx`, change the row "`up` fails on a port publication" (line 29) so that it names the Sync API port, and the Prefect port only when `prefect-ui.yml` is used

**Checkpoint**: US1 and US2 pass together; `docker compose ps` shows one publisher.

---

## Phase 5: User Story 3 - Store plans and run files in the team's own S3-compatible store, or in the bundled MinIO (Priority: P1)

**Goal**: `.env` settings point Sync at an external S3-compatible store with either addressing
style, `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` leaves MinIO out, bootstrap checks the bucket and
the create-only behaviour, and the bundled MinIO stays at the pinned digest.

**Independent test**: quickstart.md scenarios 7 and 8.

**Depends on**: Phase 2 (CA trust store and mount) for scenario 5 (HTTPS with a private CA).

### Tests for User Story 3

- [X] T032 [P] [US3] In `tests/service/test_storage.py`, test `build_s3_client(values, builder=...)` of `infrahub_sync/service/storage.py` with a recording fake builder: it passes `endpoint_url`, `region_name` and a `botocore.config.Config` whose `s3` is `{"addressing_style": "path"}` by default and `"virtual"` when `INFRAHUB_SYNC_S3_ADDRESSING_STYLE=virtual`; any other style raises `ValueError` whose text names `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` and not the value; an endpoint with a user name or password raises the fixed message of `storage.py:198`; `service_product_projection` (line 224) builds its client through `build_s3_client`
- [X] T033 [P] [US3] In `tests/service/test_compose_bootstrap.py`, with a fake S3 client: existing bucket (`HeadBucket` 200) and a client that would refuse `CreateBucket` completes with no `CreateBucket` call; `HeadBucket` 404 calls `CreateBucket` with `CreateBucketConfiguration={"LocationConstraint": region}` only when the region is set and is not `us-east-1`; `BucketAlreadyOwnedByYou` and `BucketAlreadyExists` still count as present; `HeadBucket` 403 stops with family `object-store-access-refused`; other errors stop with `object-store-unavailable`; `check_create_only` passes when the second write raises `DuplicateArtifactError`, stops with `object-store-unsupported` when it does not, and deletes the key `<prefix>/.infrahub-sync/bootstrap-check/<uuid4>` on every path, including when a write raises; with an endpoint host other than `object-store` while `socket.getaddrinfo("object-store", ...)` succeeds (patched), bootstrap stops with `bundled-object-store-not-left-out` before any S3 call; the bootstrap client comes from `build_s3_client` (so an endpoint with a user name or password is refused, FR-034); the printed output of each failure holds only the family name
- [X] T034 [US3] In `tests/compose/test_compose_contract.py`: `INFRAHUB_SYNC_S3_ENDPOINT_URL` and `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` reach `sync-bootstrap`, `sync-api` and `sync-worker`, default to `http://object-store:9000` and `path`, and follow `.env`; with `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, `object-store-init` and `object-store` have scale 0 and `sync-bootstrap` still resolves with its dependency on `object-store` marked `required: false`; keep `test_object_store_uses_the_pinned_minio_build_and_migrates_existing_data` (line 394) asserting the digest `sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba` on both object store services; `INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY` reach exactly `sync-bootstrap`, `sync-api`, `sync-worker` and `object-store`
- [X] T035 [P] [US3] In `tests/compose/test_compose_minimum.py`, check that Compose 2.24 (the image or binary the file already uses) accepts `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}` and `depends_on.<service>.required: false`, with the setting unset and set to `0`
- [X] T036 [P] [US3] Write `tests/compose/test_object_store_upgrade.py` (marker `compose`, FR-019, SC-006): start a project from the previous release's `docker-compose.yml` (read with `git show <previous release tag>:docker-compose.yml`), plan one run, switch to the working tree's file, `up -d --wait`, and assert that `docker compose run --rm --no-deps -T cli runs show <run id>` reads the plan and its artifacts, with no reset step
- [X] T037 [P] [US3] Write `tests/compose/test_external_object_store.py` (marker `compose`, FR-028 to FR-031, SC-012): generate a test CA and a server certificate in `tmp_path`; start a MinIO container from `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba` outside the deployment's services, over HTTPS with that certificate and `MINIO_DOMAIN` set, on a network the deployment joins, with a network alias that resolves `<bucket>.<host>`; create the bucket and a credential limited to object operations on that bucket beforehand (with `mc` from the same image); set `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, the endpoint, bucket, region, credential and `INFRAHUB_SYNC_CA_CERTIFICATES` pointing at the test CA directory; assert that no `object-store-init` or `object-store` container exists, that a plan and apply complete, that the run's objects are under the prefix in the external bucket, and that `sync-bootstrap`'s log holds no endpoint, bucket or credential value; run once with `INFRAHUB_SYNC_S3_ADDRESSING_STYLE=path` and once with `virtual`; add the negative case without `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, which must stop `sync-bootstrap` with `bundled-object-store-not-left-out`

### Implementation for User Story 3

- [X] T038 [US3] In `infrahub_sync/service/storage.py`, add `build_s3_client(values: Mapping[str, str], builder: Callable[..., Any] = boto3.client) -> Any` that validates the endpoint with `_endpoint` (lines 193-205), reads the region, validates `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` (`path` or `virtual`, default `path`, fixed error text that names the setting), and passes `config=botocore.config.Config(s3={"addressing_style": style})`; use it in `service_product_projection` (line 224). Never set `verify=False`. Make T032 pass.
- [X] T039 [US3] In `infrahub_sync/service/bootstrap.py`: build the client with `build_s3_client` in `_converge` (lines 146-150); add the guard for `bundled-object-store-not-left-out` before any object store request; change `converge_bucket` (lines 79-90) to call `HeadBucket` first and `CreateBucket` only on 404, with the location constraint rule, and map 403 to `object-store-access-refused`; add `check_create_only(store_client, bucket, prefix)` that writes the check key twice through `Boto3S3Client.put(if_absent=True)` and deletes it in a `finally` block, stopping with `object-store-unsupported` when the second write is accepted; add the three new families to the fixed family table (lines 66-77). Keep the family-only output of `main()` (lines 182-189). Make T033 pass.
- [X] T040 [US3] In `docker-compose.yml`: in `x-sync-storage`, set `INFRAHUB_SYNC_S3_ENDPOINT_URL: "${INFRAHUB_SYNC_S3_ENDPOINT_URL:-http://object-store:9000}"` (line 49) and add `INFRAHUB_SYNC_S3_ADDRESSING_STYLE: "${INFRAHUB_SYNC_S3_ADDRESSING_STYLE:-path}"`; add `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}` to `object-store-init` and `object-store`; add `required: false` to `sync-bootstrap`'s dependency on `object-store` (lines 205-206); rewrite the `object-store` comment (line 121) to describe the bundled MinIO build pinned by digest, say that a team can use its own S3-compatible store instead, and that a different bundled server may come in a later release (FR-016). Leave the image, user, command, volume, owner repair and health checks unchanged. Make T034 and T035 pass.
- [X] T041 [P] [US3] In `development/docker-compose.dev.yml`: read `INFRAHUB_SYNC_S3_ENDPOINT_URL` and `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` from the environment in `x-sync-storage` (line 44) with the current values as defaults; rewrite the `object-store` comment (line 85) as in T040; keep the image and the fixed development credentials (lines 42-47)
- [X] T042 [P] [US3] In `development/docker-compose.preview.yml`, rewrite the comments of `sync-minio` and `sync-minio-bootstrap` (lines 32 and 47) as in T040; leave the images and the `mc` call unchanged (FR-015)
- [X] T043 [US3] In `docs/docs/compose-deployment.mdx`: add the section "Use your own S3-compatible store" with the settings table of `dev/specs/013-compose-images/contracts/object-store.md`, the operations and behaviours table of that contract, the need to set `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` together with the endpoint, the statement that moving from the bundled MinIO does not copy earlier plans and run files, the recommendation of `https` for an external store, and the statement that OpsMill tests with the bundled MinIO build and with a MinIO instance outside the deployment (no other product named); list `INFRAHUB_SYNC_S3_ENDPOINT_URL`, `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` and `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` in the optional settings (lines 101-121); mark the object store services as optional in the services table; in the required credentials table (lines 80-90), say that the S3 credential reaches `object-store` only while the bundled store runs; rewrite the paragraph that calls the image "a temporary, pinned MinIO build" (lines 823-827) as required by FR-016. `test_the_page_documents_every_variable_the_compose_file_reads` in `tests/compose/test_documentation.py` must pass.
- [X] T044 [P] [US3] In `docs/docs/operations/supported-platforms-and-limits.mdx`, add the S3 operations and behaviours Sync relies on (link to the section of T043) and the stores OpsMill tests with
- [X] T045 [P] [US3] In `docs/docs/operations/compose-troubleshooting.mdx`, add rows for `object-store-access-refused`, `object-store-unsupported` and `bundled-object-store-not-left-out`, each with its cause and the `.env` or store change that fixes it
- [X] T046 [US3] Build `infrahub-sync:compose-test` and run the compose tier for `tests/compose/test_external_object_store.py` and `tests/compose/test_object_store_upgrade.py`, then the offline tests of this phase; fix failures in the files of this phase

**Checkpoint**: US3 complete: both object store modes work, with both addressing styles.

---

## Phase 6: User Story 5 - Pass every adapter's credentials and a custom CA to the worker (Priority: P1)

**Goal**: `sync-worker` receives the 17 credential variables of the bundled adapters, no other
service receives an adapter credential it does not read, and a CA directory named in `.env` is
trusted for sources, Infrahub and the object store.

**Independent test**: quickstart.md scenario 9.

**Depends on**: Phase 2 (trust store and CA mount).

### Tests for User Story 5

- [X] T047 [US5] In `tests/compose/test_compose_contract.py`, add a receiver test for every credential setting: build the expected adapter variables from `credential_setting_paths` of every adapter in the capability registry (`infrahub_sync/configuration/capabilities.py:510-582`) with the naming rule of spec 012 FR-006 (exception: `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`), assert that the result equals the 17 names of `dev/specs/013-compose-images/contracts/environment-variables.md`, and assert that each credential of that file's "Receivers of each credential" table reaches exactly the listed services; rework `test_only_the_worker_is_given_a_source_credential` (line 823), `test_a_source_credential_is_optional_and_resolves_empty_when_unset` (line 836) and `test_a_declared_source_credential_reaches_only_the_worker` (line 854) to cover all 16 non-Infrahub variables; keep `test_the_destination_credential_reaches_the_api_and_the_worker_and_no_job` (line 691)
- [X] T048 [P] [US5] In `tests/compose/test_source_credentials.py`, add a compose test (marker `compose`, SC-008) that sets a generated value for each of the 17 variables, starts the root file, and in `sync-worker` resolves every `$credential` reference of a package for each bundled adapter through the credential resolver that runs use; assert each reference resolves and print only the identifiers
- [X] T049 [P] [US5] Write `tests/compose/test_custom_ca.py` (marker `compose`, FR-024, SC-009): generate a test CA and a server certificate; start a test HTTPS source (for example a small NetBox-shaped HTTP server container from the Sync image, or the source the other compose tests use, behind TLS) on the deployment network; register a package that reads it; assert that a run fails without `INFRAHUB_SYNC_CA_CERTIFICATES` and succeeds with it; once spec 009 is merged, assert that the failure type is `tls_certificate_rejected`, and until then only that the run fails (see "Cross-spec dependencies"); add the case of a private key file in the CA directory, where `sync-bootstrap`, `sync-api` and `sync-worker` stop and their logs name the file and hold no PEM content; add the case with no setting, where every service starts as before

### Implementation for User Story 5

- [X] T050 [US5] In `docker-compose.yml`, add to `sync-worker` (lines 275-284) the 14 new variables of `contracts/environment-variables.md`, each as `NAME: "${NAME:-}"`, next to the three existing ones; add no adapter credential to any other service. Make T047 pass.
- [X] T051 [P] [US5] In `development/docker-compose.dev.yml`, add the same 14 variables to `sync-worker`
- [X] T052 [US5] In `docs/docs/compose-deployment.mdx`: list the 17 credential variables per bundled adapter with the service that receives each (`sync-worker`; `sync-api` and `sync-worker` for `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`), and say that a package that names another identifier gets no value on Compose; add `INFRAHUB_SYNC_CA_CERTIFICATES` to the optional settings with the absolute or `./` path rule, and link to `custom-certificates.mdx`; add a note that `docker compose config` without `--quiet` and `docker inspect` print credential values, so their output is not shared, and use `docker compose config --quiet` or `--no-interpolate` wherever the page shows the rendered file (FR-035)
- [X] T053 [P] [US5] In `docs/docs/custom-certificates.mdx`, replace "Current limit of the Compose deployment" (lines 28-33) with the procedure: put the PEM files in a host directory, set `INFRAHUB_SYNC_CA_CERTIFICATES`, run `up -d --wait`, read the log line with the certificate count; state that the CA applies to sources, Infrahub and the object store, that certificate checks stay on, and that a private key file stops the services
- [X] T054 [P] [US5] In `docs/docs/operations/compose-troubleshooting.mdx`, add rows for a CA file that cannot be loaded and for a CA file that holds a private key, each naming the error text of `infrahub_sync/service/trust_store.py`
- [X] T055 [US5] Build `infrahub-sync:compose-test` and run the compose tier for `tests/compose/test_custom_ca.py` and `tests/compose/test_source_credentials.py`; fix failures in the files of this phase

**Checkpoint**: US5 complete; every bundled adapter's credentials reach the worker only.

---

## Phase 7: User Story 4 - Develop and preview with the same Prefect server (Priority: P3)

**Goal**: the development and preview stacks run the Prefect server from a Sync image built from
the checkout, and no Prefect image is pinned anywhere.

**Independent test**: quickstart.md scenario 11.

### Tests for User Story 4

- [X] T056 [US4] In `tests/compose/test_compose_contract.py`: assert that none of `docker-compose.yml`, `development/docker-compose.dev.yml` and `development/docker-compose.preview.yml` uses an image whose repository is `prefecthq/prefect` (FR-010, SC-004); assert that every image other than the Sync image in the three files carries `@sha256:<64 hex>` (FR-014), with the existing exception `postgres:16-alpine` without a digest in the development file recorded in the test; assert that development `prefect-server` and preview `sync-prefect` have a `build` from the repository root `Dockerfile` and the command `python -m infrahub_sync.service.prefect_server`; remove the Prefect part of `test_preview_env_tags_match_compose_pin_defaults` (line 529) and of `test_image_overrides_keep_their_matching_digests` (line 554); assert that no file of the repository other than `dev/` and `changelog/` contains `PREVIEW_PREFECT_IMAGE_` (FR-011)
- [X] T057 [P] [US4] In `tests/preview/test_preview_configuration.py`, remove the checks of `PREVIEW_PREFECT_IMAGE_TAG` and `PREVIEW_PREFECT_IMAGE_DIGEST`, and add a check that `sync-prefect` resolves with `image: infrahub-sync:preview`, a `build` with context the repository root, and the published port `127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}`; check the port 4200 references in `tests/preview/test_preview_legacy_state.py` and `tests/preview/test_preview_worker_identity.py` and keep them only if they name the container port

### Implementation for User Story 4

- [X] T058 [US4] In `development/docker-compose.dev.yml`, change `prefect-server` to `image: *sync-image`, `build: *sync-build`, `command: ["python", "-m", "infrahub_sync.service.prefect_server"]`; keep `127.0.0.1:4230:4200` and its environment
- [X] T059 [US4] In `development/docker-compose.preview.yml`, change `sync-prefect` to `image: "infrahub-sync:preview"`, `build: {context: .., dockerfile: Dockerfile}` (the project directory is `development/`), `command: ["python", "-m", "infrahub_sync.service.prefect_server"]`; keep `127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}:4200` and its environment
- [X] T060 [P] [US4] Remove `PREVIEW_PREFECT_IMAGE_TAG` and `PREVIEW_PREFECT_IMAGE_DIGEST` from `development/preview.env` (lines 43-47)
- [X] T061 [US4] In `tasks/preview.py`, remove the Prefect image checks (lines 116-117, 139-140, 155-158) and add `--build` to the `up` call of the `up` task (line 524). Make T056 and T057 pass.
- [X] T062 [P] [US4] In `docs/docs/development/guides/preview-stack.md`, say that `sync-prefect` is built from the checkout and remove every mention of the Prefect image settings
- [X] T063 [US4] Run `uv run invoke start`, check `curl -s http://127.0.0.1:4230/api/health`, run `uv run invoke destroy`; run `uv run invoke preview.up`, check `curl -s http://127.0.0.1:4210/api/health`, and tear the preview down with its documented task in `tasks/preview.py`. ⚠️ Partial on 2026-10-15: host port 8080 was held by an unrelated container, so the preview ran with `PREVIEW_INFRAHUB_PORT=8081` in an untracked `development/preview.local.env`, removed afterwards; both health checks returned 200

**Checkpoint**: all five user stories complete.

---

## Phase 8: Lifecycle reliability and release image (FR-026, FR-027)

**Purpose**: fix the causes recorded in T004, prove SC-010, and publish the image digest and
SBOM with each release. Starts after T004; the fixes touch only test and workflow files.

- [ ] T064 Fix candidate cause C1 (rest): in `tests/compose/lifecycle.py` (lines 28-33), choose a free host port for `INFRAHUB_SYNC_API_PORT` per deployment (bind a socket to `127.0.0.1:0`, read the port, close it) instead of the fixed `8061`, and pass it to every caller that builds the API URL — Not done: product owner decision 2026-10-16, no failure observed in run 38806056523 (20/20).
- [ ] T065 Fix candidate cause C2: in `tests/compose/conftest.py` (line 215), pull every non-Sync image of the root file once per session before the first `up`, with up to three attempts and a growing pause, and run each later `up` with `--pull never` instead of `--quiet-pull`, so that no test deployment pulls again — Not done: product owner decision 2026-10-16, no failure observed in run 38806056523 (20/20).
- [X] T066 Fix candidate cause C3: in `tests/compose/lifecycle.py` (line 41) and `tests/compose/conftest.py` (lines 316-322), record the time each service took to become healthy in the diagnostics that the tier keeps; set the readiness bound from the times measured in T004, and update `tests/compose/test_readiness_bounds.py` to match
- [ ] T067 Fix candidate cause C4: in `tests/compose/conftest.py`, at session start, remove the Compose projects, containers and volumes whose project name carries the suite's prefix (`tests/compose/lifecycle.py:98`), left by an aborted session — Not done: product owner decision 2026-10-16, no failure observed in run 38806056523 (20/20).
- [ ] T068 For each cause that T004 recorded and T064 to T067 do not cover, fix it in the file named in its row of `dev/specs/013-compose-images/plan.md`, "Lifecycle failure causes"; if a cause lies outside the compose files and tests, stop and ask the user before changing another area (plan.md, "Open risks") — Not done: product owner decision 2026-10-16, no failure observed in run 38806056523 (20/20).
- [X] T069 Fill the "Fix" column of `dev/specs/013-compose-images/plan.md`, "Lifecycle failure causes", with the task and file of each fix
- [ ] T070 SC-010: with the user's approval, push the branch and dispatch `gh workflow run workflow-nightly-e2e.yml --ref <branch> -f compose_repeat=10`; both legs must pass ten iterations. Record "Measured after the fixes on <date>: amd64 10/10, arm64 10/10, run <URL>" in `dev/specs/013-compose-images/plan.md`, "Lifecycle failure causes". If an iteration fails, classify it as in T004 and return to T068. — Deferred: run after merge into feature/v3-develop; the sha guard rejects unmerged commits.
- [X] T071 [P] Add a job `record_image` to `.github/workflows/workflow-publish.yml`, after `publish_docker_image` (lines 178-191) and only when `publish` is true: download the SBOM workflow artifact of `ci-docker-image.yml`, append a "Container image" section to the GitHub Release body with `registry.opsmill.io/opsmill/infrahub-sync:<version>@<digest>` (from the `digest` output) and the `cosign verify-attestation` command, and upload `infrahub-sync-sbom.spdx.json` and `infrahub-sync-sbom.cdx.json` as release assets with `gh release upload`; use only `GITHUB_TOKEN` and print no token; give the job `contents: write`
- [X] T072 Change the permission given to the called publish workflow in `.github/workflows/trigger-release.yml` from `contents: read` to `contents: write`, so that `record_image` can edit the release
- [X] T073 [P] Add to `RELEASING.md` one paragraph that says the `record_image` job appends the image digest and the SBOM files to each GitHub Release, and that a failed `record_image` job is re-run from the Actions page

---

## Phase 9: Polish and cross-cutting concerns

- [X] T074 Write `tests/compose/test_credential_leaks.py` (marker `compose`, FR-035, SC-013): start the root file with generated values for every credential setting (the 17 adapter variables, the S3 pair, the PostgreSQL, product and Prefect passwords, the Prefect auth string, the service bearer tokens, the CLI token); run each command documented in `docs/docs/compose-deployment.mdx`, `docs/docs/quickstart-compose.mdx` and `docs/docs/operations/*.mdx` that the test can run, and a plan and apply; search the raw output of each command, every container log, and the environment of each service that `contracts/environment-variables.md` does not list as a receiver, for each generated value; report the setting names found, never the values (follow `Captured.unredacted` in `tests/compose/redaction.py:26-30`); assert zero findings. Do not extend `tests/compose/redaction.py`.
- [X] T075 [P] Check `docs/docs/running-a-sync.mdx` near line 235 and change only the sentence that names the Prefect UI at `127.0.0.1:4200` on Compose so that it points to the opt-in in `docs/docs/compose-deployment.mdx`. **Spec 009 owns this page**; change nothing else.
- [X] T076 [P] Search `docs/docs/` for the remaining mentions of the Prefect host port, the `prefecthq/prefect` image and "temporary" MinIO build (`grep -rn "4200\|prefecthq\|temporary" docs/docs --include=*.mdx --include=*.md`) and correct each one that describes the root deployment
- [X] T077 Add the changelog fragment `changelog/+013-compose-images.changed.md` with `uv run --extra dev towncrier create -c "<text>" +013-compose-images.changed.md`. The text names: the Prefect server now runs from the Sync image and the `prefecthq/prefect` image is no longer pulled; the Prefect port is no longer published and `prefect-ui.yml` opens it on loopback; `INFRAHUB_SYNC_S3_ENDPOINT_URL`, `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` and `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` select an external S3-compatible store; the 14 new `INFRAHUB_SYNC_CREDENTIAL_*` variables reach `sync-worker`; `INFRAHUB_SYNC_CA_CERTIFICATES` names a CA directory
- [X] T078 Append the amendment to `dev/adr/0019-prefect-is-an-internal-component-of-sync.md` under the heading "## Amendment (2026-10): the Prefect server runs from the Sync image", with the text drafted in `dev/specs/013-compose-images/research.md`, section R12, "Amendment to ADR 0019", without the quote markers; cite no internal source (no Slack, Jira or session link)
- [X] T079 Write `dev/adr/0023-the-compose-deployment-takes-an-external-object-store-and-custom-ca-certificates-from-env.md` with the text drafted in `dev/specs/013-compose-images/research.md`, section R12, "ADR 0023 (draft)", without the quote markers and with status `Accepted` once the user approves the pull request (keep `Proposed` until then); cite no internal source. The product owner accepted ADR 0023 on 2026-10-17, so its status is `Accepted`
- [X] T080 Add ADR 0023 to "Current records" in `dev/adr/README.md`, in number order, with a one-line summary in the style of the other entries, and add "amended 2026-10" to the 0019 entry
- [X] T081 Run `uv run invoke docs.generate` and `uv run invoke docs.docusaurus` (after `cd docs && pnpm install --frozen-lockfile` once), and fix every broken link or build error in `docs/docs/`
- [X] T082 Run `uv run invoke format` then `uv run invoke lint` (rumdl, ruff, pylint, yamllint, ty) and fix every finding in the files changed by this specification; add no `[[tool.ty.overrides]]` block to `pyproject.toml`
- [X] T083 Run `uv run invoke tests.tests-unit`, the image tier and the full compose tier (`INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -n 0`) on an image built from the branch; all pass
- [X] T084 Run the CLI sanity checks `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help` and `uv run infrahub-sync runs --help`, then walk through `dev/specs/013-compose-images/quickstart.md` scenarios 1 to 11 and note any step that differs from the result described there
- [ ] T085 After merge, on the first pre-release that runs `record_image` (SC-011): run the three commands of `dev/specs/013-compose-images/quickstart.md`, scenario 13, and record the result in the pull request of the release

---

## Dependencies and execution order

### Phase dependencies

- Phase 1 (Setup): no dependency.
- Phase 2 (Foundational): after Phase 1. T004 needs T003 pushed on a branch. T006 needs T005;
  T007, T008 and T010 need T006; T012 needs T011.
- Phase 3 (US1): after Phase 2. No dependency on another story.
- Phase 4 (US2): after T021 of US1 (both edit `prefect-server` in `docker-compose.yml`).
- Phase 5 (US3): after Phase 2. Independent of US1 and US2 apart from shared files edited in
  sequence (`docker-compose.yml`, `tests/compose/test_compose_contract.py`,
  `docs/docs/compose-deployment.mdx`).
- Phase 6 (US5): after Phase 2. Same shared files as US3.
- Phase 7 (US4): after T019 of US1 (the factory module). T056 asserts the three files, so it
  passes only after T021, T058 and T059.
- Phase 8: after T004. T064 to T067 can start once T004 has recorded the causes; T070 after all
  stories and T064 to T069. T071 to T073 have no dependency on the stories.
- Phase 9: after all stories. T074 needs US3 and US5 (it reads every receiver). T078 and T079
  can be written at any time after Phase 2.

### Story completion order

```text
Setup -> Foundational (T003-T004 measure; T005-T013 trust store)
  -> US1 (P1) -> US2 (P1)
  -> US3 (P1)            (after Foundational)
  -> US5 (P1)            (after Foundational)
  -> US4 (P3)            (after T019)
  -> Phase 8 (after T004; T070 last)
  -> Polish
```

### Files edited by more than one story (edit in sequence, never in parallel)

- `docker-compose.yml`: T012, T021, T027, T040, T050
- `development/docker-compose.dev.yml`: T013, T041, T051, T058
- `tests/compose/test_compose_contract.py`: T011, T016, T025, T034, T047, T056
- `docs/docs/compose-deployment.mdx`: T023, T029, T043, T052
- `docs/docs/operations/compose-troubleshooting.mdx`: T031, T045, T054
- `tests/compose/lifecycle.py`: T028, T064, T066
- `tests/compose/conftest.py`: T028, T065, T066, T067

## Parallel opportunities

- Phase 2: T005 alongside T003; T008 alongside T007; T013 alongside T012.
- US1: T014, T015, T017 and T018 together; then T019 and T020 together.
- US2: T026 alongside T025; T030 and T031 alongside T029.
- US3: T032, T033, T035, T036 and T037 together; T041, T042, T044 and T045 alongside T040 and
  T043.
- US5: T048 and T049 alongside T047; T051, T053 and T054 alongside T050 and T052.
- US4: T057 alongside T056; T060 and T062 alongside T058 and T059.
- Across stories: once Phase 2 is done, US1 and US3 code tasks (`prefect_server.py`,
  `storage.py`, `bootstrap.py`) can run at the same time; only the shared files above need
  sequence.
- Phase 8: T071 to T073 can run at any time, alongside the stories.

### Example: US1 tests together

```text
T014 tests/service/test_prefect_server.py
T015 tests/image/test_image_artifact.py
T017 tests/compose/test_lifecycle.py
T018 tests/compose/test_prefect_server_upgrade.py
```

### Example: US3 tests together

```text
T032 tests/service/test_storage.py
T033 tests/service/test_compose_bootstrap.py
T035 tests/compose/test_compose_minimum.py
T036 tests/compose/test_object_store_upgrade.py
T037 tests/compose/test_external_object_store.py
```

## Implementation strategy

### First increment

1. Phase 1, then T003 and T004: measure the lifecycle tier on both architectures before any
   change, and record the result in plan.md.
2. The rest of Phase 2 (trust store and CA mount).
3. Phase 3 (US1). Stop and validate: three images, plan and apply pass, `prefect-server` from the
   Sync image (quickstart.md scenarios 1, 2, 3 and 5).

### Then, in order

4. US2 (closed Prefect port and opt-in).
5. US3 and US5, which share Phase 2 and can be worked at the same time apart from the shared
   files.
6. US4 (development and preview stacks).
7. Phase 8 (reliability fixes, SC-010 run, `record_image`).
8. Phase 9 (leak test, ADRs, changelog, docs build, format, lint, full tiers).

Every requirement of this specification belongs to the MVP release
([V3 release scope](../v3-release-scope.md)), so the release needs all phases, not only the first
increment.
