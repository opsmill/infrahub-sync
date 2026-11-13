# Implementation Plan: Plan problem reporting

**Branch**: `020-plan-problem-reporting` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/020-plan-problem-reporting/spec.md`

## Summary

A customer team that plans a first import from NetBox cannot review its bad source data in one
pass: a plan stops at the first bad record, and a failed run records class names but not
Infrahub's error code or the system that failed.

Approach, on the current code (details in [research.md](research.md)):

- The adapter base class owns the per-record read loop. Its admission step catches a record
  that cannot be built (wrong type, duplicate identity, excluded or missing required peer, failed
  transform), records it in a load report, and continues (R2). Each bundled adapter keeps only
  `list_records` and `record_to_data`.
- A problem pass after the operations are derived finds the nine problem types, walks required
  relationships to find dependents, and a pure selection step removes the left-out records'
  operations and the kept records' dropped relationships. The stores and the DiffSync diff stay as
  read (R3 to R7).
- The plan is saved in format 4 with a report covered by the checksum (R9). The apply needs
  `confirm_left_out` when records are left out (R10). A `sync` stops after its plan through the
  `result["failure"]` key that the worker flow already reads (R11).
- The `<stage>_failure` record of spec 017 gains a failure type, the system, Infrahub's error
  code, kind, attribute and message, and the failed operation's detail, built by one function from
  allowlisted fields (R12, R13). Decision record 0021 replaces the "class name, never a message"
  rule.
- `configs validate --branch` reaches the destination schema check and adds Dropdown literals,
  worker credential names and the import account's permissions (R15).

## Technical Context

**Language/Version**: Python 3.10 to 3.13; the Sync service and worker run on 3.11 to 3.13.

**Primary Dependencies**: DiffSync and pydantic (models and stores), FastAPI (Sync API), Prefect
(worker flow, internal per ADR 0019), Infrahub SDK, pynetbox. No new dependency.

**Storage**: plan directory files (ADR 0001) and the run bundle (ADR 0011); product store
(PostgreSQL in Compose) for run records and one new table, `worker_environments` (R15).

**Testing**: pytest through `uv run invoke tests.tests-unit`; `from-netbox` live tier for the
NetBox example.

**Target Platform**: Linux containers from the root `docker-compose.yml`; the CLI on any platform
with Python 3.10 to 3.13.

**Project Type**: CLI and web service (Sync API and worker) in one Python package.

**Performance Goals**: no added run failure at tier M (about 10,000 mapped objects). The time the
new steps add is not known; measured before and after (R19).

**Constraints**: client response limit 16 MiB; lists returned in pages of at most 500; a problem
reason at most 512 characters; Infrahub's message at most `MAX_FAILURE_CAUSE_TEXT` (1,000)
characters.

**Scale/Scope**: every bundled source adapter (NetBox, Nautobot, ACI, generic REST and the
adapters built on it, Prometheus, Slurp'it, IP Fabric, Infrahub).

No item is left as NEEDS CLARIFICATION. Two questions wait for the product owner (spec, "Open
questions").

**Unverified behavior (decision 11)**: today's handling of a duplicate source identity, a
Dropdown value outside its choices and a value of the wrong type was verified in code during
this plan; see [research R1](research.md#r1-behavior-today-for-the-three-unverified-problem-types-decision-11).
Task T001 pins it in tests before any change.

## Constitution Check

*Gate before Phase 0, checked again after Phase 1.*

| Principle | Result | How the design meets it |
| --- | --- | --- |
| I. Read-only and dry-run by default | Pass | A plan writes nothing. Apply needs `confirm_writes`, the checksum and, when records are left out, `confirm_left_out`. A `sync` with records left out stops before any write |
| II. Idempotency and safety | Pass | The stores are compared as read; a plan of unchanged data has the same bytes. A new plan after a stopped apply holds only what the destination does not match (R20) |
| III. Adapter symmetry | Pass | One read loop and admission step in the base class and one peer resolution helper for every adapter (R2, R5) |
| IV. Type safety | Pass | Typed models for problems, left-out and kept records and the failure fields; specific exceptions (`TransformError`, `SideLoadError`) |
| V. Test discipline | Pass | Tests per problem type, per adapter, per failure type, per refusal; a credential absence test (R13) |
| VI. Security and secrets | Pass, with an existing gap | Credentials are kept out by construction (R13). Infrahub's own message is recorded (ADR 0021). Existing gap, not introduced here: the worker and Prefect logs keep error text, masked only for values of 6 characters or more; 3.0 work |
| VII. Simplicity | Pass | Eight adapter loops become one; `continue_on_error` is removed; no new dependency; the report reuses ADR 0001's encoding |

Post-design check: unchanged. The design adds one product-store table and no service.

## Project Structure

### Documentation (this feature)

```text
dev/specs/020-plan-problem-reporting/
├── spec.md
├── plan.md
├── research.md          # R1 to R20, drafts of ADR 0020, 0021, 0022
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── sync-api.md
│   ├── cli.md
│   └── plan-artifact-format-4.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
infrahub_sync/
├── __init__.py                     # DiffSyncMixin.model_loader and load_record, TransformError
├── load_report.py                  # new: load report entries (old branch reference)
├── infrahub_errors.py              # GraphQL error code, node kind, field name
├── adapters/
│   ├── netbox.py, nautobot.py, aci.py, genericrestapi.py, slurpitsync.py,
│   │   prometheus.py, ipfabricsync.py, peeringmanager.py   # list_records + record_to_data
│   ├── references.py               # new: shared peer resolution helper
│   └── infrahub.py                 # load_record from its own loader; destination identity ambiguity
├── runtime_schema/                 # Dropdown choices; choices in the consumed-schema fingerprint
├── configuration/
│   ├── schema_validation.py        # Dropdown literals; permission and lineage findings
│   └── worker_credentials.py       # new: credential names a package references
├── plan/
│   ├── problems.py                 # new: problem pass, dependency walk, selection
│   ├── models.py                   # format 4, report models, summary counts
│   ├── writer.py, reader.py, verify.py
│   ├── keying.py, derive.py        # collision and merge groups returned, not raised or logged
│   └── errors.py
├── potenda/__init__.py             # SideLoadError; problem pass before the plan is written; apply gate
├── product_store/
│   ├── store.py                    # worker_environments table
│   └── configs.py                  # register response without declared_content
├── service/
│   ├── failure_cause.py            # failure_record: allowlisted fields
│   ├── flow.py                     # _failure_evidence; sync stop; comment rewritten (ADR 0021)
│   ├── service.py, app.py, models.py   # left-out gate, report routes
│   ├── config_routes.py            # validate ?branch=
│   ├── worker.py                   # publish credential names
│   └── run_artifact.py             # left-out and problem counts, next steps
├── client/models.py, client/client.py  # summary fields, report pages, confirm_left_out
└── cli.py                          # runs plan --problems/--left-out/--kept, --confirm-left-out,
                                    # configs validate --branch, failure fields

examples/netbox_to_infrahub/package.yml, README.md   # status translation (R18)
dev/adr/0020-*.md, 0021-*.md, 0022-*.md
docs/docs/development/guidelines/secret-redaction.md
docs/docs/ (user guides and generated references)
changelog/  # one fragment per delivered part
```

**Structure Decision**: the existing single package. New modules: `load_report.py`,
`adapters/references.py`, `plan/problems.py`, `configuration/worker_credentials.py`.

## Delivery order

1. Failure record extension, `SideLoadError`, ADR 0021, the `flow.py` comment and the secret
   redaction guideline (FR-014 to FR-020, FR-029, FR-030). Independent of the rest.
2. Base-class read loop, admission step and adapter changes; `continue_on_error` removed (R2).
   Valid fixtures plan to the same bytes before and after.
3. Problem pass, selection, Dropdown choices, plan format 4 and ADR 0020, ADR 0022 (FR-001 to
   FR-009, FR-024, FR-025, FR-031).
4. Apply confirmation, `sync` stop, CLI lists and the Prefect artifact (FR-010 to FR-012, FR-026
   to FR-028).
5. Stopped apply detail and the replan test (FR-021 to FR-023).
6. `configs validate`, register response and the NetBox example (FR-033 to FR-038).

Steps 3 to 4 ship only after the AD-12 amendment is merged in `infrahub-sync-process` (spec,
"Dependencies"). Each step updates `docs/` and adds a changelog fragment.

## Dependencies on other work

- Spec 012 task T031 waits for the counts of step 3.
- Spec 018's read state names the side that failed; R12's `SideLoadError` carries the same role,
  so both agree on which side failed.
- The worker liveness rule (`service/liveness.py`) decides which workers count as live for
  FR-035.

## Open risks

- R11: a `sync` run holds a write admission from its creation. The stop releases it in the same
  commit when no operation was dispatched (old 009's design). The store has changed since the
  old branch, so task T033 tests the admitted and the refused cases before T039 changes the
  store. The product owner is asked to confirm the rule change.
- R12: a source adapter's refused token or unreachable address is classified from the
  exception's type and HTTP status code attribute only (for example pynetbox `RequestError`,
  `requests` `HTTPError` and `ConnectionError`, `httpx` errors), never from its text. The
  per-adapter table is written and tested in step 1; an adapter error that the table does not
  name is `internal_error` with `system` set.
- Infrahub 1.11 may not return the node kind and attribute for every rejection; the fields stay
  empty then.
- Behavior change for four adapters: a reference to a peer not in the source no longer fails the
  plan (NetBox, ACI, generic REST) or is no longer dropped with a warning (Nautobot).
- `continue_on_error` no longer applies to the plan path.
- The time added at tier M is not known (R19).

## Complexity Tracking

| Change | Why needed | Simpler alternative rejected because |
| --- | --- | --- |
| Infrahub's own message reaches the run record (principle VI: error messages must not leak internal details) | FR-016: the team acts on Infrahub's message (#370) | Class names only give no cause; a fixed text per code says nothing for an unknown code |
| The adapter contract moves the record loop into the base class | Only one loop can catch a failed transform, a missing peer, a wrong type and a duplicate per record and continue (R2) | A helper around `model(**data)` misses errors raised inside the conversion generators |
| Plan format 4 instead of an additive manifest key | The left-out confirmation is an apply safety gate | An older reader ignores an additive key and applies without the confirmation |
| A stopped `sync` releases its write admission when nothing was dispatched | FR-012: the team applies the plan it reviewed, on the same run | Appending under the `sync` receipt needs a second change to receipts; a new `plan` run drops the reviewed plan (R11) |
| New product-store table `worker_environments` (database change gate) | FR-035: the Sync API cannot see the worker's environment otherwise | Reading the variables in the API process checks the wrong process; Compose gives the worker and the API different variables |
| Worker and Prefect logs keep masked error text | Product owner decision of 2026-10-15: masking stays unchanged in the testing release | Logging only the structured cause is 3.0 work |
