# Implementation report: automatic resume of an interrupted apply (spec 030)

- **Spec directory**: `dev/specs/030-automatic-resume/`
- **Branch**: `030-automatic-resume`, from `feature/v3-develop` at `44743494`
- **Base commit** (start of implementation): `000b3b29`
- **Head commit** (before this report): `9b2fc805`
- **Wall-clock time**: about 4 hours 30 minutes, 2026-11-23 20:20 UTC to 2026-11-24 00:50 UTC
- **Result**: all 44 tasks done. T044 ran without the preview stack check, which is deferred (see §3 and §6).

## 1. Chunk ledger

Each chunk ran in a subagent with a clean context, one at a time.

| # | Chunk (tasks.md heading) | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged upward |
|---|---|---|---|---|---|
| 1 | Phase 1: Setup | T001–T002 | 2 / 0 / 0 | `f69b8443` | An invalid value of the setting stops the Sync API at startup through `validate_settings()`, the same way as `INFRAHUB_SYNC_REQUIRE_APPROVAL`. |
| 2 | Phase 2: The stored decision | T003–T007 | 5 / 0 / 0 | `8fee3d5c`, `5e50cc77` | Foreign key to `prefect_executions`. `move_automatic_resume` refuses moves that data-model.md does not allow. `interrupt_execution` refuses a record that names another execution. |
| 3 | Phase 2: The live worker rule and the decision | T008–T011 | 4 / 0 / 0 | `25371149` | `worker_is_live` and `PoolWorker.name` are written to match spec 029, which copied them word for word in `7144b2a5`. `decide_automatic_resume` takes an extra `branch_review` argument. |
| 4 | Phase 2: The reconciler step and the submitter | T012–T018 | 7 / 0 / 0 | `2aa3ef42` | The step submits first and then records the move. The order in the tasks could not work. Exactly once comes from the idempotency keys. `confirm_left_out` is derived from the run, because receipts keep only a fingerprint. HTTP authentication refuses the reserved actor. |
| 5a | Phase 3: User Story 1, backend | T019–T025 | 7 / 0 / 0 | `078523b6` | New event detail keys. A field-parity guard test allows the derived `automatic_resume_count`. `start_after` counts from 0, so `runs show` prints 5999 where the contract example shows 6000. |
| 5b | Phase 3: User Story 1, web interface | T026 | 1 / 0 / 0 | `dfecaba6` | Types generated from an OpenAPI document dumped in-process. The resume action is in `RunPage.tsx`, not `RunActions.tsx`. The timeline had no resume entries before; it has them now. |
| 6 | Phase 4: User Story 2 | T027–T031 | 5 / 0 / 0 | `23611735`, `accf4ab0`, `4009c900` | A person is refused with 409 while an automatic submission exists. With several Sync API processes a short window remains. The `next:` wording for `not-resumable` is new text. |
| 7 | Phase 5: User Story 3 | T032–T035 | 4 / 0 / 0 | `14bbd2bb`, `65106e12` | The store follows `resume_flow_run_id` to find the run an automatic resume belongs to, not the actor. A chain of resumes counts once. |
| 8 | Phase 6: Governance and documentation | T036–T042 | 7 / 0 / 0 | `76afcc51`, `5fb9a3d5` | The ADR is 0056, because spec 029 took 0055. ADR 0027 is marked as amended. The Helm chart has no value for the setting, so the docs use `api.extraEnv`. |
| 9 | Phase 6: Checks | T043–T044 | 1 / 1 / 0 | `9c8de6f3` | T044 ⚠️: the preview stack check is deferred (see §6). Every other gate passed. |

Review fixes (§5): `c6347a37`, `a8b53061`, `420f9769`, `b4658d28`, `25c10ad5`, `a2dec2cd`, `b184e6a4`, `6023c343`, `b7b1cdc4`, `f45fd71a`, `9b2fc805`.

## 2. Tasks not completed

None. Every task is `[X]`. T044 carries the note "(preview stack deferred, see run-report.md)":

- **What ran:** the SC-001 and SC-002 steps that can be reproduced in-process.
- **What did not run:** the preview stack check. The only development stack on the machine belongs to another session.

## 3. Local-pass evidence

The table has one row per group of tests, each run with one command. Every test ID in a group was reported `PASSED` in that run. Python runs used 3.11 with the full profile on macOS. PostgreSQL runs used a throw-away `postgres:16-alpine` container that the subagent started and then removed.

| Test id | Type | Run command | Passed at (UTC) | Environment context | Verbatim pass line |
|---|---|---|---|---|---|
| `tests/service/test_settings_automatic_resume.py` (5 tests, 16 cases) | unit | `uv run pytest tests/service/test_settings_automatic_resume.py -v -p no:randomly` | 2026-11-23T20:29:45Z | n/a | `25 passed in 2.64s` (with `test_settings_web_directory.py`) |
| `tests/product_store/test_automatic_resumes.py` (47 IDs, `[sqlite]` and `[postgresql]`) and `test_schedules.py::test_an_interrupted_scheduled_run_with_an_automatic_resume_counts_later[...]` | unit + PostgreSQL | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=... uv run pytest tests/product_store/test_automatic_resumes.py tests/product_store/test_schedules.py -v` | 2026-11-23T20:44:37Z | Own container `pg030-t007` | `238 passed in 40.59s` |
| `tests/product_store/test_contract.py::test_public_surface_is_exactly_the_supported_contract` | unit | `uv run pytest "<id>" -v` | 2026-11-23T20:55:18Z | n/a | `1 passed in 0.32s` |
| `tests/service/test_automatic_resume.py` (decision tests: reasons, order, rules 6 and 8) and `tests/service/test_liveness_policy.py` (`live_workers`, worker name) | unit | `uv run pytest tests/service/test_automatic_resume.py tests/service/test_liveness_policy.py -v -p no:cacheprovider` | 2026-11-23T21:06:02Z | SQLite | `117 passed, 1 warning in 1.84s` |
| Step and submitter tests (17 tests, from `test_a_record_moves_from_the_check_to_an_ended_resume` to `test_an_http_request_cannot_act_as_the_reserved_actor`), `test_serve_site.py::test_build_service_serves_the_api_under_api_and_runs_the_liveness_loop`, `test_account_routes.py::test_create_refuses_an_invalid_or_reserved_name[the-reserved-automatic-resume-actor]` | unit | `uv run pytest tests/service/test_automatic_resume.py tests/service/test_serve_site.py ... -q -rA` | 2026-11-23T21:36:09Z | SQLite | `173 passed, 1 warning in 24.78s` |
| `tests/product_store/test_postgresql_isolation.py::test_postgresql_two_reconcilers_moving_one_automatic_resume_record_move_it_once` | PostgreSQL | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=... uv run pytest <id> -v` | 2026-11-23T21:25:50Z | Own container | `PASSED` (and 5 more runs in a row) |
| US1: `test_an_apply_whose_worker_stopped_ends_applied_with_one_automatic_resume`, the cause-text, reason-text and limit-reason tests, `test_a_check_that_reports_differs_ends_the_record` | unit | `uv run pytest -rA -q tests/service/test_automatic_resume.py -k "..."` | 2026-11-23T22:02:21Z | SQLite | `16 passed, 49 deselected` |
| US1: new tests in `test_account_presentation.py`, `tests/client/test_resume.py`, `tests/cli/test_runs_resume.py` | unit | `uv run pytest ... -rA -q` | 2026-11-23T22:02:29Z | n/a | `15 passed` |
| `frontend/src/features/runs/automatic-resume.test.ts` (first 15 cases) | unit (Vitest) | `pnpm exec vitest run src/features/runs/automatic-resume.test.ts --reporter=verbose` | 2026-11-23T22:10:26Z | Node, offline | `Tests  15 passed (15)` |
| US2: `test_every_reason_code_has_a_not_started_case`, `test_a_record_that_ends_without_a_resume_starts_nothing_and_says_why[9 codes]`, the limit tests, the race tests, `test_resume_run.py` take-over tests, `test_runs_resume.py` `next:` tests | unit | `uv run pytest -q -rA tests/service/test_automatic_resume.py tests/service/test_resume_run.py tests/cli/test_runs_resume.py -k "..."` | 2026-11-23T22:28:26Z | SQLite | `37 passed` |
| US3: `test_schedules.py` automatic-resume, chain and row tests (`[sqlite]` and `[postgresql]`) | unit + PostgreSQL | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=... uv run pytest tests/product_store/test_schedules.py -v -k "automatic_resume or chain or row_that_ends"` | 2026-11-23T22:46:55Z | Own container, port 55435 | `18 passed` |
| US3: `tests/service/test_schedule_automatic_resume.py` and the new `test_schedule_admission.py` cases | unit | `uv run pytest tests/service/test_schedule_automatic_resume.py tests/service/test_schedule_admission.py -q -rA -k "..."` | 2026-11-23T22:57:49Z | SQLite | `11 passed` |
| Review fixes F1–F6: the 15 tests in the fix report (`test_a_refused_person_resume_leaves_the_record_waiting` … `test_a_submitted_resume_that_no_worker_picked_up_is_counted[sqlite\|postgresql]`) | unit + PostgreSQL | `uv run pytest -q <ids>`, one run for each finding | 2026-11-23T23:54:58Z to 2026-11-24T00:13:00Z | SQLite and own container | `3 passed`, `1 passed`, `11 passed`, `2 passed`, `2 passed`, `2 passed`, `2 passed`, `2 passed` |
| Review fixes F7, F8 and examples: the 42 IDs in the fix report, including `test_runs_show_prints_a_resume_that_sync_submitted_and_no_worker_started` and `test_a_run_answer_names_the_actor_of_each_automatic_resume_record` | unit | `uv run pytest -v -p no:randomly <ids>` | 2026-11-24T00:44:12Z | SQLite | `42 passed` |
| `frontend/src/features/runs/automatic-resume.test.ts` (final, 26 cases) | unit (Vitest) | `pnpm exec vitest run src/features/runs/automatic-resume.test.ts` | 2026-11-24T00:44Z | Node, offline | `Tests  26 passed (26)` |
| Full offline unit tier (final) | unit | `uv run invoke tests.tests-unit` | 2026-11-24T00:36:59Z | n/a | `11334 passed, 9 skipped, 655 deselected, 2 xfailed` |
| Product-store PostgreSQL tier (after F1–F6) | PostgreSQL | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=... uv run invoke tests.tests-product-store-postgresql` | 2026-11-24T00:20:18Z | Own container | `420 passed, 1440 deselected` |
| Python 3.10 checks (T043) | unit | `uv run invoke tests.check-310` | 2026-11-23 (chunk 9) | Python 3.10 | `check-310 passed all three Python 3.10 legs` |
| Full frontend suite (final) | unit (Vitest) | `pnpm test` in `frontend/` | 2026-11-24T00:44Z | Node | `Tests 436 passed (436)` |
| Preview stack check, SC-001 (`quickstart.md`) | e2e | The commands in `run-report.md`, "Preview stack check" | deferred — the only development stack on this machine belongs to another session | Needs a free machine with the preview stack | not run |

## 4. Review findings

Review mode: command (/code-review)

The review ran at effort `high` over `000b3b29..9c8de6f3`.

| Severity | File | Summary | Status |
|---|---|---|---|
| important | `infrahub_sync/service/service.py:755` | A refused `runs resume` or `verify` by a person still ended the automatic resume. | Fixed `c6347a37` |
| important | `infrahub_sync/service/liveness.py:352` | `start_after` could be read later than the real start after a replayed submission, which could give a false `no-progress`. | Fixed `a8b53061` |
| important | `infrahub_sync/product_store/store.py:2952` | A scheduled run was not counted when its resume ended before the record named it. | Fixed `420f9769` |
| important | `infrahub_sync/service/liveness.py:52` | A 5xx refusal, such as `plan-unavailable`, ended the automatic resume for good. | Fixed `b4658d28` |
| important | `infrahub_sync/service/liveness.py:429` | The check was judged from any `verification` report, and an empty report counted as a pass. | Fixed `25c10ad5` |
| nit (treated as a fix for FR-009) | `infrahub_sync/service/liveness.py:456` | A submitted resume that no worker picked up did not count toward the limit of 3. | Fixed `a2dec2cd` |
| nit | `infrahub_sync/cli.py:1317`, `automatic-resume.ts:52` | The passed check line was hidden for records that ended after the check. | Fixed `b184e6a4` |
| nit | `frontend/src/features/runs/automatic-resume.ts:8` | The web page used a hard-coded actor name instead of the name the Sync API returns. | Fixed `6023c343` |

## 5. Autonomous decisions

- **Submit first, then record the move.** The reconciler step submits the check or the resume, then records the move to `checking` or `resuming` with a compare-and-set. tasks.md said the opposite order, which could not work. Exactly once comes from the fixed idempotency keys. ADR 0056, research R3 and R4, and data-model.md describe this order.
- **ADR 0056, not 0055.** Spec 029 (`/api/health`) took 0055.
- **Shared worker liveness rule.** `worker_is_live` and `PoolWorker.name` in `orchestration.py` are identical to the text on branch `029-api-health`, so the two branches merge without a conflict. The other session confirmed this.
- **Preview stack check deferred (T044).** The only development stack on the machine (`infrahub-sync-dev-*`) belongs to another session, so this run did not start, stop or touch it. `run-report.md` lists the commands to run on a free machine. Three steps are covered only by that check:
  - a real plan of 10,000 operations;
  - stopping a real worker container;
  - a new plan afterwards that shows 0 creates and 0 updates.

  `runs cancel` during an apply, followed by a check that no record exists, also has no test yet.
- **Known limits that remain** (recorded in ADR 0056):
  - with several Sync API processes, a short window remains in which a person's takeover and an automatic submission can both proceed; Sync logs a warning when it happens;
  - retries after a 5xx answer have no time limit;
  - the Sync API and the worker must be upgraded together, because the verification report now carries `flow_run_id`;
  - a destination that cannot read planned nodes always ends `check-failed`.
- **The web page was built without a prototype first.** The changes are small: notices, timeline rows and hiding the resume button. A project memory note asks for `emil-prototype` before new web screens, so review the run page wording.
- **Open questions kept open.** spec.md still has two `[NEEDS CLARIFICATION]` markers:
  - the key of the Jira issue on the 2,000-operation limit of `verify`;
  - the source of the limit of 3.

  The requirements checklist keeps one unchecked item for them.
- **Contract wording that follows the code:**
  - `runs show` prints the 0-based start position;
  - the `person-took-over` event names the person, not the reserved actor.

## 6. Suggested next steps

1. Run the preview stack check of `quickstart.md` (SC-001, SC-002) on a machine with a free development stack, and add the result to `run-report.md`.
2. Answer the two open questions in spec.md.
3. Decide whether the takeover race with several Sync API processes needs a fix inside the store's reservation transaction before 3.0.
4. Open the pull request into `feature/v3-develop`.
