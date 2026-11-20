# Implementation report: Manage schedules from the web interface

**Status**: INCOMPLETE. 49 of 51 tasks are done. T009 and T048 are partly done, because the
development stack was not available to this branch (see "Tasks not completed").

- **Spec directory**: `dev/specs/026-schedules-web-page`
- **Branch**: `026-schedules-web-page`
- **Base commit**: `cc9e5e4a`
- **Head commit**: `0da5a399` (before this report)
- **Wall-clock time**: about 2 hours 15 minutes, 2026-11-20 09:25 to 11:40 UTC.

## Chunk ledger

| # | Chunk (tasks.md heading) | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged upward |
| --- | --- | --- | --- | --- | --- |
| 1 | Phase 1: Setup | T001-T002 | 2 / 0 / 0 | `4ab79314` | `vite.config.ts` limits Vitest to `src/**/*.test.ts`. Without it, Vitest also ran the 378 test files of the vendored `infrahub` submodule. |
| 2 | Phase 2: Sync API additions | T003-T009 | 6 / 1 / 0 | `cc7ec90c`, `18fd6456` | T009 is partly done (see below). Two contract-guard tests were updated for the new public names. The query plans were checked on near-empty databases only. |
| 3 | Phase 2: Pure rules (frontend) | T010-T013 | 4 / 0 / 0 | `49c9c824`, `eca2a501` | An event's run is in `primary_resource`. A change request cannot clear the late-start limit, because the Sync API reads `null` as "not changed". |
| 4 | Phase 2: Queries and actions | T014-T016 | 3 / 0 / 0 | `85b7c8fd` | The texts for most refusals were written from the service messages. |
| 5 | Phase 3: User Story 1 | T017-T024 | 8 / 0 / 0 | `f0c1090d`, `1b3525cc` | The table layout starts at `lg`, not `sm`. "Settings" comes before "Next starts", as in contracts/web-pages.md. |
| 6 | Phase 4: User Story 2 | T025-T029 | 5 / 0 / 0 | `ea2c082b` | The "Scheduled" mark sits beside the row link, not inside it. The schedule line is in `run-sections.tsx`. |
| 7 | Phase 5: User Story 7 | T030 | 1 / 0 / 0 | `7eac6792` | The step "read the newest events again and merge them" was added here, because the queries chunk had not done it. |
| 8 | Phase 6: User Story 3 | T031-T032 | 2 / 0 / 0 | `dd1ef83a` | `MutationError` gained an optional `describe` property. |
| 9 | Phase 7: User Story 4 | T033-T037 | 5 / 0 / 0 | `bf260f21`, `e97be3c1` | The form opens on "Every day" at 02:00, as T035 says. data-model.md says the field starts empty. |
| 10 | Phase 8: User Story 5 | T038-T039 | 2 / 0 / 0 | `9b05696d`, `c50d58d7` | After a `schedule-changed` refusal, the form keeps its retry key, because the Sync API releases the key on a refusal. |
| 10b | Phase 9b: Maintainer's changes | T049-T051 | 3 / 0 / 0 | `1a06b7ca` (spec), `7882aca6` | This phase was added during the run from the maintainer's test of the pages (FR-035 to FR-037). The labels use the `latest_version` that the configurations list returns, so the Sync API did not change. |
| 11 | Phase 9: User Story 6 | T040-T041 | 2 / 0 / 0 | `205dc14c` | Take-over is shown only once `GET /me` has answered with an administrator, as FR-014 and data-model.md say. The earlier rule also showed it while the account was loading. |
| 12 | Phase 10: Polish | T042-T048 | 6 / 1 / 0 | `8051368a`, `e3e70b63` | T048 is partly done (see below). Two places that showed a schedule identifier as text were found and fixed. |
| R | Review fixes | none | n/a | `0da5a399` | See "Review findings". |

## Tasks not completed

- **T009**, partly done.
  - Why: the running development stack mounts the main checkout's source, so it was not restarted from this worktree.
  - What was done instead: `types.generated.ts` was generated from this branch's `build_app().openapi()`. The same method on the base commit reproduces the committed file exactly.
  - What remains: regenerate the types from a Sync API running this branch.
- **T048**, not run.
  - Why: the quickstart scenarios need the development stack, which was stopped during the run (no `infrahub-sync-dev` container). This run did not stop it.
  - What was tested instead: the maintainer tested the first pages by hand at <http://localhost:5180> before the stack stopped, and their feedback became T049-T051.
  - What remains: run quickstart.md after `uv run invoke dev.start`.

## Local-pass evidence

| Test id | Type | Run command | Passed at (UTC) | Environment | Verbatim pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/service/test_schedule_next_starts.py` (20 tests: starts equal the scheduler's starts, defaults, the daylight-saving start, fewer starts than the count, the count from 1 to 20, a missing cron, an invalid cadence recording no event, a credential refused and not repeated, a token required, the first start equal to a created schedule's next start) | unit (pytest) | `uv run pytest tests/service/test_schedule_next_starts.py tests/service/test_events_route.py -q` | 2026-11-20T11:34:55Z | Python 3.11, local SQLite | `61 passed, 1 warning` |
| `tests/service/test_events_route.py` `order` cases (desc reverses asc, ties ordered by `event_id`, desc with `until` pages without overlap, an invalid value refused) | unit (pytest) | same | 2026-11-20T11:34:55Z | same | `61 passed, 1 warning` |
| `tests/product_store/test_contract.py::test_public_surface_is_exactly_the_supported_contract` | unit (pytest) | `uv run pytest -p no:cacheprovider -o addopts="" -v <file>::<test>` | 2026-11-20T09:52:08Z | Python 3.11 | `63 passed, 1 warning in 10.93s` (run together with the two files above) |
| `tests/service/test_http_api.py::test_the_schedule_routes_are_authenticated_and_declare_their_errors` | unit (pytest) | same | 2026-11-20T09:52:08Z | Python 3.11 | same |
| Full Python unit tier | unit | `uv run invoke tests.tests-unit` | 2026-11-20 (chunk 12) | Python 3.11 | `10709 passed, 4 skipped` |
| `src/domain/schedules.test.ts` (scheduleActions, stateView, lateStartLabel, changeRequest, createRequest, scheduleErrorField, branchKeptOpen) | unit (Vitest) | `cd frontend && pnpm exec vitest run --reporter=verbose` | 2026-11-20T11:34:29Z | Node 26.8.1, Vitest 5.1.1 | `Test Files 10 passed (10) / Tests 289 passed (289)` |
| `src/domain/cadence.test.ts` (forms in both directions, expressions that stay custom, cronFields) | unit (Vitest) | same | 2026-11-20T11:34:29Z | same | same |
| `src/domain/schedule-events.test.ts` (eventText, eventRunId, DEFAULT_HISTORY_EVENT_TYPES, withNewestEvents, untilAfter, historyNeedsReread) | unit (Vitest) | same | 2026-11-20T11:34:29Z | same | same |
| `src/domain/start-times.test.ts` (formatInZone, startTimeText, startsIn, scheduledForText) | unit (Vitest) | same | 2026-11-20T11:34:29Z | same | same |
| `src/api/queries.test.ts` (describeScheduleError, nextStartsQuery, scheduleEventsQuery, scheduleNewestEventsQuery, runPagesQuery) | unit (Vitest) | same | 2026-11-20T11:34:29Z | same | same |
| `src/features/schedules/cadence-draft.test.ts`, `time-zones.test.ts`, `form-rules.test.ts`, `config-choices.test.ts` | unit (Vitest) | same | 2026-11-20T11:34:29Z | same | same |
| `src/features/runs/schedule-choices.test.ts` (scheduleChoices, scheduleFilterText) | unit (Vitest) | same | 2026-11-20T11:34:29Z | same | same |

The pages and dialogs have no component tests. Their rules are pure functions with the Vitest
tests above. There are no end-to-end tests. The manual check is T048, which is not run.

## Review findings

Three reviewers covered the diff `cc9e5e4a..e3e70b63`: bugs and project rules; tests and errors;
types, comments and simplification. None found a critical issue.

| Severity | File | Summary | Outcome |
| --- | --- | --- | --- |
| High | `frontend/src/api/queries.ts`, `domain/schedule-events.ts` | The history took `until` from the browser's clock. With a clock difference, an event showed twice or was missing (FR-034). | Fixed in `0da5a399`: `until` comes from the first page's newest event, and events are shown once across pages. |
| Medium | `queries.ts` | Events after the newest 50 and before `until` were shown nowhere. | Fixed: the pages are read again when the newest read leaves a gap. |
| Medium | `features/schedules/ScheduleHistory.tsx` | A failed read of the newest events showed no notice. | Fixed: stale notice. |
| Medium | `ScheduleFormDialog.tsx:254-270`, `CadenceField.tsx:65-78` | The "Save" rule and the choice of which preview refusal to show sit in components, without tests. | Deferred. Move them into `form-rules.ts` and test them. |
| Medium | `ScheduleFormDialog.tsx:313-326` | After `orchestration-unavailable`, an empty `catch` drops the failure of the follow-up read. The refusal stays shown. | Deferred. |
| Medium | `NextStarts.tsx` | A network failure of the preview has no retry button. | Deferred. |
| Medium | `domain/cadence.ts:28`, `domain/schedules.ts:182`, `form-rules.ts:54` | Loose types: the weekly days can be empty, `write_target` and `overlap_policy` are `string`, and `undefined` and `null` have unwritten meanings. | Deferred. |
| Low | `domain/schedules.ts` | `changeRequest` did not trim a custom cron. | Fixed. |
| Low | `CadenceField.tsx` | A preview was requested with an empty time zone. | Fixed. |
| Low | `schedules.py`, `sync-http-api.mdx` | "Five years" did not match the 1,830-day window. | Fixed. |
| Low | `routes/runs.index.tsx` | `schedule` was checked with `\S+`. | Fixed: `^s-[0-9a-f]{32}$`. |
| Low | `tests/service/test_schedule_next_starts.py` | No test covers a valid cadence with no start in the window (`0 0 29 2 1`). | Deferred. |
| Low | `docs/docs/orchestration.mdx` | "Shown under the field it concerns" holds for 8 refusal codes only. | Deferred. |
| Advisory | `ScheduleFormDialog.tsx` and others | Nested ternaries; the default cadence defined three times; repeated helpers (`two`, `viewerTimeZone`/`browserTimeZone`, the "same label" rule, the `CONFIG_ID` pattern); a 731-line form. | Deferred. |

## Autonomous decisions

- **Phase 9b added mid-run:** after the maintainer's test of the pages, FR-035 to FR-037 and tasks T049-T051 were written into spec.md and tasks.md and built before User Story 6.
- **Branch processes for the manual test:** this branch's Sync API was started on port 8012 and its web interface on port 5180. The stack's own Sync API on 8001 was not touched, and port 8011 belonged to another session.
- **Spec over task text, when they disagreed:**
  - take-over is shown only to a known administrator;
  - the refused write target shows the Sync API's message;
  - no schedule identifier is shown as text.
- **Task text kept against data-model.md:** the form opens on "Every day" at 02:00.
- **Review run in parallel:** the six review aspects ran as three parallel reviewers, not one after another, to save time. The review covered `cc9e5e4a..e3e70b63`, because `detect-changed-files.sh` diffs against `main` and returned about 1,500 unrelated files.
- **Commit trailers:**
  - `cc7ec90c`, `18fd6456`, `49c9c824` and `eca2a501` carry a `Claude-Session` trailer;
  - the later commits leave it out, following the user's rule to strip it before a push.

## Suggested next steps

1. Start the development stack (`uv run invoke dev.start` from the main checkout).
2. Regenerate the API types from a Sync API running this branch (T009).
3. Run quickstart.md (T048).
4. Remove the `Claude-Session` trailers from the four commits named above before pushing.
5. Address the deferred review findings, starting with the untested "Save" rule and the loose types.
6. Open a pull request into `feature/v3-develop` when you are ready.
