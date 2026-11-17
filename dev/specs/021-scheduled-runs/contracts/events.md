# Contract: schedule events

Every event below names the schedule as a related resource of the kind `schedule`, and the
configuration and its version as related resources. `GET /events?related=schedule:<schedule_id>`
returns the history of a schedule.

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

Written by the schedule loop in the transaction that advances the schedule. The actor is the
schedule's owner. `operation` is `schedule-start`. `reason` holds `start=<time in UTC>`.

| Event type | When | `run_id` | `outcome` |
| --- | --- | --- | --- |
| `infrahub_sync.schedule.start_skipped` | A run of the same configuration is in progress, or a start already waits. | The run in progress. | `skipped-run-in-progress` or `skipped-start-waiting` |
| `infrahub_sync.schedule.start_queued` | The start waits under the `queue` policy (user story 3). | The run in progress. | `queued` |
| `infrahub_sync.schedule.start_missed` | The start time passed while the Sync service was not running. | The late run, if one started. | `missed-skipped` or `missed-started-late` |
| `infrahub_sync.schedule.stopped` | The schedule moved to the state `stopped`. | none | `owner-token-invalid` or `approval-required` |

- A start that creates a run on time writes no schedule event. The run's own events
  (`infrahub_sync.run.accepted` and the stage result) name the schedule as a related resource.
- When more than 500 starts of one schedule were missed, the oldest are recorded in one
  `start_missed` event whose `reason` is `starts=<count> from=<time> to=<time>`.

## Events of a proposed change

Written by the worker with the stage result, in the transaction that saves it. `run_id` is the
scheduled run. The actor is the schedule's owner. `operation` is `sync`.

| Event type | When | `outcome` |
| --- | --- | --- |
| `infrahub_sync.schedule.proposed_change_opened` | The run opened a proposed change. | `opened` |
| `infrahub_sync.schedule.proposed_change_replaced` | The run closed the schedule's earlier proposed change and deleted its branch. | `replaced` |
| `infrahub_sync.schedule.proposed_change_kept` | The earlier proposed change was left in place. | `kept-approved`, `kept-merged`, `kept-closed` or `kept-missing` |

## Result event of a scheduled run

The existing event `infrahub_sync.run.synced` or `infrahub_sync.run.failed`. For a scheduled run
its `reason` holds `left_out=<count>` (FR-005).

## What no event holds

No credential, no source data, and no text from Infrahub other than identifiers and branch names
(FR-025).
