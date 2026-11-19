# Implementation Plan: Manage schedules from the web interface

**Branch**: `026-schedules-web-page` | **Date**: 2026-11-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/026-schedules-web-page/spec.md`

## Summary

Add schedule pages to the Sync web interface over the schedule routes that spec 021 delivered: a
schedules list, a schedule page with its state, stop reason and the action it needs, its next
starts and its event history, dialogs to create, change, pause, resume, delete and take over a
schedule with a cron helper and a preview of the next start times, a schedule filter and a
"Scheduled" mark on runs, and a schedule line on the configuration page.

The Sync API gets two read-only additions: `GET /schedules/next-starts`, which computes start
times with the scheduler's own code (`schedule_cadence`), and an `order` parameter on
`GET /events` for a newest-first history ([research R12, R13](research.md)). The pages reuse the
runs pages' routing, polling, paging, retry keys and refusal display. The change form builds its
request against the values it opened with, because the Sync API has no version check on a change
([critique E1](critiques/critique-20261119.md)). Each action's result is announced in the
existing live region (`components/announcer.tsx`). The permission rule, the state texts, the
change request, the cron helper and the history texts are pure functions with Vitest tests; the
two API additions have pytest tests.

## Technical Context

**Language/Version**: TypeScript 6, React 19 and Vite 8 (pnpm catalog in
`frontend/pnpm-workspace.yaml`) for the web interface; Python 3.11 to 3.13 for the Sync service.

**Primary Dependencies**: Frontend: TanStack Router (file routes) and TanStack Query through
`openapi-react-query` and `openapi-fetch`; `@infrahub/ui` (`Button`, `Modal`, `Select`,
`Checkbox`, `Card`). New development dependency: `vitest`, in a release that supports Vite 8,
added to the pnpm catalog. No new runtime dependency. Service: FastAPI and Pydantic; Prefect's
`CronSchedule` through `infrahub_sync/service/schedule_cadence.py`. No new Python dependency.

**Storage**: No new table or column. `ProductStore.list_events` gains a sort direction.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`) for the two API additions;
Vitest (`pnpm test`) for `frontend/src/domain/`; `pnpm check`, `pnpm typecheck` and `pnpm build`
in the `frontend-lint` CI job; `uv run invoke lint`; the scenarios of [quickstart.md](quickstart.md)
by hand against the development stack.

**Target Platform**: Current desktop and mobile browsers; the Sync service container (ADR 0043:
the site is served at the root, the API under `/api`).

**Project Type**: Single-page web interface of an existing web service, with two additions to the
service.

**Performance Goals**: Lists poll every 10 seconds and load 50 items a page, as the runs pages. A
preview answers within one second for 20 starts (one `CronSchedule` computation, no input or
output). The history reads 50 events a page, sorted in the store.

**Constraints**: No account identifier on any page (`AGENTS.md`, "Sync frontend"); no value of a
refused field repeated in a message; no second schedule or repeated action from a retry (FR-021);
no event written by the preview route; an existing `GET /events` caller sees no change.

**Scale/Scope**: Two new frontend routes, about eleven new frontend files, changes to five existing
frontend files; one new service route and one parameter, with tests; two docs pages, the frontend
README and one CI step.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only and dry-run by default | The pages read by default. Every write is a button the person presses, with a confirmation for delete and take-over. Both API additions are read-only and store nothing. | Pass |
| II. Sync idempotency and safety | Every `POST` sends an `Idempotency-Key` that stays the same across a retry (R4). The new route is a `GET`. | Pass |
| III. Adapter symmetry and pattern consistency | No adapter change. The pages copy the runs pages' patterns (R2 to R5). The new route follows `schedule_routes.py`; `order` follows the existing query parsing of `GET /events`. | Pass |
| IV. Type safety and explicit contracts | Requests and answers use the generated OpenAPI types, regenerated for the two additions. The new route has a response model. Unknown states, reasons and event types are shown as returned, not cast. | Pass |
| V. Test discipline | pytest for the route and the order, including a daylight-saving case and "no event on refusal"; Vitest for the pure rules (R9); quickstart scenarios for the pages. | Pass |
| VI. Security, secrets and input boundaries | The preview checks its values for a known credential like every request field. A `secret-parameter-refused` refusal is shown without the field value (R5). The Sync API enforces permissions; the page only hides what it would refuse. | Pass |
| VII. Simplicity and maintainability | No new runtime dependency; one development dependency (Vitest). No cron parser in the browser (R7, R12). | Pass |

Re-check after Phase 1: the design adds one read-only route and one query parameter, no storage
and no runtime dependency. All principles still pass.

## Project Structure

### Documentation (this feature)

```text
dev/specs/026-schedules-web-page/
├── spec.md
├── plan.md              # this file
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md      # the two Sync API additions
│   └── web-pages.md
├── critiques/
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
infrahub_sync/
├── service/
│   ├── schedule_routes.py            # + GET /schedules/next-starts, before /schedules/{schedule_id}
│   ├── schedules.py                  # + ScheduleService.next_starts (credential check of
│   │                                 #   _refuse_secret_values, without recording an event)
│   ├── app.py                        # + `order` on GET /events
│   └── service.py                    # + `order` in RunService.list_events
├── client/
│   └── models.py                     # + ScheduleNextStarts, next to ScheduleResource (:800)
└── product_store/
    └── store.py                      # + `order` in list_events (protocol and implementations)

tests/service/
├── test_schedule_next_starts.py      # new
└── test_events_route.py              # + order cases

frontend/
├── package.json                      # + vitest, "test" script
├── pnpm-workspace.yaml               # + vitest in the catalog
├── vite.config.ts                    # + test settings, if Vitest needs any
├── README.md                         # + schedule pages in the table of pages
└── src/
    ├── api/
    │   ├── types.generated.ts        # regenerated after the API additions
    │   ├── queries.ts                # + schedule queries, nextStartsQuery, scheduleEventsQuery,
    │   │                             #   describeScheduleError; runPagesQuery gains `schedule`
    │   └── actions.ts                # + six schedule mutation hooks
    ├── domain/
    │   ├── schedules.ts              # scheduleActions, stateView, labels, change and create requests
    │   ├── cadence.ts                # cronFromForm, formFromCron, cadence words, field labels
    │   ├── schedule-events.ts        # eventText
    │   └── *.test.ts                 # Vitest tests for the three modules
    ├── features/schedules/           # new
    │   ├── SchedulesPage.tsx
    │   ├── SchedulesTable.tsx
    │   ├── SchedulePage.tsx
    │   ├── ScheduleNotices.tsx
    │   ├── CadenceField.tsx          # helper, custom field labels, preview
    │   ├── NextStarts.tsx            # list of start times (form preview and schedule page)
    │   ├── ScheduleHistory.tsx       # history section
    │   ├── ScheduleFormDialog.tsx    # create and change
    │   ├── ScheduleActionDialogs.tsx # pause, resume, delete, take over
    │   ├── ScheduleLine.tsx          # configuration page and run page lines
    │   └── time-zones.ts
    ├── routes/
    │   ├── __root.tsx                # + "Schedules" entry
    │   ├── schedules.index.tsx       # new
    │   ├── schedules.$scheduleId.tsx # new
    │   ├── runs.index.tsx            # + `schedule` search parameter
    │   └── routeTree.gen.ts          # regenerated
    └── features/
        ├── runs/RunsPage.tsx, RunsTable.tsx, RunPage.tsx
        └── configurations/ConfigurationPage.tsx

docs/docs/orchestration.mdx           # + "Manage schedules in the web interface"
docs/docs/reference/sync-http-api.mdx # + next-starts route, `order` on events
.github/workflows/workflow-linter.yml # + `pnpm test` in frontend-lint
changelog/+schedules-web-interface.added.md
```

**Structure Decision**: The web interface is one TanStack Router application under `frontend/`.
New pages follow the existing split: route files in `routes/`, page components in
`features/<area>/`, pure rules in `domain/`, Sync API access in `api/`. The service additions sit
in the modules that already hold the schedule routes and the events route.

## Order of work

1. The two Sync API additions with their tests, then regenerate `types.generated.ts` against a
   Sync API built from this branch (`pnpm codegen:openapi`).
2. `domain/` modules and their tests.
3. Queries and actions.
4. List, navigation entry and read-only schedule page, with next starts (User Story 1).
5. Runs filter, "Scheduled" mark and run page line (User Story 2).
6. History (User Story 7).
7. Pause, resume and delete (User Story 3).
8. Create dialog with the cadence helper and preview, configuration page line (User Story 4).
9. Change dialog (User Story 5).
10. Take over (User Story 6).
11. Docs, README, changelog, CI step; quickstart by hand.

Each step leaves the interface working. After step 6 the feature is complete for reading.

## Complexity Tracking

No constitution violation to justify.
