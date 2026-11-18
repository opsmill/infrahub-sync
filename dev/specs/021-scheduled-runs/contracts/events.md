# Contract: schedule events

Every event below names the schedule as a related resource of the kind `schedule`, and the
configuration and its version as related resources. `GET /events?related=schedule:<schedule_id>`
returns the history of a schedule. Every event of a scheduled run also names its schedule, so the
same request returns the events of the schedule's runs.

Facts of an event are in its `details` field, described in [data-model.md](../data-model.md).
`reason` holds only the text that a person gave with a request.

## Events of a request

Written by the Sync API in the transaction that saves the answer. The actor is the caller.

| Event type | When | `operation` |
| --- | --- | --- |
| `infrahub_sync.schedule.created` | `POST /schedules` is accepted. | `schedule-create` |
| `infrahub_sync.schedule.changed` | A change is accepted. | `schedule-change` |
| `infrahub_sync.schedule.paused` | A pause is accepted. | `schedule-pause` |
| `infrahub_sync.schedule.resumed` | A resume is accepted. | `schedule-resume` |
| `infrahub_sync.schedule.deleted` | A deletion is accepted. | `schedule-delete` |
| `infrahub_sync.schedule.taken_over` | A take-over is accepted (user story 3). | `schedule-take-over` |
| `infrahub_sync.schedule.refused` | A schedule request is refused. `outcome` holds the cause. | the operation of the request |
| `infrahub_sync.schedule.replayed` | A schedule request repeats an idempotency key. | the operation of the request |

## Events of a start

Written by the worker when it admits a scheduled flow run, in the transaction of that decision.
The actor is the schedule's owner. `operation` is `schedule-start`. `details.start_at` holds the start time.

| Event type | When | `run_id` | `outcome` |
| --- | --- | --- | --- |
| `infrahub_sync.schedule.start_skipped` | A run of the same configuration is in progress, a proposed change of the schedule is approved and not merged, a start already waits, or the schedule writes to the default branch while `INFRAHUB_SYNC_REQUIRE_APPROVAL` is on in the worker's environment. In the last case the `schedule.stopped` event of the same admission follows. | The run in progress, or the run that opened the approved proposed change; none for `skipped-approval-required`. | `skipped-run-in-progress`, `skipped-approved-change-open`, `skipped-start-waiting` or `skipped-approval-required` |
| `infrahub_sync.schedule.start_queued` | The start waits under the `queue` policy (user story 3). | The run in progress. | `queued` |
| `infrahub_sync.schedule.start_missed` | The start time passed while the worker or the task manager was not running. Recorded when the next flow run of the schedule is admitted. | The late run, if one started. | `missed-skipped` or `missed-started-late` |
| `infrahub_sync.schedule.start_failed` | The run could not be created, for example because the configuration version can no longer be run. `details.cause` holds the refusal code. | none | `failed` |
| `infrahub_sync.schedule.stopped` | The schedule moved to the state `stopped`. Written by the Sync API when it starts, by a resume of a paused or stopped schedule for which a reason to stop holds, by the worker when it admits a start of a schedule that writes to the default branch while approval is required (actor: the owner, `operation`: `schedule-start`), by a take-over that leaves the schedule stopped for `approval-required`, or, for `repeated-failures`, with the end of the fifth failed run. For a resume or a take-over, the actor is the caller and `operation` is the operation of the request (`schedule-resume` or `schedule-take-over`). `details.start_at` is absent. | The fifth failed run, for `repeated-failures`. | `owner-not-found`, `approval-required` or `repeated-failures` |

- A start that creates a run on time writes no schedule event. The run's own events
  (`infrahub_sync.run.accepted` and the stage result) name the schedule.
- When more than 500 starts of one schedule were missed, the oldest are recorded in one
  `start_missed` event whose `details` hold `starts`, `first_start_at` and `last_start_at`.

## Events of a proposed change

Written by the worker after the write is committed, in one transaction with the second part of
the stage result. `run_id` is the scheduled run. The actor is the schedule's owner. `operation`
is `sync`.

| Event type | When | `outcome` |
| --- | --- | --- |
| `infrahub_sync.schedule.proposed_change_opened` | The run opened a proposed change. | `opened` |
| `infrahub_sync.schedule.proposed_change_replaced` | The run closed an earlier proposed change of the schedule, or deleted an earlier branch that had none. One event for each. | `replaced` |
| `infrahub_sync.schedule.proposed_change_kept` | An earlier proposed change was left in place: a person merged, closed or deleted it, or approved it while the run was writing. Written by the run, or by the admission of a start that found it. | `kept-merged`, `kept-closed`, `kept-missing` or `kept-approved` |
| `infrahub_sync.schedule.proposed_change_failed` | Opening the proposed change, or replacing an earlier one, failed. `details.cause` holds the failure class. | `failed` |

## Result event of a scheduled run

The existing event `infrahub_sync.run.synced` or `infrahub_sync.run.failed`. For a scheduled run
its `details.left_out` holds the plan's count of records left out (FR-005).

## What no event holds

No credential, no source data, and no text from Infrahub other than identifiers and branch names
(FR-025).
