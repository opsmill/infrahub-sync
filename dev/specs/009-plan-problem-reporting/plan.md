# Implementation Plan: Plan problem reporting

**Branch**: `009-plan-problem-reporting` (planned on `plan-009`) | **Date**: 2026-10-15 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/009-plan-problem-reporting/spec.md`

## Summary

A customer team that plans a first import from NetBox cannot review its bad source data in one
pass: a plan stops at the first bad record, and a failed run shows only an error class name.

The plan collects six problem types where each is first detectable, instead of raising:

- in a shared record-admission method that every bundled source adapter calls while loading;
- in the engine after the load, for the Dropdown check against the destination schema record;
- in the identity checks after derivation.

It then leaves out each record with a problem and every record that depends on it through a
required relationship, keeps records that refer to a left-out record only through an optional
relationship, and removes the matching operations. The problems, left-out records and kept
records are saved in a new plan report file whose digest the plan checksum covers (plan format
4). Apply requires the confirmed left-out count in the CLI and the Sync API.

A failed or stopped run records a structured cause, with the message after secret replacement
and a length limit, and the CLI and the Sync API show it. `configs validate --branch` runs the
destination schema check, and reports credentials that the worker does not hold.

Three decision records are drafted in [research.md](research.md): 0020 (records with problems
are left out at the plan stage, R8), 0021 (a run's failure record holds a redacted message,
R9) and 0022 (saved plan format 4, R7). They are not created in `dev/adr/` during planning.

## Technical Context

**Language/Version**: Python 3.10 to 3.13; the Sync service and worker run on 3.11 to 3.13.

**Primary Dependencies**: DiffSync and pydantic (models and stores), FastAPI (Sync API),
Prefect 3.8.6 (worker flow, `service/flow.py`), Infrahub SDK (destination, GraphQL errors),
pynetbox, requests and httpx (source adapters). No new dependency.

**Storage**: plan directory files (ADR 0001) and the run bundle (ADR 0011); product store
(PostgreSQL in Compose) for run records, public artifacts and the new worker-environment record;
S3-compatible object store for artifacts.

**Testing**: pytest through `uv run invoke tests.tests-unit`; live tiers in
`docs/docs/development/guidelines/testing-tiers.md` for the NetBox example check.

**Target Platform**: Linux containers from the root `docker-compose.yml`; the CLI on any
platform with Python 3.10 to 3.13.

**Project Type**: CLI and web service (Sync API and Prefect worker) in one Python package.

**Performance Goals**: no added run failure at tier M (10,207 mapped objects). No time target is
set by the spec. Evidence: research R6 and R15.

**Constraints**: client response limit 16 MiB (`client/client.py`), bundle limit 64 MiB
(`product_store/bundle.py:54`); lists returned in pages; a cause message at most 1,024
characters; a problem reason at most 512 characters.

**Scale/Scope**: up to about 10,000 mapped objects (idea brief); every bundled source adapter
(NetBox, Nautobot, ACI, generic REST, Prometheus, Slurp'it, IP Fabric).

No item is marked NEEDS CLARIFICATION. The questions that remain open are listed under
"Open risks".

## Constitution Check

*Gate before Phase 0, checked again after Phase 1.*

| Principle | Result | How the design meets it |
| --- | --- | --- |
| I. Read-only and dry-run by default | Pass | A plan writes nothing (FR-004). Apply needs `confirm_writes`, the checksum and now the left-out count. A `sync` run with records left out stops before any write (FR-012) |
| II. Idempotency and safety | Pass | A stopped apply reports what was written; a new plan contains only what the destination does not match (R14). The plan of a stopped apply is refused with `new-plan-required` |
| III. Adapter symmetry | Pass | One admission method in `DiffSyncMixin` and one set of typed exceptions for every bundled adapter (R2); the engine stays `potenda` |
| IV. Type safety | Pass | Typed models for problems, left-out and kept records, cause; specific exception classes (`ExcludedRequiredPeerError`, `LeftOutPeerError`, `SideLoadError`) |
| V. Test discipline | Pass | Tests per problem type, per adapter, per failure type, per refusal, and for masking (quickstart) |
| VI. Security and secrets | Pass with a recorded change | Destination and source messages now reach the run record (ADR 0021 draft). Every collected and every referenced secret is replaced first, in raw and JSON forms, and the text is bounded (R9, R11). See Complexity Tracking |
| VII. Simplicity | Pass | The admission method has seven adapter call sites; the report reuses ADR 0001's encoding; no new dependency |

Post-design check: unchanged. The design adds no service, no dependency and no configuration
key.

## Project Structure

### Documentation (this feature)

```text
dev/specs/009-plan-problem-reporting/
├── spec.md
├── plan.md              # this file
├── research.md          # decisions R1 to R15, drafts of ADR 0020, 0021, 0022
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
├── __init__.py                     # DiffSyncMixin.admit_record, filtered-peer bookkeeping
├── problems.py                     # new: problem types, ProblemCollector, left-out index, closure
├── failure_cause.py                # new: RunFailureCause and classification (R10)
├── adapters/
│   ├── netbox.py, nautobot.py, aci.py, genericrestapi.py   # admission + typed peer exceptions
│   └── prometheus.py, slurpitsync.py, ipfabricsync.py      # admission only
├── configuration/
│   ├── capabilities.py             # Dropdown choices in the schema read
│   ├── credentials.py              # collect_referenced_credential_values
│   └── schema_validation.py        # Dropdown static values; shared keyability check
├── runtime_schema/
│   ├── domain.py                   # NormalizedAttribute.choices
│   └── projection.py               # choices in the consumed-schema fingerprint
├── plan/
│   ├── models.py                   # format 4, PlanReport, summary counts
│   ├── writer.py, reader.py, verify.py   # report.jsonl
│   ├── derive.py, keying.py        # collect identity problems instead of raising; filter
│   └── errors.py
├── potenda/__init__.py             # SideLoadError, Dropdown check, closure, filtering, count check
├── execution.py                    # merged secret set, cause at run_remote_request
├── product_store/
│   ├── store.py                    # JSON artifact redaction, worker_environment record
│   └── configs.py                  # register response without declared_content
├── service/
│   ├── app.py, service.py, models.py     # report routes, apply count check, failure field
│   ├── flow.py                     # cause in _failure_evidence; sync stop after plan
│   ├── config_routes.py            # validate ?branch=, credential names check
│   ├── worker.py                   # publish credential names
│   └── worker_environment.py       # new: names of set INFRAHUB_SYNC_CREDENTIAL_* variables
├── client/
│   ├── models.py                   # resources for report pages, failure, apply count
│   └── client.py                   # paged report reads
└── cli.py                          # runs problems/left-out/kept, --confirm-left-out, cause output

examples/netbox_to_infrahub/package.yml, README.md      # device_status transform (R13)

tests/
├── plan/          # report format 4, closure, filtering, identity problems
├── adapters/      # admission per bundled adapter
├── runtime_schema/  # choices and fingerprint
├── configuration/ # validate with branch, credential reference collection
├── service/       # test_worker_environment.py, test_failure_cause.py,
│                  # test_plan_report_routes.py, test_apply_left_out_confirmation.py
├── client/, cli/  # resources and output
└── product_store/ # JSON artifact redaction stays valid JSON

docs/docs/
├── reference/sync-http-api.mdx, reference/cli.mdx (generated)
├── running-a-sync.mdx                # reviewing problems, confirming the count
├── operations/compose-troubleshooting.mdx   # one section at the end (owned by spec 013)
└── development/guidelines/secret-redaction.md
```

**Structure Decision**: the existing single package. Two new modules (`problems.py`,
`failure_cause.py`) because both have callers in more than one package (adapters and engine;
service flow and execution). One new service module (`worker_environment.py`).

## Delivery order

1. Masking (FR-027, FR-028) and the failure cause (FR-014 to FR-020, FR-025). FR-014 to FR-019
   ship only after the product owner signs off ADR 0021 (FR-030).
2. Problem collection, closure and the plan report (FR-001 to FR-009, FR-023, FR-024), plan
   format 4. Ships after sign-off of the AD-12 amendment and ADR 0020 (FR-029).
3. Apply confirmation and the `sync` stop (FR-010 to FR-013, FR-026).
4. Stopped apply and the next plan (FR-020 to FR-022).
5. `configs validate` (FR-032 to FR-035) and the NetBox example (FR-031).

Each step updates `docs/` and adds one changelog fragment named `+009-<topic>.<type>.md`.

## Coordination with spec 013

Agreed with `plan-013` on 2026-10-15. One owner per shared item:

| Shared item | Owner | Agreement |
| --- | --- | --- |
| `docker-compose.yml`, `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml`, `development/preview.env`, `Dockerfile` | 013 | 009 changes none of them |
| `infrahub_sync/service/serve.py` | 013 | 009 does not change it. 013's Prefect factory is a new module `service/prefect_server.py`, and 013 adds the `trust_store` call in `main()` and mounts the CA directory into `sync-api`, so the FR-032 schema check in `sync-api` trusts a private CA. 009's check uses the existing Infrahub schema accessor and sets no TLS option of its own |
| `infrahub_sync/service/worker.py` | 009 | 013 adds only an import and one call in `main()` to its new `service/trust_store.py`; whichever merges second rebases |
| Credentials in `sync-api` | 013 | `sync-api` keeps only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` (009's validate route uses it); `sync-worker` receives all bundled-adapter credential variables; no other service receives any |
| Credential variable names | 012 and 013 | 009 depends only on the `INFRAHUB_SYNC_CREDENTIAL_` prefix (`configuration/credentials.py:28`). Compose passes unset variables as empty strings; 009 treats an empty value as unset |
| Custom CA variables | 013 | `INFRAHUB_SYNC_CA_CERTIFICATES` (host), `INFRAHUB_SYNC_CA_DIRECTORY` (container); the worker sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE`. None matches the secret name shapes, so masking does not hide paths. 009 reports a certificate failure as `tls_certificate_rejected` |
| Masking helpers (`execution.py` collection and redaction, `configuration/credentials.py`) and `docs/docs/development/guidelines/secret-redaction.md` | 009 | 013 adds no secret-bearing variable outside the name shapes |
| `docs/docs/operations/compose-troubleshooting.mdx` | 013 | 009 adds one section at the end, "A run failed: read its cause" |
| `docs/docs/compose-deployment.mdx`, `custom-certificates.mdx`, `operations/day-2-operations.mdx` | 013 | 009 does not edit them |
| Changelog fragments | each | 009 uses `+009-…`, 013 uses `+013-…` |
| Tests | each | 013: `tests/compose/`, `tests/image/`, new `tests/service/test_prefect_server.py` and `tests/service/test_trust_store.py`. 009: the new `tests/service/` files listed above and the other directories; neither edits the other's files |
| Decision record numbers | each | 009: 0020, 0021, 0022. 013: amendment to 0019, and 0023 if needed |

## Dependencies on other specifications

- Spec 011 consumes [contracts/sync-api.md](contracts/sync-api.md). The report routes already
  carry `offset`, `limit`, `total`, `next_offset`, `plan_checksum` and the `problem_type` and
  `kind` filters of spec 011 FR-014 to FR-016, so 011 adds no second route for these lists.
- Spec 010 owns incremental reads and plan-step timing. 009 passes it one rule: a plan after a
  stopped apply of the same configuration reads the destination in full (R14), and one phase
  name, `problems`, for its timing.

## Open risks

- Product sign-off of ADR 0020 with the AD-12 amendment and of ADR 0021 is not given yet. The
  work they cover cannot ship before it (FR-029, FR-030).
- Infrahub versions before 1.11 return no error code (#370); the cause then holds only the
  message. Whether every Infrahub 1.11 rejection carries the node kind and attribute in
  `extensions` is not confirmed; the cause leaves them `null` when absent.
- Masking a referenced credential of one or two characters replaces every occurrence of those
  characters in messages (R11).
- The six problem types are checked for every bundled source adapter, but only four adapters
  resolve references while loading. Whether Prometheus, Slurp'it and IP Fabric need the
  dependency rules has to be confirmed per adapter in the tasks.
- The time added to the plan step at tier M is not known (R15); spec 010's phase timing will
  show it.
- `GET /configs/{id}/versions/{v}` still returns redacted content next to the original checksum;
  FR-035 names only the registration response.
- Removing the raw byte pass for JSON artifacts relies on the decoded pass covering every
  string, key and number (R11); a test with the SC-007 values must prove it.

## Complexity Tracking

| Change | Why needed | Simpler alternative rejected because |
| --- | --- | --- |
| A destination's or source's message now reaches the run record (principle VI: "error messages MUST NOT leak internal details") | FR-014 to FR-019: the team must read the cause without container logs (#369, #370) | Class names only, as today, give no cause; a fixed message per code says nothing for an unknown code (R9) |
| Plan format 4 instead of an additive manifest key | The left-out count is a safety gate for apply (R7) | An additive key is ignored by an older reader, which would apply without the confirmation |

## Agent context

The SPECKIT block in `CLAUDE.md` is not updated by this plan. The lead updates it once after
the plans for specs 009 and 013 are merged.
