# Implementation Plan: Manage schedules from the web interface

**Branch**: `026-schedules-web-page` | **Date**: 2026-11-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/026-schedules-web-page/spec.md`

## Summary

Add schedule pages to the Sync web interface over the schedule routes that spec 021 delivered: a
schedules list, a schedule page with its state, stop reason and the action it needs, dialogs to
create, change, pause, resume, delete and take over a schedule, a schedule filter and a
"Scheduled" mark on runs, and a schedule line on the configuration page. The Sync API does not
change ([research R1](research.md#r1-whether-the-sync-api-covers-every-page)). The pages reuse
the runs pages' routing, polling, paging, retry keys and refusal display. The permission rule,
the state texts and the change request are pure functions with Vitest tests.

## Technical Context

**Language/Version**: TypeScript 6, React 19 and Vite 8, from the pnpm catalog in
`frontend/pnpm-workspace.yaml`. No Python change.

**Primary Dependencies**: TanStack Router (file routes) and TanStack Query through
`openapi-react-query` and `openapi-fetch`; `@infrahub/ui` (`Button`, `Modal`, `Select`,
`Checkbox`, `Card`). New development dependency: `vitest`, in a release that supports Vite 8,
added to the pnpm catalog.

**Storage**: None. Every value comes from the Sync API.

**Testing**: Vitest for `frontend/src/domain/schedules.ts` (`pnpm test`); `pnpm check` (Biome),
`pnpm typecheck` and `pnpm build`, which the `frontend-lint` CI job runs; the scenarios of
[quickstart.md](quickstart.md) by hand against the development stack.

**Target Platform**: Current desktop and mobile browsers; the site is served by the Sync service
at the root (ADR 0043).

**Project Type**: Single-page web interface of an existing web service.

**Performance Goals**: The same as the runs pages: lists poll every 10 seconds and load 50 items a
page.

**Constraints**: No account identifier on any page (`AGENTS.md`, "Sync frontend"); no value of a
refused field repeated in a message; no second schedule or repeated action from a retry (FR-021);
no Sync API change.

**Scale/Scope**: Two new routes, about eight new frontend files, changes to five existing frontend
files, one docs page, the frontend README and one CI step.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only and dry-run by default | The pages read by default. Every write is a button the person presses, with a confirmation for delete and take-over. Scheduled runs write only as spec 021 defined; this feature adds no write path. | Pass |
| II. Sync idempotency and safety | Every `POST` sends an `Idempotency-Key` that stays the same across a retry (R4). | Pass |
| III. Adapter symmetry and pattern consistency | No adapter change. The pages copy the runs pages' patterns (R2 to R5). | Pass |
| IV. Type safety and explicit contracts | Requests and answers use the generated OpenAPI types. Unknown states and reasons are shown as returned, not cast. | Pass |
| V. Test discipline | Vitest tests for the permission rule, state texts and change request (R9); quickstart scenarios for the pages. | Pass |
| VI. Security, secrets and input boundaries | No secret is handled. A `secret-parameter-refused` refusal is shown without the field value (R5). The Sync API enforces permissions; the page only hides what it would refuse. | Pass |
| VII. Simplicity and maintainability | No new runtime dependency; one development dependency (Vitest). No cron parser in the browser (R7). | Pass |

Re-check after Phase 1: the design adds no Sync API route, no storage and no runtime dependency.
All principles still pass.

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
│   └── web-pages.md
├── checklists/
│   └── requirements.md
└── tasks.md             # created by speckit-tasks
```

### Source Code (repository root)

```text
frontend/
├── package.json                      # + vitest, "test" script
├── vite.config.ts                    # + test settings, if Vitest needs any
├── README.md                         # + schedule pages in the table of pages
└── src/
    ├── api/
    │   ├── queries.ts                # + schedulePagesQuery, scheduleQuery, configScheduleQuery,
    │   │                             #   describeScheduleError; runPagesQuery gains `schedule`
    │   └── actions.ts                # + useCreateSchedule, useChangeSchedule, usePauseSchedule,
    │                                 #   useResumeSchedule, useDeleteSchedule, useTakeOverSchedule
    ├── domain/
    │   ├── schedules.ts              # new: scheduleActions, stateText, changeRequest, labels
    │   └── schedules.test.ts         # new: Vitest tests
    ├── features/schedules/           # new
    │   ├── SchedulesPage.tsx         # list, filters, empty states, load older
    │   ├── SchedulesTable.tsx        # table at sm and wider, list on small screens
    │   ├── SchedulePage.tsx          # header, notices, next start, settings, branch
    │   ├── ScheduleNotices.tsx       # stop panel, task-manager warning, newer version
    │   ├── ScheduleFormDialog.tsx    # create and change
    │   ├── ScheduleActionDialogs.tsx # pause, resume, delete, take over
    │   └── ScheduleLine.tsx          # schedule line for the configuration page and run page
    ├── routes/
    │   ├── __root.tsx                # + "Schedules" entry, sectionOf case
    │   ├── schedules.index.tsx       # new
    │   ├── schedules.$scheduleId.tsx # new
    │   ├── runs.index.tsx            # + `schedule` search parameter
    │   └── routeTree.gen.ts          # regenerated
    └── features/
        ├── runs/RunsPage.tsx         # + schedule filter shown and removable
        ├── runs/RunsTable.tsx        # + "Scheduled" mark
        ├── runs/RunPage.tsx          # + "Started by schedule" line
        └── configurations/ConfigurationPage.tsx  # + schedule line, create dialog

docs/docs/orchestration.mdx           # + "Manage schedules in the web interface"
.github/workflows/workflow-linter.yml # + `pnpm test` in frontend-lint
changelog/+schedules-web-interface.added.md
```

**Structure Decision**: The web interface is one TanStack Router application under `frontend/`.
New pages follow the existing split: route files in `routes/`, page components in
`features/<area>/`, pure rules in `domain/`, Sync API access in `api/`. Names of new files follow
the existing `features/runs/` naming.

## Order of work

1. `domain/schedules.ts` and its tests (R6, R9): no dependency on the pages.
2. Queries and actions (R3, R4, R5).
3. The schedules list, the navigation entry and the schedule page, read only (User Story 1).
4. The runs filter, the "Scheduled" mark and the run page line (User Story 2).
5. Pause, resume and delete dialogs (User Story 3).
6. Create dialog and the configuration page line (User Story 4).
7. Change dialog (User Story 5).
8. Take-over dialog (User Story 6).
9. Docs, README, changelog, CI step; quickstart by hand.

Each step leaves the interface working, so the work can stop after step 4 with a read-only
feature.

## Complexity Tracking

No constitution violation to justify.
