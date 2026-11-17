# Data model: Scheduled runs that write without a person present

Research references (R1 to R14) are to [research.md](research.md).

## Schedule (new, stored)

Table `schedules` of the product store. One row for each schedule, kept after deletion.

| Field | Type | Rule |
| --- | --- | --- |
| `schedule_id` | text, primary key | Generated: `s-` and 32 hexadecimal characters. |
| `name` | text | Identifier pattern of the API. Unique among schedules that are not deleted. |
| `consecutive_failures` | integer | The count of failed runs in a row (FR-032). Default 0. |
| `config_id` | text | A registered configuration. At most one schedule that is not deleted for each configuration. |
| `registry_version` | integer | A registered version of that configuration. |
| `cron` | text | A five-field cron expression that Prefect's `CronSchedule` accepts (R3). |
| `timezone` | text | A time zone name that Prefect's `CronSchedule` accepts. Default `UTC`. |
| `write_target` | text | `branch-review` (default) or `default-branch`. |
| `write_when_left_out` | boolean | Default false. |
| `left_out_set_by` | text or null | The actor who last turned `write_when_left_out` on. |
| `left_out_set_at` | timestamp or null | When they did. |
| `overlap_policy` | text | `reject` (default) or `queue`. `queue` is accepted from user story 3. |
| `start_late_by_at_most_seconds` | integer | Default 3600, from 60 to 604800. Used, and shown by the API, from user story 3. |
| `state` | text | `active`, `paused`, `stopped` or `deleted`. |
| `state_reason` | text or null | For `stopped`: `owner-not-found`, `approval-required` or `repeated-failures`. |
| `owner` | text | The actor name of the owner (R8). No token is stored. |
| `next_start_at` | timestamp or null | In UTC. Null when the state is not `active`. Computed, not stored (R3). |
| `last_start_at` | timestamp or null | The most recent start that was admitted or recorded (R10). |
| `prefect_schedule_id` | text or null | The Prefect schedule of this schedule (R1). |
| `prefect_synced` | boolean | False while the Prefect schedule does not match this record (R13). |
| `waiting_start_at` | timestamp or null | The start that waits under the `queue` policy. |
| `created_at`, `updated_at` | timestamp | In UTC. |

Rules:

- `write_target = default-branch` is refused while the Sync API requires approval (R6).
- `write_target = branch-review` is refused when the configuration version declares a destination
  branch (R11).
- `write_when_left_out = true` is refused for the write target `branch-review`: a run with branch
  review always writes the remaining records (R11). A change of the write target to
  `branch-review` sets it to false.
- `left_out_set_by` and `left_out_set_at` are set when `write_when_left_out` changes from false to
  true, and cleared when it changes to false.
- `next_start_at` is computed from `cron` and `timezone` each time the schedule is returned. It
  is always later than the time of the computation.
- The Prefect schedule is active only when the state is `active`.
- `consecutive_failures` is updated in the transaction that saves the end of a scheduled run: plus
  one for a run that ends as failed, interrupted or ambiguous; zero for a run that succeeds or ends
  with no change; unchanged for a run that a person cancelled or that stopped for a review of
  records left out. At 5, the same transaction stops the schedule and saves the event. The worker
  cannot reach Prefect's schedule from there: until the next match of Prefect to the store, the
  admission of a start answers `schedule-not-active` and creates no run.
- A change may set `registry_version`, `cron`, `timezone`, `write_target`, `write_when_left_out`,
  `overlap_policy` and `start_late_by_at_most_seconds`. It may not set `name`, `config_id` or
  `owner`.

State transitions:

| From | Action or cause | To |
| --- | --- | --- |
| (none) | create | `active` |
| `active` | pause | `paused` |
| `paused` | resume | `active` |
| `active` | the owner's actor does not exist on the Sync API, when the API process starts | `stopped`, reason `owner-not-found` |
| `active` | the Sync API requires approval and the write target is the default branch, when the API process starts | `stopped`, reason `approval-required` |
| `active` | the fifth consecutive failed run ends | `stopped`, reason `repeated-failures` |
| `stopped` | resume, when the reason no longer holds | `active` |
| `stopped` (`repeated-failures`) | resume, always | `active`, with `consecutive_failures` at 0 |
| `stopped` (`owner-not-found`) | take over by an administrator (user story 3) | `active`, with the administrator as owner |
| `stopped` (`approval-required`) | change of the write target to `branch-review` | `active` |
| `active`, `paused`, `stopped` | delete | `deleted` |

A `deleted` schedule accepts no action. A paused or stopped schedule starts no run and records no
missed start. A resume of a stopped schedule whose reason still holds is refused.

## Schedule branch (new, stored)

Table `schedule_branches`. One row for each run with branch review.

| Field | Type | Rule |
| --- | --- | --- |
| `schedule_id` | text | The schedule. |
| `run_id` | text, primary key | The run. |
| `branch` | text | The branch name, derived from the schedule and the start time (R11). |
| `proposed_change_id` | text or null | Set when the run opened one. |
| `state` | text | `pending`, `open`, `removed`, `replaced` or `kept`. |
| `replaced_by_run_id` | text or null | The run that replaced this one. |
| `created_at` | timestamp | In UTC. |

Rules:

- `pending`: written by the admission of a scheduled start, in the transaction that creates the
  run. The branch may or
  may not exist in Infrahub.
- `open`: the run finished its write and opened its proposed change.
- `removed`: the run itself deleted its branch, because the plan held no operation.
- `replaced`: a later run of the schedule closed the proposed change, if any, and deleted the
  branch.
- `kept`: a person merged or closed the proposed change. The schedule never touches the branch
  or the proposed change again.
- A row in the state `open` whose proposed change a person approved stays `open`. While it is
  approved and open, the schedule starts no run (FR-031).
- A later successful run handles every row of its schedule in the states `pending` and `open`
  other than its own. After it, the schedule has at most one row in the state `open` (SC-004).

## Run (changed)

Three columns on `product_runs`, and the same three fields on `ProductRun`, `RunResource` and
`PublicRunResource`.

| Field | Type | Rule |
| --- | --- | --- |
| `trigger_type` | text | `manual` or `scheduled`. A run of `POST /runs` holds `manual`. |
| `schedule_id` | text or null | Set when `trigger_type` is `scheduled`. |
| `scheduled_start_at` | timestamp or null | The start time `T` of the cadence, in UTC. Set when `trigger_type` is `scheduled`. |

- A unique index on `(schedule_id, scheduled_start_at)` allows one run for each start (R2).
- `started_at` is the time the run was created. For a late or queued run it is later than
  `scheduled_start_at`.
- `actor` of a scheduled run is the schedule's owner when the run is created.
- `destination_branch` of a run with branch review is the derived branch name.
- `GET /runs` filters on `trigger_type` and `schedule_id`. Both are stored columns, as ADR 0032
  requires, and `schedule_id` has an index.
- `verify`, `apply` and `resume` requests on a run with branch review are refused (R11).
- The columns reach only a new product database (R12).

## Event (changed)

- `event_type` accepts the segment `schedule` (R9).
- `RelatedResource.kind` and `PrimaryResource.kind` accept `schedule`.
- The primary resource of an event is the run when `run_id` is set, else the schedule when the
  event names one, else the configuration.
- A new optional field `details`, stored in a new nullable column of `audit_events`. Its keys are
  closed:

| Key | Type | Used by |
| --- | --- | --- |
| `start_at` | timestamp | Every event of a start. |
| `starts`, `first_start_at`, `last_start_at` | integer, timestamps | The one event that records more than 500 missed starts. |
| `left_out` | integer | The result event of a scheduled run. |
| `branch`, `proposed_change_id` | text | Events of a proposed change. |
| `replaced_run_id` | text | `proposed_change_replaced`. |
| `cause` | text | A refusal code or a failure class. Never free text from Infrahub. |

## Mutation receipt (changed)

- `resource_kind` accepts `schedule`, for the mutations of the schedule routes. The kinds are
  also named in a `CHECK` constraint, SQLite triggers and model invariants (R12).
- A scheduled run's receipt has the resource kind `run`, the owner as actor, the key digest of
  `schedule:<schedule_id>:<scheduled_start_at>`, and a request fingerprint computed from the
  schedule identifier and the start time only. The admission writes it already accepted (R2).

## Stage result of a scheduled `sync` (changed, in the run's results)

The result of the `sync` stage gains one optional entry, written only for a run with branch
review:

| Field | Type | Meaning |
| --- | --- | --- |
| `branch_review.branch` | text | The run's branch. |
| `branch_review.branch_state` | text | `written`, or `removed` when the run deleted its branch. |
| `branch_review.proposed_change_id` | text or null | The proposed change the run opened. |
| `branch_review.replaced` | list | For each earlier run that was replaced: its run identifier. |
| `branch_review.kept` | list | For each earlier run that was left in place: its run identifier and the cause, `approved` (approved while this run was writing), `merged` or `closed`. |
| `branch_review.failure` | text or null | The failure class when opening the proposed change or replacing an earlier one failed. The write verdict does not change. |

The result also holds `left_out`, the plan's count of records left out, for every scheduled run.

The store updates `schedule_branches` from this entry in the transaction that saves it. The part
that is known when the write is committed (`branch`, `branch_state`) is saved with the stage
result. The part that follows the write (`proposed_change_id`, `replaced`, `kept`, `failure`) is
saved by a second store call with its events.

## Flow parameters and admission result of a scheduled run (changed, in memory only)

The Prefect schedule gives the flow run two parameters:

| Parameter | Meaning |
| --- | --- |
| `schedule_id` | New. The Sync schedule. |
| `stage` | Exists today. Always `sync`. |

`run_id` becomes optional in the signature of `service_sync_run`: a flow run of the Sync API
carries it, and a scheduled flow run gets it from the admission.

The admission of R2 returns one of two results:

- **Not started**: the cause (`schedule-not-active`, `missed`, `skipped-run-in-progress`,
  `skipped-approved-change-open`, `skipped-start-waiting`, `queued`, `failed`). The flow run
  ends with no run record.
- **Started**: the run identifier, the configuration binding of the run, and the values the
  stage needs from the schedule:

| Value | Meaning |
| --- | --- |
| `confirm_left_out` | True when the schedule's left-out setting is on, and always true for branch review (R5, R11). |
| `branch_review` | True for the write target `branch-review`. |
| `earlier_branches` | The schedule's rows in the states `pending` and `open`: run identifier, branch, proposed change identifier. |
