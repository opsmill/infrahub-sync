# Implementation Plan: Fewer images in the Compose deployment

**Branch**: `013-compose-images` (planned on `replan-013`) | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/013-compose-images/spec.md`, revised after the
product owner's decisions of 2026-10-15

## Summary

A customer team that deploys V3 from the root `docker-compose.yml` pulls four images, runs a
Prefect server pinned apart from the Prefect client in the Sync image, can store plans and run
files only in the bundled MinIO, cannot pass the credentials of most bundled adapters to the
worker, and cannot add a private CA. This plan:

- runs the Prefect server from the Sync image, as its own service, through a new factory module
  `infrahub_sync.service.prefect_server`, in the root, development and preview compose files
  ([research R1 to R3](research.md#r1-how-the-prefect-server-starts-from-the-sync-image));
- stops publishing the Prefect port in the root file, and documents a loopback opt-in through a
  second Compose file named on the command line ([R4](research.md#r4-published-ports-and-the-prefect-opt-in-fr-007-fr-008));
- keeps the bundled MinIO build
  `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`
  unchanged, so no reset is needed ([R5](research.md#r5-bundled-object-store-image-fr-012-to-fr-016-fr-019));
- lets the team point Sync at its own S3-compatible store from `.env`, with the addressing style
  it needs, and leave the bundled MinIO out ([R6](research.md#r6-external-s3-compatible-store-fr-028-to-fr-034));
- passes the 17 credential variables of the bundled adapters to `sync-worker` only, and each
  other credential only to the services that read it ([R7](research.md#r7-which-service-receives-which-credential-fr-002-fr-023-fr-029-fr-035));
- lets the operator name a directory of CA certificates that `sync-bootstrap`, `sync-api` and
  `sync-worker` trust in addition to the public roots, for sources, Infrahub and the object store
  ([R8](research.md#r8-custom-ca-certificates-fr-024-fr-025-fr-030));
- keeps credentials out of text by construction, with no value masking
  ([R13](research.md#r13-keeping-credentials-out-of-text-in-this-specification-fr-035));
- measures the compose lifecycle tier on linux/amd64 and linux/arm64, records each failure cause
  in this plan, and fixes them ([R10](research.md#r10-compose-lifecycle-tier-reliability-fr-026-sc-005-sc-010));
- appends the image digest and the SBOM files to each GitHub Release
  ([R11](research.md#r11-release-digest-and-sbom-fr-027-sc-011)).

## Technical Context

**Language/Version**: Python 3.13 in the image (`Dockerfile:8`); the package supports 3.10–3.13,
and the Sync service 3.11–3.13.

**Primary Dependencies**: Prefect 3.8.6 and uvicorn 0.52.0 (`service` extra, `uv.lock:2099-2100,3801-3802`),
boto3 and botocore 1.43.83 (`uv.lock:229-230,243-244`), FastAPI, certifi 2026.2.25, requests
2.33.1, httpx 0.28.1, Infrahub SDK 1.23.2, structlog. No new dependency.

**Storage**: PostgreSQL 16 (product and Prefect databases); an S3-compatible object store, either
the bundled MinIO (`object-store-data` volume) or the team's own store. No schema change.

**Testing**: pytest. Offline: `tests/compose/test_compose_contract.py` (`docker compose config`
only), `tests/service/`. Docker: `tests/image/` (built image). Opt-in: `-m compose tests/compose`
(lifecycle tier).

**Target Platform**: Linux hosts running Docker Compose 2.24 or later, linux/amd64 and
linux/arm64.

**Project Type**: Python service and CLI, shipped as one container image and one Compose file.

**Performance Goals**: none new. `prefect-server` must reach healthy within its 20-second start
period (planning probe: 8 seconds). The create-only check adds three object store requests to
each bootstrap.

**Constraints**:

- read-only root filesystem and the three tmpfs scratch roots for every Sync service, including
  `prefect-server`;
- each credential only in the services that read it (contract table);
- only `sync-api` publishes a host port, on `127.0.0.1` by default;
- every image other than the Sync image pinned by digest; the MinIO digest does not change;
- the deployment stays one file plus `.env` (ADR 0016);
- no value masking added (product owner decision 3).

**Scale/Scope**: 3 compose files, 1 `Dockerfile`, 2 new modules (`service/prefect_server.py`,
`service/trust_store.py`), 2 edited storage modules (`service/storage.py`,
`service/bootstrap.py`), 3 edited entry points (`bootstrap.main()`, `serve.main()`,
`worker.main()`), 3 workflows, 5 docs pages, tests in `tests/compose/`, `tests/image/`,
`tests/service/`, `tests/preview/`.

No item is marked NEEDS CLARIFICATION: [research.md](research.md) resolves each question.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (`.specify/memory/constitution.md`) | Before research | After design | Notes |
| --- | --- | --- | --- |
| I. Read-only and dry-run by default | Pass | Pass | No command changes what it writes to a destination. Bootstrap writes and deletes one check object in Sync's own bucket (R6). |
| II. Sync idempotency and safety | Pass | Pass | `up` stays safe to repeat; bootstrap creates the bucket only when absent. The object store data stays in place (R5). A store that would let an artifact be replaced is refused at bootstrap (FR-032). |
| III. Adapter symmetry | Pass | Pass | No adapter changes. Credential variables follow the capability registry for every bundled adapter, and a contract test keeps them in step. |
| IV. Type safety and explicit contracts | Pass | Pass | Small typed modules; one shared S3 client builder; contracts in `contracts/`. No `ty` override. |
| V. Test discipline | Pass | Pass | Contract, unit, image and lifecycle tests listed under "Tests", including an external store over TLS with both addressing styles. |
| VI. Security, secrets and input boundaries | Pass | Pass | Credentials come from `.env`, never defaulted to a value. Each credential reaches only the services that read it; with an external store, no MinIO container receives it. Credentials are kept out of text by construction (R13): fixed failure names, no provider text, no job variables, a CA setup that logs a count and a path. No masking is added or relied on. The endpoint URL cannot carry a credential. Certificate checks stay on. The Prefect port is closed by default. |
| VII. Simplicity | Pass | Pass | One image fewer; no new dependency; no new image; the factory adds no route; one S3 client builder replaces two. |
| Documentation standard | Pass | Pass | `compose-deployment.mdx`, `custom-certificates.mdx`, `quickstart-compose.mdx`, `operations/*.mdx` and a changelog fragment are in scope. |
| Logging standard | Pass | Pass | `trust_store` logs with `structlog`: certificate count and bundle path. Bootstrap keeps its family-only log line (`bootstrap.py:182-189`). |

No violation; Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
dev/specs/013-compose-images/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: decisions R1-R14, ADR drafts
├── data-model.md        # Phase 1: deployment entities, object store settings, CA bundle
├── quickstart.md        # Phase 1: validation scenarios
└── contracts/
    ├── compose-services.md        # service, image, command, mount and port contract
    ├── environment-variables.md   # .env settings and the receivers of each credential
    ├── object-store.md            # S3 settings and the operations Sync relies on
    └── ports.md                   # published ports per stack and the Prefect opt-in
```

### Source Code (repository root)

```text
Dockerfile                                   # ENV PREFECT_UI_STATIC_DIRECTORY
docker-compose.yml                           # prefect-server from Sync image; no Prefect port;
                                             # MinIO unchanged, scale setting; S3 endpoint and
                                             # addressing style from .env; 17 credential
                                             # variables; CA mount
development/
├── docker-compose.dev.yml                   # prefect-server from infrahub-sync:dev; credential
│                                            # variables; CA mount; S3 endpoint setting
├── docker-compose.preview.yml               # sync-prefect built from checkout; comments only
│                                            # for MinIO
└── preview.env                              # PREVIEW_PREFECT_IMAGE_* removed
infrahub_sync/service/
├── prefect_server.py                        # NEW: create_prefect_server(), main()
├── trust_store.py                           # NEW: install_custom_ca_bundle()
├── storage.py                               # build_s3_client(): endpoint, region, addressing style
├── bootstrap.py                             # shared client; HeadBucket first; create-only check;
│                                            # trust_store call in main()
├── worker.py                                # main(): one call to trust_store (file owned by spec 009)
└── serve.py                                 # main(): one call to trust_store
tasks/preview.py                             # drop Prefect image checks; `up --build`
.github/workflows/
├── workflow-nightly-e2e.yml                 # compose-suite on amd64 and arm64; repeat input
├── workflow-publish.yml                     # NEW job record_image
└── trigger-release.yml                      # contents: write for record_image
docs/docs/
├── compose-deployment.mdx
├── custom-certificates.mdx
├── quickstart-compose.mdx
└── operations/{compose-troubleshooting,day-2-operations,supported-platforms-and-limits}.mdx
tests/
├── compose/test_compose_contract.py         # updated properties (see Tests)
├── compose/test_compose_minimum.py          # scale and optional dependency on Compose 2.24
├── compose/test_lifecycle.py, lifecycle.py, conftest.py
├── compose/test_object_store_upgrade.py     # NEW
├── compose/test_external_object_store.py    # NEW
├── compose/test_prefect_server_upgrade.py   # NEW
├── compose/test_custom_ca.py                # NEW
├── compose/test_credential_leaks.py         # NEW
├── image/test_image_artifact.py             # Prefect server command form
├── service/test_prefect_server.py           # NEW
├── service/test_trust_store.py              # NEW
├── service/test_storage.py                  # client builder and addressing style (existing file)
├── service/test_compose_bootstrap.py        # bucket check and create-only check (existing file)
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
  (`docker-compose.yml:157-173`) except `PREFECT_SERVER_UI_API_URL` fixed to `127.0.0.1`, the same
  health check (R3).
- The release tasks `invoke release.update-docker-compose` and `validate-docker-compose` already
  handle every Sync image line; `prefect-server` joins them (the contract test checks the count).

### Ports (FR-007 to FR-009)

See [contracts/ports.md](contracts/ports.md). The opt-in is `prefect-ui.yml`, written by the
engineer and passed with `-f` (R4). Development keeps `127.0.0.1:4230`, preview keeps
`127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}`.

### Bundled object store (FR-012 to FR-016, FR-019)

The image reference, user, command, volume, owner repair and health checks stay as they are
(R5). Only the comments change. `object-store-init` and `object-store` gain
`scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}`, and `sync-bootstrap`'s dependency on
`object-store` gains `required: false` (R6).

### External S3-compatible store (FR-028 to FR-034)

See [contracts/object-store.md](contracts/object-store.md) and R6.

- `x-sync-storage`: `INFRAHUB_SYNC_S3_ENDPOINT_URL` defaults to `http://object-store:9000` and is
  overridable; new `INFRAHUB_SYNC_S3_ADDRESSING_STYLE`, default `path`.
- `storage.py`: new `build_s3_client(values, builder=boto3.client)`, used by
  `service_product_projection` (`storage.py:224`) and by bootstrap (`bootstrap.py:146-150`).
- `bootstrap.py`:
  - `converge_bucket` (`bootstrap.py:79-90`): `HeadBucket` first; `CreateBucket` only on 404,
    with a location constraint for a region other than `us-east-1`; 403 gives the family
    `object-store-access-refused`.
  - new `check_create_only(store_client, bucket, prefix)`: two create-only writes of one
    check key, the second must be refused; family `object-store-unsupported` otherwise; the key
    is deleted in all cases.
  - new guard: an endpoint host other than `object-store` while `object-store` resolves gives
    the family `bundled-object-store-not-left-out`, before any object store request.
  - `main()` calls `install_custom_ca_bundle()` first.

### Credentials (FR-002, FR-023, FR-035)

- `sync-worker`: the 17 variables of [contracts/environment-variables.md](contracts/environment-variables.md).
- `sync-api`: `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` only, as today.
- No other service gains a credential. `prefect-server` keeps only the Prefect database
  connection string and the Prefect API credential.
- R13 lists how each place that produces text stays free of credentials without masking.

### Custom CA (FR-024, FR-025, FR-030)

- `sync-bootstrap`, `sync-api` and `sync-worker`: read-only mount of
  `${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}` at `/etc/infrahub-sync/ca`, and
  `INFRAHUB_SYNC_CA_DIRECTORY=/etc/infrahub-sync/ca`.
- `infrahub_sync/service/trust_store.py` refuses a private key, checks each file, builds
  `$TMPDIR/infrahub-sync-ca-bundle.pem`, and sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`,
  `AWS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE` (R8, [data-model.md](data-model.md)).
- `bootstrap.main()`, `serve.main()` and `worker.main()` call it first.

### Release digest and SBOM (FR-027)

New job `record_image` in `workflow-publish.yml` (R11).

### Lifecycle failure causes (FR-026)

This section is the record FR-026 asks for. At planning time no failure of the tier is recorded
anywhere (R10). The implementation fills the "Confirmed" column from the measurement runs and
adds any cause it finds.

| # | Candidate cause | Evidence at planning | Confirmed | Fix |
| --- | --- | --- | --- | --- |
| C1 | Fixed host ports shared by test modules | code reading, `tests/compose/lifecycle.py:28-33` | to measure | Prefect ports removed; API port chosen free per deployment |
| C2 | Image pulls from three registries in every `up` | code reading, `tests/compose/conftest.py:215` | to measure | session-wide pull with retries; `prefecthq/prefect` removed |
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
| FR-002, FR-023, FR-035 | contract: for every credential setting, the set of services that receive it equals the table in `contracts/environment-variables.md`; the expected adapter variables come from `credential_setting_paths` of every adapter in the capability registry | offline |
| FR-005, FR-017 | contract: `prefect-server` read-only over the scratch roots, no volume, no mount | offline |
| FR-007, SC-003 | contract: root file publishes only `sync-api`; every address loopback; with `-f prefect-ui.yml`, `prefect-server` publishes `127.0.0.1` only | offline |
| FR-011 | contract and preview tests: no `PREVIEW_PREFECT_IMAGE_*` anywhere | offline |
| FR-012 to FR-014, SC-004 | contract: every non-Sync image carries a `sha256` digest in all three files; the object store services use the digest of FR-012 | offline |
| FR-028, FR-029 | contract: endpoint and addressing style follow `.env`; with `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, `object-store-init` and `object-store` have scale 0 and `sync-bootstrap` still resolves; Compose 2.24 accepts `scale` and `required: false` (`test_compose_minimum.py`) | offline |
| FR-024 | contract: CA mount read-only on `sync-bootstrap`, `sync-api` and `sync-worker` only; unset gives the named volume; set gives a bind | offline |
| FR-003, FR-004 | `tests/service/test_prefect_server.py`: factory returns `create_app(final=True)`, adds no route; `main()` passes the CLI's uvicorn arguments | unit |
| FR-024 | `tests/service/test_trust_store.py`: empty directory changes nothing; bundle starts with certifi roots; invalid PEM stops with the file name; a private key stops the process and the error holds no PEM content; existing variables are kept; the log record holds a count and a path only | unit |
| FR-028, FR-034 | storage unit tests: `build_s3_client` passes `addressing_style`; an invalid style and an endpoint with user name or password raise the fixed messages; bootstrap uses the same builder | unit |
| FR-031, FR-032 | bootstrap unit tests with a fake client: existing bucket and no create permission completes; 404 creates with the location constraint rule; 403 gives `object-store-access-refused`; a store that accepts the second create-only write gives `object-store-unsupported`; the check key is deleted on every path; an external endpoint while `object-store` resolves gives `bundled-object-store-not-left-out` | unit |
| FR-018 | image: `python -m infrahub_sync.service.prefect_server` and `uvicorn --factory …` under a read-only root serve `/api/health`, and `/` answers with the UI | Docker |
| FR-018, SC-002, SC-005 | lifecycle: full plan and apply with the new server and the bundled MinIO; server `/api/admin/version` equals worker `prefect.__version__` | compose |
| US1 AS3 | `test_prefect_server_upgrade.py`: start with the previous file (stock Prefect image), create runs, switch, history and work pool remain | compose |
| FR-019, SC-006 | `test_object_store_upgrade.py`: write with the previous file, update, `runs show` reads plan and artifacts | compose |
| FR-028 to FR-030, SC-012 | `test_external_object_store.py`: a MinIO instance from the same pinned image, outside the deployment's services, over HTTPS with a certificate from a test CA, with a bucket created beforehand and a credential that may not create buckets; bundled store left out; plan and apply with `path`, then with `virtual` (the test store sets `MINIO_DOMAIN`, and a network alias resolves `<bucket>.<host>`) | compose |
| FR-024, SC-009 | `test_custom_ca.py`: a test HTTPS source with a private CA; run fails without the CA (failure type `tls_certificate_rejected` once spec 009 is merged) and succeeds with it | compose |
| FR-035, SC-013 | `test_credential_leaks.py`: generated test values for every credential; search the raw output of each documented command, every container log, and the environment of every non-receiver service; report names found, never values | compose |
| FR-026, SC-010 | nightly dispatch with `compose_repeat: 10` on amd64 and arm64 | CI |
| FR-027, SC-011 | `record_image` job; manual check of the first pre-release after merge | CI |

## Documentation

- `docs/docs/compose-deployment.mdx`:
  - services table (`:50-59`), with `prefect-server` from the Sync image and the object store
    services marked optional
  - prerequisites (`:35`)
  - required credentials (`:80-90`): "Used by" names exact services, and the S3 credential
    reaches `object-store` only when the bundled store runs
  - optional settings (`:101-121`): the 17 credential variables with their receiver,
    `INFRAHUB_SYNC_CA_CERTIFICATES`, `INFRAHUB_SYNC_S3_ENDPOINT_URL`,
    `INFRAHUB_SYNC_S3_ADDRESSING_STYLE`, `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE`
  - new section "Use your own S3-compatible store" (FR-033)
  - surfaces table (`:303-308`), Prefect UI paragraph (`:359-363`) rewritten as the opt-in
  - object store paragraph (`:823-827`) rewritten (FR-016)
  - upgrade notes for `INFRAHUB_SYNC_PREFECT_PORT`
  - a note that `docker compose config` without `--quiet` and `docker inspect` print credential
    values, so their output is not shared (FR-035)
- `docs/docs/custom-certificates.mdx`: replace "Current limit of the Compose deployment"
  (`:28-33`) with the procedure, including the object store.
- `docs/docs/quickstart-compose.mdx:35`: only the Sync API port must be free.
- `docs/docs/operations/compose-troubleshooting.mdx`: port row (`:29`), a row for an invalid or
  private-key CA file, and rows for `object-store-access-refused`, `object-store-unsupported` and
  `bundled-object-store-not-left-out`.
- `docs/docs/operations/day-2-operations.mdx` ("Change the image", `:58-62`): the Prefect server
  now changes with `VERSION`.
- `docs/docs/operations/supported-platforms-and-limits.mdx`: the S3 behaviours Sync relies on, and
  the store OpsMill tests with.
- `docs/docs/development/guides/preview-stack.md`: `sync-prefect` is built from the checkout; no
  Prefect image settings.
- `docs/docs/running-a-sync.mdx:235` (owned by spec 009): one sentence that names the Prefect UI
  at `127.0.0.1:4200` on Compose points to the opt-in instead.
- `changelog/+013-compose-images.changed.md`: removed Prefect image, unpublished Prefect port,
  external object store settings, credential variables, CA directory.
- ADR 0019 amendment and ADR 0023: text drafted in [research.md R12](research.md#r12-decision-records-drafts-dev-adr-is-not-edited-during-planning);
  written to `dev/adr/` during implementation.

## Coordination with spec 009

Agreed with replan-009 on 2026-10-15, replacing the agreement with plan-009 of the first plan.
replan-009 records the same agreement in spec 009's plan.

| Shared item | Owner | Agreement |
| --- | --- | --- |
| `docker-compose.yml`, `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml`, `development/preview.env`, `Dockerfile` | 013 | 009 changes none of them. |
| `infrahub_sync/service/storage.py`, `infrahub_sync/service/bootstrap.py` | 013 | 009 edits neither (confirmed by replan-009). |
| `infrahub_sync/service/serve.py` | 013 | 013 adds the `trust_store` call in `main()`. |
| `infrahub_sync/service/worker.py` | 009 | 013 adds only the `trust_store` import and one call at the start of `main()`. The second branch to merge rebases. |
| New modules | each | 013: `service/prefect_server.py`, `service/trust_store.py`. 009: `infrahub_sync/failure_cause.py` and its own modules. |
| Credential variable names and receivers | 013 | `sync-api` keeps only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, which 009 FR-032's destination schema check needs (`service/config_routes.py:368`). `sync-worker` gets all 17. Unset variables arrive empty; 009 treats empty as unset and FR-033 reports names only. 009 depends only on the `INFRAHUB_SYNC_CREDENTIAL_` prefix. New 013 names (`INFRAHUB_SYNC_S3_ADDRESSING_STYLE`, `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE`, `INFRAHUB_SYNC_CA_CERTIFICATES`) need nothing from 009. |
| Keeping secrets out of text | 009 | The one layer is spec 009's `build_failure_cause()` in `infrahub_sync/failure_cause.py`, called at the remote boundary `_failure_evidence` (`infrahub_sync/service/flow.py:697`). It reads only allowlisted fields. Credential values are resolved only into adapter settings and never formatted into messages. 013 adds no masking and depends on no masking helper. The existing `collect_secret_values` and `redact` (`execution.py`) stay unchanged in the MVP, owned by 009. |
| Object store failures inside a run | 009 | Recorded as failure type `internal_error` with the exception class name and no message. If 013 later raises a typed object store exception, 009 may add it to its allowlist. |
| TLS failures inside a run | 009 | Classified by exception type (`ssl.SSLCertVerificationError` in the cause chain) as `tls_certificate_rejected`, with no message text. `test_custom_ca.py` asserts that type once 009 is merged; before that it asserts that the run fails. |
| CA trust in `sync-api` and `sync-bootstrap` | 013 | 009 FR-032's schema check uses the existing SDK accessor with no TLS setting of its own, so it gets the trust from 013's setup. |
| `docs/docs/compose-deployment.mdx`, `custom-certificates.mdx`, `quickstart-compose.mdx`, `operations/day-2-operations.mdx`, `operations/supported-platforms-and-limits.mdx` | 013 | — |
| `docs/docs/operations/compose-troubleshooting.mdx` | 013 | 009 adds one section, "A run failed: read its cause", at the end. |
| `docs/docs/running-a-sync.mdx` | 009 | 013 changes only the sentence near `:235` that names the Prefect UI port on Compose. |
| Secret-redaction guideline, `execution.py` collection helpers, `configuration/credentials.py` | 009 | 013 edits none of them. |
| `dev/specs/v3-release-scope.md` | 013 for this round | 013 made the edits, including the 009 wording that replan-009 supplied for the MVP blocker list and the #369 paragraph. replan-009 does not edit the page. |
| Changelog fragments | each | 013 uses `+013-`, 009 uses `+009-`. |
| Tests | each | 013: `tests/compose/`, `tests/image/`, `tests/service/test_prefect_server.py`, `tests/service/test_trust_store.py`, `tests/service/test_storage.py`, `tests/service/test_compose_bootstrap.py`. 009 adds nothing to `tests/compose/` or `tests/image/`. |
| Decision records | each | 009: 0020–0022. 013: amendment to 0019, and 0023. |

## Open risks

- The tier's failure causes are not known yet (R10). If the measurement finds a cause outside the
  compose files and tests, FR-026 may need work in another area.
- No source states how long `cgr.dev` keeps the pinned MinIO digest, which no tag names any more
  (R5). If it disappears, every compose file fails to pull the object store, and a new digest
  needs a separate, reviewed change.
- An external store must honour `If-None-Match: *` on `PutObject`. Bootstrap refuses a store that
  does not (FR-032), so such a store cannot be used at all.
- botocore 1.43.83 sends request checksums by default (`configprovider.py:195-206`). A store that
  rejects them fails writes. No source shows such a store among customers; this plan adds no
  setting for it, and the documentation lists the behaviour.
- `scale` and `depends_on.required` must work on Compose 2.24, the documented minimum. The probe
  ran on Compose 5.1.2; `test_compose_minimum.py` must cover 2.24.
- An operator who sets an external endpoint but not `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` gives
  the bundled MinIO the external store's credential before bootstrap stops the deployment
  (R6, item 6). The guard limits how long that state lasts; it cannot prevent it.
- `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` accepts any integer. A value above 1 would start two MinIO
  containers on one volume. The documentation names 0 and 1 only; Compose cannot restrict it.
- Moving from the bundled MinIO to an external store does not move earlier files (spec edge
  case).
- The bundled MinIO uses the Sync S3 credential as its root credential
  (`docker-compose.yml:131-132`), as today. This plan does not change that.
- The factory server writes nothing to its log on a successful start (R1). The diagnostics of the
  lifecycle tier must not rely on a start message.
- The UI copy adds about 57 MB of tmpfs memory to `prefect-server` (R2).
- `record_image` edits a published GitHub Release; if it fails, the release exists without the
  digest section, and the job must be re-run.

## Agent context

The SPECKIT block of `CLAUDE.md` is not updated by this plan. The lead updates it once after the
plans for specs 009 and 013 merge.

## Complexity Tracking

No constitution violation.
