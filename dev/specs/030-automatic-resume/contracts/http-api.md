# Contract: Sync API changes for automatic resume

The API stays `v3-unstable`. Every change is additive.

## `GET /runs/{run_id}`

The run resource gains two fields.

```json
{
  "automatic_resume_count": 1,
  "automatic_resumes": [
    {
      "interrupted_flow_run_id": "6f1c…",
      "actor": "Sync (automatic resume)",
      "cause": "rule-8",
      "cause_text": "the worker infrahub-sync-worker-3b2f… stopped sending heartbeats",
      "state": "ended",
      "decision": "resume",
      "reason": null,
      "reason_text": null,
      "operations_not_checked": 0,
      "start_after": 6000,
      "check_flow_run_id": "a81d…",
      "resume_flow_run_id": "c02e…",
      "decided_at": "2026-11-23T21:04:11Z",
      "updated_at": "2026-11-23T21:06:40Z"
    }
  ]
}
```

- `automatic_resumes` is empty for a run that no reconciler ever considered, including every run interrupted while
  the setting was off (FR-004).
- `cause_text` and `reason_text` are fixed English sentences built from the codes of
  [data-model.md](../data-model.md). For rule 6: "Prefect reported the flow run as crashed". For rule 8: "the worker
  `<name>` stopped sending heartbeats", or "the claiming worker stopped sending heartbeats" when the name is not known.
- `reason_text` for `check-found-differs` names the count, for example "the check found 1 node that differs from the
  plan". For `check-failed` it names the failed check or the cause.
- The `actor` of the resume entry in `resumes` (spec 016) is `sync:automatic-resume`; the answer shows it as
  `Sync (automatic resume)`, through the account name middleware.
- Each row of `automatic_resumes` carries the same `actor`, shown as `Sync (automatic resume)`, so the web interface
  shows the name the Sync API returns. Changed during implementation (review F8).

## `GET /runs`

The run summaries gain `automatic_resume_count`.

## Run events

Two new event types, written with the actor `sync:automatic-resume` and the operation `automatic-resume`:

| Type | When | Details |
| --- | --- | --- |
| `infrahub_sync.run.automatic_resume_started` | A row moves to `resuming`. | `cause`, `start_after`, `operations_not_checked`, `resume_flow_run_id`. |
| `infrahub_sync.run.automatic_resume_not_started` | A row ends `no-resume`. | `cause`, `reason`, `reason_detail`. |

The check's `verify` and the resume record the existing `run.verified`, `run.resumed` and `run.failed` events, with
the actor `sync:automatic-resume`.

## Refusals

No new refusal. A manual `POST /runs/{run_id}/resume` while a row is in `checking` or `resuming` gets the existing
`run-execution-conflict`. While a row is in `waiting-check` or `waiting-resume`, a manual resume or `verify` that the
service accepts ends the row as `no-resume`, `person-took-over`, and the manual request proceeds as today: a person who
acts takes over. A manual request that is refused, such as a resume refused with `verify-required`, leaves the row
waiting.

## Settings

`INFRAHUB_SYNC_AUTOMATIC_RESUME`: `true` or `false`, default `false`, with the same accepted values as
`INFRAHUB_SYNC_REQUIRE_APPROVAL`. An unknown value stops the Sync API at start-up with a message that names the
variable.
