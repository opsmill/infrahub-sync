# Contract: Sync HTTP API changes

All changes are additive under `v3-unstable`.

## `POST /runs/{run_id}/resume` (new)

Headers: `Authorization`, `Idempotency-Key` (required, as for `apply`).

Body:

```json
{"expected_checksum": "sha256:…", "confirm_writes": true, "confirm_left_out": false, "reason": null}
```

Responses:

| Status | Code | When |
| --- | --- | --- |
| 202 | — | Resume accepted; the response is the run, with a new execution of purpose `resume`. |
| 409 | `confirmation-required` | `confirm_writes` is not true. |
| 409 | `checksum-conflict` | The checksum does not match the saved plan. |
| 409 | `left-out-confirmation-required` | The plan left out records and `confirm_left_out` is not true. |
| 409 | `approval-required` / `approval-checksum-mismatch` | The site requires approval and the run has none for this checksum. The `approval-required` message names `infrahub-sync approve RUN_ID --expected-checksum CHECKSUM`. |
| 409 | `run-not-resumable` | The run is not interrupted, is cancelled, saved no plan, or has an unfinished execution. The message says which. A run that saved no plan is a `sync` whose worker was killed while it was still planning; its message says that no plan was saved and that a new `plan` or `sync` run is the next step (research R14). |
| 409 | `verify-required` | No `verify` passed on the run after its latest interruption. |
| 409 | `run-execution-conflict` | Another resume of the run is in progress. |
| 403 | `forbidden` | The actor did not start the run and is not an administrator. |

Every request, accepted or refused, is recorded in the run's audit history and event list with operation `resume`.

## `POST /runs/{run_id}/approve` (extended)

Spec 019's approve request also accepts a resumable run: `reconciliation_required` is true, the run is not
cancelled, its plan is saved, and no execution is unfinished (spec FR-019, research R12). The request, the checks and the responses
are unchanged. The `run-not-approvable` message names the run's phase and says that only a run in phase `planned`,
or a run whose write stopped part-way and can be resumed, can be approved.

## `GET /runs/{run_id}` (extended)

New fields:

```json
{
  "apply_progress": {
    "position": 5999,
    "saved_at": "2026-11-12T10:15:00Z",
    "resumable": true,
    "verify_required": false,
    "by_kind": [{"kind": "DcimDevice", "committed": 1200, "uncertain": 0, "not_reached": 0}]
  },
  "resumes": []
}
```

After a resume that succeeded, `resumable` is false, every operation is `committed`, and `resumes` holds:

```json
[
  {"actor": "ops", "requested_at": "…", "reason": null, "start_after": 5999, "outcome": "succeeded",
   "operations_written": 4000}
]
```

Positions count from 0 and include recorded deletes: position 5999 is the 6,000th operation of the plan.

For a `sync` whose worker was killed while it was still planning, `apply_progress` has `position: null`,
`resumable: false`, `verify_required: false` and an empty `by_kind`: the run saved no plan. A `verify` of that run
is refused with `409 run-execution-conflict`, as for any run whose write started and that cannot be resumed, and the
refusal is recorded in the audit history (research R14).

The Sync API reads the operations' kinds and actions from the saved plan once per run and plan, and keeps them in
memory, so a client that polls the run, for example with `--wait`, does not make the API read the whole plan on every
request (research R14). When the plan cannot be read, `by_kind` is empty and the API logs a warning that names the
run, the error class and the error code.

`by_kind` counts are over plan positions, so a recorded delete is `committed` at or before `position`,
`uncertain` inside the uncertain window, and `not_reached` after it. The uncertain window starts after
`position` and runs (research R11):

- through the failed operation, when the failure record of the latest write execution names it. Every failure
  record carries the `flow_run_id` of the execution that wrote it; a record without it, or naming another
  execution, is not used;
- through the 500th create or update, with the recorded deletes between them, when the last save is known to have
  succeeded: the execution still runs (counted only while a resume runs, because the window is computed only when
  `reconciliation_required` is true), or its own failure record is not `ApplyProgressLostError` and its last
  applied operation is the one the marker names;
- through the end of the plan in every other case, including a killed worker.

`resumes[].operations_written` counts the creates and updates that resume wrote after its `start_after`: through
the end of the plan for a resume that succeeded, through its own last marker otherwise. It is `null` when the saved
plan cannot be read (research R6).

The run's `summary` after a resume that finishes holds the whole plan's counts, as after an apply that was never
interrupted. A stopped write's failure record (`results.<purpose>_failure`) carries `operations_written`, the creates
and updates of the whole run, beside `applied_operations`, the list of the execution that wrote the record.

`apply_progress` is null for a run that never started a write.

## `POST /runs/{run_id}/verify` (extended result)

For a resumable run, `results.verification.operations` lists `{operation_id, kind, action, state}` for each
create and update of the uncertain window (recorded deletes are never listed), with `state` one of `matches`,
`differs`, `missing`, and `results.verification.operations_truncated` when more than 2,000 operations were not
reported. A create whose key more than one destination node holds is `differs` (research R13). When the read of
those nodes fails on a system or a credential, the record's `next_action` ends with running `verify` again, then
`runs resume`.
