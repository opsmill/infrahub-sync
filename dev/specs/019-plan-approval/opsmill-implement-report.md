# Implementation report: plan approval and a `verify` that checks the destination

- **Feature:** approve a plan without applying it, a `verify` that runs every pre-write check of `apply` against the destination, an optional approval requirement, and withdrawal of a finished plan with `runs cancel`.
- **Spec directory:** `dev/specs/019-plan-approval/`
- **Branch:** `verify-apply-approval`, in the worktree `.claude/worktrees/verify-wording`. Not pushed.
- **Base commit:** `b46ecd79`
- **Head commit:** `2a4cdc73`
- **Wall-clock time:** about 1 hour 50 minutes, from the baseline run to the review fixes (2026-11-10, 14:50Z to 16:45Z).
- **Status:** DONE. Every task is ticked. Two preview tests are deferred because the local preview stack could not start (see §4 and §6).

## 1. Summary

| Measure | Value |
| --- | --- |
| Tasks in `tasks.md` | 48, all `[X]` |
| Chunks dispatched | 7, plus one review pass |
| Unit tier at the base | 6449 passed, 4 skipped, 1 xfailed, 0 failed |
| Unit tier at the head | 6598 passed, 4 skipped, 1 xfailed, 0 failed |
| Review findings fixed | 5 high (4 code, 1 test gap) |
| Review findings deferred | 6 medium, 12 low |

## 2. Chunk ledger

### Chunk 1: Phase 1, setup (T001)

- **Tasks:** 1, all done.
- **Commit:** `85a420ce`
- **Notes:** recorded the baseline: 6449 passed, 4 skipped, 1 xfailed, 0 failed; `ruff check .` clean. No tests added.

### Chunk 2: Phase 2, foundational (T002–T004)

- **Tasks:** 3, all done.
- **Commit:** `a5b10b8a`
- **Notes:**
  - `RunApproval` is not exported from `infrahub_sync.product_store`, because a test locks the package's public names. Code imports it from `.models`.
  - No database CHECK constraint enforces "all approval columns set or none". The model refuses a partial row.
  - The client run-resource projection test failed until T011 added `approval`, as expected.

### Chunk 3: Phase 3, user story 1, approve (T005–T016)

- **Tasks:** 12, all done.
- **Commit:** `a9c62fc1`
- **Notes:**
  - `approve` receipts have no Prefect flow run, so the receipt validator exempts the `approve` operation (`SERVICE_ANSWERED_OPERATIONS`).
  - The approve response is `ApprovedRunResource` with `plan_summary` (`creates`, `updates`, `deletes_recorded`).
  - `ErrorDetail` gained an optional `details` field. Other error bodies do not change.
  - The `ServiceAPIError` handler moved to module level to keep `create_app` under the ruff complexity limit.
  - `approve` prints `idempotency_key` first, as the other run mutations do. This is an addition to `contracts/cli.md`.

### Chunk 4: Phase 4, user story 2, `verify` checks the destination (T017–T026)

- **Tasks:** 10, all done.
- **Commit:** `ecfb917c`
- **Notes:**
  - The tests and the code were written in the same pass, not tests-first. The parity test has a control case in which every check passes.
  - `apply` raises the same errors, in the same order, with the same messages. `PlanVerificationError` gained a `checks` field.
  - A skipped check is not reported as passed:
    - `planned_fields` runs only for Infrahub destinations;
    - `lineage` runs only for destinations that record lineage;
    - `destination_binding` runs only when both sides record a binding.
  - The `verify` stage also checks the configuration binding, as `sync` does.
  - Three existing tests whose subject is file-only `verify` now stub the destination check.
  - `service/flow.py` reached 1101 lines, so it has a pylint `too-many-lines` disable with a reason.

### Chunk 5: Phase 5, user story 3, approval required (T027–T032)

- **Tasks:** 6, all done.
- **Commit:** `39c4624e`
- **Notes:**
  - The new tests were seen failing before the code was written.
  - In `apply`, the approval check runs after the confirmation check and before the plan checksum check. In the other order, `approval-checksum-mismatch` could never be returned.
  - An empty value for `INFRAHUB_SYNC_REQUIRE_APPROVAL` stops the service at start-up.
  - On the client, `PrincipalResource.approval_required` defaults to `false`, so an older Sync API that does not send it still works.

### Chunk 6: Phase 6, user story 4, withdrawal (T033–T041)

- **Tasks:** 9, all done.
- **Commit:** `badc1fdd`
- **Notes:**
  - The new tests were seen failing first: 19 failed, 17 passed.
  - **Client fix outside the task list:** the client accepted only `202` for a cancel. It now accepts `200` and `202`, with a test.
  - A `cancel` receipt answered `200` may have no flow run ID.
  - **FR-022 is checked in a narrower form:** "a cancel and an approve never both succeed" would forbid approving and then withdrawing, which is the normal withdrawal. The test asserts instead that an approval is never stored on a cancelled run.
  - No existing test expected `execution-terminal` when cancelling a planned run, so none changed.

### Chunk 7: Phase 7, docs, changelog, gates and live check (T042–T048)

- **Tasks:** 7, all done.
- **Commit:** `8da2d83a`
- **Notes:**
  - Neither Compose file passed `INFRAHUB_SYNC_REQUIRE_APPROVAL` to the service. It was added to `docker-compose.yml`, to `development/docker-compose.yml`, and to the copy in `infrahub_sync/testcontainers/`, which a test requires to be byte-equal.
  - `changelog/+verify-wording.fixed.md` was removed. It said that `verify` does not contact the destination, which is no longer true. `+verify-destination-checks.changed.md` covers the topic.
  - The two tests in `tests/preview/test_run_completion.py` now accept a withdrawal as an outcome. They were not run (see §4).
  - All six quickstart scenarios passed on the development stack.

### Review pass (Phase 6 of this command)

- **Commit:** `2a4cdc73`
- **Notes:** fixed every high finding (§5). A failed `verify` now prints the failed check names before `error: run-terminal`.

## 3. Tasks not completed

None. All 48 tasks are `[X]`.

## 4. Local-pass evidence

The environment was macOS with Python 3.11.8, in the worktree virtual environment created by `uv`. PostgreSQL cases ran against a throwaway `postgres:16-alpine` container, passed through `PRODUCT_STORE_TEST_POSTGRESQL_DSN` and removed afterwards. Parametrized tests are grouped with `[*]` and their count. The full logs are in the session scratchpad: `evidence*.txt`, `evidence-us2.txt`, `evidence-us3.txt`, `evidence-us4*.txt`, `unit-*.txt` and `review-*.txt`.

| Test id | Type | Run command | Passed at (UTC) | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/product_store/test_run_approval.py` (19 cases: migration, round trip, partial rows) | unit | `uv run pytest tests/product_store/test_run_approval.py -v` | 2026-11-10T15:07:29Z | SQLite and PostgreSQL 16 | `19 passed in 1.52s` |
| `tests/product_store/test_run_approval.py::test_record_approval_*[*]` (12) and `tests/product_store/test_postgresql_isolation.py::test_postgresql_concurrent_approvals_of_one_run_store_exactly_one` | unit | `uv run pytest -v` on those tests, with the service and client tests in the next row | 2026-11-10T15:26:03Z | SQLite and PostgreSQL 16 | `52 passed, 54 deselected` |
| `tests/service/test_approval.py::*` (15); `tests/service/test_http_api.py` (OpenAPI test and projection test); `tests/client/test_client.py::test_approve_*` (3) | unit | Same command as the previous row | 2026-11-10T15:26:03Z | n/a | Included in `52 passed, 54 deselected` |
| `tests/client/test_models.py` approval tests (9) and `tests/cli/test_client_commands.py` approve and `runs show` tests (6) | unit | `uv run pytest -v tests/client/test_models.py tests/cli/test_client_commands.py -k "approv or Approve or reason"` | 2026-11-10T15:26:13Z | n/a | `46 passed, 138 deselected` |
| `tests/test_potenda_check_plan.py::*` (11); `tests/service/test_verify_destination.py::*` (17, with 10 parity cases); `test_http_api.py::test_verify_checks_the_destination_branch_the_run_was_created_for[*]` (2); 2 CLI `verify` tests; 3 modified tests | unit | `uv run pytest -v` on those tests (full command in `evidence-us2.txt`) | 2026-11-10T15:46:09Z | n/a | `35 passed, 1 warning in 1.24s` |
| `tests/service/test_approval_required.py::*` (24); `/me` tests (4); `whoami` test (2) | unit | `uv run pytest tests/service/test_approval_required.py` and the `/me` and `whoami` tests, `-q -rA` | 2026-11-10T15:51:07Z | SQLite | `33 passed, 1 warning in 1.31s` |
| `tests/service/test_cancel_planned_run.py::*` (13); `tests/product_store/test_write_admission.py` withdrawal tests (16); 2 PostgreSQL race tests; 3 CLI tests; 1 client test | unit | `uv run pytest -rA -q` on those files, `-k` withdrawal filter (full command in `evidence-us4.txt`) | 2026-11-10T16:08:20Z | SQLite and PostgreSQL 16 | `45 passed, 266 deselected, 1 warning in 4.16s` |
| Review fixes: 2 failed-`verify` CLI tests and 1 modified missing-permission test | unit | `uv run pytest tests/cli/test_client_commands.py -k "failed_verify or missing_permission"` | 2026-11-10T16:35:26Z | n/a | `6 passed, 154 deselected in 0.24s` |
| Review fixes: `test_approval.py::test_a_plan_that_cannot_be_read_is_refused_and_the_refusal_is_audited[*]` (2); `test_write_admission.py::test_an_unresolved_approve_receipt_does_not_block_an_apply[*]` (2); `test_run_approval.py::test_record_approval_refuses_a_cancelled_run_with_run_cancelled[*]` (2) | unit | `uv run pytest` on the store and service tests | 2026-11-10T16:40:22Z | SQLite and PostgreSQL 16 | `PASSED` for each; the PostgreSQL run gave `909 passed, 2 warnings in 27.84s` |
| Review fix: `test_approval_required.py::test_a_retried_apply_whose_submission_failed_is_refused_once_approval_is_required` | unit | `uv run pytest tests/service/test_approval_required.py -k retried_apply` | 2026-11-10T16:43:26Z | n/a | `1 passed, 27 deselected, 1 warning in 0.68s` |
| `tests/preview/test_run_completion.py::test_the_python_client_cancels_a_run_it_has_just_admitted` (modified) | e2e | `uv run invoke preview.up && uv run pytest tests/preview/test_run_completion.py -m preview` | deferred — local E2E not supported | The preview stack needs port 8080, which another project's container uses | not run |
| `tests/preview/test_run_completion.py::test_raw_http_cancels_a_run_it_has_just_admitted` (modified) | e2e | Same command as the previous row | deferred — local E2E not supported | Same as the previous row | not run |
| Live quickstart, 6 scenarios: plan, approve, verify passing and failing, setting on, withdraw, setting off | live | Steps in `quickstart.md`, results under "Live results" | 2026-11-10T16:25Z to 16:30Z | Development stack `infrahub-sync-dev`, stopped afterwards | 6 of 6 scenarios passed |

Unit tier after each chunk:

| After | Passed | Skipped | xfailed | Failed |
| --- | --- | --- | --- | --- |
| Base | 6449 | 4 | 1 | 0 |
| Chunk 2 | 6461 | 4 | 1 | 1, expected until T011 |
| Chunk 3 | 6502 | 4 | 1 | 0 |
| Chunk 4 | 6534 | 4 | 1 | 0 |
| Chunk 5 | 6564 | 4 | 1 | 0 |
| Chunk 6 | 6589 | 4 | 1 | 0 |
| Chunk 7 | 6590 | 4 | 1 | 0 |
| Review | 6598 | 4 | 1 | 0 |

## 5. Review findings

`speckit-review-run` ran its six reviews: code, tests, errors, comments, types and simplify. No review reported a critical finding.

| Severity | File:line | Summary | Status |
| --- | --- | --- | --- |
| High | `infrahub_sync/service/service.py:353` | Retrying an `apply` whose submission had failed got around `INFRAHUB_SYNC_REQUIRE_APPROVAL`: it was accepted with `202` and no approval | Fixed in `2a4cdc73` |
| High | `infrahub_sync/product_store/store.py:2068` | An `approve` receipt left reserved after a crash or rollback blocked every later `apply` with a conflict | Fixed in `2a4cdc73` |
| High | `infrahub_sync/service/service.py:472` | Approving a plan that had expired or could not be read left no audit record (FR-008) | Fixed in `2a4cdc73` with the new outcome `refused-plan-unavailable`, added to `data-model.md` |
| High | `infrahub_sync/cli.py:1054` | A failed `verify` did not name the failed check (SC-004) | Fixed in `2a4cdc73`: the CLI prints `verify failed:`, one `failed_check:` line per check, and the `cause:` and `hint:` lines when recorded |
| High (test gap) | `tests/product_store/test_run_approval.py:301` | The store's refusal to approve a cancelled run was tested only on PostgreSQL | Fixed in `2a4cdc73` |
| Medium | `infrahub_sync/product_store/store.py:2127` | An approval can be stored while an `apply` is admitted but not yet running; only the audit record is affected | Deferred |
| Medium | `infrahub_sync/service/service.py:505-515` | No test covers how store refusals map to audit outcomes | Deferred |
| Medium | `infrahub_sync/service/app.py:308` | OpenAPI lists only `202` for the cancel route; a withdrawal returns `200` | Deferred |
| Medium | `infrahub_sync/service/flow.py:298` | A run-binding mismatch during `verify` records no failed check | Deferred |
| Medium | `infrahub_sync/service/service.py:585` | The `cancel_run` docstring is out of date | Deferred |
| Medium | `infrahub_sync/plan/errors.py:399,500` | Check names are typed `str` rather than `PreWriteCheck` | Deferred |
| Low | various | Wrong audit outcome for `run-not-found`; three out-of-date comments; five simplifications; three gaps in CLI and model tests | Deferred |

## 6. Decisions made during the run

- **Chunking:** user story 1 ran as one chunk of 12 tasks instead of being split into tests and code. A chunk with only tests could not have shown its tests passing locally. User stories 2 (10 tasks) and 4 (9 tasks) also ran as one chunk each.
- **Order of work in chunk 4:** the tests were not written first. The parity test's control case reduces the risk that a test passes for the wrong reason.
- **Deferred preview tests:** the two tests in `tests/preview/test_run_completion.py` were changed to accept a withdrawal and were not run. The preview stack needs port 8080, which another project's running container uses, and the agent did not stop it. Run `uv run invoke preview.up && uv run pytest tests/preview/test_run_completion.py -m preview` when the port is free, or rely on CI.
- **Narrower FR-022 check:** see chunk 6.
- **Compose files and changelog:** see chunk 7. The Compose change touches the operator deployment file `docker-compose.yml`.
- **Review triage:** only high findings were fixed. The medium findings are real but don't affect what an operator sees, except the OpenAPI `200` and the run-binding case. Those two are small follow-ups.

## 7. Suggested next steps

1. Run the two preview tests once port 8080 is free, or let CI run them.
2. Fix the two medium findings an operator can see: the OpenAPI response for a withdrawal (`app.py:308`) and the failed check name for a run-binding mismatch in `verify` (`flow.py:298`).
3. Push `verify-apply-approval` and open a draft pull request into `feature/v3-develop`. It needs one `changes/*` label only if it targets `main`.
4. After the pull request merges, run `speckit-opsmill-extract` to move the approval and withdrawal decisions into the developer docs, and record an ADR for "approval is optional and set for the whole service".
5. Reply to the customer with the new flow: plan, review, verify, approve, then apply. The approval requirement is optional.
