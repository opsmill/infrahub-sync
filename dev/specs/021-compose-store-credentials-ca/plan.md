# Implementation Plan: an external object store, every adapter's credentials and a private CA in the Compose deployment

**Branch**: `021-compose-store-credentials-ca` | **Date**: 2026-11-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/021-compose-store-credentials-ca/spec.md`

## Summary

A customer team that deploys Sync with the root `docker-compose.yml` cannot store plans and run files
in its own S3-compatible store, cannot run a package for most bundled adapters because their
credentials never reach `sync-worker`, and cannot trust a private certificate authority. OpsMill
engineers cannot rely on the compose lifecycle tier, and evaluators cannot pull a release by the
digest in its notes.

The approach, from [research.md](research.md):

- Two new `.env` settings for the store endpoint and the addressing style, one setting that scales
  the bundled store to zero, and one S3 client builder shared by bootstrap, the API and the worker
  (R1).
- Bootstrap checks that the bucket exists before creating it, checks that the store refuses a second
  create-only write, and stops when the bundled store would hold an external store's credential
  (R1, R2).
- A fixed list of 24 credential variables on `sync-worker`, built from the shipped examples, the
  adapter pages and the capability registry (R3).
- An optional read-only CA directory on `sync-bootstrap`, `sync-api` and `sync-worker`, turned into
  one bundle at process start (R4).
- Leak checks over command output, container logs and service environments in the lifecycle tier
  (R5); a measured search for the random lifecycle failures and an arm64 leg (R6); a release notes
  section with the digest and the narrowed `cosign` identity (R7); a digest pin for the development
  PostgreSQL image (R8); ADR 0026 (R10).

## Technical Context

**Language/Version**: Python 3.11 to 3.13 for the service code (the Sync image runs 3.13); YAML for
Compose files and GitHub Actions workflows

**Primary Dependencies**: boto3 and botocore 1.43.118 (S3 client, `Config(s3=...)`), certifi
2026.10.19 (public roots), Python `ssl` and `socket`, structlog; no new dependency

**Storage**: S3-compatible object store (bundled Silo or the team's own store); PostgreSQL unchanged

**Testing**: pytest unit tests (`tests/service`), compose contract tests and the compose lifecycle
tier (`tests/compose`, `-m compose`), image tests (`tests/image`), workflow contract tests
(`tests/test_workflow_contracts.py`)

**Target Platform**: Docker Compose 2.24 or later on linux/amd64 and linux/arm64

**Project Type**: CLI and service deployment (Compose)

**Performance Goals**: bootstrap adds at most four S3 requests (one `HeadBucket`, two writes, one
delete) per start

**Constraints**:

- one self-contained operator file and no secret files (ADR 0016)
- read-only root filesystem and tmpfs scratch roots owned by UID 10001
- no value masking added (spec, earlier decisions)
- the packaged copy `infrahub_sync/testcontainers/docker-compose.yml` must equal the root file (R9)

**Scale/Scope**: three Compose files (root, packaged copy, development), three Sync entry points,
nine bundled adapters, two CI workflows

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Note |
| --- | --- | --- |
| I. Read-only and dry-run by default | Pass | No change to `plan`, `sync` or `apply`. Bootstrap writes only its check key and deletes it. |
| II. Sync idempotency and safety | Pass | Bootstrap stays safe to re-run: `HeadBucket` before `CreateBucket`, a new check key each time. 403 and 404 are handled explicitly. |
| III. Adapter symmetry | Pass | No adapter changes. Credential names follow each adapter's documentation page. |
| IV. Type safety | Pass | New functions are typed; `ty` stays clean with no overrides. |
| V. Test discipline | Pass | Unit tests for each new function, contract tests for each Compose property, lifecycle tests for each user story. |
| VI. Security, secrets, and input boundaries | Pass | Each credential reaches only its readers (contract test); private keys are refused; no log line holds a value; fixed failure names. |
| VII. Simplicity | Pass | No new dependency; one client builder replaces two client constructions. |

Post-design re-check: Pass. The one property narrowed is "a Sync service mounts nothing": the CA
mount is a read-only exception on three services, recorded in ADR 0026 (R4).

## Project Structure

### Documentation (this feature)

```text
dev/specs/021-compose-store-credentials-ca/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── environment-variables.md
│   └── bootstrap-and-startup.md
├── checklists/requirements.md
└── tasks.md            # created by speckit-tasks
```

### Source Code (repository root)

```text
docker-compose.yml                              # settings, scale, CA mount, credential list
infrahub_sync/testcontainers/docker-compose.yml # copy of the root file
development/docker-compose.yml                  # same settings, credential list and CA mount; PostgreSQL digest pin
infrahub_sync/service/
├── storage.py        # s3_client_arguments, s3_key_prefix, addressing style (build_s3_client was removed in review)
├── bootstrap.py      # client builder, half-set guard, HeadBucket, create-only check, CA call
├── trust_store.py    # new: install_custom_ca_bundle
├── serve.py          # CA call in main
└── worker.py         # CA call in main
tests/service/
├── test_storage.py
├── test_compose_bootstrap.py
└── test_trust_store.py                         # new
tests/compose/
├── test_compose_contract.py  # scale, settings, credential list, receivers, CA mount, narrowed mount tests
├── test_image_pins.py        # digest over every compose file
├── redaction.py              # leak checks over logs and environments
├── lifecycle.py, conftest.py # reliability fixes found in R6
└── test_external_store.py    # new: lifecycle with an external store, both addressing styles
tests/image/test_prefect_server_image.py        # new: Prefect server under a read-only root
tests/test_workflow_contracts.py                # release notes job, arm64 leg, repeat dispatch
.github/workflows/
├── workflow-nightly-e2e.yml  # arm64 leg, repeat count as a parallel matrix
├── workflow-publish.yml      # record_image job, the only job with contents: write
└── trigger-release.yml       # publish job grants contents: write to the called workflow
docs/docs/
├── compose-deployment.mdx    # external store, credential list, CA, receivers
├── custom-certificates.mdx   # replace "Current limit of the Compose deployment"
└── container-image.mdx       # cosign identity
dev/adr/0026-*.md             # new decision record
changelog/+compose-store-credentials-ca.added.md
```

**Structure Decision**: the existing service package and test layout; one new module
(`trust_store.py`) and three new test files.

## Phases of implementation

1. **Probe** (R1): confirm on Compose 2.24.0 that an interpolated `scale: 0` and
   `depends_on.required: false` behave as on 5.1.2. Stop and record in ADR 0026 if not.
2. **External store** (US1): client builder, settings, scale, guard, bucket check, create-only check,
   docs.
3. **Credentials** (US2): list, receivers, contract tests, docs.
4. **Private CA** (US3): trust store module, mounts, narrowed contract tests, docs.
5. **Leak checks** (FR-016 to FR-018).
6. **Lifecycle reliability** (US4): repeat dispatch, measurement, fixes, arm64 leg.
7. **Release notes and pins** (US5, FR-023, FR-024).
8. **Records**: ADR 0026, news fragment, agent context.

## Dependencies on other work

- Status on 2026-11-13 (T001): specification 010 "PR 2" is implemented on branch
  `010-prefect-pr2-preview-removal`, not yet merged into `feature/v3-develop`. This branch is
  rebased onto it, so the preview stack does not exist here and T053 does not apply. This
  branch merges after that one.

- Specification 010 "PR 2" (spec decision 1). Phase 7's digest test over every compose file cannot
  pass while `development/docker-compose.preview.yml` exists. If PR 2 is not merged when phase 7
  starts, phase 7 keeps the existing strict `xfail` on the preview file
  (`tests/compose/test_image_pins.py:51-52`) and covers every other file.
- Specification 020 is not merged. The documentation of FR-010 names its check as planned until it
  merges.

## Risks

- **arm64 runner.** Whether `ubuntu-24.04-arm` runs the tier is not known (R6). If it cannot, SC-006
  cannot be met in CI and the arm64 runs are made by hand on an arm64 host and recorded in
  `research.md`, as SC-006 allows (clarified on 2026-11-12).
- **A random failure not found in 10 runs.** The 10-run threshold comes from old 013. Causes that do
  not appear in 20 runs are not found by this plan.
- **Half-set store guard and name resolution.** The guard relies on `object-store` resolving only
  when the bundled store runs. A team network that resolves that name otherwise is not known; the
  guard message names the setting to change.

## Open questions for implementation

These test fixtures are not decided by any source. The task that needs each one decides it and
records the choice in `research.md`:

- how a virtual-hosted bucket name resolves inside the test network for the Silo instance that
  stands in for the external store (T016; the store itself was settled on 2026-11-12)
- which server accepts a second create-only write, for the negative check (T017)
- whether a recorded ACI server exists for the lifecycle tier, or which stand-in answers the ACI
  calls (T023)
- how Infrahub and a NetBox stand-in are served over TLS from the test CA (T033)
- how `workflow-nightly-e2e.yml` is started from a branch other than `main` (T039)

## Complexity Tracking

No constitution violation to justify.
