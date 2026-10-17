# Implementation Plan: Plan problem reporting

**Branch**: `009-plan-problem-reporting` (planned on `replan-009`) | **Date**: 2026-10-15 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/009-plan-problem-reporting/spec.md`

This is the second plan for spec 009. It replaces the first plan after the product owner
rejected, on 2026-10-15, its changes to what DiffSync compares and its masking of credential
values. [research.md](research.md) lists what changed and why.

## Summary

A customer team that plans a first import from NetBox cannot review its bad source data in one
pass: a plan stops at the first bad record, and a failed run shows only an error class name.

How the plan finds problems without changing what DiffSync compares:

- The adapter base class owns the per-record load loop. One admission step catches a record
  that cannot be built (a type the model refuses, a duplicate identity, a required peer removed
  by a filter, a required peer that is not in the source, a peer that was not loaded) and
  records it, then continues with the next record.
- A reference to a peer that is not in the source at all is the seventh problem type,
  `peer_not_found_in_source`, and no longer fails the plan. Through a required relationship the
  record is left out; through an optional one it is kept without that relationship (R4).
  Adapters provide `list_records` and `record_to_data` and keep no loop of their own.
- Every other record is loaded as the source holds it, references included. DiffSync's
  `diff_from` compares the stores as loaded, and operations are derived from that diff.
- A separate problem pass then reads the load reports, the source store, the destination
  schema record and the derived operations, and decides which records are left out and which
  are kept without a relationship. One pure selection step applies those decisions to the
  plan's operations. Neither store and not the diff is changed after the load.
- `continue_on_error`, which skipped bad records without a count in two adapters, is removed.

The problems, left-out records and kept records are saved in a report file whose digest the
plan checksum covers (plan format 4). Apply requires the confirmed left-out count.

How credentials stay out of what the team views: a failed run records a structured cause built
by one module, `infrahub_sync/failure_cause.py`, at the remote boundary of ADR 0005, from
allowlisted fields only. It never formats an error's text. Problem reasons are rendered from
fixed templates over source values and schema names. No new value replacement is added, and the
existing one stays as it is.

`configs validate --branch` runs the destination schema check and reports credentials that the
worker does not hold.

Three decision records are drafted in research.md and not created in `dev/adr/` during
planning: 0020 (records with problems are left out at the plan stage, R11), 0021 (a run's
failure record holds a structured cause, R12) and 0022 (saved plan format 4, R10).

## Technical Context

**Language/Version**: Python 3.10 to 3.13; the Sync service and worker run on 3.11 to 3.13.

**Primary Dependencies**: DiffSync 2.2.3 and pydantic 2.12.5 (models and stores), FastAPI (Sync
API), Prefect 3.8.6 (worker flow), Infrahub SDK, pynetbox, requests and httpx. No new
dependency.

**Storage**: plan directory files (ADR 0001) and the run bundle (ADR 0011); product store
(PostgreSQL in Compose) for run records, public artifacts and the new worker-environment record;
S3-compatible object store for artifacts (owned by spec 013, unchanged here).

**Testing**: pytest through `uv run invoke tests.tests-unit`; live tiers in
`docs/docs/development/guidelines/testing-tiers.md` for the NetBox example check.

**Target Platform**: Linux containers from the root `docker-compose.yml`; the CLI on any
platform with Python 3.10 to 3.13.

**Project Type**: CLI and web service (Sync API and Prefect worker) in one Python package.

**Performance Goals**: no added run failure at tier M (10,207 mapped objects). The spec sets no
time target. The new steps run in memory over loaded data; their time is not known and spec 010
FR-009 times them (research R9).

**Constraints**: client response limit 16 MiB (`client/client.py`), bundle limit 64 MiB
(`product_store/bundle.py:54`); lists returned in pages; Infrahub's message at most 1,024
characters; a problem reason at most 512 characters.

**Scale/Scope**: up to about 10,000 mapped objects (idea brief); every bundled source adapter
(NetBox, Nautobot, ACI, generic REST with Peering Manager, Prometheus, Slurp'it, IP Fabric,
Infrahub).

No item is marked NEEDS CLARIFICATION. The questions that remain open are under "Open risks".

## Constitution Check

*Gate before Phase 0, checked again after Phase 1.*

| Principle | Result | How the design meets it |
| --- | --- | --- |
| I. Read-only and dry-run by default | Pass | A plan writes nothing (FR-004). Apply needs `confirm_writes`, the checksum and the left-out count. A `sync` run with records left out stops before any write (FR-012) |
| II. Idempotency and safety | Pass | A stopped apply reports what was written; a new plan contains only what the destination does not match (R17). DiffSync compares the source as loaded, so a re-run on unchanged data gives the same plan |
| III. Adapter symmetry | Pass | One admission step in the base class for every adapter, one peer resolution helper for the five adapters that resolve references (R3, R4); the engine stays `potenda`. The Infrahub adapter calls the same admission step from its own loader (R3) |
| IV. Type safety | Pass | Typed models for the load report, problems, left-out and kept records and the cause; specific exceptions (`ExcludedRequiredPeerError`, `PeerNotLoadedError`, `PeerNotFoundError`, `RecordLoadError`, `SideLoadError`, `TransformError`) |
| V. Test discipline | Pass | Tests per problem type, per adapter, per failure type, per refusal; a store-unchanged test and an absence test for credentials (quickstart) |
| VI. Security and secrets | Pass, with one existing gap recorded | Credentials are kept out of causes, problem reasons and plan files by construction (R13). Infrahub's own message reaches the run record (ADR 0021 draft). Existing gap, not introduced here: the worker log can hold a credential shorter than six characters in error text (R14, Complexity Tracking) |
| VII. Simplicity | Pass | Seven adapter loops become one; `continue_on_error` is removed; no new dependency; the report reuses ADR 0001's encoding |

Post-design check: unchanged. The design adds no service, no dependency and no configuration
key.

## Project Structure

### Documentation (this feature)

```text
dev/specs/009-plan-problem-reporting/
├── spec.md
├── plan.md              # this file
├── research.md          # decisions R1 to R17, drafts of ADR 0020, 0021, 0022
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── sync-api.md
│   ├── cli.md
│   └── plan-artifact-format-4.md
├── checklists/
└── tasks.md             # /speckit-tasks, not created here
```

### Source Code (repository root)

```text
infrahub_sync/
├── __init__.py                     # DiffSyncMixin: default model_loader, load_record (admission),
│                                   # peer_unique_id, load report; TransformError
├── failure_cause.py                # new: RunFailureCause, build_failure_cause (the one secret layer)
├── adapters/
│   ├── netbox.py, nautobot.py, aci.py, genericrestapi.py, slurpitsync.py
│   │                               # list_records + record_to_data; peer_unique_id
│   ├── prometheus.py, ipfabricsync.py, peeringmanager.py   # list_records + record_to_data
│   └── infrahub.py                 # load_record from its own loader; continue_on_error removed
├── configuration/
│   ├── capabilities.py             # Dropdown choices in the schema read
│   └── schema_validation.py        # Dropdown static values; shared keyability check
├── runtime_schema/
│   ├── domain.py                   # NormalizedAttribute.choices
│   └── projection.py               # choices in the consumed-schema fingerprint
├── plan/
│   ├── problems.py                 # new: problem pass, dependency walk, select_operations
│   ├── models.py                   # format 4, PlanReport, summary counts
│   ├── writer.py, reader.py, verify.py   # report.jsonl; refuse placeholders
│   ├── derive.py                   # placeholder for references to records not loaded
│   ├── keying.py                   # collision checks return groups
│   └── errors.py
├── potenda/__init__.py             # SideLoadError; problem pass and selection in
│                                   # _write_plan_artifact; count check; continue_on_error removed
├── cache/incremental.py            # changed rows through load_record
├── execution.py, utils.py          # continue_on_error removed
├── product_store/configs.py        # register response without declared_content
├── service/
│   ├── app.py, service.py, models.py     # report routes, apply count check, failure field
│   ├── flow.py                     # _failure_evidence calls build_failure_cause; sync stop
│   ├── config_routes.py            # validate ?branch=, credential names check
│   ├── worker.py                   # publish credential names
│   └── worker_environment.py       # new
├── client/
│   ├── models.py                   # report pages, failure, apply count
│   └── client.py                   # paged report reads
└── cli.py                          # runs problems/left-out/kept, --confirm-left-out, cause output

examples/netbox_to_infrahub/package.yml, README.md      # device_status transform (R16)

tests/
├── plan/          # problems, walk, selection, format 4, stores unchanged
├── adapters/      # admission per adapter; a test adapter with list_records only
├── runtime_schema/  # choices and fingerprint; no _children
├── configuration/ # validate with branch
├── service/       # test_failure_cause.py, test_worker_environment.py,
│                  # test_plan_report_routes.py, test_apply_left_out_confirmation.py
└── client/, cli/  # resources and output; credential absence

docs/docs/
├── reference/sync-http-api.mdx, reference/cli.mdx (generated)
├── running-a-sync.mdx                        # reviewing problems, confirming the count
├── operations/compose-troubleshooting.mdx    # one section at the end (file owned by 013)
├── development/knowledge/adapter-anatomy.md  # list_records, record_to_data, admission
├── development/guidelines/writing-an-adapter.md
└── development/guidelines/secret-redaction.md   # prevention rule, cause builder
```

**Structure Decision**: the existing single package. Two new modules with callers in several
packages: `plan/problems.py` (engine and tests) and `failure_cause.py` (service flow and CLI
tests). One new service module, `worker_environment.py`.

## Delivery order

1. The failure cause builder and `SideLoadError`, `TransformError` (FR-014 to FR-020, FR-025,
   FR-027). FR-014 to FR-019 ship only after the product owner signs off ADR 0021 (FR-030).
2. The base-class load loop and admission step, the adapter changes, and the removal of
   `continue_on_error` (research R3 to R5). This step changes no plan output on valid data: a
   test plans the existing fixtures before and after and compares the plans.
3. The problem pass, dependency walk, selection step and plan format 4 (FR-001 to FR-009,
   FR-023, FR-024). Ships after sign-off of the AD-12 amendment and ADR 0020 (FR-029).
4. Apply confirmation and the `sync` stop (FR-010 to FR-013, FR-026).
5. Stopped apply and the next plan (FR-020 to FR-022).
6. `configs validate` (FR-032 to FR-035) and the NetBox example (FR-031).

Each step updates `docs/` and adds one changelog fragment named `+009-<topic>.<type>.md`.

## Coordination with spec 013

Agreed with `replan-013` on 2026-10-15, second round. One owner per shared item:

| Shared item | Owner | Agreement |
| --- | --- | --- |
| `docker-compose.yml`, `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml`, `development/preview.env`, `Dockerfile` | 013 | 009 changes none of them |
| `infrahub_sync/service/serve.py` | 013 | 013 adds one `trust_store` call in `main()`. 009 does not edit it |
| `infrahub_sync/service/storage.py`, `infrahub_sync/service/bootstrap.py` | 013 | 009 does not edit them |
| `infrahub_sync/service/worker.py` | 009 | 013 adds one import and one call at the top of `main()`; the second branch to merge rebases |
| New service modules | each | 013: `service/prefect_server.py`, `service/trust_store.py`. 009: `service/worker_environment.py`, `infrahub_sync/failure_cause.py` |
| Credentials in `sync-api` | 013 | `sync-api` keeps only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, which 009's validate route (FR-032) uses. `sync-worker` receives all bundled-adapter credential variables |
| Credential variable names | 012 and 013 | 009 depends only on the `INFRAHUB_SYNC_CREDENTIAL_` prefix (`configuration/credentials.py:29`). Compose passes unset variables as empty strings; 009 treats an empty value as unset. 013's new names (`INFRAHUB_SYNC_S3_ENDPOINT_URL`, `INFRAHUB_SYNC_S3_ADDRESSING_STYLE`, `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE`, `INFRAHUB_SYNC_CA_CERTIFICATES`) need nothing from 009 |
| Secret handling | 009 | One layer: `build_failure_cause` in `infrahub_sync/failure_cause.py`, called only from `_failure_evidence` (`service/flow.py:697`). Credential values are resolved only into adapter and SDK settings (`configuration/runtime.py:52`, `configuration/capabilities.py:208`) and never formatted into messages. Neither spec adds value masking. 013 does not depend on `collect_secret_values` for anything it passes; its leak checks assert absence. The existing helpers (`execution.py` collection and redaction, `configuration/credentials.py`) and `docs/docs/development/guidelines/secret-redaction.md` are owned by 009 and stay unchanged in behaviour |
| Failure types for 013 | 009 | A certificate failure is `tls_certificate_rejected`, recognised by `ssl.SSLCertVerificationError` in the cause chain. An object-store failure inside a run is `internal_error` with the class name and no text, until 013 raises a typed exception that 009 can add |
| `docs/docs/operations/compose-troubleshooting.mdx` | 013 | 009 adds one section at the end, "A run failed: read its cause" |
| `docs/docs/running-a-sync.mdx` | 009 | 013 changes only the sentence near line 235 that names the Prefect UI port |
| `docs/docs/compose-deployment.mdx`, `custom-certificates.mdx`, `operations/day-2-operations.mdx` | 013 | 009 does not edit them |
| `dev/specs/v3-release-scope.md` | 013 (this round) | 009 sent its wording for the SYNC-82 line and the #369 line; 013 edits the page. The product owner's decision to move SYNC-262 after the MVP was applied to the page with this revision |
| Changelog fragments | each | 009 uses `+009-…`, 013 uses `+013-…` |
| Tests | each | 013: `tests/compose/` (including the test-only `tests/compose/redaction.py`), `tests/image/`, `tests/service/test_prefect_server.py`, `tests/service/test_trust_store.py`. 009: the files listed under Project Structure. Neither edits the other's files. 013's `test_custom_ca.py` may assert `tls_certificate_rejected` once 009 is merged |
| Decision record numbers | each | 009: 0020, 0021, 0022. 013: amendment to 0019, and 0023 |

## Dependencies on other specifications

- Spec 011 consumes [contracts/sync-api.md](contracts/sync-api.md). The report routes carry
  `offset`, `limit`, `total`, `next_offset`, `plan_checksum` and the `problem_type` and `kind`
  filters of spec 011 FR-014 to FR-016. Spec 011 FR-029 shows the cause fields; the field
  `message` of the first plan is now `infrahub_message` plus `summary` (data-model.md).
- Spec 010 owns incremental reads and plan step timings. 009 passes it two rules: a plan after a
  stopped apply reads the destination in full, and a plan after one that left out records reads
  the source in full (research R17). The new steps run inside 010's part "checking the plan
  before it is saved".

## Open risks

- Product sign-off of ADR 0020 with the AD-12 amendment, and of ADR 0021, is not given. The work
  they cover cannot ship before it (FR-029, FR-030).
- SYNC-262 is not part of the MVP (product owner, 2026-10-15): the existing byte pass can still
  make a published plan invalid JSON. Its removal is follow-up work after the MVP, listed under
  3.0 on the release scope page. It can be removed safely because plan files no longer receive
  credential values (research R13, R14).
- Part of SYNC-82 stays: a credential shorter than six characters can appear in error text in
  the worker log and the orchestration log, which are read only by OpsMill engineers, until
  those logs record the structured cause instead of the error text. That is follow-up work
  after the MVP (spec assumption, research R14).
- Infrahub versions before 1.11 return no error code (#370). Whether every Infrahub 1.11
  rejection carries the node kind and attribute in `extensions` is not confirmed; the cause
  leaves them null when absent.
- Behaviour change in four adapters: a reference to a peer that is not in the source is now
  listed as the problem `peer_not_found_in_source`. NetBox, ACI and generic REST no longer fail
  the plan on it, and Nautobot no longer drops the reference with only a warning
  (`adapters/nautobot.py:286`, `:300`; research R4).
- An adapter that overrides `model_loader` opts out of the admission step. The Infrahub adapter
  does so and calls `load_record` itself; duplicates from an Infrahub source are found only
  within one kind's records (research R3).
- A record whose identifier fields cannot be converted has no identity. A record that refers to
  it through an optional relationship is kept without that relationship (a `kept` entry with the
  cause `peer_left_out`); a record that refers to it through a required relationship is left out
  as a dependent. Its Infrahub object, if any, cannot be matched to it, so the plan computes no
  delete of its kind, and the plan summary counts such records per kind
  (`deletes_withheld_by_kind`; research R4, R7).
- The time added to the plan step at tier M is not known; spec 010's timings will show it.

## Complexity Tracking

| Change | Why needed | Simpler alternative rejected because |
| --- | --- | --- |
| Infrahub's own error message reaches the run record (principle VI: "error messages MUST NOT leak internal details") | FR-016: the team acts on Infrahub's message (#370) | Class names only, as today, give no cause; a fixed message per code says nothing for an unknown code (R12) |
| The worker log keeps exception text, protected only by the six-character replacement (principle VI: "tracebacks and structured logs MUST NOT contain credentials") | Existing behaviour; the product owner ruled that existing redaction stays in the MVP | Logging only the structured cause is the follow-up of R14 |
| Plan format 4 instead of an additive manifest key | The left-out count is a safety gate for apply (R10) | An older reader ignores an additive key and applies without the confirmation |
| The adapter contract moves the record loop into the base class | Only a loop in the base class can catch a record that cannot be built and continue (R3) | `add` does not see `ValidationError`; pydantic cannot return a substitute from construction; a call per adapter was rejected |

## Agent context

The SPECKIT block in `CLAUDE.md` is not updated by this plan. It already points to this file.
