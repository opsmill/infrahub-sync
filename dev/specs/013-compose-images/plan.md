# Implementation Plan: Fewer images in the Compose deployment

**Branch**: `013-compose-images` (planned on `plan-013`) | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/013-compose-images/spec.md`

## Summary

A customer team that deploys V3 from the root `docker-compose.yml` pulls four images, runs a
Prefect server pinned apart from the Prefect client in the Sync image, runs an object store image
that the file calls temporary, cannot pass the credentials of most bundled adapters to the
worker, and cannot add a private CA. This plan:

- runs the Prefect server from the Sync image, as its own service, through a new factory module
  `infrahub_sync.service.prefect_server`, in the root, development and preview compose files
  ([research R1–R3](research.md#r1-how-the-prefect-server-starts-from-the-sync-image));
- stops publishing the Prefect port in the root file, and documents a loopback opt-in through a
  second Compose file named on the command line ([R4](research.md#r4-published-ports-and-the-prefect-opt-in-fr-007-fr-008));
- replaces the Chainguard MinIO build with `pgsty/silo:RELEASE.2026-09-16T00-00-00Z`, pinned by
  digest, which read a volume written by the current build in a planning probe, so no reset is
  needed ([R5](research.md#r5-object-store-image-fr-012-to-fr-016-fr-019));
- passes the 17 credential variables of the bundled adapters to `sync-worker`
  ([R6](research.md#r6-worker-credential-variables-fr-023-fr-025));
- lets the operator name a directory of CA certificates that `sync-worker` and `sync-api` trust
  in addition to the public roots ([R7](research.md#r7-custom-ca-certificates-fr-024-fr-025));
- measures the compose lifecycle tier on linux/amd64 and linux/arm64, records each failure cause
  in this plan, and fixes them ([R9](research.md#r9-compose-lifecycle-tier-reliability-fr-026-sc-005-sc-010));
- appends the image digest and the SBOM files to each GitHub Release
  ([R10](research.md#r10-release-digest-and-sbom-fr-027-sc-011)).

## Technical Context

**Language/Version**: Python 3.13 in the image (`Dockerfile:8`); the package supports 3.10–3.13,
and the Sync service 3.11–3.13.

**Primary Dependencies**: Prefect 3.8.6 and uvicorn 0.52.0 (`service` extra, `uv.lock:2099-2100,3801-3802`),
FastAPI, certifi 2026.2.25, requests 2.33.1, httpx 0.28.1, Infrahub SDK 1.23.2, structlog. No new
dependency.

**Storage**: PostgreSQL 16 (product and Prefect databases); S3-compatible object store
(`object-store-data` volume). No schema change.

**Testing**: pytest. Offline: `tests/compose/test_compose_contract.py` (`docker compose config`
only), `tests/service/`. Docker: `tests/image/` (built image). Opt-in: `-m compose tests/compose`
(lifecycle tier).

**Target Platform**: Linux hosts running Docker Compose 2.24 or later, linux/amd64 and
linux/arm64.

**Project Type**: Python service and CLI, shipped as one container image and one Compose file.

**Performance Goals**: none new. `prefect-server` must reach healthy within its 20-second start
period (planning probe: 8 seconds).

**Constraints**:

- read-only root filesystem and the three tmpfs scratch roots for every Sync service, including
  `prefect-server`;
- the Prefect database credential only in `prefect-server` and `db-bootstrap`;
- only `sync-api` publishes a host port, on `127.0.0.1` by default;
- every image other than the Sync image pinned by tag and digest;
- the deployment stays one file plus `.env` (ADR 0016).

**Scale/Scope**: 3 compose files, 1 `Dockerfile`, 2 new modules (`service/prefect_server.py`,
`service/trust_store.py`), 2 edited entry points (`service/worker.py` `main()`,
`service/serve.py` `main()`), 3 workflows, 4 docs pages, tests in `tests/compose/`,
`tests/image/`, `tests/service/`, `tests/preview/`.

No item is marked NEEDS CLARIFICATION: [research.md](research.md) resolves each question.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (`.specify/memory/constitution.md`) | Before research | After design | Notes |
| --- | --- | --- | --- |
| I. Read-only and dry-run by default | Pass | Pass | No command changes what it writes. The trust setup reads files and writes one bundle on tmpfs. |
| II. Sync idempotency and safety | Pass | Pass | `up` stays safe to repeat. The object store update keeps the data (R5); an invalid CA file stops the process at start, naming the file, instead of failing runs later. |
| III. Adapter symmetry | Pass | Pass | No adapter changes. Credential variables follow the capability registry for every bundled adapter, and a contract test keeps them in step. |
| IV. Type safety and explicit contracts | Pass | Pass | Two small typed modules; contracts in `contracts/`. No `ty` override. |
| V. Test discipline | Pass | Pass | Contract, unit, image and lifecycle tests listed under "Tests". |
| VI. Security, secrets and input boundaries | Pass | Pass | Credentials come from `.env`, never defaulted to a value. The Prefect database password stays out of `sync-api` and `sync-worker`. Credential values are masked by name (`CREDENTIAL`). The CA paths are not secrets and do not match the secret name shapes. The Prefect port is closed by default. |
| VII. Simplicity | Pass | Pass | One image fewer; no new dependency; the factory adds no route. |
| Documentation standard | Pass | Pass | `compose-deployment.mdx`, `custom-certificates.mdx`, `operations/*.mdx` and a changelog fragment are in scope. |
| Logging standard | Pass | Pass | `trust_store` logs with `structlog`: certificate count and bundle path, never certificate content. |

No violation; Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
dev/specs/013-compose-images/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: decisions R1-R12, ADR drafts
├── data-model.md        # Phase 1: deployment entities and the CA bundle
├── quickstart.md        # Phase 1: validation scenarios
└── contracts/
    ├── compose-services.md        # service, image, command, mount and port contract
    ├── environment-variables.md   # .env settings and per-service environment
    └── ports.md                   # published ports per stack and the Prefect opt-in
```

### Source Code (repository root)

```text
Dockerfile                                   # ENV PREFECT_UI_STATIC_DIRECTORY
docker-compose.yml                           # prefect-server from Sync image; no Prefect port;
                                             # pgsty/silo; 17 credential variables; CA mount
development/
├── docker-compose.dev.yml                   # prefect-server from infrahub-sync:dev; pgsty/silo;
│                                            # credential variables; CA mount
├── docker-compose.preview.yml               # sync-prefect built from checkout; pgsty/silo
└── preview.env                              # PREVIEW_PREFECT_IMAGE_* removed
infrahub_sync/service/
├── prefect_server.py                        # NEW: create_prefect_server(), main()
├── trust_store.py                           # NEW: install_custom_ca_bundle()
├── worker.py                                # main(): one call to trust_store (owned by spec 009)
└── serve.py                                 # main(): one call to trust_store
tasks/preview.py                             # drop Prefect image checks; `up --build`
.github/workflows/
├── workflow-nightly-e2e.yml                 # compose-suite on amd64 and arm64; repeat input
├── workflow-publish.yml                     # NEW job record_image
└── trigger-release.yml                      # contents: write for record_image
docs/docs/
├── compose-deployment.mdx
├── custom-certificates.mdx
└── operations/{compose-troubleshooting,day-2-operations,supported-platforms-and-limits}.mdx
tests/
├── compose/test_compose_contract.py         # updated properties (see Tests)
├── compose/test_lifecycle.py, lifecycle.py, conftest.py
├── compose/test_object_store_upgrade.py     # NEW
├── compose/test_prefect_server_upgrade.py   # NEW
├── compose/test_custom_ca.py                # NEW
├── image/test_image_artifact.py             # Prefect server command form
├── service/test_prefect_server.py           # NEW
├── service/test_trust_store.py              # NEW
└── preview/test_preview_configuration.py    # drop Prefect image settings
changelog/+013-compose-images.changed.md     # NEW
```

**Structure Decision**: the existing single-project layout. New runtime code goes into
`infrahub_sync/service/`, beside the other entry points of the image.

## Design

### Prefect server (FR-001 to FR-006)

- `infrahub_sync/service/prefect_server.py`:
    - `create_prefect_server() -> FastAPI` returns `create_app(final=True)` and adds nothing (R1).
    - `main() -> None` calls `uvicorn.run` with `factory=True`, host and port from
      `PREFECT_SERVER_API_HOST` and `PREFECT_SERVER_API_PORT`, keep-alive from
      `PREFECT_SERVER_API_KEEPALIVE_TIMEOUT`, WebSocket ping values from Prefect settings, as
      `prefect server start` does (`prefect/cli/_server_utils.py:341-352`).
- `Dockerfile` runtime `ENV`: `PREFECT_UI_STATIC_DIRECTORY=/var/lib/infrahub-sync/prefect/ui` (R2).
- Root `prefect-server`: Sync image reference, `command: ["python", "-m", "infrahub_sync.service.prefect_server"]`,
  `read_only: true`, `tmpfs: *sync-scratch`, no `ports`, no volume, the same environment as today
  except `PREFECT_SERVER_UI_API_URL` fixed to `127.0.0.1`, the same health check (R3).
- The release task `invoke release.update-docker-compose` and `validate-docker-compose` already
  handle every Sync image line; `prefect-server` joins them (the contract test checks the count).

### Ports (FR-007 to FR-009)

See [contracts/ports.md](contracts/ports.md). The opt-in is `prefect-ui.yml`, written by the
engineer and passed with `-f` (R4). Development keeps `127.0.0.1:4230`, preview keeps
`127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}`.

### Object store (FR-012 to FR-016, FR-019)

`pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46`
in five service definitions; health checks move from `wget` to `curl`; `user: "65532:65532"` on
every long-running object store service. FR-019 is met without a reset: the probe in R5 read a
volume written by the current build. The release notes still state the image change.

### Credentials and CA (FR-023 to FR-025)

- `sync-worker`: the 17 variables of [contracts/environment-variables.md](contracts/environment-variables.md).
- `sync-api`: unchanged credential (`INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` only).
- `sync-worker` and `sync-api`: read-only mount of `${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}`
  at `/etc/infrahub-sync/ca`, and `INFRAHUB_SYNC_CA_DIRECTORY=/etc/infrahub-sync/ca`.
- `infrahub_sync/service/trust_store.py` builds `$TMPDIR/infrahub-sync-ca-bundle.pem` and sets
  `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE` (R7, [data-model.md](data-model.md)).
- `worker.main()` and `serve.main()` call it first.

### Release digest and SBOM (FR-027)

New job `record_image` in `workflow-publish.yml` (R10).

### Lifecycle failure causes (FR-026)

This section is the record FR-026 asks for. At planning time no failure of the tier is recorded
anywhere (R9). The implementation fills the "Confirmed" column from the measurement runs and adds
any cause it finds.

| # | Candidate cause | Evidence at planning | Confirmed | Fix |
| --- | --- | --- | --- | --- |
| C1 | Fixed host ports shared by test modules | code reading, `tests/compose/lifecycle.py:28-33` | to measure | Prefect ports removed; API port chosen free per deployment |
| C2 | Image pulls from three registries in every `up` | code reading, `tests/compose/conftest.py:215` | to measure | session-wide pull with retries; two registries left |
| C3 | One 420-second readiness bound for cold starts | code reading, `tests/compose/lifecycle.py:41` | to measure | per-service start times in diagnostics; bound from measurements |
| C4 | Leftover projects from an aborted session | code reading, `tests/compose/lifecycle.py:98` | to measure | remove labelled leftover projects at session start |
| C5 | No linux/arm64 run | `workflow-nightly-e2e.yml:191` | not applicable | arm64 leg added |

Measurement procedure: dispatch `workflow-nightly-e2e.yml` with a new `compose_repeat` input of
10, on both legs, before and after the changes. A compose lifecycle run is not a tier M
measurement.

## Tests

| Requirement | Test | Tier |
| --- | --- | --- |
| FR-001, FR-010, FR-017 | contract: no `prefecthq/prefect` in any of the three files; Prefect service uses the Sync image (`ALL_SYNC_SERVICES` gains `prefect-server`); preview and development `build` from the checkout | offline |
| FR-002 | contract: Prefect database URL and password only in `prefect-server` and `db-bootstrap` | offline |
| FR-005, FR-017 | contract: `prefect-server` read-only over the scratch roots, no volume, no mount | offline |
| FR-007, SC-003 | contract: root file publishes only `sync-api`; every address loopback; with `-f prefect-ui.yml`, `prefect-server` publishes `127.0.0.1` only | offline |
| FR-011 | contract and preview tests: no `PREVIEW_PREFECT_IMAGE_*` anywhere | offline |
| FR-014, SC-004 | contract: every non-Sync image matches tag plus `sha256` digest in all three files | offline |
| FR-023, SC-008 | contract: `sync-worker` holds the variable for each `credential_setting_paths` entry of every adapter in the capability registry; `sync-api` holds only the Infrahub one; no other service holds any | offline |
| FR-024 | contract: CA mount read-only on `sync-api` and `sync-worker` only; unset gives the named volume; set gives a bind | offline |
| FR-003, FR-004 | `tests/service/test_prefect_server.py`: factory returns `create_app(final=True)`, adds no route; `main()` passes the CLI's uvicorn arguments | unit |
| FR-024 | `tests/service/test_trust_store.py`: empty directory changes nothing; bundle starts with certifi roots; invalid PEM stops with the file name; existing variables are kept | unit |
| FR-018 | image: `python -m infrahub_sync.service.prefect_server` and `uvicorn --factory …` under a read-only root serve `/api/health`, and `/` answers with the UI | Docker |
| FR-018, SC-002, SC-005 | lifecycle: full plan and apply with the new server; server `/api/admin/version` equals worker `prefect.__version__` | compose |
| US1 AS3 | `test_prefect_server_upgrade.py`: start with the previous file (stock image), create runs, switch, history and work pool remain | compose |
| FR-019, SC-006 | `test_object_store_upgrade.py`: write with the Chainguard build, update, `runs show` reads plan and artifacts | compose |
| FR-024, SC-009 | `test_custom_ca.py`: a test HTTPS server with a private CA; run fails without the CA (failure type `tls_certificate_rejected` once spec 009 is merged) and succeeds with it | compose |
| FR-026, SC-010 | nightly dispatch with `compose_repeat: 10` on amd64 and arm64 | CI |
| FR-027, SC-011 | `record_image` job; manual check of the first pre-release after merge | CI |

## Documentation

- `docs/docs/compose-deployment.mdx`: services table (`:50-59`), prerequisites (`:35`), optional
  settings (`:101-121`) with the 17 credential variables and `INFRAHUB_SYNC_CA_CERTIFICATES`,
  surfaces table (`:303-308`), Prefect UI paragraph (`:359-363`) rewritten as the opt-in,
  object store paragraph (`:823-827`), upgrade notes for `INFRAHUB_SYNC_PREFECT_PORT`.
- `docs/docs/custom-certificates.mdx`: replace "Current limit of the Compose deployment" with the
  procedure.
- `docs/docs/operations/compose-troubleshooting.mdx`: port row (`:29`) and a row for an invalid
  CA file.
- `docs/docs/operations/day-2-operations.mdx` ("Change the image", `:58-62`): the Prefect server
  now changes with `VERSION`.
- `docs/docs/quickstart-compose.mdx:35`: only the Sync API port must be free.
- `docs/docs/development/guides/preview-stack.md`: `sync-prefect` is built from the checkout; no
  Prefect image settings.
- `docs/docs/running-a-sync.mdx:235` (owned by spec 009): one sentence that names the Prefect UI
  at `127.0.0.1:4200` on Compose points to the opt-in instead.
- `changelog/+013-compose-images.changed.md`: removed Prefect image, unpublished Prefect port, new
  object store image, credential variables, CA directory.
- ADR 0019 amendment and ADR 0023: text drafted in [research.md R11](research.md#r11-decision-records-drafts-dev-adr-is-not-edited-during-planning);
  written to `dev/adr/` during implementation.

## Coordination with spec 009

Agreed with plan-009 on 2026-10-15 (009's plan records the same agreement, commit `db59e63c` on
`plan-009`).

| Shared item | Owner | Agreement |
| --- | --- | --- |
| `docker-compose.yml`, `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml`, `development/preview.env`, `Dockerfile` | 013 | 009 changes none of them. |
| `infrahub_sync/service/serve.py` | 013 | 013 adds the `trust_store` call in `main()`. 009 does not edit it. |
| `infrahub_sync/service/worker.py` | 009 | 013 adds only the `trust_store` import and one call at the start of `main()`. The second branch to merge rebases. |
| New service modules | each | 013: `service/prefect_server.py`, `service/trust_store.py`. 009: `service/worker_environment.py`, `infrahub_sync/failure_cause.py`. |
| Credential variable names | 013 | `sync-api` keeps only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`; `sync-worker` gets all 17. Unset variables arrive empty; 009 FR-033 treats empty as unset. |
| Masking helpers (`execution.py`, `configuration/credentials.py`, secret-redaction guideline) | 009 | 013's CA variables (`INFRAHUB_SYNC_CA_CERTIFICATES`, `INFRAHUB_SYNC_CA_DIRECTORY`) do not match the secret name shapes; the variables the trust setup sets do not either. |
| CA trust in `sync-api` | 013 | 009 FR-032's schema check uses the existing SDK accessor with no TLS setting of its own, so it gets the trust from 013's setup. |
| `docs/docs/compose-deployment.mdx`, `custom-certificates.mdx`, `operations/day-2-operations.mdx` | 013 | — |
| `docs/docs/operations/compose-troubleshooting.mdx` | 013 | 009 adds one section, "A run failed: read its cause", at the end. |
| `docs/docs/running-a-sync.mdx` | 009 | 013 changes only the sentence at `:235` that names the Prefect UI port on Compose. Agreed by plan-009 after its plan was committed; recorded here only. |
| Changelog fragments | each | 013 uses `+013-`, 009 uses `+009-`. |
| Tests | each | 013: `tests/compose/`, `tests/image/`, and the new files `tests/service/test_prefect_server.py`, `tests/service/test_trust_store.py`. 009 adds nothing to `tests/compose/` or `tests/image/`. |
| Decision records | each | 009: 0020–0022. 013: amendment to 0019, and 0023. |
| CA test assertion | 013 | `test_custom_ca.py` may assert failure type `tls_certificate_rejected` once 009 is merged; before that it asserts that the run fails. |

## Open risks

- The tier's failure causes are not known yet (R9). If the measurement finds a cause outside the
  compose files and tests, FR-026 may need work in another area.
- Silo is maintained by one community publisher; its README promises a release at most a quarter
  apart. A future release that stops reading MinIO volumes would need the reset of FR-019.
- The Silo image runs as root unless the compose file sets the user. A custom compose file that
  drops `user:` runs it as root.
- The factory server writes nothing to its log on a successful start (R1). Startup errors are
  logged. The diagnostics of the lifecycle tier must not rely on a start message.
- The UI copy adds about 57 MB of tmpfs memory to `prefect-server` (R2).
- `record_image` edits a published GitHub Release; if it fails, the release exists without the
  digest section, and the job must be re-run.
- Docker Hub anonymous pull limits apply to two of the three images.

## Agent context

The SPECKIT block of `CLAUDE.md` is not updated by this plan. The lead updates it once after the
plans for specs 009 and 013 merge.

## Complexity Tracking

No constitution violation.
