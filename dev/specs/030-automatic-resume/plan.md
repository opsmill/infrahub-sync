# Implementation Plan: Automatic resume of an interrupted apply

**Branch**: `030-automatic-resume` | **Date**: 2026-11-23 | **Spec**: [spec.md](./spec.md)

## Summary

When the liveness reconciler ends a write execution because its worker disappeared (rule 6 with a `crashed` or
`failed` flow run, or rule 8), the product store records a decision for that execution in the same transaction. When
the site turned automatic resume on and the run qualifies, the reconciler then steps a stored state machine on its
next loops: it waits for a live worker, submits the automatic check through `RunService.verify_run`, reads the
check's result, and submits the resume through `RunService.resume_run`. Both submissions use the reserved actor
`sync:automatic-resume` and idempotency keys derived from the interrupted execution, so the existing receipts give
exactly-once submission across restarts and across several Sync API processes. The run resource, `runs show` and the
run page show each decision and its reason. Schedules count the run by its final result. The constitution's
Principle I gains a named case, and a new ADR amends ADR 0027. Design decisions are in [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11–3.13 (service profile); the CLI and client also run on 3.10. TypeScript for the web
interface (`frontend/`).

**Primary Dependencies**: FastAPI, Pydantic, Typer, HTTPX, Prefect client, React (no new dependency).

**Storage**: the product store on SQLite (tests, local) and PostgreSQL (service). One new table `automatic_resumes`
(data-model.md). No change to existing tables. No backfill; pre-release V3 databases are recreated (ADR 0010).

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`): `tests/service/`, `tests/product_store/`,
`tests/client/`, `tests/cli/`. The PostgreSQL isolation test (`PRODUCT_STORE_TEST_POSTGRESQL_DSN`) for two concurrent
reconcilers. Vitest for the run page. The preview stack check of [quickstart.md](./quickstart.md) for SC-001.

**Target Platform**: Linux server (Sync API and worker); CLI on any platform; the web interface in a browser.

**Project Type**: web service with a CLI client and a web interface.

**Performance Goals**: the reconciler's extra step reads only rows that are not `ended`, so a loop with no
interrupted run does one indexed query more. An automatic resume starts within 2 reconciler loops of a live worker
appearing (the check) plus the check's own duration.

**Constraints**:

- The API stays `v3-unstable`; every response change is additive.
- With the setting off (the default), nothing changes for operators, apart from rows ending `turned-off` and their
  events. Every existing test passes unchanged.
- No secret, source value or destination value in a decision row, an event, a log or an error
  (`docs/docs/development/guidelines/secret-redaction.md`). Rows hold codes, counts and identifiers only.
- The vendored `opsmill_prefect_extras` stays byte-identical.
- No HTTP credential can resolve to the actor `sync:automatic-resume`.

**Scale/Scope**: about 10 source files, 10 test files, 4 documentation pages, the constitution, 1 ADR, the web run
page.

## Constitution Check

| Principle | Status |
| --- | --- |
| I. Read-only and dry-run by default | Crossed, justified. An automatic resume is a write that no person starts at that moment. The spec requires an amendment that adds automatic resume as a named case (FR-019, research R13). The write is behind a flag that is off by default, which meets "New mutating behavior MUST ship behind explicit flags". |
| II. Sync idempotency and safety | Pass. A resume repeats only convergent upserts (ADR 0012, ADR 0013). The check stops the resume on any `differs`. Exactly-once submission (research R4). |
| III. Adapter symmetry and pattern consistency | Pass. No adapter change. The submission reuses `verify_run` and `resume_run`. |
| IV. Type safety and explicit contracts | Pass. New Pydantic fields; the HTTP contract is in contracts/. |
| V. Test discipline | Pass. Unit tests per state move and per reason; the PostgreSQL concurrency test; a preview stack check. |
| VI. Security, secrets, and input boundaries | Pass. The reserved actor is not an account and no credential resolves to it; rows hold no values. |
| VII. Simplicity and maintainability | Pass. One table and one loop step; no new process, no new dependency. |

**Complexity Tracking**: Principle I is the only crossing, and the amendment is part of this work.

Re-check after design: unchanged. The design adds no other crossing.

## Project Structure

### Documentation (this feature)

```text
dev/specs/030-automatic-resume/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── cli-and-web.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
infrahub_sync/
├── product_store/
│   ├── store.py          # automatic_resumes table; insert in interrupt_execution; compare-and-set moves;
│   │                     # _run_in_progress and _settle_scheduled_run_end changes (in-memory and SQL stores)
│   └── models.py         # AutomaticResume record and its codes
├── service/
│   ├── liveness.py       # decision at rules 6 and 8; step of the rows; live_workers(); actor and limit constants
│   ├── service.py        # _owned_run accepts the reserved actor for a decided run; a person's verify or
│   │                     # resume ends a waiting row (research R15)
│   ├── settings.py       # INFRAHUB_SYNC_AUTOMATIC_RESUME
│   ├── presentation.py   # shows the reserved actor as "Sync (automatic resume)"
│   ├── events.py         # the two new event types
│   ├── models.py         # run resource fields (service side)
│   └── app.py            # passes the RunService to the reconciler
├── client/models.py      # run resource fields (client side)
└── cli.py                # runs show and runs list output
frontend/src/features/runs/   # run page notices and the resume action rule
docs/docs/                    # day-2 operations, running a sync, HTTP reference, deployment settings
dev/adr/0055-…md              # new ADR
.specify/memory/constitution.md
tests/
├── service/test_automatic_resume.py      # decisions, moves, reasons, limits, restarts
├── service/test_liveness_policy.py       # rule 6 states, live_workers()
├── product_store/test_automatic_resumes.py
├── product_store/test_postgresql_isolation.py  # two reconcilers, one submission
├── service/test_schedule_*.py            # count by final result, overlap
├── cli/                                   # runs show blocks
└── client/                                # model fields
```

**Structure Decision**: the existing service layout. The decision and its loop step live with the liveness
reconciler, because only it knows rules 6 and 8 (research R1).

## Delivery order

1. Store: table, record, compare-and-set moves, interruption insert (both stores).
2. Reconciler: decision, rule 6 states, live workers, the step loop with the reserved actor.
3. Service: ownership rule for the reserved actor, a person taking over a waiting row, the setting.
4. Schedules: count by final result, overlap.
5. Presentation, events, run resource, client, CLI.
6. Web run page.
7. Governance and documentation: constitution amendment, ADR 0055, user documentation, changelog fragment.
8. The preview stack check (SC-001) and the refusal matrix (SC-002).
