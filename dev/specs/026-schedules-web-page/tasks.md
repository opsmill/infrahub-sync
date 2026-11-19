# Tasks: Manage schedules from the web interface

**Input**: Design documents from `dev/specs/026-schedules-web-page/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/web-pages.md](contracts/web-pages.md),
[quickstart.md](quickstart.md), [critique](critiques/critique-20261119.md)

**Tests**: The plan asks for Vitest tests of the pure functions in `frontend/src/domain/schedules.ts`
(research R9). Pages are checked with the quickstart scenarios.

**Paths**: frontend paths are relative to `frontend/src/` unless they start with `frontend/`,
`docs/`, `changelog/` or `.github/`. Run frontend commands from `frontend/`.

**Patterns to copy**: routes `routes/runs.index.tsx` and `routes/runs.$runId.tsx`; paging
`runPagesQuery` (`api/queries.ts:127-153`); actions and retry keys `api/actions.ts` and
`features/runs/StartRunDialog.tsx:138-193`; dialogs `DialogFrame`, `Field`, `ReasonField`,
`MutationError` in `features/runs/form.tsx`; confirmations in `routes/accounts.tsx:441,513,597`;
permission text `features/runs/RunPage.tsx:454,580-586`; page parts `components/page.tsx`; times
`components/run-status.tsx` (`When`) and `lib/time.ts`; titles `lib/title.ts`; announcements
`components/announcer.tsx`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 to US6)

---

## Phase 1: Setup

**Purpose**: Add the test runner the plan asks for.

- [ ] T001 Add `vitest` (a release that supports Vite 8) to the pnpm catalog in `frontend/pnpm-workspace.yaml` and to `devDependencies` in `frontend/package.json` as `catalog:`; add the script `"test": "vitest run"`; run `pnpm install` from `frontend/` and commit `frontend/pnpm-lock.yaml`. Add a `test` block to `frontend/vite.config.ts` only if Vitest needs one (environment `node`, include `src/**/*.test.ts`). Check that `pnpm check` and `pnpm typecheck` still pass with a test file present (Biome and `tsconfig.json` must accept `*.test.ts`).
- [ ] T002 [P] Add a step `pnpm test` after `pnpm typecheck` in the `frontend-lint` job of `.github/workflows/workflow-linter.yml` (lines 133-166), with the same working directory as the other `pnpm` steps.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Pure rules, queries, actions and refusal messages that every story uses.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T003 Create `domain/schedules.ts` with, using the generated types `ScheduleResource`, `ChangeScheduleRequest` and `CreateScheduleRequest` from `api/types.ts` or `api/types.generated.ts`:
  - `type ScheduleAction = "change" | "pause" | "resume" | "delete" | "take-over"` and `scheduleActions(me: {actor: string; administrator: boolean} | undefined, schedule)` returning, per action, `{shown: boolean; available: boolean; unavailableReason?: string}` per data-model "Permissions". While `me` is undefined, every action allowed by the state is shown and available. The unavailable text is "Only the owner of this schedule or an administrator can do this."
  - `stateView(schedule)` returning `{label, panel?: {text, action?, offers: ScheduleAction[] | "runs"}}` per data-model "State and stop reason", with the texts given there; unknown states and reasons return the raw value and no action.
  - Labels: `writeTargetLabel`, `overlapPolicyLabel`, `lateStartLabel(seconds)` (null → "Not set", 3600 → "1 hour").
  - `ScheduleFormValues` (the editable fields of data-model "Form values"), `formValuesOf(schedule)`, `changeRequest(opening: ScheduleFormValues, form: ScheduleFormValues, reason: string): ChangeScheduleRequest | null` that holds only fields that differ from `opening`, plus `reason` when not empty after trim, and returns `null` when no field differs; `createRequest(form, name, configId, reason)`; `differsFromOpening(opening, schedule)` for the "changed since you opened this form" notice.
  - `scheduleErrorField(code)` mapping the codes of data-model "Refusals mapped to a field" to a field name.
- [ ] T004 [P] Create `domain/schedules.test.ts` with Vitest tests, parametrized with `it.each` where possible:
  - `scheduleActions`: owner, administrator who is not owner, other account, `me` undefined, deleted schedule, deleted owner name `Deleted account 3f2a9c`, pause shown only when active, resume only when paused or stopped, take-over only for an administrator who is not the owner.
  - `stateView`: each row of the data-model table, an unknown reason and an unknown state.
  - `changeRequest`: no change → `null`; reason only → `null`; one field changed → only that field and the reason; a later read of the schedule that changed another field does not appear in the request (critique E1); `write_when_left_out` sent only when it differs.
  - `lateStartLabel` and `scheduleErrorField`.
- [ ] T005 [P] In `api/queries.ts`, add `schedulePagesQuery(filters: {state?: string; config?: string})` copied from `runPagesQuery` (key `["get", "/schedules", "pages", filters]`, `limit: "50"`, `cursor`, `state`, `config_id`), `scheduleQuery(scheduleId)` polling every `LIST_REFRESH_MS` through `poll()`, and `configScheduleQuery(configId)` reading `GET /schedules` with `config_id` and `limit: "1"`.
- [ ] T006 [P] In `api/queries.ts`, add `describeScheduleError(error)` next to `describeAccountError` (`:526`), with a `{title, detail}` for `forbidden` ("Only the owner of this schedule or an administrator can do this"), `schedule-not-found`, `schedule-deleted`, `schedule-stopped`, `schedule-changed` ("Another change was saved at the same time. Save again."), `schedule-name-in-use`, `schedule-exists-for-configuration`, `approval-required-for-schedule`, `schedule-branch-declared`, `schedule-left-out-not-applicable`, `schedule-cron-invalid`, `schedule-timezone-invalid`, `configuration-version-not-found`, `orchestration-unavailable`, `secret-parameter-refused` (title and detail only, never the Sync API message, research R5) and `request-invalid`. For the cron, time zone, `schedule-stopped` and `schedule-exists-for-configuration` refusals the detail is the Sync API message. Unknown codes fall back to `describeError`. Do not change `ACTION_ERRORS`.
- [ ] T007 [P] In `api/actions.ts`, add `useCreateSchedule`, `useChangeSchedule`, `usePauseSchedule`, `useResumeSchedule`, `useDeleteSchedule` and `useTakeOverSchedule` with `$api.useMutation` on the routes of contracts/web-pages.md and `onSettled: invalidate`, as `useCancelRun` does. In `onSuccess`, write the returned `ScheduleResource` into the cache of `scheduleQuery(schedule_id)` with `queryClient.setQueryData` (research R4).

**Checkpoint**: `pnpm test`, `pnpm check` and `pnpm typecheck` pass.

---

## Phase 3: User Story 1 - View schedules and their state (Priority: P1) 🎯 first increment

**Goal**: A "Schedules" entry, a schedules list and a read-only schedule page.

**Independent Test**: Create schedules in the active, paused and stopped states with the CLI; quickstart scenarios 1, 16 and 17, and the stop panel and notices of User Story 1.

- [ ] T008 [P] [US1] Create `features/schedules/SchedulesTable.tsx`: a `<table aria-label="Schedules">` at `sm` and wider and a `<ul>` on small screens, as `features/runs/RunsTable.tsx:352-415`, with the columns of contracts/web-pages.md (name linked to `/schedules/$scheduleId`, configuration name and version, state label from `stateView`, cron and time zone, next start, owner) and a `SchedulesTableSkeleton`. The configuration name comes from `configsQuery()`; show the identifier when the configuration is not in the list. The next start shows the time in the schedule's time zone (`Intl.DateTimeFormat` with `timeZone`) and the relative time with `When`; add the viewer's local time when the two time zones differ (FR-002).
- [ ] T009 [US1] Create `features/schedules/SchedulesPage.tsx`: `useDocumentTitle("Schedules")`, a `SectionHeading`, a state filter (`Select` with "Not deleted", Active, Paused, Stopped, Deleted) and a configuration filter (copy `ConfigSelect` from `features/runs/RunsPage.tsx:452`), both written to the search parameters; `useInfiniteQuery(schedulePagesQuery(...))`; error state with `ErrorPanel`; the empty states of contracts/web-pages.md; "Load older schedules" copied from `LoadOlder` (`RunsPage.tsx:495-527`); a "Create schedule" button, hidden until T027 connects it.
- [ ] T010 [US1] Create `routes/schedules.index.tsx` with `createFileRoute("/schedules/")`: `validateSearch` keeping `state` only when it is one of `active`, `paused`, `stopped`, `deleted`, and `config` only when it matches the `CONFIG_ID` pattern of `routes/runs.index.tsx:7`; `loaderDeps` on both; a loader that calls `void queryClient.prefetchInfiniteQuery(schedulePagesQuery(...))` and `prefetchQuery(configsQuery())` and never throws; renders `SchedulesPage`.
- [ ] T011 [P] [US1] Create `features/schedules/ScheduleNotices.tsx`: the stop panel from `stateView` (a `Notice`, warning tone, with the reason text, the action text and, for `repeated-failures`, the count `consecutive_failures`); the warning when `prefect_synced` is false ("This schedule starts no run until the Sync API hands it to the task manager. Restarting the Sync API or changing the schedule hands it over again."); the newer version notice when `newer_version` is set ("Version N of this configuration is registered. This schedule still runs version M."). Action buttons inside the panel are passed in as props so later stories can add them.
- [ ] T012 [US1] Create `features/schedules/SchedulePage.tsx` with the sections of contracts/web-pages.md in order: header (name, state label, configuration and version linked to `/configs/$configId`, owner as the Sync API returns it), notices (T011), next start and last run (next start as in T008; `waiting_start_at` when set; last run read with `runQuery(last_run_id)` and shown with `StateLabel` and a link; "Failed runs in a row: N"), settings (cron, time zone, write target, "write when records are left out" with `left_out_set_by` and `left_out_set_at` when on, overlap policy, late start), review branch (`open_branch`: branch name, link to the run, proposed change identifier), record (created and updated times with `formatAbsolute`). `useDocumentTitle(schedule.name, "Schedules")`. A skeleton while loading, `ErrorPanel` on error, and "Schedule not found" with a link to `/schedules` for `404 schedule-not-found`. Leave a slot in the header for the action buttons of later stories. Never render `schedule_id`, `owner` or `left_out_set_by` as anything other than the strings the Sync API returns.
- [ ] T013 [US1] Create `routes/schedules.$scheduleId.tsx` with `createFileRoute("/schedules/$scheduleId")`, a loader that prefetches `scheduleQuery(params.scheduleId)` and never throws, rendering `<SchedulePage key={scheduleId} scheduleId={scheduleId} />`.
- [ ] T014 [US1] In `routes/__root.tsx`, add a "Schedules" `<Link to="/schedules">` after "Runs" (`:474-482`) with the same `className`, `activeOptions` and `data-current`/`aria-current`, and a `schedules` case in `sectionOf` (`:361-367`). Run `pnpm build` (or `pnpm generate-routes`) so `routes/routeTree.gen.ts` includes the two new routes, and commit it.

**Checkpoint**: quickstart scenarios 1, 16 and 17 pass; a stopped schedule shows its panel.

---

## Phase 4: User Story 2 - Follow a schedule to the runs it started (Priority: P1)

**Goal**: Links between a schedule and its runs in both directions.

**Independent Test**: Quickstart scenarios 6, 7 and 8.

- [ ] T015 [US2] In `api/queries.ts`, give `runPagesQuery` an optional `schedule` argument sent as `schedule_id` and included in the query key; keep every existing caller working unchanged.
- [ ] T016 [US2] In `routes/runs.index.tsx`, add the search parameter `schedule` to `RunsSearch` and `validateSearch` (keep a non-empty string without spaces, drop anything else), add it to `loaderDeps` and pass it to `runPagesQuery`.
- [ ] T017 [US2] In `features/runs/RunsPage.tsx`, pass `schedule` to `runPagesQuery` and, when it is set, show a removable filter "Schedule: <name>" (name from `scheduleQuery(schedule)`; the identifier while loading or when not found) whose remove action clears the search parameter.
- [ ] T018 [P] [US2] In `features/runs/RunsTable.tsx`, in `IdLine` (`:174`), add a "Scheduled" mark for a run with `trigger_type === "scheduled"` and a `schedule_id`, linking to `/schedules/$scheduleId`. Keep the row's existing link behavior (the mark must not nest an anchor inside the row anchor; follow how `RunLink` at `:214` handles inner links, or render the mark as text with the link on the run page only if nesting is not possible, and note the choice in the commit).
- [ ] T019 [P] [US2] Create `features/schedules/ScheduleLine.tsx` exporting `RunScheduleLine({run})`: for a scheduled run, "Started by schedule <name>, scheduled for <time>" with a link to the schedule; the name from `scheduleQuery(run.schedule_id)`, the time from `scheduled_start_at` with `formatAbsolute`. Add it to `features/runs/RunPage.tsx` near the line that names the account that started the run.
- [ ] T020 [US2] In `features/schedules/SchedulePage.tsx`, add "View all runs of this schedule" linking to `/runs?schedule=<schedule_id>` next to the last run.

**Checkpoint**: Both P1 stories work; the feature is useful read-only.

---

## Phase 5: User Story 3 - Pause, resume and delete a schedule (Priority: P2)

**Goal**: The actions an operator takes most often.

**Independent Test**: Quickstart scenarios 9, 12, 15.

- [ ] T021 [US3] Create `features/schedules/ScheduleActionDialogs.tsx` with `PauseScheduleDialog`, `ResumeScheduleDialog` and `DeleteScheduleDialog`, each a `DialogFrame` with a `ReasonField`, a retry key from `useIdempotencyKey()` renewed when the reason changes (as `StartRunDialog.tsx:160-163`), the mutation from T007 with the `Idempotency-Key` header, and `MutationError` showing `describeScheduleError`. Delete uses `submitVariant="danger"`, `submitOnEnter={false}`, and the description "A run in progress is not cancelled. An open review branch and its proposed change stay in Infrahub." (add the branch name when `open_branch` is set). On success, close the dialog and `announce("Schedule paused" | "Schedule resumed" | "Schedule deleted")`.
- [ ] T022 [US3] In `features/schedules/SchedulePage.tsx`, render the actions from `scheduleActions(useQuery(meQuery()).data, schedule)`: Pause and Resume as buttons, Delete as a `danger-outline` button; when an action is shown but not available, show its `unavailableReason` once in place of the buttons, as `RunPage.tsx:580-586`. A deleted schedule shows no action. Pass Resume into the stop panel of `ScheduleNotices` for `repeated-failures`.

**Checkpoint**: Owner and administrator can pause, resume and delete; another account sees the reason.

---

## Phase 6: User Story 4 - Create a schedule (Priority: P2)

**Goal**: Create a schedule from the list or a configuration page.

**Independent Test**: Quickstart scenarios 2 to 5.

- [ ] T023 [P] [US4] Create `features/schedules/time-zones.ts` exporting `timeZoneChoices(stored?: string): string[]` (from `Intl.supportedValuesOf("timeZone")`, with `UTC` added when missing and `stored` added when missing, sorted) and `browserTimeZone(): string` (`Intl.DateTimeFormat().resolvedOptions().timeZone`, else `UTC`). When `supportedValuesOf` does not exist, `timeZoneChoices` returns `null` and the form uses a text field. Add tests to `domain/schedules.test.ts` or a new `features/schedules/time-zones.test.ts` for the `stored` and `UTC` cases.
- [ ] T024 [US4] Create `features/schedules/ScheduleFormDialog.tsx` in create mode: fields of data-model "Form values" (Create) with `Field`, plain `<input className={INPUT}>`, `Select` and `Checkbox`; configuration choice from `configsQuery()` when none is given, version choice from `versionsQuery(configId)` with the latest version filled in; cron hint "Five fields: minute, hour, day of month, month, day of week. Examples: `0 2 * * *` every day at 02:00; `*/30 * * * *` every 30 minutes."; time zone from T023 with the browser's time zone filled in; write target with "Default branch" unavailable and its reason when `me.approval_required`; "Write when records are left out" unavailable with its reason when the write target is branch review; overlap policy; late start as a number of minutes converted to seconds; `ReasonField`. Retry key renewed on any field change. Build the body with `createRequest`. On a refusal, show the message under the field from `scheduleErrorField`, else `MutationError`; for `schedule-exists-for-configuration`, link to `/schedules?config=<config_id>`. Keep every value on a refusal.
- [ ] T025 [US4] In `ScheduleFormDialog.tsx`, on a `201` navigate to `/schedules/$scheduleId` of the returned schedule and `announce("Schedule created")`; on `503 orchestration-unavailable`, read `configScheduleQuery(config_id)` with `queryClient.fetchQuery` and navigate to the stored schedule (contracts/web-pages.md).
- [ ] T026 [US4] In `features/configurations/ConfigurationPage.tsx`, under the header state line (`:251-297`), add a "Schedule" line from `configScheduleQuery(configId)`: the schedule's name linked to its page, its state label and its next start; or "No schedule" with a "Create schedule" button opening `ScheduleFormDialog` with this configuration and its latest version. Put the line's component in `features/schedules/ScheduleLine.tsx` as `ConfigScheduleLine`.
- [ ] T027 [US4] In `features/schedules/SchedulesPage.tsx`, connect "Create schedule" (header and empty state) to `ScheduleFormDialog` with no configuration chosen.

**Checkpoint**: A schedule can be created from both places; a second one is refused with a link.

---

## Phase 7: User Story 5 - Change a schedule's settings (Priority: P3)

**Goal**: Change version, cadence, time zone, write target and the other settings.

**Independent Test**: Quickstart scenarios 10, 11 and 11a.

- [ ] T028 [US5] Add change mode to `features/schedules/ScheduleFormDialog.tsx`: name and configuration shown as text; every other field filled from `formValuesOf(schedule)` taken once when the dialog opens and kept as the opening values; version choice from `versionsQuery(config_id)`, with the newest version filled in when the dialog is opened from the newer-version notice; time zone choices from `timeZoneChoices(schedule.timezone)`. On save, build the body with `changeRequest(opening, form, reason)`; when it returns `null`, show "Nothing changed" and send nothing. While the dialog is open, compare the polled `scheduleQuery` with the opening values using `differsFromOpening`; when they differ, show "This schedule changed since you opened this form." with "Reload", which asks for confirmation before replacing the entered values. A `schedule-changed` refusal keeps the values and says the save can be sent again. When the write target changes to the default branch and `open_branch` is set, show "The open review branch <branch> and its proposed change stay in Infrahub." On success, close and `announce("Schedule changed")`.
- [ ] T029 [US5] In `features/schedules/SchedulePage.tsx`, add the "Change" button (permission from `scheduleActions`), a "Change version" action in the newer-version notice, and "Change the write target" in the stop panel for `approval-required`, each opening the change dialog.

**Checkpoint**: Changes keep another person's concurrent change of another field.

---

## Phase 8: User Story 6 - Take over a schedule (Priority: P3)

**Goal**: An administrator takes over a schedule.

**Independent Test**: Quickstart scenarios 13 and 14.

- [ ] T030 [US6] Add `TakeOverScheduleDialog` to `features/schedules/ScheduleActionDialogs.tsx`: `DialogFrame` with `submitOnEnter={false}`, `ReasonField`, retry key, the description "You become the owner of this schedule. Its next runs record you as the account that started them." On success, close and `announce("You own this schedule")`; the page then shows the returned state, which can be stopped with `approval-required` (spec User Story 6, scenario 3).
- [ ] T031 [US6] In `features/schedules/SchedulePage.tsx`, show "Take over" when `scheduleActions` marks it shown, in the header and in the stop panel for `owner-not-found`.

**Checkpoint**: All six stories work.

---

## Phase 9: Polish and cross-cutting concerns

- [ ] T032 [P] In `docs/docs/orchestration.mdx`, add the section "Manage schedules in the web interface" after "Change, pause, resume, delete and take over a schedule" (`:251`): where "Schedules" is in the navigation, what the list and the schedule page show, which actions are offered to whom, the schedule line on the configuration page, and the "Scheduled" mark and filter on runs. Add a web interface line to the table in "Check a schedule" (`:275-281`). Run `uv run invoke docs.rumdl` (FR-024).
- [ ] T033 [P] In `frontend/README.md` (`:86-102`), add `/schedules` and `/schedules/$scheduleId` to the table of pages with the Sync API routes each calls (contracts/web-pages.md), and the runs and configuration page changes.
- [ ] T034 [P] Create `changelog/+schedules-web-interface.added.md` with one sentence: "The web interface lists schedules, shows each schedule's state and the action a stopped schedule needs, and creates, changes, pauses, resumes, deletes and takes over schedules."
- [ ] T035 Search the new and changed files for any rendering of an account identifier or a raw `schedule_id` as visible text (only in addresses), and for any text that repeats the Sync API message of `secret-parameter-refused`; fix what is found (FR-007, SC-006).
- [ ] T036 From `frontend/`, run `pnpm check`, `pnpm typecheck`, `pnpm test` and `pnpm build`; all exit 0. From the repository root, run `uv run invoke docs.rumdl`.
- [ ] T037 Run the scenarios of [quickstart.md](quickstart.md) against the development stack and record the result of each in the pull request.

---

## Dependencies & Execution Order

### Phase dependencies

- Setup (Phase 1) → Foundational (Phase 2) → user stories.
- US1 (Phase 3) is needed by every later story, because they add to `SchedulePage.tsx` and
  `SchedulesPage.tsx`.
- US2 (Phase 4) depends on US1 only.
- US3 (Phase 5) depends on US1.
- US4 (Phase 6) depends on US1. T027 connects the list button created in T009.
- US5 (Phase 7) depends on US4 (it extends `ScheduleFormDialog.tsx`).
- US6 (Phase 8) depends on US3 (it extends `ScheduleActionDialogs.tsx`).
- Polish (Phase 9) after the stories that are delivered.

### Within each phase

- T003 before T004's assertions can pass; write T004 with T003.
- T005, T006, T007 touch different parts of `api/` and can run in parallel with T004.
- T008 and T011 can run in parallel; T012 needs both.

### Parallel opportunities

- Phase 1: T002 in parallel with T001.
- Phase 2: T004, T005, T006, T007 in parallel after T003's types exist.
- Phase 3: T008 and T011 in parallel.
- Phase 4: T018 and T019 in parallel; T015 to T017 in sequence.
- After US1: US2, US3 and US4 can be built in parallel by different people.
- Phase 9: T032, T033, T034 in parallel.

### Parallel example: Phase 2

```text
Task: "Vitest tests for domain/schedules.ts in frontend/src/domain/schedules.test.ts"
Task: "schedulePagesQuery, scheduleQuery, configScheduleQuery in frontend/src/api/queries.ts"
Task: "Schedule mutation hooks in frontend/src/api/actions.ts"
```

## Implementation Strategy

### First increment (read only)

1. Phases 1 and 2.
2. Phase 3 (US1) and Phase 4 (US2).
3. Stop and check quickstart scenarios 1, 6 to 8, 16 and 17. The web interface then answers "what
   runs on a schedule, and why did it stop?" with no write action.

### Incremental delivery

1. US3 (pause, resume, delete) → check scenarios 9, 12, 15.
2. US4 (create) → scenarios 2 to 5.
3. US5 (change) → scenarios 10, 11, 11a.
4. US6 (take over) → scenarios 13, 14.
5. Phase 9.

Each increment leaves the interface working and can be merged on its own.
