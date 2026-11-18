# Implementation report: Scheduled runs that write without a person present

**Status**: INCOMPLETE. All 13 chunks ran and every test written locally passed. Five tasks stay
open, because each needs a development stack that runs this branch, and the two write targets are
still refused for users.

| Item | Value |
| --- | --- |
| Spec directory | `dev/specs/021-scheduled-runs` |
| Branch | `021-scheduled-runs`, in the worktree `.worktrees/021-scheduled-runs`, not pushed |
| Base commit | `56223bf7` (2026-11-17 23:33 +0100) |
| Head commit before this report | `e7a7e68d` (2026-11-18 16:27 +0100) |
| Commits | 37, each with only the `Co-Authored-By` trailer |
| Change set | 91 files, 17,742 lines added, 330 removed |
| Tasks | 58 of 63 ticked |
| Wall-clock time | about 17 hours, including two usage-limit stops and one change of model |

## 1. What this build does and does not do

- **Built and tested**: all three user stories.
    - The schedule record, routes, client and `infrahub-sync schedules` commands.
    - One Prefect schedule for each Sync schedule, matched from the store at startup and in the
      bootstrap job.
    - The worker's admission of a scheduled flow run: one run per start, skipped, queued, missed
      and failed starts, and the stop after 5 failed runs.
    - Branch review: an own branch, a proposed change, and the replacement of the earlier one.
    - A write to the default branch with the left-out setting.
    - The take-over, and the newer-version warning.
- **Not available to users**: the set of delivered write targets that `serve.py` passes is empty,
  so a request to create a schedule is refused with `schedule-setting-not-available`. T058
  (branch review) and T041 (default branch) turn the two write targets on. They wait until the
  feature has been observed end to end on a stack that runs this branch. The user guide states this
  at the top of `docs/docs/orchestration.mdx`.

## 2. Chunk ledger

| # | Chunk (tasks) | Tasks | Outcome | Commits | Points raised |
| --- | --- | --- | --- | --- | --- |
| 1 | Cadence and release note (T002, T003, T007) | 3 | 3 done | `2358be68`, `6b0ae046` | On Python 3.13 the night with no 02:30 starts at 03:30 local time; on 3.11 at 01:30. One start either way. |
| 2 | Client models, owner lookup (T006, T012, T013) | 3 | 3 done | `7f2c719e` | One parity test failed on purpose until chunk 3. |
| 3 | Store tables and columns (T005, T008, T009) | 3 | 3 done | `7c755e5e` | The refusal of an earlier database was hidden on two service paths; fixed in chunks 5 and 6. |
| 4 | Store methods, admission, events (T004, T010, T011, T014) | 4 | 4 done | `5c7ed106` | Interrupted by a usage limit and resumed. |
| 5 | Prefect schedules, service, routes, match step (T024, T022, T023, T027, T015, T017, T018, T001) | 8 | 8 done | `e76722bd`, `fd090b04` | Two broad `except Exception`; resume of a paused schedule did not check its owner. Both fixed later. |
| 6 | Worker admission, run filters (T026, T025, T028, T016, T019, T020) | 6 | 6 done | `22e2aed2`, `6ff84c59` | First attempt stopped by a usage limit before any change; the model changed to Opus 5.5. |
| 7 | Client and CLI (T029, T030, T021) | 3 | 3 done | `9b6c719e`, `a103a63c` | |
| 8 | Facts about Infrahub (T059) | 1 | 1 done from source code only | `3157fd03`, then `93624026`, `96d70bb7` | The live check was refused by the permission classifier (see section 6). Four facts corrected decision R15 and the contract. |
| 9 | Branch and proposed change operations (T060, T061, T062) | 3 | 3 done, T061 written and not run | `bc55eb26`, `6da590b5` | Branch requests go to `main`, because Infrahub answers 404 to a request sent to a branch that does not exist. |
| 10 | Branch review in the flow (T063, T033, T034, T035, T031, T032) | 6 | 5 done, T032 partial | `c625fc93`, `3987a12e` | T058 not run. |
| 11 | User story 2 (T036 to T040) | 5 | 5 done | `d655ee0f`, `cdded247` | T041 not run. Interrupted by a usage limit and resumed. |
| 12 | User story 3 (T042 to T048) | 7 | 7 done | `4cdcbd19`, `8ca2833e` | A take-over of a default-branch schedule while approval is required stays stopped; accepted. |
| 13 | Documentation and checks (T049 to T053, T056, T057) | 7 | 7 done | `189647d3`, `c058ac5c`, `5fac9c14`, `e27e39a2`, `e07df66a`, `b5df91c4` | T054 and T055 not run. |
| Fix | Review findings 1 to 7 | — | 7 done | `e3d2974e`, `922ce274`, `c37dda97`, `9fa5e347`, `869a2be0`, `018b45e8`, `b7214f86`, `d7616aa8`, `e7a7e68d` | See section 5. |

## 3. Tasks not completed

| Task | What it does | Why it is open |
| --- | --- | --- |
| T032 | Smoke test of branch review on the development stack | Written, not run. The `infrahub-sync-dev-*` containers on this machine belong to another session. |
| T055 | Smoke test of the default branch, and the 1-minute start check (SC-007) | Not written; same reason. |
| T054 | Regenerate the frontend API types | Needs a running Sync service of this branch. |
| T058 | Turn branch review on for users | Waits for T032 to be observed end to end. |
| T041 | Turn the default branch on for users, with the CLI write-target options | Waits for T055 to be observed end to end. |

## 4. Local-pass evidence

Every chunk agent ran each test it added or changed and reported its pass line. Tests that one
command covers are grouped in one row; the chunk reports hold every node identifier.

| Test id | Type | Run command | Passed at (UTC) | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/service/test_schedule_cadence.py` (31 tests) | unit | `uv run pytest tests/service/test_schedule_cadence.py -q --no-cov -p no:cacheprovider` | 2026-11-17T23:03:21Z | n/a; also Python 3.13 | `31 passed in 0.61s` |
| `tests/client/test_models.py`, `test_public_surface.py`, `tests/service/test_principal_resolver.py`, `test_events_route.py::test_the_event_list_returns_the_contract_shape` (88 added or changed) | unit | `uv run pytest <files> --no-cov -p no:cacheprovider -q -rA` | 2026-11-17T23:26:02Z | n/a | `235 passed, 1 warning in 2.23s` |
| `tests/product_store/test_contract.py`, `test_run_approval.py`, `test_postgresql_isolation.py`, `tests/service/test_http_api.py` parity test (33 added, 3 changed) | unit and integration | `uv run pytest <files> -m "integration or not integration" -v -k <names> --no-cov -p no:cacheprovider` | 2026-11-17T23:50:35Z | PostgreSQL 127.0.0.1:55432 for the `[postgresql]` cases | `94 passed, 609 deselected, 1 warning in 4.74s` |
| `tests/product_store/test_schedules.py` (50 functions), `tests/service/test_event_transactions.py` (13), `test_contract.py` (1 changed) | unit | `uv run pytest <files> -q --no-cov -p no:cacheprovider` | 2026-11-18T07:35:38Z | n/a | `593 passed, 113 skipped, 1 warning in 11.88s` |
| Same store tests, `[postgresql]` ids, including two concurrent admissions of one start | integration | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=… uv run pytest -m integration tests/product_store/test_schedules.py tests/product_store/test_contract.py -q --no-cov -p no:cacheprovider` | 2026-11-18T07:35:52Z | PostgreSQL 127.0.0.1:55432 | `113 passed, 531 deselected, 1 warning in 21.84s` |
| `tests/service/test_schedule_routes.py` (72) | unit | `uv run pytest tests/service/test_schedule_routes.py -q --no-cov -p no:cacheprovider` | 2026-11-18T08:20:46Z | n/a | `72 passed, 1 warning in 7.67s` |
| `test_schedule_owner.py` (7), `test_schedule_prefect.py` (30), `test_schedule_approval.py` (8), `test_schedule_bootstrap.py` (7) | unit | same form, one file each | 2026-11-18T08:20:55Z to 08:21:04Z | n/a | `7 passed`, `30 passed`, `8 passed`, `7 passed` |
| 12 existing service test files whose fakes changed | unit | `uv run pytest <12 paths> -q --no-cov -p no:cacheprovider` | 2026-11-18T08:21:07Z | n/a | `511 passed, 1 warning in 23.20s` |
| `tests/service/test_schedule_admission.py` (18) | unit | `uv run pytest tests/service/test_schedule_admission.py -rA --no-cov -p no:cacheprovider` | 2026-11-18T09:26:41Z | n/a | `18 passed, 1 warning in 2.46s` |
| `tests/service/test_schedule_branch_review.py`, service part (10) | unit | same form | 2026-11-18T09:26:46Z | n/a | `10 passed, 1 warning in 1.25s` |
| Chunk 6 changes in `test_http_api.py` (3), `test_events_route.py` (1), `test_worker_environment.py` (1), `test_worker_claim.py` (6), `test_flow_and_prefect.py` (2), `test_registered_schema_guard.py` (1) | unit | `uv run pytest <file> -k <names> -rA --no-cov -p no:cacheprovider` | 2026-11-18T09:26:49Z to 09:27:01Z | n/a | `3 passed`, `1 passed`, `1 passed`, `6 passed`, `2 passed`, `1 passed` |
| `tests/client/test_schedules.py` (26), `tests/cli/test_schedule_commands.py` (27), `test_parity_and_closure.py` (3), `test_client_commands.py` (2 changed) | unit | `uv run pytest <files> -q --no-cov -p no:cacheprovider` | 2026-11-18T09:41:34Z | n/a | `282 passed in 2.84s` |
| `tests/service/test_branch_review_operations.py` (43), `test_report_models.py::test_the_failure_type_names` with `test_failure_cause.py` | unit | `uv run pytest <files> -q --no-cov -p no:cacheprovider` | 2026-11-18T10:11:56Z, 10:11:58Z | n/a | `43 passed in 0.22s`, `125 passed in 0.84s` |
| `tests/service/test_schedule_branch_review.py`, worker part (26 in the file) | unit | `uv run pytest tests/service/test_schedule_branch_review.py -rA -p no:logging --no-cov -p no:cacheprovider` | 2026-11-18T10:45:49Z | n/a | `26 passed, 1 warning in 2.98s` |
| Branch review store tests, `[sqlite]` and `[postgresql]` (4 functions) | unit and integration | `uv run pytest tests/product_store/test_schedules.py -m 'integration or not integration' -k <names> -rA` | 2026-11-18T10:45:54Z | SQLite; PostgreSQL 127.0.0.1:55432 | `8 passed, 142 deselected in 2.29s` |
| User story 2: `test_schedule_left_out.py` (12) and changes in 5 other files (14) | unit | `uv run pytest <ids> -q --no-cov -p no:cacheprovider -rA` | 2026-11-18T13:06:32Z | n/a | `78 passed, 1 warning in 5.10s` |
| User story 3: `test_schedule_missed_starts.py`, `test_schedule_takeover.py` and changes in 8 files | unit | `uv run pytest <10 files> -q --no-cov -p no:cacheprovider` | 2026-11-18T13:43:37Z; per test 13:55:31Z and 13:55:51Z | n/a; one test on Prefect's local test server | `481 passed, 86 skipped`; `75 passed`; `12 passed` |
| User story 3 store tests of the waiting start, `[postgresql]` | integration | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=… uv run pytest -m integration … -k "waiting or queued …"` | 2026-11-18T13:56:03Z | PostgreSQL 127.0.0.1:55432 | `11 passed, 161 deselected in 4.81s` |
| `tests/cli/test_parity_and_closure.py::test_live_docs_and_examples_close_removed_cli_paths` and 3 cases | unit | `uv run pytest tests/cli/test_parity_and_closure.py tests/test_run_events_docs.py -v --no-cov -p no:cacheprovider` | 2026-11-18T14:19:14Z | n/a | `37 passed in 0.57s` |
| Fix pass: 37 added or changed tests in 9 files | unit and integration | the commands of the fix report, `--no-cov -p no:cacheprovider` | 2026-11-18T15:33:18Z to 15:34:11Z | n/a; PostgreSQL 127.0.0.1:55432 for the store cases | each `passed` |
| `test_branch_review_operations.py::test_close_of_a_proposed_change_that_does_not_exist_is_done_and_sends_no_update` | unit | only in the full unit tier | 2026-11-18T15:32:03Z | n/a | unit tier `9533 passed` |
| `tests/integration/test_branch_review_operations_integration.py::test_the_five_operations_against_infrahub` | integration | `INFRAHUB_ADDRESS=… INFRAHUB_API_TOKEN=… uv run pytest -m integration tests/integration/test_branch_review_operations_integration.py` | deferred: no Infrahub that belongs to this work | needs an Infrahub of this work | skips without the variables |
| `tests/dev_stack/smoke/test_scheduled_run.py::test_a_schedule_with_branch_review_opens_and_replaces_its_proposed_changes` | e2e | `uv run pytest tests/dev_stack/smoke/test_scheduled_run.py -m smoke` | deferred: local E2E not supported here | a development stack of this branch | skips until T058 |

Totals on the final tree, before this report: unit tier `9533 passed, 4 skipped, 479 deselected, 2
xfailed`; PostgreSQL tier `277 passed, 1152 deselected`. The Python 3.10 legs passed in chunk 12.
The Compose suite was not run.

## 5. Review findings

Four reviewers read `56223bf7..e07df66a`: code and documentation, error handling, tests, and types.

### Fixed

| Severity | File | Finding | Fix |
| --- | --- | --- | --- |
| critical (latent) | `service/schedules.py` | A resume of a stopped schedule did not check approval, so a default-branch schedule stopped after 5 failures could run with no approved plan. | A resume checks every stop reason that holds now. |
| high | `service/schedules.py` | A resume that had to stop a paused schedule took two writes; a failure of the second left it active. | One store write, `paused` to `stopped`, with its event. |
| high | `service/schedules.py`, `schedule_admission.py`, `docker-compose.yml` | Only the Sync API checked approval; a store error at startup skipped the stop step. | The worker refuses a default-branch start while approval is required and stops the schedule. Compose passes the setting to the worker. The startup stop step is retried. |
| medium | `service/flow.py`, `branch_review.py` | A failure after the write was committed could fail the flow run and lose the proposed change identifier. | A boundary around the steps after the commit; a malformed response is a typed failure. |
| medium | `product_store/store.py` | `update_schedule` validated the record only when secrets were set. | Always validated. |
| medium | `schedule_admission.py`, `branch_review.py` | A proposed change deleted in Infrahub failed every later start. | Read as `missing`; the row is kept and the start runs. |
| medium | `service/schedules.py`, `bootstrap.py` | Broad `except Exception` in the Prefect match step (constitution principle IV). | Named classes, one schedule's failure does not stop the others. One catch-all stays, logged at error level, because the local store's SQLite errors are not wrapped. |

### Deferred

| Severity | File | Finding |
| --- | --- | --- |
| medium | `service/schedule_admission.py`, `store.py` | `run_in_progress` is read outside the admission transaction; a person's `sync` can be accepted between the read and the admission. The write guard still serializes the writes (SC-006). No concurrency test covers it. |
| medium | `tests/service/test_schedule_branch_review.py` | The fake Infrahub accepts what Infrahub refuses: requests to a branch that does not exist, a close of a proposed change that is not open, a create of an existing branch. |
| medium | `tests/dev_stack/smoke/test_scheduled_run.py` | The test reads `branch_review` before the proposed change is opened; it can fail once T058 is done. |
| medium | `service/schedules.py`, `store.py` | A store error after a receipt is reserved leaves it `processing`; a retry with the same key gets `idempotency-in-progress`. |
| medium | `infrahub_sync/cli.py` | No CLI option sets the write target before T041, so `--write-when-left-out` on `create` is always refused and the `approval-required` hint cannot be followed from the CLI. |
| medium | `service/schedules.py` | A store error in the Prefect match is still answered `503 orchestration-unavailable`. |
| low | several | A start outside the stored cadence leaves only a log line; events of one admission share `created_at`; a change during an outage drops earlier missed starts; a waiting `queue` start whose flow run never returns blocks later starts until a pause and resume; `PublicRunResource.trigger_type` is a closed literal; `CloseResult` and `ScheduleBranch` invariants are loose; `EventDetails.cause` has no pattern; error classes are not picklable; weak or missing tests for FR-025 on the worker path, FR-028, FR-029 (`sync_with_git`), SC-002 mix and SC-007. |
| low | `dev/adr/0021-…` | The decision record says 17 failure types; there are 24. Update it when the specification is extracted. |

## 6. Autonomous decisions

- **The run was done in a separate worktree**, because the main checkout was on another session's
  branch. A throwaway PostgreSQL container `sync021-pg` on 127.0.0.1:55432 served the store tests.
- **T058 and T041 were not run**, so the feature stays off for users until it is observed end to end.
  The user guide says so.
- **T059 was answered from the Infrahub source code only.** A live check would have created a branch
  and a proposed change on the Infrahub of another session's stack, with a token from its
  configuration. The permission classifier refused it, and it was not worked around. Four facts
  from the source changed decision R15 and `contracts/branch-review.md` before any code used them.
- **Accepted deviations from the documents**, now written into them:
    - a take-over of a default-branch schedule while approval is required stays stopped;
    - the worker checks approval;
    - a resume of a paused schedule stops it in one write when a reason holds;
    - a proposed change deleted in Infrahub is kept with the cause `missing`.
- **Test order**: several chunk agents wrote tests after the code, so those tests were not seen
  failing first. The reviews looked for tests that pin the implementation.
- **The `speckit-implement` skill** was not invoked by most chunk agents; they followed the tasks
  directly.
- **Commits** carry `Co-Authored-By: Claude Fable 5.2` for chunks 1 to 5 and `Claude Opus 5.5`
  after the model changed. No commit carries a `Claude-Session` line, following the project rule.

## 7. Suggested next steps

1. Run the smoke tests on a development stack of this branch: section 4 of `quickstart.md`
   (T032), and section 3 with T055. Observe a schedule end to end, including the start time (SC-007).
2. Run `tests/integration/test_branch_review_operations_integration.py` against an Infrahub that
   belongs to this work, to confirm the GraphQL requests and the permissions of the worker's token.
3. Then do T058 (branch review) and T041 (default branch), and regenerate the frontend types (T054).
4. Address the deferred medium findings, first the `run_in_progress` race and the stricter
   Infrahub fakes.
5. Remove the throwaway container with `docker rm -f sync021-pg` when the PostgreSQL tests are no
   longer needed.
6. Open a pull request when the steps above are done; this run did not open one.
