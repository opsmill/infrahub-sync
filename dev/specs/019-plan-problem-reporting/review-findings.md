# Review findings for spec 019

The review covered `2c7caaee..HEAD` on branch `019-plan-problem-reporting`. It ran these
`speckit-review-run` reviews: code (twice: one pass on six named risks, one general pass),
errors, types, tests, comments and simplify.

Two findings are HIGH, and both are fixed:

- A duplicate source identity let the kept record be written when the adapter gave it no
  record ID.
- No test checked that a branch validation needs a valid token.

Not decided yet: which role may run a branch validation. The specification and
`contracts/sync-api.md` do not say.

Counts: 0 critical, 2 high, 6 medium, 15 low.

The review did not report the state the team had already accepted:

- T081 is deferred.
- T072 and the live half of T077 have not run.
- The T080 sign-off gate is open.
- ADR 0020 to 0022 have the status Proposed.
- `ty` reports 20 `unused-ignore-comment` warnings.
- `tests/compose/test_image_pins.py` fails as expected (xfail).

## Findings

| Severity | File:line | Summary | Status |
| --- | --- | --- | --- |
| HIGH | `infrahub_sync/plan/problems.py:365` | The problem pass found the kept record of a duplicate source identity only by its source ID. When the adapter builds a record without a record ID (for example a generic REST record with no `id`), the pass left out only the refused duplicate, and the plan wrote the kept record. The specification says every record of a duplicate is left out. | Fixed inline in `86415f46`: the pass finds the kept record by identity. Test: `tests/plan/test_problem_pass.py::test_a_duplicate_whose_kept_record_has_no_record_id_leaves_out_both`. |
| HIGH | `infrahub_sync/service/config_routes.py:536` | Every test of `validate` runs as the administrator. A validation with `branch` reads Infrahub with the registered credential, but no test sends it without a token or with a token the server does not accept. | Partly fixed in `dbfcd038`: a new test expects 401 and no schema read when the token is missing or not accepted. Deferred: which role may run a branch validation (administrator only, or any authenticated caller). The specification does not decide this, and the choice is a design change. |
| MEDIUM | `infrahub_sync/infrahub_errors.py:264`, `infrahub_sync/service/failure_cause.py:441` | `rejected_write` cut Infrahub's message to 1000 characters before `failure_record` masked secrets in it. A secret that crossed the limit stayed partly visible in the failure record and in the CLI output. | Fixed inline in `dbfcd038`: secrets are masked first, then the message is cut. Test: `tests/service/test_failure_cause.py::test_a_secret_in_infrahubs_rejection_message_is_masked_before_the_message_is_cut`. |
| MEDIUM | `infrahub_sync/adapters/references.py:89-107` | A peer that the read loads later, of the same kind or of a kind later in the order (for example `Location.parent`), is reported as `peer_not_found_in_source`. Through an optional relationship, the record is kept without that relationship. Through a required relationship, the record is left out with the wrong reason. | Deferred: the fix is to resolve these references after every kind is read, which is a design change. |
| MEDIUM | `infrahub_sync/cli.py:1233-1236` | When `sync --wait` stops because the plan leaves out records, the command exits 1 only if a second `get_results` call succeeds. If that call fails, the command exits 0 although nothing was written. | Deferred: medium severity. The fix is to exit 1 whenever the outcome is `left-out-review-required`. |
| MEDIUM | `infrahub_sync/plan/models.py:531` | A `PlanManifest` at format 4 without `report` passes validation, and the left-out gate then counts 0 records left out. Only `parse_plan_artifact` requires the report, and every apply path goes through it. | Deferred: no current path reaches it. The fix is a model validator. |
| MEDIUM | `infrahub_sync/plan/writer.py:247` | When `report` is `None`, the writer saves an empty report, and `dropdown_values_checked` is true by default. Potenda, the only caller, passes both values. | Deferred: no current path reaches it. The fix is to make both keyword arguments required. |
| MEDIUM | `tests/product_store/test_stopped_sync_admission.py:263-301` | Each case in the table of dispatched runs breaks two conditions at once, so no test fails if the `applied_operations` condition is removed. No test covers the clause that keeps the admission of a run that requires reconciliation. | Deferred: the release logic is correct, as checked under point 1. Only the test is missing. |
| LOW | `infrahub_sync/service/worker.py:65`, `infrahub_sync/product_store/store.py:136` | The worker ID is new on every start, so each restart adds a row to `worker_environments`, and no step deletes rows. | Deferred. |
| LOW | `infrahub_sync/service/config_routes.py:271-284` | Any authenticated caller can start the destination reads of a branch validation. The reads block a request thread, with a 60-second timeout and no rate limit. | Deferred: linked to the role decision above. |
| LOW | `infrahub_sync/configuration/capabilities.py:194-230` | The permission findings describe the account of the API's copy of the token, not the worker's import account. | Deferred: to document. |
| LOW | `infrahub_sync/plan/problems.py:273-274` | When `_operation_identity` cannot derive an identity, it returns `None`, and a left-out record without an identity matches no operation. Derivation fails first today, but no code enforces that. | Deferred. |
| LOW | `infrahub_sync/adapters/references.py:89-99` | When a filter removes the peer of an optional relationship, the read records no load report entry, and the plan clears the link without an explanation in the report. | Deferred. |
| LOW | `infrahub_sync/configuration/capabilities.py:562-568` | A Dropdown attribute without stated choices is not checked, but the plan still records `dropdown_values_checked` as true. | Deferred. |
| LOW | `infrahub_sync/client/models.py:756` | When the server's failure record does not validate, `stage_failure_resource` returns `None`, and the CLI shows no failure. | Deferred. |
| LOW | `infrahub_sync/plan/models.py:471`, `:613` | No model enforces that `left_out` equals `left_out_with_problem` plus `left_out_dependent`. | Deferred. |
| LOW | `infrahub_sync/plan/models.py:815` | `PlanProblem` does not enforce the fields that each problem type requires. | Deferred. |
| LOW | `infrahub_sync/plan/models.py:674` | The frozen models hold mutable dictionaries, and code outside `LoadReport` changes its entries. | Deferred. |
| LOW | `infrahub_sync/adapters/ipfabricsync.py:134` | An IP Fabric peer that the read cannot resolve is recorded as `wrong_value_type` with the value `None`. | Deferred. |
| LOW | `infrahub_sync/adapters/aci.py:270`, `:286` | An ACI record without an ID has the record ID `""` but reference entries with source ID `None`, so the read does not attach the record's identity to those entries. | Deferred. |
| LOW | `infrahub_sync/adapters/nautobot.py:253`, `infrahub_sync/adapters/genericrestapi.py:199` | An entry in a list of peers that has no `id` becomes a peer with the ID `"None"`. | Deferred. |
| LOW | `tests/plan/test_problem_pass.py:288-297` | The check that no operation belongs to a left-out record passes without checking anything when `left_out` is empty. | Deferred. |
| LOW | `infrahub_sync/service/failure_cause.py:418` | The docstring says `failure_record` does not raise, and its caller still guards the call. The two do not contradict the code, but they contradict each other. | Deferred. |

The simplify review made five suggestions. None changes behavior, so this table does not list
them.

## The six points named for this review

1. **A stopped `sync` gives back its write admission: no finding.**
   - The flow sets `release_write_admission=True` in one place only (`flow.py:867`). That
     place records phase `planned`, after the plan checkpoint and before `verify` and
     `apply`.
   - `_releases_write_admission` also requires the verdict `completed/succeeded` and no
     `applied_operations`.
   - The DELETE matches only the run's own `sync` admission and skips a run that requires
     reconciliation. It runs in the same transaction as the terminal update, which applies
     only while the worker holds the claim.
   - `write_admissions.run_id` is a primary key, and `_arbitrate_reservation` locks the run
     row, so two writers cannot both win the next admission.
   - The PostgreSQL test cases were skipped because no PostgreSQL connection was set for
     them, so this verdict rests on reading the code.
2. **Credentials: one medium finding, fixed in `dbfcd038`.**
   - Before the fix, a secret in Infrahub's own rejection message could stay partly
     visible, because the message was cut before the secrets were masked.
   - Every other value in failure records, problem records, plan files, run artifacts and
     CLI output comes from typed attributes, status codes or fixed templates. The flow
     records `cause_type` as a class name, never as the text of an exception.
3. **`report.jsonl` bound to the plan checksum: no finding.**
   - The report's digest, size and five counts are in the checksummed manifest.
   - `report_mismatch` runs when the plan is read and again before apply
     (`verify._report_failures`).
   - A plan at format 4 without a report is refused.
4. **A placeholder `infrahub-sync:not-in-source:` identity reaching a write: no finding.**
   - `writer._refuse_not_in_source_peers` checks every operation line before anything is
     written.
   - Apply dispatches only the operations of the verified artifact.
5. **`configs validate --branch` and `worker_environments`: two low findings and one high
   finding.**
   - The high finding is the missing authorization test in the table above. The 401
     behavior now has a test. Which role may call the route is not decided yet.
   - A caller can choose only the branch, not the address or the credential.
   - The two low findings are the reads that any caller can start, without a rate limit,
     and the row that each worker restart adds to `worker_environments`.
6. **Rows of left-out records in `plan.parquet`: no finding on writes.**
   - `apply_plan` reads only `plan/`.
   - Only `compute_plan_fingerprint` reads `plan.parquet`.

## Verification after the fixes

- `uv run invoke format`: clean.
- `uv run invoke lint`: passes, with the 20 known `ty` warnings.
- `uv run ruff check .`: passes.
- `uv run invoke tests.tests-unit`: 7372 passed, 4 skipped, 285 deselected, 5 xfailed.
