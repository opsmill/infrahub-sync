# Implementation report: Resume an interrupted apply

**Status**: INCOMPLETE. All code tasks and review fixes are done, and the PostgreSQL store tests pass. The stack test
(T034) and the benchmarks (T035) have not run, because they need infrastructure.

- **Spec directory**: `dev/specs/022-resume-interrupted-apply/`
- **Branch**: `022-resume-interrupted-apply`, stacked on `020-plan-problem-reporting` (PR #409), which was merged in at
  `e0dd6568` (merge commit `ae108474`)
- **Base commit**: `5f005776`
- **Head commit**: `b76c5163`
- **Unit tier**: 7,569 passed at baseline; 7,830 passed at head, 0 failed

## Chunk ledger

| Chunk | Tasks | ✅ / ⚠️ / ❌ | Commits |
| --- | --- | --- | --- |
| A. Engine progress marker | T001–T005 | 5 / 0 / 0 | `67422c7a`, `d06ea24c` |
| B. Marker in store and service, `verify` admission | T006–T012 | 7 / 0 / 0 | `477210ba` |
| C. Resume admission in the store, models | T013–T016 | 3 / 1 / 0 | `ce4a1853` |
| D. Resume through service, flow, API, client, CLI | T017–T023 | 7 / 0 / 0 | `17288ddb`, `d8690f18` |
| E. Per-operation `verify` report | T024–T028 | 5 / 0 / 0 | `2af6d000`, `fc7feaac` |
| F. Resume of `sync` runs | T029–T030 | 2 / 0 / 0 | `5b717709` |
| G. ADR, docs, changelog, checks | T031–T036 | 4 / 2 / 0 | `a4c4a0ed`, `2cc75957` |

## Tasks not completed

- **T015 (partial)**: the PostgreSQL cases of `tests/product_store/test_resume_admissions.py` skip without
  `PRODUCT_STORE_TEST_POSTGRESQL_DSN`. The same holds for the PostgreSQL cases added by the review fixes. All SQLite
  cases and the emulated PostgreSQL adapter pass.
- **PostgreSQL variants (run 2026-11-13T16:29:55Z)**: `PRODUCT_STORE_TEST_POSTGRESQL_DSN` pointed at a throwaway
  `postgres:16-alpine` container. `uv run invoke tests.tests-product-store-postgresql` reported `174 passed, 963
  deselected`, with no test skipped. It printed
  `PASSED tests/product_store/test_resume_admissions.py::test_exactly_one_of_two_concurrent_resumes_is_admitted[postgresql]`,
  and the PostgreSQL cases added by the review fixes also passed. The whole PostgreSQL suite ran at head `e1abfad5`.
- **T034 (not run)**: `tests/preview/test_resume_interrupted_apply.py` is written but was never run, because no stack
  was running. It targets the preview stack, which PR #412 removes. The steps for the development stack are in
  `tasks.md` under T034, and apply once #412 is merged into this branch. It is also the first run of the `verify`
  destination filters against a live Infrahub.
- **T035**: the SC-003 and SC-004 benchmarks need the tier M and L datasets. The baseline runs at `5f005776`.

## Review findings

The Phase 6 review (code, errors, tests, types, comments, simplify) and `/code-review xhigh --fix` ran on
`5f005776..3a6c3555`. No critical finding. Every important finding was fixed:

| Finding | Fix | Commits |
| --- | --- | --- |
| A checksum-conflict hint could replace the refusal; write purposes defined four times | Hint guarded; one exported constant | `d45ac207` |
| A worker whose execution had ended kept writing for up to 5 minutes | The store answers `ended`; the apply stops before its next dispatch | `f9b27828` |
| A failure record of an earlier execution bounded the uncertain window | Failure records carry the execution identifier | `f9b27828`, `e6c48b3e` |
| The window was too small after failed saves | A killed worker leaves every remaining operation uncertain (research R11) | `e6c48b3e` |
| The window counted plan positions, not commits | The 500 counts creates and updates only | `f9b27828` |
| An interrupted run that was never approved could not be resumed when approval is required | `approve` accepts a resumable run (FR-019, research R12) | `ccf58105` |
| Run counts after a resume covered only the resume | Counts cover every execution of the run (research R6) | `444040c8`, `079f8dea` |
| `verify` compared against an arbitrary node when several share a key | Reported as `differs` (research R13) | `11958520` |
| A create with a null optional key value was never read | Each create keyed on the values it sets | `11958520` |
| Attribute keys dropped the peer `__ids` filter | Both filters sent | `11958520` |
| Saves fired at almost every commit ("end of each kind") | Kind-change save only at the plan's last operation | `ec846323` |
| Next-step text said "plan again" after an interrupted write | Verify, then `runs resume`, on a resumable run | `65562788`, `3a5dec34` |
| HTTP example position did not match its counts | Example uses position 5999 | `6e1a843f` |
| A `sync` killed while planning looked resumable and failed with 503 | Resumable only once its plan is published; 409 refusals, recorded | `0b32502b`, `b76c5163` |
| `GET /runs/{id}` read the whole plan on every request | Per-process plan-layout cache, bounded at 200,000 operations (research R14) | `0b32502b` |

Not fixed:

- A kind with no human-friendly ID whose uniqueness constraint spans several fields is reported as `differs` when the
  key is ambiguous, not resolved to the right node.
- AGENTS.md has no `runs resume` line in its CLI command list.

## Decisions made during the review fixes

- A killed worker now leaves every remaining operation uncertain, not only the next 500. The store keeps no record
  that tells whether the last save before a kill succeeded, so the narrow window could leave out written operations.
- The progress marker is no longer saved at each change of kind, because kinds interleave within a tier.
- `reconciliation_required` stays set for a `sync` killed while planning, because a worker that liveness ended can
  still publish its plan and write later.

## Suggested next steps

1. Run the PostgreSQL store tests with `PRODUCT_STORE_TEST_POSTGRESQL_DSN` set.
2. Run T034 on the preview stack, then T035.
3. Bring in `feature/v3-develop`, #411 and #412 when the coordinator gives this branch its turn; resolve the
   conflicts in `product_store/store.py`, `tests/product_store/test_run_approval.py` and the dev-stack smoke test.
4. Push and open the stacked PR on #409 when the user asks.
