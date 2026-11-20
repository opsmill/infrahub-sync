# Implementation Plan: Stored credentials managed through the Sync API

**Branch**: `027-stored-credentials` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/027-stored-credentials/spec.md`

## Summary

The Sync administrator creates a credential once and every package that names it uses it, on any
worker, with no restart:

- **The Sync API stores credentials.** Four new tables hold credentials, their encrypted revisions,
  the credentials each version names, and the revisions each stage execution used
  ([data-model.md](data-model.md)). Values are encrypted with AES-256-GCM; only `sync-api` holds the key
  (research R3).
- **A package names one credential per block** with `credential: <name>` in the source and destination
  blocks. The `credentials` block, `$credential` references and the `env` provider are
  removed (R1, R2).
- **Workers get values from one route.** The environment defines a `worker` account and its token,
  shared by all workers. The route releases values only for a claimed, unfinished stage execution, only
  for the blocks that stage needs, and records each release (R4, R5).
- **The Sync API uses a value in its own process** to read the destination schema when it validates a
  package, and returns it nowhere (FR-004).
- **Registration and run start check** that each named credential exists and has the fields its block
  needs (R7). The worker variable publishing of ADR 0039 is removed.
- **New CLI group and web page** let every account list and view credentials, and let administrators
  create, change and delete them ([contracts](contracts/)).

## Delivery order

| Step | User story | Content | Depends on |
| --- | --- | --- | --- |
| 1 | US1 (P1) | Encryption, store tables, the `worker` account, credential routes, worker route, worker client, package format change, removal of `env` and of the worker variable publishing, registration and run-start checks, run records, CLI group, the conversion output (because the removal of `env` breaks the old one), deployment files (Compose, development stack, testcontainers), the shared test helper and the moved tests, ADR 0054, constitution 1.3.0, documentation | none |
| 2 | US2 (P2) | `credentials_used` on the run detail route and in `runs show`, the plan-then-apply revision test | step 1 |
| 3 | US4 (P3) | Deletion check and its refusal details, CLI `delete` | step 1 |
| 4 | US3 (P3) | The conversion report that lists the credentials to create, and the registration message for a converted package | step 1 |
| 5 | US1, US3 | Web interface: Credentials page, run details section; the drop-area offer if spec 025 is merged | steps 1 to 4 |
| 6 | — | Helm chart pull request in `opsmill/infrahub-helm` | step 1 |

Step 1 is large because the format change, the worker route and the removal of `env` cannot be split:
between them, no registered package can run and the tests fail (research R11). Its tasks are ordered so
each phase keeps `invoke tests.tests-unit` green (critique X1, E11):

1. Add the new path beside the old one: encryption, tables, the worker account, routes, the worker
   client, the `credential:` key accepted next to `$credential`.
2. Move the tests, the examples, the development tasks and the conversion to the new path, one test
   folder per commit.
3. Remove the old path (`env`, the `credentials` block, `$credential`, the worker variable publishing),
   change the deployment files and the tests that check them, and write the documentation, the decision
   record and the constitution amendment.

## Technical Context

**Language/Version**: Python 3.11–3.13 for the service and the worker; the CLI and client also run on
Python 3.10. TypeScript for `frontend/`.

**Primary Dependencies**: FastAPI, Pydantic, Typer, httpx, psycopg. New: `cryptography` in the
`service` extra only (`AESGCM`), with a range pin, already present indirectly in `uv.lock`, so the
image does not grow (critique E13). React, TanStack Router,
`openapi-fetch` in `frontend/`.

**Storage**: the product store on SQLite (tests) and PostgreSQL (service). Four new tables in `_SCHEMA`
with `CREATE TABLE IF NOT EXISTS`; no column added to an existing table; `worker_environments` leaves
`_SCHEMA`.

**Testing**: pytest offline tier (`uv run invoke tests.tests-unit`) on both store dialects where the
existing store tests run on both. About 80 existing test files set `INFRAHUB_SYNC_CREDENTIAL_*` or use
`$credential` (research report list); they move to one shared helper
`tests/helpers/stored_credentials.py` that creates credentials in the store or through the API. New
tests:

- the marker search over responses, CLI output, logs, run records and a database dump, in one test
  (SC-004, critique P6);
- an error that holds a released or decrypted value, raised at each point between release and use, in
  the worker and in the Sync API's schema read: the value is redacted (critique E6);
- the worker client's timeout and its three retries on transient errors, and no retry on a refusal
  (critique E3);
- the worker account refused on every route outside `/worker/`, parametrized over the app's routes;
- the worker route refusals (finished run, other run, unnamed credential, administrator token);
- deletion refused by a latest version and by a schedule, allowed for an older version;
- registration and run-start refusals for a missing credential and a missing field;
- a ciphertext copied to another row fails to decrypt.

Compose, development-stack and testcontainers tiers create credentials through the API. The frontend
has no test runner: `pnpm check`, `pnpm typecheck`, `pnpm build` and [quickstart.md](quickstart.md).

**Target Platform**: Linux server (Sync API and workers); CLI on any platform; a current browser.

**Project Type**: web service with workers, a CLI client and a web interface.

**Performance Goals**: one extra HTTP request per stage execution, from the worker to the Sync API, with
one decryption per named credential (at most three). Registration adds one indexed read per named
credential.

**Constraints**:

- The API stays `v3-unstable`.
- No value in a response other than the worker route, a CLI output, a log line, an audit event, a
  mutation receipt or a run record ([`secret-redaction.md`](../../../docs/docs/development/guidelines/secret-redaction.md)).
- The base install stays free of the `service` extra and of `cryptography`. The worker client uses
  `httpx`, a base dependency.
- The vendored `opsmill_prefect_extras` stays byte-identical.
- The direct Prefect flow (`infrahub_sync/orchestration/`) does not change: it runs v2 files with its
  own environment.

**Scale/Scope**: tens to a few hundred credentials per deployment. About 25 source files, 12 new test
files, about 80 test files moved to the helper, 3 Compose files, 1 Helm chart (other repository),
about 15 documentation pages, 1 new decision record and 3 notes on existing ones, the constitution and
`AGENTS.md`, about 8 files in `frontend/`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only by default | Credential commands change Sync's own records, not a system of record. `credentials delete` asks for confirmation. Plan, verify and the read commands are unchanged. | Pass |
| II. Idempotency and safety | Mutating credential routes take `Idempotency-Key`. A worker retry of the same execution gets the same answer and replaces its `run_credential_uses` row. The worker's call has a 10-second timeout and three retries on transient errors (research R6). A missing value fails the run before the source is read, with a named cause. | Pass |
| III. Adapter symmetry | No adapter code changes. The map from stored fields to settings sits next to the existing `credential_setting_paths`, one entry per adapter. | Pass |
| IV. Type safety | New resources are Pydantic models; new errors are specific exceptions (`CredentialUnavailableError`, `CredentialInUseError`, `CredentialDecryptionError`) and API codes. | Pass |
| V. Test discipline | Unit tests for every requirement; parametrized refusals; marker search. | Pass |
| VI. Secrets (redaction) | Released and decrypted values join the process's redaction list at the moment of release or decryption, not later (research R6). | Pass |
| VI. Secrets | Today: "Credentials MUST come from environment variables or a secret manager." Values now come from the Sync database, encrypted, and are released only to the worker account. This is a deviation until the amendment (FR-026, constitution 1.3.0) lands in the same change. | Deviation, justified |
| VII. Simplicity | One mechanism replaces two (`env` is removed). The worker account reuses the account and token tables and the resolver. One new dependency, justified in R3. | Pass |

Post-design re-check: same result. The only deviation is principle VI, resolved by the amendment.

## Project Structure

### Documentation (this feature)

```text
dev/specs/027-stored-credentials/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── cli.md
│   ├── environment.md
│   └── web-interface.md
├── checklists/requirements.md
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── configuration/
│   ├── models.py               # `credential` on adapter blocks; `credentials` and
│   │                           #   `$credential` refused; CredentialReference* removed
│   ├── credentials.py          # env provider, prefix, resolve_reference removed; field map (R2)
│   ├── capabilities.py         # stored-field map per adapter; schema read takes resolved values
│   ├── runtime.py              # resolve_runtime_instance fills settings from released values
│   ├── validation.py           # credential-not-used finding; removed reference findings
│   ├── conversion.py           # writes `credential:`; report lists credentials to create
│   └── worker_credentials.py   # removed
├── product_store/
│   ├── store.py                # four tables in _SCHEMA; worker_environments removed; facade methods
│   ├── stored_credentials.py   # new: credential, revision, use and in-use statements
│   ├── configs.py              # credential findings at registration; worker findings removed
│   └── models.py               # StoredCredential, CredentialUse; WorkerEnvironment removed
├── service/
│   ├── credential_crypto.py    # new: AES-GCM encrypt and decrypt, key loading, key identifier
│   ├── credential_routes.py    # new: /credentials and /worker routes
│   ├── credential_actions.py   # new: rights, checks, refusals, audit events of each route
│   ├── credential_client.py    # new: the worker's call to the worker route
│   ├── auth.py                 # apply the worker account; worker-only rule in the authenticator
│   ├── app.py                  # mount the routes; new error codes
│   ├── serve.py                # load the key and the worker token at start
│   ├── settings.py             # the new variables
│   ├── service.py              # run-start credential check; credentials_used on runs
│   ├── flow.py                 # use credential_client in _worker_execution_context and branch review
│   ├── branch_review.py        # destination token from released values
│   ├── worker.py               # no credential-name publishing; worker settings
│   └── failure_cause.py        # CredentialUnavailableError reasons
├── client/
│   ├── client.py               # credential methods
│   └── models.py               # credential resources
├── cli_credentials.py          # new: the `credentials` group
├── cli.py                      # register the group; convert report
└── testcontainers/             # docker-compose.yml, helpers.py

docker-compose.yml
development/docker-compose.yml
tasks/dev.py, tasks/bench.py, .github/scripts/nightly_e2e.py
examples/*/package.yml          # `credential:` instead of the credentials block
pyproject.toml                  # cryptography in the service extra

frontend/src/
├── routes/credentials.tsx      # new
├── features/credentials/       # new: list, forms, delete
├── features/runs/              # credentials used
├── routes/__root.tsx           # navigation entry
└── api/queries.ts, actions.ts, types.generated.ts

docs/docs/                      # pages listed in research R12; guides/credentials.mdx new
dev/adr/0054-the-sync-api-stores-credentials-and-releases-them-to-the-worker-account.md
.specify/memory/constitution.md, AGENTS.md
changelog/+stored-credentials.added.md, changelog/+credential-env-provider.removed.md
```

**Structure Decision**: the existing single-repository layout. New service modules follow the account
modules of spec 022 (`account_routes.py`, `account_actions.py`). The new modules are named
`credential_*` and `stored_credentials.py` because `service/credentials.py` already holds the account
token and password primitives.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| Principle VI: values come from the Sync database | The brief's goal: change a value once, with no shell access and no restart, on any worker | Environment variables need a restart and a copy on every worker; an external secret store is planned for 3.x and out of scope |
| New dependency `cryptography` | Authenticated encryption with associated data | The standard library has no authenticated cipher; `pgcrypto` puts the key in SQL and does not run on SQLite |
| One large delivery step | The format change, the worker route and the removal of `env` cannot pass the tests separately | Keeping `env` beside stored credentials contradicts the brief and doubles the paths to test |
