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
| 409 | `run-not-resumable` | The run is not interrupted, is cancelled, or has an unfinished execution. The message says which. |
| 409 | `verify-required` | No `verify` passed on the run after its latest interruption. |
| 409 | `run-execution-conflict` | Another resume of the run is in progress. |
| 403 | `forbidden` | The actor did not start the run and is not an administrator. |

Every request, accepted or refused, is recorded in the run's audit history and event list with operation `resume`.

## `POST /runs/{run_id}/approve` (extended)

Spec 019's approve request also accepts a resumable run: `reconciliation_required` is true, the run is not
cancelled, and no execution is unfinished (spec FR-019, research R12). The request, the checks and the responses
are unchanged. The `run-not-approvable` message names the run's phase and says that only a run in phase `planned`,
or a run whose write stopped part-way and can be resumed, can be approved.

## `GET /runs/{run_id}` (extended)

New fields:

```json
{
  "apply_progress": {
    "position": 6000,
    "saved_at": "2026-11-12T10:15:00Z",
    "resumable": true,
    "verify_required": false,
    "by_kind": [{"kind": "DcimDevice", "committed": 1200, "uncertain": 0, "not_reached": 0}]
  },
  "resumes": [
    {"actor": "ops", "requested_at": "…", "reason": null, "start_after": 6000, "outcome": "succeeded",
     "operations_written": 3999}
  ]
}
```

`resumes[].operations_written` counts the creates and updates that resume wrote, from its own last marker; it is
`null` when the saved plan cannot be read (research R6).

The run's `summary` after a resume that finishes holds the whole plan's counts, as after an apply that was never
interrupted. A stopped write's failure record (`results.<purpose>_failure`) carries `operations_written`, the creates
and updates of the whole run, beside `applied_operations`, the list of the execution that wrote the record.

`apply_progress` is null for a run that never started a write.

## `POST /runs/{run_id}/verify` (extended result)

For a resumable run, `results.verification.operations` lists `{operation_id, kind, action, state}` for each
operation after the marker, with `state` one of `matches`, `differs`, `missing`, and
`results.verification.operations_truncated` when more than 2,000 operations were not reported.
