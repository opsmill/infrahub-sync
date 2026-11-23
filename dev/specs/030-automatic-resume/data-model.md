# Data Model: Automatic resume of an interrupted apply

## New table `automatic_resumes`

One row for each interrupted write execution that the liveness reconciler considered (research R1, R3). The
interruption transaction inserts it.

| Column | Type | Meaning |
| --- | --- | --- |
| `run_id` | text, primary key part | The run. |
| `interrupted_flow_run_id` | text, primary key part | The write execution (apply, `sync` or resume) that the reconciler ended. |
| `cause` | `rule-6` or `rule-8` | Why the reconciler ended it. |
| `worker_name` | text, nullable | The claiming worker's name, when the pool snapshot had it (rule 8). |
| `state` | `waiting-check`, `checking`, `waiting-resume`, `resuming`, `ended` | Where the row is (research R3). |
| `decision` | `resume`, `no-resume`, nullable | Set when the row reaches `resuming` (`resume`) or ends without a resume (`no-resume`). |
| `reason` | text, nullable | For `no-resume`, one of the reason codes below. |
| `reason_detail` | JSON, nullable | The failed check's name, the number of `differs`, or the failure cause. No values from the destination or the source. |
| `operations_not_checked` | integer, nullable | Operations the check left out because of the 2,000-operation limit (FR-007). |
| `start_after` | integer, nullable | The progress marker the automatic resume starts after. |
| `check_flow_run_id` | text, nullable | The `verify` execution of the check. |
| `resume_flow_run_id` | text, nullable | The resume execution. |
| `decided_at` | timestamp | When the row was inserted. |
| `updated_at` | timestamp | The last move. |

**Reason codes for `no-resume`** (FR-016):

| Code | Meaning |
| --- | --- |
| `not-resumable` | `runs resume` would refuse the run: no saved plan, cancelled, or a scheduled run with branch review. |
| `approval-missing` | The site requires approval and the run's plan has none. |
| `no-progress` | The previous automatic resume of the run did not move the progress marker forward. |
| `limit-reached` | The run already received 3 automatic resumes. |
| `check-failed` | The check failed a pre-write check, could not reach the destination, or its execution failed. `reason_detail` names the cause. |
| `check-found-differs` | The check reported at least one `differs`. `reason_detail` holds the count. |
| `not-picked-up` | The submitted check or resume was abandoned because no worker claimed it in time. |
| `refused` | `verify_run` or `resume_run` refused the request. `reason_detail` holds the refusal code. |
| `person-took-over` | A person ran `verify` or `runs resume` while the row waited for a live worker (FR-019). |

### State transitions

```text
                    ┌──────────────────────────────── ended (no-resume, reason) ◄─┐
insert ─► waiting-check ─► checking ─► waiting-resume ─► resuming ─► ended (resume) │
             │  (live worker,      │ (check passed,      │ (live worker,           │
             │   verify submitted) │  no differs)        │  resume submitted)      │
             └─────────────────────┴─────────────────────┴─────────────────────────┘
```

- **Insert**: in the interruption transaction. While `INFRAHUB_SYNC_AUTOMATIC_RESUME` is off, no row is inserted (FR-004). When `not-resumable`, `approval-missing`, `no-progress`
  or `limit-reached` applies, the row is inserted directly as `ended`, `no-resume`.
- **`waiting-check` → `checking`**: in a loop of the reconciler whose pool snapshot has a live worker. The submission
  uses the idempotency key `automatic-check:<run_id>:<interrupted_flow_run_id>` (research R4).
- **`checking` → `waiting-resume` or `ended`**: when the check's execution has a verdict (research R7).
- **`waiting-resume` → `resuming`**: in a loop with a live worker; idempotency key
  `automatic-resume:<run_id>:<interrupted_flow_run_id>`. `decision` becomes `resume`.
- **`resuming` → `ended`**: when the resume execution has a verdict. If the verdict is `interrupted` under rule 6 or
  rule 8, the interruption transaction inserts the next row.
- Every move is a compare-and-set on `(run_id, interrupted_flow_run_id, state)`, so only one Sync API process moves a
  row (FR-014).

## Changed records

- **Mutation receipts and executions**: unchanged in shape. The automatic check and resume have the actor
  `sync:automatic-resume` (research R5).
- **Run resource** (`RunResource`, Sync API): gains `automatic_resumes`, the list of the run's rows, oldest first,
  and `automatic_resume_count`, the number of rows whose decision is `resume`.
- **Schedules**: no column change. `_settle_scheduled_run_end` is called later for a run with a row that is not
  `ended` (research R11).

## Constants

| Name | Value | Where |
| --- | --- | --- |
| `AUTOMATIC_RESUME_ACTOR` | `sync:automatic-resume` | `infrahub_sync/service/liveness.py` |
| `AUTOMATIC_RESUME_LIMIT` | 3 | `infrahub_sync/service/liveness.py` |
| `AUTOMATIC_RESUME_ENV` | `INFRAHUB_SYNC_AUTOMATIC_RESUME` | `infrahub_sync/service/settings.py` |
| `AUTOMATIC_RESUME_DISPLAY_NAME` | `Sync (automatic resume)` | `infrahub_sync/service/presentation.py` |
