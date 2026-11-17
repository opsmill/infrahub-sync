# Implementation Plan: A second plan shows only real changes, and the plan step is measured by part

**Branch**: `019-second-plan-real-changes` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/019-second-plan-real-changes/spec.md`

## Summary

A team that plans again after an import gets a plan that holds only real changes:

- **No delete of nodes that Infrahub creates itself.** The Infrahub destination adapter marks
  the nodes that Infrahub creates itself while it reads them, and the derivation of deletes
  skips those nodes when no source record matches them (research R1, R2). The three acceptances
  of the `default` IP namespace delete by name are removed (R3).
- **An emptied one-peer relationship is planned and applied.** An update records it as a
  one-peer reference with no peer, in a new plan format 5. Apply writes it as `null` in the same
  Upsert, and verify compares it without change (R4 to R8).
- **Tests of the second plan in the offline unit tier.** A stand-in destination applies the
  operations, so the second plan depends on what apply wrote (R9).

The plan step is measured by part:

- **Four new parts.** The worker records preparing, comparing, checking and saving through an
  engine observer, in one new JSON column beside the two existing read columns. Waiting is
  derived from `submitted_at` and `claimed_at` (R10 to R13).
- **Where the timings appear.** The run response and `runs show` show them, marked as subject to
  change (R14).
- **Benchmark records.** They gain the timings by part, the read mode of each side, the plan
  size and an operations digest (R15, R16). A maintainer measures tiers S and M on the benchmark
  machine and commits a results page (R17).

## Technical Context

**Language/Version**: Python 3.11–3.13 for the service profile; the CLI, the client and the plan
package also run on Python 3.10.

**Primary Dependencies**: Pydantic, DiffSync, Infrahub SDK (`infrahub-sdk>=1.18`, 1.23.2 locked),
FastAPI, Typer, Prefect client. No new dependency.

**Storage**: the product store on SQLite (tests, local) and PostgreSQL (service). One nullable
TEXT column `plan_step` on `prefect_executions`, declared in `CREATE TABLE`. Since PR #425, start-up
no longer upgrades a product database that an earlier build created, so the column is not added to
an existing database. No backfill. Saved plans gain format 5;
format 4 plans stay readable and applicable.

**Testing**: pytest offline unit tier (`uv run invoke tests.tests-unit`): `tests/plan/`,
`tests/adapters/`, `tests/service/`, `tests/product_store/`, `tests/client/`, `tests/cli/`,
`tests/test_benchmark_runner.py`. Two opt-in live tests: the Infrahub 1.11.9 probe of nodes
that Infrahub creates itself, and the write of an emptied one-peer relationship (marked
`integration`). The measurement of tiers S and M is manual, on the benchmark machine.

**Target Platform**: Linux server (Sync API and worker); CLI on any platform.

**Project Type**: web service with a CLI client and a plan library.

**Performance Goals**:

- The timing writes add at most four small store writes per plan step.
- The check of nodes that Infrahub creates itself adds no destination query beyond one extra
  field per read of a kind that needs it.
- At tier M, the median plan time of a no-change plan and of the `changed` plan is at most that
  of the first sync (FR-034).

**Constraints**:

- The API stays `v3-unstable`; all response changes are additive. Old runs load (FR-019).
- An Upsert stays the only destination write of an operation (ADR 0010, ADR 0012).
- Apply still executes no delete (ADR 0004).
- The plan report gains one problem type, for a required one-peer relationship that the source
  empties (spec FR-013a). No new kept record.
- No error message or data value in a timing field (FR-021, `secret-redaction.md`).
- The vendored `opsmill_prefect_extras` stays byte-identical.

**Scale/Scope**: about 15 source files, 15 test files, 4 documentation pages, 1 results page and
2 decision records.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only by default | `plan`, `runs show` and `runs plan` stay non-writing. Apply writes an emptied relationship only through a reviewed plan, by checksum. | Pass |
| II. Idempotency and safety | This feature exists to make the second plan idempotent. A refused plan format writes nothing. A failed or interrupted plan step keeps its timings. | Pass |
| III. Adapter symmetry | The rule for nodes that Infrahub creates itself is an optional protocol on the destination, as `MetadataReadingDestination` is. The filtered-peer signal sits in the shared reference helper, so every adapter that uses it gets it. No change goes around `potenda`. | Pass |
| IV. Type safety | New models are Pydantic models with explicit types. New exceptions are specific. | Pass |
| V. Test discipline | Unit tests for each requirement; live tests opt-in with the `integration` marker. | Pass |
| VI. Secrets | Timing documents are checked against a fixed list of keys. The operations digest covers plan operations, which hold no credential. | Pass |
| VII. Simplicity | One column, one observer protocol, one encoding reused from cardinality many. The operations digest is a new helper with two callers: the benchmark and the tests. | Pass |

Post-design re-check: pass. No violation, so "Complexity Tracking" is empty.

## Project Structure

### Documentation (this feature)

```text
dev/specs/019-second-plan-real-changes/
├── spec.md
├── plan.md              # This file
├── research.md          # R1 to R17
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── plan-format-5.md
│   ├── http-api.md
│   ├── cli.md
│   └── benchmark-record.md
├── checklists/requirements.md
└── tasks.md             # Created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── plan/
│   ├── models.py               # format 5, empty one-peer reference rule
│   ├── derive.py               # skip built-in deletes, emptied relationship, remove warning
│   ├── write_surface.py        # optional protocol: nodes that Infrahub creates itself
│   └── digest.py               # new: operations digest
├── adapters/
│   ├── infrahub.py             # mark built-in nodes on read, write an emptied relationship
│   └── references.py           # record peers removed by a filter
├── potenda/
│   ├── __init__.py             # plan-step observer calls, pass the built-in set and filter signal
│   └── plan_step.py            # new: observer protocol
├── execution.py                # private _plan_step_observer keyword
├── service/
│   ├── flow.py                 # store observer, end of saving, end on failure
│   ├── plan_step.py            # new: record and derive the parts
│   └── service.py              # add plan_step to the orchestration summary
├── product_store/
│   ├── store.py                # plan_step column, record_plan_step
│   └── models.py               # PrefectExecutionLink.plan_step
├── client/
│   ├── models.py               # PlanStepTimings, PlanStepPart
│   └── __init__.py             # exports
└── cli.py                      # runs show lines, runs plan --detail "becomes empty"

tasks/bench.py                  # new record fields, empty-plan check, left-out path
development/bench/records.py     # new record fields, remove builtin_deletes
tests/
├── plan/test_second_plan.py    # new
├── plan/second_plan.py         # new helper: ApplyingDestination
├── data/second_plan/           # new fixture data
├── data/v2_gate/expected_deletes.yml
├── adapters/test_*_peer_order.py
├── service/test_plan_step.py   # new
├── product_store/test_plan_step_column.py  # new
└── integration/test_infrahub_*_integration.py  # two opt-in live tests

docs/docs/
├── running-a-sync.mdx
├── reference/sync-http-api.mdx
├── reference/cli.mdx           # generated
├── development/knowledge/plan-artifact.md
└── development/guides/netbox-benchmark-{runner,tiers,results}.md
dev/adr/0043-*.md, dev/adr/0046-*.md
examples/netbox_to_infrahub/README.md
changelog/387.fixed.md, changelog/+plan-step-timings.added.md
```

**Structure Decision**: The existing single-project layout. Each change goes in the module that
owns the behavior today; three new modules (`plan/digest.py`, `potenda/plan_step.py`,
`service/plan_step.py`) follow the pattern of `service/read_progress.py`.

## Implementation notes

- **Order.** The probe of nodes that Infrahub creates itself (R2) and the live test of a write
  of `rel: null` (R8) come first, because both can change the design. If the probe finds no
  usable mark, or the write does not remove the edge, the implementation stops and this plan is
  amended.
- **User Story 1 needs the work of User Story 2.** The second-plan tests of User Story 1 include
  a LAG case that the emptied relationship affects. The tasks put the shared test helper first.
- **Two decision records.** ADR 0043, "A plan leaves out the deletes of nodes that Infrahub
  creates itself"; ADR 0046, "Saved plan format 5 records an emptied one-peer relationship".
  ADR 0041 and ADR 0042 get a one-line note that points to ADR 0043. The timings follow ADR 0035
  and need no new record.
- **Tests that assert format 4** are updated: `tests/plan/test_models.py:100`,
  `test_lineage_manifest.py:106`, `test_plan_format_3.py:94`, `test_problem_pass.py:331`,
  `test_problems.py:80`, `test_format_4.py:112, 310`.
- **`runs show` order.** The part lines come after the two read lines
  (`tests/cli/test_client_commands.py:876-890`).
- **Apply path.** Only an operation that holds an emptied one-peer relationship takes the new
  write path (research R8). Every other operation renders the same input as today.
- **Upgrade note.** The news fragment states that the API and the worker are upgraded together,
  because an earlier worker refuses a plan in format 5.
- **Benchmark machine.** User Stories 4 and 5 need it for their measurement only. Their code
  changes (record fields, empty-plan check) are unit-tested.

## Complexity Tracking

No violation.
