# Data model: Resume an interrupted apply

## Execution progress marker (`prefect_executions.apply_progress`, new nullable JSON column)

| Field | Type | Meaning |
| --- | --- | --- |
| `position` | integer ≥ 0 | Index in the saved plan's order of the last operation known to be committed by this execution. |
| `operation_id` | string | Identifier of the operation at `position`, so a reader can check the marker against the plan. |
| `total` | integer | Number of operations in the saved plan. |
| `start_after` | integer or null | For a resume, the position it started after; null for the first apply. |
| `saved_at` | ISO 8601 time | When this marker was saved. |

Written only while the execution is not terminal (`WHERE terminal_at IS NULL`). Never ahead of a commit.

## Resume admission (`resume_admissions`, new table)

| Column | Type | Meaning |
| --- | --- | --- |
| `run_id` | text, FK `product_runs` | The interrupted run. |
| `resume_number` | integer ≥ 1 | 1 for the first resume of the run, 2 for the next. |
| `receipt_id` | text, unique, FK `mutation_receipts` | The accepted resume request. |

Primary key `(run_id, resume_number)`. The original `write_admissions` row is kept.

## Resume request (`ResumeRunRequest`)

| Field | Type | Rules |
| --- | --- | --- |
| `expected_checksum` | string | Must equal the saved plan's checksum. |
| `confirm_writes` | boolean | Must be true. |
| `confirm_left_out` | boolean, default false | Required when the plan left out records. |
| `reason` | string or null | Optional. Checked for secrets as on `apply`. |

## Run progress view (`RunResource.apply_progress`, derived, not stored)

| Field | Type | Meaning |
| --- | --- | --- |
| `position` | integer or null | The highest saved position over all the run's write executions (critique E2). |
| `saved_at` | time or null | When it was saved. |
| `by_kind` | list of `{kind, committed, uncertain, not_reached}` | Derived from the position and the saved plan. `uncertain` counts the uncertain operations: through the failing operation when it is known; otherwise the next 500 after the position when the last save attempt succeeded, and every remaining operation when it did not (critique E3). |
| `resumable` | boolean | True when the run is interrupted and no execution is unfinished. |
| `verify_required` | boolean | True when no `verify` passed after the latest interruption. |

## Resumes (`RunResource.resumes`, derived from executions and receipts)

List of `{actor, requested_at, reason, start_after, outcome}` in order.

## Verify report (`results.verification.operations`)

| Field | Type | Meaning |
| --- | --- | --- |
| `operation_id` | string | An uncertain operation (see `by_kind.uncertain`). |
| `kind` | string | Its kind. |
| `action` | `create` or `update` | Deletes are never written, so never reported. |
| `state` | `matches`, `differs`, `missing` | Destination state compared with the plan. |

`results.verification.operations_truncated` (integer, present only when the window holds more than 2,000
operations) gives how many were not reported. The report never holds values.

## State rules

- A run is **resumable** when `reconciliation_required` is true, it is not cancelled, and none of its executions is
  unfinished.
- A resume is **admitted** only when the run is resumable and its latest `verify` execution comes after its latest
  write execution and succeeded.
- A resume that ends `succeeded` clears `reconciliation_required` and sets the run's phase to `applied`. A resume
  that ends interrupted keeps the run resumable and makes a new `verify` necessary.
