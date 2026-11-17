# Implementation Plan: Scheduled runs that write without a person present

**Branch**: `021-scheduled-runs` | **Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/021-scheduled-runs/spec.md`

## Summary

An operator creates a schedule for one version of a configuration, with a cron expression and a
time zone. Prefect starts a `sync` run at each start time, as the schedule's owner.
By default the run writes to a new Infrahub branch and opens a proposed change, which replaces
the schedule's earlier unapproved one. On a Sync API that does not require approval, a schedule
can write to the default branch instead.

Technical approach, from [research.md](research.md):

- Prefect holds the cadence: each Sync schedule has one Prefect schedule on the existing
  deployment, as Infrahub does for its system workflows. Infrahub Sync has no loop (R1).
- The worker admits a flow run that Prefect's scheduler created: it writes the run record, or
  records why the start does not run. One run for each schedule and start time (R2).
- The worker's `sync` stage gains the left-out setting (R5) and the branch review steps (R11):
  the branch is created before the plan, and the proposed change is opened after the write is
  committed.
- Schedule events use the existing event record, with the schedule as a related resource and a
  new `details` field (R9).

## Order of delivery

The product owner decided the order on 2026-11-17. Nothing in this plan is blocked.

| Step | Content | Delivered to a team |
| --- | --- | --- |
| 1 | Foundation, and the service side of user story 1: schedule record, routes, Prefect schedule, client, CLI, admission of a start, `reject` policy, trigger type, events | No. Both write targets are refused until they are delivered. |
| 2 | The branch and proposed change operations (FR-029, FR-030), and the worker side of user story 1 | **Yes: scheduled runs with review in Infrahub.** |
| 3 | User story 2: a write to the default branch and the left-out setting | Yes |
| 4 | User story 3: `queue` policy, late run, newer-version warning, take-over | Yes |

Steps 1 and 2 are merged together, because step 1 alone gives a team nothing to use.

## Technical Context

**Language/Version**: Python 3.11 to 3.13 for the Sync service and its tests. The CLI and the
client models also run on Python 3.10.

**Primary Dependencies**: FastAPI 0.141.1, Prefect 3.8.6, Pydantic 2, Typer, httpx, psycopg 3.
No new dependency: Prefect validates the cadence and computes the start times (R3).

**Storage**: the product store: PostgreSQL in a deployment, SQLite in the unit tier. Two new
tables, three new columns and two indexes on `product_runs`, one new column on `audit_events`
(R12).

**Testing**: pytest. Unit tier with the SQLite store, a fake orchestration and times that the
test passes (R14). PostgreSQL product store tier for two admissions of one start. Smoke tier on
the development stack, which is the only tier that runs Prefect's scheduler.

**Target Platform**: the Compose deployment (`docker-compose.yml`), Linux containers.

**Project Type**: a web service with a worker, a CLI and an HTTP client, in one Python package.

**Performance Goals**: a run starts within 1 minute of its start time while the worker and the
Prefect server are running (SC-007). Prefect marks a run late after 15 seconds, and the worker asks for
work every 10 seconds.

**Constraints**: no credential in a schedule record or an event. No new container and no
periodic task in the API process. `POST /runs` is not changed. A product database of an earlier build must be replaced (R12).

**Scale/Scope**: at most one schedule for each configuration, so tens of schedules for each deployment, with cadences from one minute to one
week. About 8 routes, 8 CLI commands, 2 tables.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result | Note |
| --- | --- | --- |
| I. Read-only and dry-run by default | Pass, with the amendment of version 1.1.0 | The product owner decided on 2026-11-17 to amend the principle, and the amendment is on this branch. Branch review is the default write target, and the person's approval is the merge of the proposed change in Infrahub. A write to the default branch needs an explicit option (`--write-to-default-branch`), and creating the schedule with it is the deliberate act. |
| II. Sync idempotency and safety | Pass | A start creates one run (R2). Making the Prefect schedules match the stored ones can be repeated (R13). Repeating the branch review steps is safe (contract, requirement 4). A failed run replaces nothing. |
| III. Adapter symmetry and pattern consistency | Pass | No adapter contract changes in this plan. Routes, models, client methods and commands follow the existing conventions (R13). |
| IV. Type safety and explicit contracts | Pass | New models are Pydantic models with closed literals. New refusals have their own codes. |
| V. Test discipline | Pass | Each requirement has a unit test with times that the test passes. Two admissions of one start have a PostgreSQL test. What Prefect's scheduler does is shown only on the development stack. |
| VI. Security, secrets, and input boundaries | Pass | A schedule stores an actor name, never a token (R8). Events hold identifiers only (FR-025). The cron expression and the time zone are validated at the API boundary. |
| VII. Simplicity and maintainability | Pass | No new container, no new package, no scheduler of our own: Prefect's is used. The five branch and proposed change operations are plain functions on the Infrahub client, with no interface in front of them. |

Re-check after design, after the critique and after the move to Prefect schedules: the result is
the same. The design adds no periodic task and no new abstraction.

## Project Structure

### Documentation (this feature)

```text
dev/specs/021-scheduled-runs/
├── spec.md
├── plan.md              # This file
├── research.md          # Decisions R1 to R14
├── data-model.md        # Schedule, schedule branch, run, event, receipt
├── quickstart.md        # Validation guide
├── contracts/
│   ├── http-api.md      # Schedule routes and changed run and event routes
│   ├── cli.md           # `infrahub-sync schedules` and changed commands
│   ├── events.md        # Schedule event types
│   └── branch-review.md # The five branch and proposed change operations
├── checklists/requirements.md
└── tasks.md             # Written by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── client/
│   ├── models.py            # Schedule request, resource and page models; run trigger fields
│   └── client.py            # Schedule methods of SyncClient; new run and event filters
├── cli.py                   # `schedules` command group; changed `runs` and `events` commands
├── product_store/
│   ├── models.py            # Schedule and schedule branch records; event and receipt kinds
│   └── store.py             # Two tables, two run columns, schedule methods, run in progress
└── service/
    ├── app.py               # Mounts the schedule router; matches Prefect to the store at startup
    ├── auth.py              # Read the principal of a named actor
    ├── events.py            # Schedule event types and builders
    ├── branch_review.py     # New: create and delete a branch; open, read and close a proposed change
    ├── flow.py              # Admission of a scheduled flow run; left-out setting; branch review steps
    ├── failure_cause.py     # Failure classes of the branch review steps
    ├── models.py            # Re-exports and emitted variants of the schedule models
    ├── orchestration.py     # Create, update and delete the Prefect schedule of a schedule
    ├── schedule_cadence.py  # New: validate a cadence and compute start times with Prefect
    ├── schedule_routes.py   # New: the schedule router
    ├── schedules.py         # New: schedule requests, rules, and the match of Prefect to the store
    ├── serve.py             # Builds the schedule service
    └── service.py           # Refuses verify, apply and resume of a run with branch review

tests/
├── service/                 # test_schedule_*.py; test_http_api.py route set
├── product_store/           # test_schedules.py
├── client/                  # test_schedules.py
├── cli/                     # test_schedule_commands.py; test_parity_and_closure.py matrix
└── dev_stack/smoke/         # test_scheduled_run.py

docs/docs/
├── development/constitution.md                    # Principle I, version 1.1.0
├── compose-deployment.mdx                         # Permissions of the worker's token in Infrahub
├── orchestration.mdx                              # "Schedule sync runs": the built-in schedule
├── operations/supported-platforms-and-limits.mdx  # Remove "no built-in schedule"
├── readme.mdx, migrating-from-netbox-or-nautobot.mdx
├── reference/sync-http-api.mdx                    # Routes, events, approval section
├── reference/durable-product-records.mdx          # Schedule records
├── reference/cli.mdx                              # Generated
└── development/knowledge/sync-architecture.md     # The schedule loop

frontend/src/api/types.generated.ts                # Regenerated; no interface change
changelog/+scheduled-runs.added.md
```

**Structure Decision**: the feature stays inside the existing package. The service gains three
modules with the prefix `schedule` and one for the branch and proposed change operations, so that `service.py` and `app.py`, which are already large,
grow by a method and a mount each.

## Implementation notes

- **Order of work.** The product store and the models come first, then the cadence module, the
  service rules, the routes, the admission in the worker, the client and the CLI. Each step has its unit tests
  before the next.
- **Two records of one schedule.** The stored schedule is the source. One step makes the Prefect
  schedules of the deployment match it, by slug. It runs at the end of the bootstrap job, when the API process starts, and after each schedule
  request. The bootstrap job needs it because applying the deployment again removes its Prefect
  schedules (R13). In the same step at startup, the API process
  stops a schedule whose owner's actor no longer exists or whose write target now needs
  approval.
- **What the unit tier cannot show.** Prefect's scheduler does not run in the unit tier. The
  unit tests call the admission with a flow run that the test describes. That Prefect creates
  the flow runs at the right times, in the right time zone, is shown on the development stack.
- **Python 3.10.** Prefect is imported only in the service modules. `client/models.py` and
  `cli.py` carry the cron expression and the time zone as text and do not validate them.
- **An earlier product database.** At startup the service checks that `product_runs` and
  `audit_events` hold the new columns, and refuses to start with a message that says the
  database must be replaced.
- **Tests that pin a list.** These must be updated with the feature:
    - `tests/service/test_http_api.py` (the set of routes)
    - `tests/cli/test_parity_and_closure.py` (the command, method and route matrix)
    - `tests/product_store/test_contract.py` (public names, receipt kinds, column lists)
    - `tests/product_store/test_run_approval.py` and `test_postgresql_isolation.py` (column
      lists)
    - `tests/test_run_events_docs.py` (documented event types)
- **Settings by user story.** `overlap_policy = queue` and `start_late_by_at_most_seconds` are
  fields of the record from the start, and are refused with `schedule-setting-not-available`
  until user story 3 is delivered. The record then needs no change of its table.
- **A write target that is not delivered is refused.** `ScheduleService` receives the set of
  write targets that are delivered. `serve.py` passes that set; the unit tests pass both.
    - `branch-review` is refused with `schedule-setting-not-available` until the worker side of
      user story 1 exists. Without this, a schedule would start a run at every start that fails in
      the worker, and on a Sync API that requires approval those runs would be accepted with no
      approval and no review.
    - `default-branch` is refused the same way until user story 2 is delivered.
    - While branch review is not delivered, a request that names no write target is refused. The
      default branch is never chosen for the operator.
  The last task of each of the two phases adds its write target to the set.
- **Flow parameters.** `service_sync_run` gains the parameter `schedule_id`, and `run_id` becomes
  optional. The schedule's settings reach the stage through the result of the admission, not as
  flow parameters. The flow refuses a run with branch review until the worker side exists.
- **Decision records.** Two records are written when this specification is extracted: Prefect
  holds the cadence and the worker admits a scheduled flow run (R1, R2), and a write to a branch that Infrahub Sync created
  for a run needs no Sync approval (R6, amends ADR 0037).
- **Product documents outside this repository.** The INFP-656 card and
  `infrahub-sync-process/product/capabilities.md` describe a schedule inside the configuration, a
  webhook on each schedule and a scheduler with "no special casing". This plan does not change
  them.

## Complexity Tracking

No violation. The two points that an earlier version of this plan listed against principle I are
settled by the amendment of version 1.1.0 of the constitution.
