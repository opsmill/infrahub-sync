# Tasks: an external object store, every adapter's credentials and a private CA in the Compose deployment

**Input**: Design documents from `dev/specs/021-compose-store-credentials-ca/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/environment-variables.md](contracts/environment-variables.md),
[contracts/bootstrap-and-startup.md](contracts/bootstrap-and-startup.md), [quickstart.md](quickstart.md)

**Tests**: included. The constitution (principle V) requires tests for new behavior, and FR-018,
FR-025 and FR-026 require specific tests. Within each story, tests are written first and must fail
before the implementation task that follows them.

**Organization**: one phase per user story. US1 (external store), US2 (adapter credentials) and US3
(private CA) touch the same compose files but different keys, so they can be built in sequence on one
branch and shipped independently. US4 (lifecycle tier) depends on US1 to US3 for its leak checks and
external store runs. US5 (released image) is independent.

**Rule for every task that changes `docker-compose.yml`**: copy the root file over
`infrahub_sync/testcontainers/docker-compose.yml` in the same commit (research R9).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1 to US5)

## Phase 1: Setup

- [X] T001 Confirm that specification 010 "PR 2" is merged into `feature/v3-develop` (`development/docker-compose.preview.yml`, `development/preview.env` and `tasks/preview.py` no longer exist) and rebase this branch onto it; if it is not merged, record in `dev/specs/021-compose-store-credentials-ca/plan.md` under "Dependencies on other work" that T053 keeps the strict `xfail` of `tests/compose/test_image_pins.py:51-52`
- [X] T002 Probe Compose 2.24.0 with the pinned image of `tests/compose/test_compose_minimum.py:43`: a two-service file with `scale: ${VAR:-1}` and `depends_on: {condition: service_healthy, required: false}`, run unset and with `VAR=0`; record the result in `dev/specs/021-compose-store-credentials-ca/research.md` R1; if `0` still creates the container, stop and record the minimum Compose version change for ADR 0026 (T061)

## Phase 2: Foundational

- [X] T003 [P] Add failing unit tests for `build_s3_client` in `tests/service/test_storage.py`: path and virtual addressing reach `botocore.config.Config(s3={"addressing_style": ...})`; an unknown style raises `ValueError` with `INFRAHUB_SYNC_S3_ADDRESSING_STYLE must be path or virtual`; an endpoint with a user name or password raises the `storage.py:198` message; neither message holds the value
- [X] T004 Implement `build_s3_client(values, builder=boto3.client)` in `infrahub_sync/service/storage.py` and make `service_product_projection` (`storage.py:208-231`) use it (research R1 item 2)

## Phase 3: User Story 1 - Store plans and run files in the team's own S3-compatible store (Priority: P1) 🎯 MVP

**Goal**: a team points Sync at its own store from `.env` and leaves the bundled store out.

**Independent Test**: quickstart sections 1 to 3.

### Tests for User Story 1

- [X] T005 [P] [US1] Add failing tests in `tests/service/test_compose_bootstrap.py`: bootstrap builds its client through `build_s3_client`; an invalid endpoint stops with `object-store-endpoint-invalid` and logs the fixed message before any S3 call (FR-005)
- [X] T006 [P] [US1] Add failing tests in `tests/service/test_compose_bootstrap.py` for the settings guard: endpoint host other than `object-store` while `object-store` resolves stops with `bundled-object-store-not-left-out`; endpoint host `object-store` while it does not resolve stops with `object-store-endpoint-not-set`; both before any S3 call (FR-002, contract "Failure names")
- [X] T007 [P] [US1] Add failing tests in `tests/service/test_compose_bootstrap.py` for `converge_bucket`: `HeadBucket` 2xx means present and no `CreateBucket`; 404 then `CreateBucket` with a location constraint when the region is not `us-east-1`; 403 stops with `object-store-access-refused` (FR-003)
- [X] T008 [P] [US1] Add failing tests in `tests/service/test_compose_bootstrap.py` for the create-only check: a refused second write passes; an accepted second write stops with `object-store-unsupported`; the check key is deleted in both cases and on an exception (FR-004)
- [X] T009 [P] [US1] Add failing contract tests in `tests/compose/test_compose_contract.py`: `INFRAHUB_SYNC_S3_ENDPOINT_URL` and `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` reach `sync-bootstrap`, `sync-api` and `sync-worker` with their defaults; with `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` the rendered model scales `object-store-init` and `object-store` to zero and every Sync service still resolves; without it they run (FR-001, FR-002, FR-025)

### Implementation for User Story 1

- [X] T010 [US1] In `infrahub_sync/service/bootstrap.py`, replace the `boto3.client` call of `_converge` (`:146-150`) with `build_s3_client`, map its `ValueError` to the new family `object-store-endpoint-invalid` and log the fixed message (T005)
- [X] T011 [US1] In `infrahub_sync/service/bootstrap.py`, add the settings guard with `socket.getaddrinfo` and the families `bundled-object-store-not-left-out` and `object-store-endpoint-not-set`, run before any S3 request (T006)
- [X] T012 [US1] In `infrahub_sync/service/bootstrap.py`, change `converge_bucket` (`:79-90`) to `HeadBucket` first and add `object-store-access-refused` (T007); reuse commit `05b1fbf5` of branch `013-compose-images`, with Silo in place of MinIO
- [X] T013 [US1] In `infrahub_sync/service/bootstrap.py`, add the create-only check with the key `<prefix>/.infrahub-sync/bootstrap-check/<uuid4>` and the family `object-store-unsupported` (T008)
- [X] T014 [US1] In `docker-compose.yml`, set `INFRAHUB_SYNC_S3_ENDPOINT_URL` and `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` from `.env` in `x-sync-storage`, add `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}` to `object-store-init` and `object-store`, and `required: false` to `sync-bootstrap`'s dependency on `object-store`; copy the file to `infrahub_sync/testcontainers/docker-compose.yml` (T009)
- [X] T015 [US1] Apply the same settings to `development/docker-compose.yml` (`:81` and its object store services)
- [X] T016 [P] [US1] Add a lifecycle test `tests/compose/test_external_store.py` (`-m compose`): start a Silo instance outside the deployment, over TLS from a test CA and with a bucket domain for virtual-hosted requests with a pre-created bucket and a credential that may not create buckets, run the root file with the bundled store left out, plan and apply the NetBox example, and check the run's objects are in the external bucket and no bundled store container exists; parametrize over `path` and `virtual` (SC-001, SC-002)
- [X] T017 [P] [US1] Add a lifecycle test in `tests/compose/test_external_store.py` that a store accepting a second create-only write stops bootstrap with `object-store-unsupported` and leaves no check key (FR-004)
- [X] T018 [US1] Write the section "Use your own S3-compatible store" in `docs/docs/compose-deployment.mdx`: the settings of FR-001 and FR-002 set together, the S3 operation table of research R2, no copy of earlier files, `https` recommended, the tested store, and the Silo paragraph saying a team can use its own store (FR-007)

**Checkpoint**: US1 works on its own: quickstart sections 1 to 3 pass.

## Phase 4: User Story 2 - Pass the credentials of every bundled adapter to the worker (Priority: P1)

**Goal**: every listed credential variable reaches `sync-worker` and no other service.

**Independent Test**: quickstart section 4.

### Tests for User Story 2

- [X] T019 [P] [US2] Add a failing contract test in `tests/compose/test_compose_contract.py` that rebuilds the expected credential names from `BUILTIN_ADAPTER_CAPABILITIES` (`infrahub_sync/configuration/capabilities.py:522-606`), every `INFRAHUB_SYNC_CREDENTIAL_*` name in `examples/*/package.yml` and `docs/docs/adapters/*.mdx`, and the added names of `contracts/environment-variables.md`, and checks that `sync-worker` holds exactly those 24 names, each as `${NAME:-}` (FR-008)
- [X] T020 [P] [US2] Add a failing contract test in `tests/compose/test_compose_contract.py` that checks the receiver list of `contracts/environment-variables.md` for every credential row, for the root and development files; replace the per-name tests at `:1046` and `:1077` with it (FR-009)

### Implementation for User Story 2

- [X] T021 [US2] In `docker-compose.yml`, add the 21 missing credential variables to `sync-worker` (`:294-303`) and update its comment; copy the file to `infrahub_sync/testcontainers/docker-compose.yml` (T019, T020)
- [X] T022 [US2] Add the same 24 variables to `sync-worker` in `development/docker-compose.yml` (`:274-276`)
- [X] T023 [P] [US2] Add a lifecycle test in `tests/compose/test_source_credentials.py` that sets both `CISCO_APIC_*` variables, registers `examples/aci_to_infrahub/package.yml` against a recorded ACI server, and checks that the worker resolves both references and no other service holds either name (SC-003)
- [X] T024 [US2] Update `docs/docs/compose-deployment.mdx` (credential table at `:122-123`): the 24 names by adapter, the rule that a Compose package uses these names, which service receives which credential, and the relation to specification 020 FR-035 (FR-010)
- [X] T025 [P] [US2] Check each adapter page in `docs/docs/adapters/` names, for each credential setting, a variable on the list; add the missing names (`REST_API_USERNAME`, `REST_API_PASSWORD`, `PEERING_MANAGER_USERNAME`, `PEERING_MANAGER_PASSWORD`, `PROMETHEUS_*`, `SLURPIT_TOKEN`) where a page shows that setting

**Checkpoint**: US2 works on its own: quickstart section 4 passes.

## Phase 5: User Story 3 - Trust a private certificate authority (Priority: P1)

**Goal**: `sync-bootstrap`, `sync-api` and `sync-worker` trust a CA bundle named in `.env`.

**Independent Test**: quickstart section 5.

### Tests for User Story 3

- [X] T026 [P] [US3] Add failing unit tests in a new `tests/service/test_trust_store.py` for `install_custom_ca_bundle`: absent directory changes nothing; empty directory logs one info line; certificates produce a bundle with the `certifi` roots first, mode `0600`, and set the four variables; an already set variable is kept and logged by name; a private key stops the process naming the file only; an invalid file stops naming the file and the `ssl` error class; no log line holds file content or a variable value (FR-011 to FR-013)
- [X] T027 [P] [US3] Add failing contract tests in `tests/compose/test_compose_contract.py`: the CA mount `"${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro"` and `INFRAHUB_SYNC_CA_DIRECTORY` reach `sync-bootstrap`, `sync-api` and `sync-worker` only, in the root and development files (FR-011, FR-025)
- [X] T028 [US3] Narrow the four mount tests to writable mounts, with the CA mount as the one read-only exception on the three services: `test_the_api_and_the_worker_share_no_mount_source` (`:171`), `test_a_sync_service_mounts_nothing_at_all` (`:185`), `test_only_postgresql_and_the_object_store_keep_a_named_volume` (`:211`), `test_no_service_mounts_a_host_path` (`:252`) in `tests/compose/test_compose_contract.py` (research R4)

### Implementation for User Story 3

- [X] T029 [US3] Create `infrahub_sync/service/trust_store.py` with `install_custom_ca_bundle()` as research R4 describes (T026)
- [X] T030 [US3] Call `install_custom_ca_bundle()` first in `bootstrap.main()` (`infrahub_sync/service/bootstrap.py:182`), `serve.main()` (`infrahub_sync/service/serve.py:94`) and `worker.main()` (`infrahub_sync/service/worker.py:465`); a refusal exits non-zero with the logged error
- [X] T031 [US3] In `docker-compose.yml`, add the CA mount, the named volume `sync-ca-certificates` and `INFRAHUB_SYNC_CA_DIRECTORY` to `sync-bootstrap`, `sync-api` and `sync-worker`, and update the file header that states the mount properties (`:19-25`); copy the file to `infrahub_sync/testcontainers/docker-compose.yml` (T027, T028)
- [X] T032 [US3] Apply the same mount and setting to `development/docker-compose.yml`
- [ ] T033 [P] [US3] Add a lifecycle test in `tests/compose/test_external_store.py` that serves the external store, a NetBox stand-in and Infrahub over TLS from a test CA, names the CA directory in `.env`, and checks a plan completes with certificate checks on (SC-004); check each bundled adapter's HTTP client honors the bundle, and record any that does not in `research.md` R4
- [X] T034 [US3] Document the CA bundle in `docs/docs/compose-deployment.mdx`: how to add it, UID 10001 permissions on the directory, the restart rule for `sync-api` and `sync-worker` (FR-014)
- [X] T035 [US3] Replace "Current limit of the Compose deployment" (`docs/docs/custom-certificates.mdx:28-35`) with the setting, for the root and development files (FR-015)

**Checkpoint**: US3 works on its own: quickstart section 5 passes.

## Phase 6: User Story 4 - Rely on the compose lifecycle tier as a regression check (Priority: P2)

**Goal**: the tier passes 10 runs in a row on each architecture, and its leak checks find nothing.

**Independent Test**: quickstart section 6.

### Leak checks (FR-016 to FR-018)

- [X] T036 [P] [US4] Extend `tests/compose/redaction.py` with searches of `docker compose logs` per service and of each service's environment (read through `docker inspect` by the test, never printed) for generated credential values; report names only
- [X] T037 [US4] Add a lifecycle test `tests/compose/test_credential_leaks.py` that generates a distinct value of 24 or more characters for each credential the deployment receives (store keys, the 24 credential variables, the Infrahub token), runs the documented commands, and checks the three surfaces of FR-018; the `sync-worker` and `prefect-server` log checks state the six-character boundary of decision 2 in their docstring (SC-005)
- [X] T038 [P] [US4] Check `docs/docs/compose-deployment.mdx` and `docs/docs/operations/*.mdx` for any command that prints interpolated values (`docker compose config` without `--quiet`, shared `docker inspect` output) and fix each one (FR-017); extend `tests/compose/test_documentation.py` to fail on them

### Reliability (FR-019, FR-020)

- [X] T039 [US4] Find out how `workflow-nightly-e2e.yml` can be started from a branch other than `main` (it has only a `workflow_dispatch` trigger and is not on `main`; research R6); if it cannot, add a `pull_request` trigger limited to a label; record the answer in `research.md` R6. Then add to `.github/workflows/workflow-nightly-e2e.yml` a `workflow_dispatch` input for a repeat count and an architecture choice; the `compose-suite` job runs as a matrix over `runner: [ubuntu-22.04, ubuntu-24.04-arm]` and `repeat`, each entry one full tier run without `-x`; add a workflow contract test in `tests/test_workflow_contracts.py`
- [ ] T040 [US4] Dispatch the workflow with a repeat count of 10 on both architectures before any reliability change; record every failure, its job link and its class (C1 to C4 of research R6 or new) in `dev/specs/021-compose-store-credentials-ca/research.md` under "Lifecycle failure causes"; if the arm64 runner cannot run the tier, record it and make the arm64 runs by hand on an arm64 host (SC-006)
- [ ] T041 [US4] Fix or remove each cause found in T040 in `tests/compose/lifecycle.py`, `tests/compose/conftest.py` or the test module concerned, one commit per cause, and record the fix and the test that shows it in `research.md`
- [ ] T042 [US4] Dispatch the workflow again with a repeat count of 10 on both architectures and record the result in `research.md` (SC-006)
- [X] T043 [US4] Make the nightly `compose-suite` job run on both architectures by default

**Checkpoint**: US4 passes: 20 runs pass and the leak checks report nothing.

## Phase 7: User Story 5 - Pull and verify a released image by digest (Priority: P2)

**Goal**: release notes name the digest; `cosign` accepts only the release workflow.

**Independent Test**: quickstart section 7.

- [X] T044 [P] [US5] Add failing workflow contract tests in `tests/test_workflow_contracts.py`: `workflow-publish.yml` has a `record_image` job after `publish_docker_image`, run only when publishing, with `contents: write` at job level while the top level stays `contents: read`; `trigger-release.yml`'s `publish` job grants `contents: write` and `id-token: write` (research R7)
- [X] T045 [US5] Add the `record_image` job to `.github/workflows/workflow-publish.yml`: append a "Container image" section to the GitHub Release body with `registry.opsmill.io/opsmill/infrahub-sync:<version>@<digest>` from the `digest` output of `ci-docker-image.yml`, the `cosign verify` command with the release-tag identity, and the `cosign verify-attestation` commands for the SPDX and CycloneDX SBOMs; print no token (FR-021)
- [X] T046 [US5] Raise the `publish` job of `.github/workflows/trigger-release.yml` (`:17-21`) to `contents: write` and update its comment
- [X] T047 [P] [US5] Change the three `--certificate-identity-regexp` values in `docs/docs/container-image.mdx` (`:136`, `:144`, `:149`) to `'^https://github\.com/opsmill/infrahub-sync/\.github/workflows/ci-docker-image\.yml@refs/tags/'` (FR-022)
- [X] T048 [P] [US5] Add a test in `tests/test_workflow_contracts.py` or `tests/compose/test_documentation.py` that the documented identity and the release notes identity are the same string
- [X] T049 [P] [US5] Add an image test `tests/image/test_prefect_server_image.py` that starts the Sync image's Prefect server with a read-only root filesystem and the tmpfs scratch roots and checks `/api/health` answers (FR-026)

**Checkpoint**: US5 passes on a release candidate (quickstart section 7).

## Phase 8: Image pins (FR-023, FR-024)

- [X] T050 [P] Resolve the `sha256` digest of the multi-architecture index of `postgres:16-alpine` with `docker buildx imagetools inspect`, check it lists linux/amd64 and linux/arm64, and pin it in `development/docker-compose.yml:96` (decision 3)
- [X] T051 Make `test_every_image_other_than_sync_is_named_by_digest` (`tests/compose/test_compose_contract.py:636`) run over the root file, `infrahub_sync/testcontainers/docker-compose.yml` and `development/docker-compose.yml` (FR-023)
- [X] T052 [P] Check `tests/compose/test_image_pins.py` covers the development PostgreSQL pin
- [X] T053 If T001 found specification 010 "PR 2" unmerged, keep the strict `xfail` on the preview file and note it in the PR description (does not apply: this branch is rebased on the PR 2 branch, so the preview file is gone)

## Phase 9: Polish and records

- [X] T054 Write `dev/adr/0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md` from the draft in `research.md` R10, with the result of T002, and add it to `dev/adr/README.md` (FR-027)
- [X] T055 [P] Add the news fragment `changelog/+compose-store-credentials-ca.added.md` naming the external store settings, the setting that leaves the bundled store out, the credential variables, the CA setting and the release notes digest (FR-028)
- [X] T056 [P] Update the operations pages that name the object store or the worker credentials (`docs/docs/operations/*.mdx`) to match
- [X] T057 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .` and `uv run invoke tests.tests-unit`; fix every finding
- [X] T058 Run `uv run invoke docs.generate` and `uv run invoke docs.docusaurus` and fix every failure
- [X] T059 Run the CLI sanity commands of `AGENTS.md` (`--help`, `configs --help`, `runs --help`)
- [X] T060 Run every quickstart section and record the result in `dev/specs/021-compose-store-credentials-ca/quickstart.md`
- [X] T061 Record in ADR 0026 any minimum Compose version change from T002

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 (T001, T002) comes first. T002 can stop the plan.
- Phase 2 (T003, T004) blocks US1 and the CA work of US3 on the object store.
- US1, US2 and US3 can follow in any order after Phase 2. They edit the same compose files, so
  their compose tasks (T014, T021, T031) run one after the other.
- US4 needs US1 to US3: its leak checks cover their credentials and its runs include the external
  store tests.
- US5 and Phase 8 are independent of every other story.
- Phase 9 comes last.

### Within Each User Story

- Tests first, failing, then implementation.
- Service code before compose changes; compose changes before lifecycle tests and documentation.

### Parallel Opportunities

- T003 with T001 and T002.
- T005 to T009 together.
- T019, T020 together; T026, T027 together.
- US5 (T044 to T049) and Phase 8 (T050 to T052) alongside US1 to US3.
- T036 and T038 together.

## Parallel Example: User Story 1

```text
T005, T006, T007, T008  tests in tests/service/test_compose_bootstrap.py (one file: write in sequence, review in parallel)
T009                    contract tests in tests/compose/test_compose_contract.py
T016, T017              lifecycle tests in tests/compose/test_external_store.py, after T014
```

## Implementation Strategy

### First increment

Phase 1, Phase 2 and US1: a team can use its own store. Stop and run quickstart sections 1 to 3.

### Then

US2 and US3 (the other two INFP-692 blockers a customer team meets), then US5 and Phase 8, then US4,
whose measurements need every other change in place, then Phase 9.
