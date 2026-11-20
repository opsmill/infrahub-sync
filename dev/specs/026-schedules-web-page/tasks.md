# Tasks: Manage schedules from the web interface

**Input**: Design documents from `dev/specs/026-schedules-web-page/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/http-api.md](contracts/http-api.md),
[contracts/web-pages.md](contracts/web-pages.md), [quickstart.md](quickstart.md), critiques
[first](critiques/critique-20261119.md) and [revision](critiques/critique-20261119-revision.md)

**Tests**: pytest for the two Sync API additions; Vitest for the pure functions in
`frontend/src/domain/` (research R9). Pages are checked with the quickstart scenarios.

**Paths**: frontend paths are relative to `frontend/src/` unless they start with `frontend/`.
Other paths are relative to the repository root. Run frontend commands from `frontend/`.

**Patterns to copy**: routes `routes/runs.index.tsx` and `routes/runs.$runId.tsx`; paging
`runPagesQuery` (`api/queries.ts:127-153`); actions and retry keys `api/actions.ts` and
`features/runs/StartRunDialog.tsx:138-193`; dialogs `DialogFrame`, `Field`, `ReasonField`,
`MutationError` in `features/runs/form.tsx`; confirmations in `routes/accounts.tsx:441,513,597`;
permission text `features/runs/RunPage.tsx:454,580-586`; page parts `components/page.tsx`; times
`components/run-status.tsx` (`When`) and `lib/time.ts`; titles `lib/title.ts`; announcements
`components/announcer.tsx`. Service: `infrahub_sync/service/schedule_routes.py`,
`infrahub_sync/service/schedules.py`, the validate route at
`infrahub_sync/service/config_routes.py:557` (a `POST` without a retry key).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 to US7)

---

## Phase 1: Setup

- [X] T001 Add `vitest` (a release that supports Vite 8) to the pnpm catalog in `frontend/pnpm-workspace.yaml` and to `devDependencies` in `frontend/package.json` as `catalog:`; add the script `"test": "vitest run"`; run `pnpm install` from `frontend/` and commit `frontend/pnpm-lock.yaml`. Add a `test` block to `frontend/vite.config.ts` only if Vitest needs one (environment `node`, include `src/**/*.test.ts`). Check that `pnpm check` and `pnpm typecheck` accept `*.test.ts` files.
- [X] T002 [P] Add a step `pnpm test` after `pnpm typecheck` in the `frontend-lint` job of `.github/workflows/workflow-linter.yml` (lines 133-166), with the same working directory as the other `pnpm` steps.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The two Sync API additions, the refreshed API types, the pure rules, and the queries
and actions every story uses.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Sync API additions

- [X] T003 Write `tests/service/test_schedule_next_starts.py` first (it fails until T005), using the fixtures of `tests/service/schedule_fixtures.py` and a fixed service clock: the answer's `starts` equal `schedule_cadence.starts_between(cron, timezone, now, now + 5 years, count)`; default `timezone` `UTC` and `count` 5; `count` 0 and 21 refused with `request-invalid`; `30 2 28 3 *` in `Europe/Paris` returns `2027-03-28T00:30:00Z` first (the value Prefect computes); `0 0 29 2 *` returns fewer than 5 starts; `0 0 30 2 *`, `61 * * * *` and a four-field expression refused with `schedule-cron-invalid`; `Mars/Olympus` refused with `schedule-timezone-invalid`, with the same messages as `POST /schedules`; a known credential as `cron` refused with `secret-parameter-refused` and not repeated in the body; no refusal adds an event to the event record; a create with the same `cron` and `timezone` answers a `next_start_at` equal to the first preview start (SC-007); no `Idempotency-Key` is needed.
- [X] T004 [P] Add `ScheduleNextStartsRequest` (`cron: str`, `timezone: str = "UTC"`, `count: int` from 1 to 20, default 5) and `ScheduleNextStarts` (`cron`, `timezone`, `starts: list[datetime]`, `searched_until: datetime`) to `infrahub_sync/client/models.py`, next to `ScheduleResource` (`:800`), following the base classes used there.
- [X] T005 Add `ScheduleService.next_starts(request)` to `infrahub_sync/service/schedules.py`: check `cron` and `timezone` for known credential values with the logic of `_refuse_secret_values` (read it first: if it records an event through `_refusal`, factor the check so the preview raises with `_error` and records nothing); call `schedule_cadence.starts_between(cron, timezone, now, now + timedelta(days=5 * 366), count)` with the service clock; map `InvalidCadenceError` to `schedule-cron-invalid` or `schedule-timezone-invalid` with `_error`, using the same message as the create path (`:1081-1083`). Add `POST /schedules/next-starts` to `infrahub_sync/service/schedule_routes.py` with `Depends(authenticate)`, no `idempotency_key` dependency, `response_model=ScheduleNextStarts`. Run `uv run pytest tests/service/test_schedule_next_starts.py`.
- [X] T006 [P] Add `order` cases to `tests/service/test_events_route.py` (they fail until T007): `desc` returns the reverse of `asc` for the same filters; events with the same `created_at` are ordered by `event_id` descending; no `order` gives the same answer as before; `order=up` is refused with `request-invalid`; `desc` with `related=schedule:<id>`, `until`, `limit` and `offset` pages without overlap.
- [X] T007 Add `order` (`asc` default, `desc`) to `GET /events` in `infrahub_sync/service/app.py:282` (refuse other values with the existing `request-invalid` error, as the other query values are refused), to `RunService.list_events` in `infrahub_sync/service/service.py:1511`, to the store protocol's `list_events` at `infrahub_sync/product_store/store.py:1031` (or to `EventFilters`), and to every implementation and test double of it (search for `def list_events`). In `store.py:3306`, use `ORDER BY created_at DESC, event_id DESC` for `desc`. Check the query plan for `desc` with a `related` filter on SQLite and PostgreSQL with the existing indexes and record the result in the pull request (revision critique E6). Run `uv run pytest tests/service/test_events_route.py`.
- [X] T008 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .` and `uv run invoke tests.tests-unit`; all pass.
- [X] T009 Restart the development stack's Sync API from this branch (`uv run invoke dev.restart`), then run `pnpm codegen:openapi` from `frontend/` to refresh `api/types.generated.ts`; check that the diff adds only the new route, its two models and the `order` parameter. Done on 2026-11-20 against this branch's Sync API on port 8012, connected to the development stack: the generated file is identical to the committed one.

### Pure rules (frontend)

- [X] T010 [P] Create `domain/schedules.ts` with, using the generated types:
  - `type ScheduleAction = "change" | "pause" | "resume" | "delete" | "take-over"` and `scheduleActions(me, schedule)` returning, per action, `{shown, available, unavailableReason?}` per data-model "Permissions". While `me` is undefined, every action allowed by the state is shown and available. The unavailable text is "Only the owner of this schedule or an administrator can do this."
  - `stateView(schedule)` returning `{label, panel?: {text, action?, offers}}` per data-model "State and stop reason"; unknown states and reasons return the raw value and no action.
  - `writeTargetLabel`, `overlapPolicyLabel`, `lateStartLabel(seconds)` (null → "Not set", 3600 → "1 hour").
  - `ScheduleFormValues`, `formValuesOf(schedule)`, `changeRequest(opening, form, reason)` holding only fields that differ from `opening` plus a non-empty `reason`, `null` when no field differs; `createRequest(form, name, configId, reason)`; `differsFromOpening(opening, schedule)`.
  - `scheduleErrorField(code)` per data-model "Refusals mapped to a field".
- [X] T011 [P] Create `domain/cadence.ts`: `CadenceForm` (`minutes {every}` with every in 5, 10, 15, 20, 30, `hourly {minute}`, `hours {every, minute}` with every in 2, 3, 4, 6, 8, 12, `daily {hour, minute}`, `weekly {days, hour, minute}`, `monthly {day, hour, minute}`, `custom {cron}`), `cronFromForm(form)`, `formFromCron(cron)` recognizing only the exact patterns of data-model "Cadence helper forms" (plain integers in range, sorted unique days 0-6, everything else `custom`), `cadenceWords(form)` with the words of that table (two-digit hours and minutes; "Months without day D have no start." for D above 28), and `cronFields(cron)` returning the five labelled fields or `null` when the expression does not have five fields.
- [X] T012 [P] Create `domain/schedule-events.ts`: `eventText(event)` returning `{text, detail?}` from `event_type`, `outcome`, `details` and the stop-reason texts of `stateView`, per data-model "History entries", with the grouped missed-start text; `eventRunId(event)` from the `related` entry of kind `run`; `DEFAULT_HISTORY_EVENT_TYPES`, the list of the `infrahub_sync.schedule.*` types of the spec 021 events contract except `infrahub_sync.schedule.replayed` (Clarifications, 2026-11-20).
- [X] T013 Create `domain/schedules.test.ts`, `domain/cadence.test.ts` and `domain/schedule-events.test.ts` with Vitest, parametrized with `it.each`:
  - `scheduleActions`: owner, administrator who is not owner, other account, `me` undefined, deleted schedule, owner `Deleted account 3f2a9c`, pause only when active, resume only when paused or stopped, take-over only for an administrator who is not the owner.
  - `stateView`: each data-model row, an unknown reason, an unknown state.
  - `changeRequest`: no change → `null`; reason only → `null`; one field → only that field and the reason; a later read that changed another field is not in the request (first critique E1).
  - `cadence`: each form both directions (`0 2 * * *` ↔ daily 02:00; `30 6 * * 1,4` ↔ weekly Mon, Thu 06:30; `15 * * * *` ↔ hourly 15; `*/15 * * * *` ↔ every 15 minutes; `10 */4 * * *` ↔ every 4 hours at minute 10; `0 3 31 * *` ↔ monthly 31 with the note); these stay custom: `*/7 * * * *`, `0 */5 * * *`, `*/15 9-17 * * 1-5`, `0 2 * * 7`, `0 2 * * 4,1`, `0 2 1 1 *`, `00 2 * * *`, `0 24 * * *`; `cronFields` on four and six fields → `null`.
  - `eventText`: every schedule type and outcome of the table, the grouped missed-start event, an unknown type, a run event.

### Queries and actions

- [X] T014 [P] In `api/queries.ts`, add `schedulePagesQuery({state, config})` copied from `runPagesQuery` (key `["get", "/schedules", "pages", filters]`, `limit: "50"`), `scheduleQuery(scheduleId)` polling every `LIST_REFRESH_MS` through `poll()`, `configScheduleQuery(configId)` (`config_id`, `limit: "1"`), `nextStartsQuery(cron, timezone, count)` calling `POST /schedules/next-starts` (key `["post", "/schedules/next-starts", {cron, timezone, count}]`, `staleTime` 60 s, `retry: false`, `enabled` only when `cronFields(cron)` is not null), and `scheduleEventsQuery(scheduleId, {includeAll})` as an infinite query on `GET /events` with `related=schedule:<id>`, `order=desc`, `limit=50`, `offset` from the pages loaded, `until` fixed to the time of the first page's read, and `event_type` repeated for each of `DEFAULT_HISTORY_EVENT_TYPES` unless `includeAll`.
- [X] T015 [P] In `api/queries.ts`, add `describeScheduleError(error)` next to `describeAccountError` (`:526`), with a `{title, detail}` for `forbidden` ("Only the owner of this schedule or an administrator can do this"), `schedule-not-found`, `schedule-deleted`, `schedule-stopped`, `schedule-changed` ("Another change was saved at the same time. Save again."), `schedule-name-in-use`, `schedule-exists-for-configuration`, `approval-required-for-schedule`, `schedule-branch-declared`, `schedule-left-out-not-applicable`, `schedule-cron-invalid`, `schedule-timezone-invalid`, `configuration-version-not-found`, `orchestration-unavailable`, `secret-parameter-refused` (title and detail only, never the Sync API message) and `request-invalid`. For the cron, time zone, `schedule-stopped` and `schedule-exists-for-configuration` refusals the detail is the Sync API message. Unknown codes fall back to `describeError`. Do not change `ACTION_ERRORS`.
- [X] T016 [P] In `api/actions.ts`, add `useCreateSchedule`, `useChangeSchedule`, `usePauseSchedule`, `useResumeSchedule`, `useDeleteSchedule` and `useTakeOverSchedule` with `$api.useMutation` and `onSettled: invalidate`, as `useCancelRun` does. In `onSuccess`, write the returned `ScheduleResource` into the cache of `scheduleQuery(schedule_id)` with `queryClient.setQueryData` (research R4).

**Checkpoint**: `pnpm test`, `pnpm check`, `pnpm typecheck` and the unit tier pass.

---

## Phase 3: User Story 1 - View schedules and their state (Priority: P1) 🎯 first increment

**Goal**: A "Schedules" entry, a schedules list and a read-only schedule page with its next starts.

**Independent Test**: Quickstart scenarios 1, 16, 17 and 18, and the stop panel and notices of User Story 1.

- [X] T017 [P] [US1] Create `features/schedules/NextStarts.tsx`: a list of start times, each in the schedule's time zone with its offset (`Intl.DateTimeFormat` with `timeZone` and `timeZoneName: "shortOffset"`), the viewer's local time when the zones differ, and the relative time of the first; "No further start in the next 5 years." when fewer than the requested count; a loading state; the message of a refusal, or "The preview is not available. The Sync API checks the expression when you save." when the request fails otherwise.
- [X] T018 [P] [US1] Create `features/schedules/SchedulesTable.tsx`: a `<table aria-label="Schedules">` at `sm` and wider and a `<ul>` on small screens, as `features/runs/RunsTable.tsx:352-415`, with the columns of contracts/web-pages.md, the next start as in T017 (single time), and a `SchedulesTableSkeleton`. Configuration names from `configsQuery()`; the identifier when not found.
- [X] T019 [US1] Create `features/schedules/SchedulesPage.tsx`: `useDocumentTitle("Schedules")`, a `SectionHeading`, state filter (`Select`: "Not deleted", Active, Paused, Stopped, Deleted) and configuration filter (copy `ConfigSelect`, `features/runs/RunsPage.tsx:452`) written to the search parameters, `useInfiniteQuery(schedulePagesQuery(...))`, `ErrorPanel`, the empty states of contracts/web-pages.md, "Load older schedules" (copy `LoadOlder`, `RunsPage.tsx:495-527`). The "Create schedule" button is added in T037.
- [X] T020 [US1] Create `routes/schedules.index.tsx` (`createFileRoute("/schedules/")`): `validateSearch` keeping `state` only when `active`, `paused`, `stopped` or `deleted`, and `config` only when it matches the `CONFIG_ID` pattern of `routes/runs.index.tsx:7`; `loaderDeps`; a loader that prefetches `schedulePagesQuery` and `configsQuery` and never throws.
- [X] T021 [P] [US1] Create `features/schedules/ScheduleNotices.tsx`: the stop panel from `stateView` (warning `Notice`, the reason text, the action text, and `consecutive_failures` for `repeated-failures`); the `prefect_synced` warning ("This schedule starts no run until the Sync API hands it to the task manager. Restarting the Sync API or changing the schedule hands it over again."); the newer version notice. Action buttons inside the panel are passed in as props.
- [X] T022 [US1] Create `features/schedules/SchedulePage.tsx` with the sections of contracts/web-pages.md in order: header (name, state label, configuration and version linked to `/configs/$configId`, owner as returned, a slot for actions); notices (T021); next start and last run (`runQuery(last_run_id)` with `StateLabel` and a link, "Failed runs in a row: N", `waiting_start_at` when set); next starts (T017 with `nextStartsQuery(schedule.cron, schedule.timezone, 5)`, active schedules only); settings; review branch; a slot for the history (T030); record. `useDocumentTitle(schedule.name, "Schedules")`. Skeleton, `ErrorPanel`, and "Schedule not found" with a link to `/schedules` for `404 schedule-not-found`. Never render `schedule_id`, `owner` or `left_out_set_by` as anything other than the strings the Sync API returns.
- [X] T023 [US1] Create `routes/schedules.$scheduleId.tsx` with a loader that prefetches `scheduleQuery(params.scheduleId)` and never throws, rendering `<SchedulePage key={scheduleId} scheduleId={scheduleId} />`.
- [X] T024 [US1] In `routes/__root.tsx`, add a "Schedules" `<Link to="/schedules">` after "Runs" (`:474-482`) with the same `className`, `activeOptions` and `data-current`/`aria-current`, and a `schedules` case in `sectionOf` (`:361-367`). Run `pnpm build` so `routes/routeTree.gen.ts` includes the new routes, and commit it.

**Checkpoint**: Quickstart scenarios 1, 16, 17 and 18 pass.

---

## Phase 4: User Story 2 - Follow a schedule to the runs it started (Priority: P1)

**Goal**: Links between a schedule and its runs in both directions.

**Independent Test**: Quickstart scenarios 6, 7 and 8.

- [X] T025 [US2] In `api/queries.ts`, give `runPagesQuery` an optional `schedule` argument sent as `schedule_id` and included in the query key; every existing caller keeps working unchanged.
- [X] T026 [US2] In `routes/runs.index.tsx`, add the search parameter `schedule` to `RunsSearch` and `validateSearch` (a non-empty string without spaces), to `loaderDeps`, and pass it to `runPagesQuery`.
- [X] T027 [US2] In `features/runs/RunsPage.tsx`, pass `schedule` to `runPagesQuery` and, when set, show a removable filter "Schedule: <name>" (name from `scheduleQuery`; the identifier while loading or when not found).
- [X] T028 [P] [US2] In `features/runs/RunsTable.tsx`, in `IdLine` (`:174`), add a "Scheduled" mark for a run with `trigger_type === "scheduled"`, linking to `/schedules/$scheduleId`. Do not nest an anchor inside the row anchor; follow how `RunLink` (`:214`) handles inner links, or render the mark as text if nesting cannot be avoided, and note the choice in the commit.
- [X] T029 [P] [US2] Create `features/schedules/ScheduleLine.tsx` exporting `RunScheduleLine({run})`: "Started by schedule <name>, scheduled for <time>" with a link; add it to `features/runs/RunPage.tsx` near the line that names the account that started the run. In `SchedulePage.tsx`, add "View all runs of this schedule" linking to `/runs?schedule=<schedule_id>`.

**Checkpoint**: Both P1 stories work; the feature is useful read-only.

---

## Phase 5: User Story 7 - Read a schedule's history (Priority: P2)

**Goal**: The "History" section of the schedule page.

**Independent Test**: Quickstart scenarios 19, 20 and 21.

- [X] T030 [US7] Create `features/schedules/ScheduleHistory.tsx`: a `SectionHeading` "History", a checkbox "Show all events" (kept in the search parameter `events=all` of the schedule route, validated in `routes/schedules.$scheduleId.tsx`), `useInfiniteQuery(scheduleEventsQuery(scheduleId, {includeAll}))`, a list of entries (time with `When`, `eventText`, the account name, the reason in quotes, "View run" from `eventRunId`), "Load older events", an empty state "No event recorded yet.", and `ErrorPanel`. Every 10 seconds, read the first page again without `until` and add the events whose `event_id` is not listed yet above the list (contracts/http-api.md). Mount it in the history slot of `SchedulePage.tsx`; it stays visible for a deleted schedule.

**Checkpoint**: A skipped start is visible with its cause and its run.

---

## Phase 6: User Story 3 - Pause, resume and delete a schedule (Priority: P2)

**Goal**: The actions an operator takes most often.

**Independent Test**: Quickstart scenarios 9, 12 and 15.

- [X] T031 [US3] Create `features/schedules/ScheduleActionDialogs.tsx` with `PauseScheduleDialog`, `ResumeScheduleDialog` and `DeleteScheduleDialog`, each a `DialogFrame` with a `ReasonField`, a retry key from `useIdempotencyKey()` renewed when the reason changes (`StartRunDialog.tsx:160-163`), the mutation with the `Idempotency-Key` header, and `MutationError` with `describeScheduleError`. Delete: `submitVariant="danger"`, `submitOnEnter={false}`, description "A run in progress is not cancelled. An open review branch and its proposed change stay in Infrahub." plus the branch name when `open_branch` is set. On success, close and `announce("Schedule paused" | "Schedule resumed" | "Schedule deleted")`.
- [X] T032 [US3] In `SchedulePage.tsx`, render the actions from `scheduleActions(useQuery(meQuery()).data, schedule)`: Pause, Resume, and Delete as a `danger-outline` button; when an action is shown but not available, show its `unavailableReason` once in place of the buttons (`RunPage.tsx:580-586`). No action for a deleted schedule. Pass Resume into the stop panel for `repeated-failures`.

**Checkpoint**: Owner and administrator can pause, resume and delete; another account sees the reason.

---

## Phase 7: User Story 4 - Create a schedule (Priority: P2)

**Goal**: Create a schedule with the cadence helper and the preview.

**Independent Test**: Quickstart scenarios 2 to 5 and 5a to 5f.

- [X] T033 [P] [US4] Create `features/schedules/time-zones.ts` exporting `timeZoneChoices(stored?)` (`Intl.supportedValuesOf("timeZone")`, with `UTC` and `stored` added when missing, sorted; `null` when `supportedValuesOf` does not exist) and `browserTimeZone()` (`Intl.DateTimeFormat().resolvedOptions().timeZone`, else `UTC`), with tests in `features/schedules/time-zones.test.ts`.
- [X] T034 [US4] Create `features/schedules/CadenceField.tsx`: a `Select` of the seven forms; for "Every N minutes" a `Select` of 5, 10, 15, 20, 30; "Every hour" a minute input; "Every N hours" a `Select` of 2, 3, 4, 6, 8, 12 and a minute input; "Every day" a time input; "Every week" seven day checkboxes (Monday first, Sunday last, stored as 1-6 and 0) and a time input; "Every month" a day-of-month input and a time input; "Custom" a text input with the five labelled fields from `cronFields` under it, or "A cron expression has five fields: minute, hour, day of month, month, day of week." when it is not five fields. Under every form, show the cron expression (`cronFromForm`) and the words (`cadenceWords`) for the six built forms. The field's value is the cron expression; on open, `formFromCron(value)` chooses the form. Below, `NextStarts` with `nextStartsQuery(debouncedCron, timezone, 5)`, debounced by 400 ms. A preview refusal for the cron expression or the time zone is shown under that field. Every input has a label; the day checkboxes are a `fieldset` with a `legend`.
- [X] T035 [US4] Create `features/schedules/ScheduleFormDialog.tsx` in create mode: name, configuration (from `configsQuery()` when none is given), version (`versionsQuery(configId)`, latest filled in), `CadenceField` (default "Every day" at 02:00), time zone (T033, the browser's time zone filled in, with the notice "The CLI fills in UTC. Schedules of your team can be in different time zones." when it is not UTC), write target ("Default branch" unavailable with its reason when `me.approval_required`), "Write when records are left out" (unavailable with its reason for branch review), overlap policy, late start in minutes (converted to seconds), `ReasonField`. Retry key renewed on any change. Body from `createRequest`. A refusal is shown under the field from `scheduleErrorField`, else with `MutationError`; for `schedule-exists-for-configuration`, link to `/schedules?config=<config_id>`. Values are kept on a refusal. Saving stays possible when the preview is not available.
- [X] T036 [US4] In `ScheduleFormDialog.tsx`, on `201` navigate to the returned schedule and `announce("Schedule created")`; on `503 orchestration-unavailable`, read `configScheduleQuery(config_id)` with `queryClient.fetchQuery` and navigate to the stored schedule.
- [X] T037 [US4] In `features/configurations/ConfigurationPage.tsx`, under the header state line (`:251-297`), add `ConfigScheduleLine` (in `features/schedules/ScheduleLine.tsx`): the schedule's name, state label and next start with a link, or "No schedule" and "Create schedule" opening `ScheduleFormDialog` with this configuration and its latest version. In `SchedulesPage.tsx`, add "Create schedule" (header and empty state) opening the dialog with no configuration chosen.

**Checkpoint**: A daily, weekly and monthly schedule can be created without typing a cron expression (SC-008); the preview matches the next start after saving.

---

## Phase 8: User Story 5 - Change a schedule's settings (Priority: P3)

**Goal**: Change version, cadence, time zone, write target and the other settings.

**Independent Test**: Quickstart scenarios 10, 11 and 11a.

- [X] T038 [US5] Add change mode to `ScheduleFormDialog.tsx`: name and configuration as text; other fields from `formValuesOf(schedule)` taken once when the dialog opens (the opening values); `CadenceField` opened on `formFromCron(schedule.cron)`; version from `versionsQuery(config_id)`, the newest filled in when opened from the newer-version notice; time zones from `timeZoneChoices(schedule.timezone)`. Body from `changeRequest(opening, form, reason)`; `null` → "Nothing changed", no request. While open, compare the polled `scheduleQuery` with the opening values (`differsFromOpening`); when they differ, show "This schedule changed since you opened this form." with "Reload", which asks for confirmation before replacing the entered values. A `schedule-changed` refusal keeps the values and says the save can be sent again. A change of the write target to the default branch while `open_branch` is set shows "The open review branch <branch> and its proposed change stay in Infrahub." On success, close and `announce("Schedule changed")`.
- [X] T039 [US5] In `SchedulePage.tsx`, add "Change" (permission from `scheduleActions`), "Change version" in the newer-version notice, and "Change the write target" in the stop panel for `approval-required`, each opening the change dialog.

**Checkpoint**: A change keeps another person's concurrent change of another field.

---

## Phase 9: User Story 6 - Take over a schedule (Priority: P3)

**Goal**: An administrator takes over a schedule.

**Independent Test**: Quickstart scenarios 13 and 14.

- [X] T040 [US6] Add `TakeOverScheduleDialog` to `ScheduleActionDialogs.tsx`: `DialogFrame` with `submitOnEnter={false}`, `ReasonField`, retry key, description "You become the owner of this schedule. Its next runs record you as the account that started them." On success, close and `announce("You own this schedule")`; the page shows the returned state, which can be stopped with `approval-required`.
- [X] T041 [US6] In `SchedulePage.tsx`, show "Take over" when `scheduleActions` marks it shown, in the header and in the stop panel for `owner-not-found`.

**Checkpoint**: All seven stories work.

---

## Phase 9b: Changes from the maintainer's test of the pages (2026-11-20)

**Goal**: FR-035 to FR-037, from the maintainer's test at http://localhost:5180.

- [X] T049 [US4] In `features/schedules/ScheduleFormDialog.tsx`, label each configuration choice with its name and latest registered version ("<name>, version <n>", from the configuration summary of `configsQuery()`), and show the configuration identifier only when two configurations share a name (FR-035). Put the labelling rule in a pure function with a Vitest test.
- [X] T050 [US4] Make the configuration choice and the time zone choice of the schedule form searchable (FR-036): use the `Autocomplete` (or a combo box) that `@infrahub/ui` exports (`frontend/vendor/infrahub/frontend/packages/ui/src/index.ts`), keep the visible label, the refusal under the field, `isInvalid` and `aria-describedby`, and the text field fallback when the browser cannot list its time zones. Filter by a case-insensitive "contains" match.
- [X] T051 [US2] In `features/runs/RunsPage.tsx`, add a searchable schedule filter next to `ConfigSelect` (FR-037): the schedules from `schedulePagesQuery` (not deleted ones first, deleted ones marked), by name; choosing one sets the `schedule` search parameter; the existing "Schedule: <name>" filter and its remove button stay. With a configuration filter set, offer only that configuration's schedules.
- [X] T052 [US4] In `features/schedules/config-choices.ts` and `ScheduleFormDialog.tsx`, replace the configuration choice and the version choice with one searchable "Configuration and version" choice, one entry per registered version (FR-035, changed 2026-11-20); keep the version choice when the configuration is fixed.
- [X] T053 [US1] Replace the schedules table of `features/schedules/SchedulesPage.tsx` with the "Triage+" list chosen in [design-schedules-list.md](design-schedules-list.md): "Needs action" cards that link to the next step, "Active" and "Paused" groups by name, tabs with counts in the `show` search parameter, a search by schedule or configuration name in `q`, the removable configuration filter, and the "Next starts" panel. Read every page of `GET /schedules` (200 at a time) and deleted schedules only in their tab; keep the rules in `domain/schedule-list.ts` with Vitest tests; remove `SchedulesTable.tsx` and the prototype.
- [X] T054 [US1] Replace the layout of `features/schedules/SchedulePage.tsx` with the "Status first+" page chosen in [design-schedule-page.md](design-schedule-page.md): a header with quiet actions; a status card with the situation in one sentence and the next step as the main button, which takes over the notices of `ScheduleNotices.tsx` (removed); the history; the settings, each value under its label, and the open review branch on the left; and a timeline of the next five starts above "Now" and the last five runs below it on the right (`GET /runs?schedule_id=&limit=5`). Keep the rules in `domain/schedule-page.ts` with Vitest tests; remove the prototype.

---

## Phase 10: Polish and cross-cutting concerns

- [X] T042 [P] In `docs/docs/orchestration.mdx`, add "Manage schedules in the web interface" after "Change, pause, resume, delete and take over a schedule" (`:251`): where "Schedules" is, what the list and the schedule page show, the actions and who may take them, the cadence helper and the preview, the history, the configuration page line, and the "Scheduled" mark and filter on runs. Add a web interface line to the table in "Check a schedule" (`:275-281`), and the history as a way to see skipped and missed starts.
- [X] T043 [P] In `docs/docs/reference/sync-http-api.mdx`, document `POST /schedules/next-starts` with the schedule routes, and the `order` parameter in "List events" (`:806`). If that reference is generated, run `uv run invoke docs.generate` instead of editing it by hand.
- [X] T044 [P] In `frontend/README.md` (`:86-102`), add `/schedules` and `/schedules/$scheduleId` to the table of pages with the Sync API routes each calls, and the runs and configuration page changes.
- [X] T045 [P] Create `changelog/+schedules-web-interface.added.md`: "The web interface lists schedules and shows each schedule's state, next start times and history. It creates and changes schedules with a cadence helper and a preview of the next start times, and pauses, resumes, deletes and takes over schedules." and `changelog/+schedule-api-additions.added.md`: "`POST /schedules/next-starts` returns the next start times of a cron expression, and `GET /events` accepts `order=desc` to list events newest first."
- [X] T046 Search the new and changed frontend files for any rendering of an account identifier or a raw `schedule_id` as visible text (only in addresses), and for text that repeats the Sync API message of `secret-parameter-refused`; fix what is found (FR-007, SC-006).
- [X] T047 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, `uv run invoke docs.rumdl`; from `frontend/`, `pnpm check`, `pnpm typecheck`, `pnpm test` and `pnpm build`. All pass.
- [ ] T048 Run the scenarios of [quickstart.md](quickstart.md) against the development stack and record the result of each in the pull request. ⚠️ Partial: the start-time previews of scenarios 5a, 5b, 5d, 5e and 5f were checked against the Sync API on 2026-11-20. The scenarios in a browser remain; scenarios 6, 7, 19 and 20 also need a local Infrahub.

---

## Dependencies & Execution Order

### Phase dependencies

- Setup (Phase 1) → Foundational (Phase 2) → user stories.
- In Phase 2: T003 to T008 (service) before T009 (types). T010 to T013 can start on the existing
  types; T014 needs T009 for the preview and the events order.
- US1 (Phase 3) is needed by every later story.
- US2 (Phase 4), US7 (Phase 5) and US3 (Phase 6) depend on US1 only.
- US4 (Phase 7) depends on US1. US5 (Phase 8) depends on US4. US6 (Phase 9) depends on US3.
- Polish (Phase 10) after the stories that are delivered.

### Parallel opportunities

- Phase 1: T002 with T001.
- Phase 2: T004 and T006 with T003; T010, T011, T012 together; T014, T015, T016 together after T009.
- Phase 3: T017, T018 and T021 together.
- Phase 4: T028 and T029 together.
- After US1: US2, US7, US3 and US4 by different people.
- Phase 10: T042 to T045 together.

### Parallel example: Phase 2, frontend rules

```text
Task: "domain/schedules.ts"
Task: "domain/cadence.ts"
Task: "domain/schedule-events.ts"
```

## Implementation Strategy

### First increment (read only)

1. Phases 1 and 2.
2. Phase 3 (US1), Phase 4 (US2) and Phase 5 (US7).
3. Check quickstart scenarios 1, 6 to 8 and 16 to 21. The web interface then answers "what runs on
   a schedule, when does it run next, and why did it not run?" with no write action.

### Incremental delivery

1. US3 (pause, resume, delete) → scenarios 9, 12, 15.
2. US4 (create, with helper and preview) → scenarios 2 to 5f.
3. US5 (change) → scenarios 10, 11, 11a.
4. US6 (take over) → scenarios 13, 14.
5. Phase 10.

Each increment leaves the interface working and can be merged on its own.
