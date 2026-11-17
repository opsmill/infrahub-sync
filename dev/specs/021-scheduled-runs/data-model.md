# Data model: Scheduled runs that write without a person present

Research references (R1 to R14) are to [research.md](research.md).

## Schedule (new, stored)

Table `schedules` of the product store. One row for each schedule, kept after deletion.

| Field | Type | Rule |
| --- | --- | --- |
| `schedule_id` | text, primary key | Generated: `s-` and 32 hexadecimal characters. |
| `name` | text | Identifier pattern of the API. Unique among schedules that are not deleted. |
| `config_id` | text | A registered configuration. |
| `registry_version` | integer | A registered version of that configuration. |
| `cron` | text | A five-field cron expression that `cronsim` accepts (R3). |
| `timezone` | text | A name that `zoneinfo` accepts. Default `UTC`. |
| `write_target` | text | `branch-review` (default) or `default-branch`. |
| `write_when_left_out` | boolean | Default false. |
| `left_out_set_by` | text or null | The actor who last turned `write_when_left_out` on. |
| `left_out_set_at` | timestamp or null | When they did. |
| `overlap_policy` | text | `reject` (default) or `queue`. `queue` is accepted from user story 3. |
| `start_late_by_at_most_seconds` | integer | Default 3600, from 60 to 604800. Used from user story 3. |
| `state` | text | `active`, `paused`, `stopped` or `deleted`. |
| `state_reason` | text or null | For `stopped`: `owner-token-invalid` or `approval-required`. |
| `owner` | text | The actor name of the owner (R8). No token is stored. |
| `next_start_at` | timestamp or null | In UTC. Null when the state is not `active`. |
| `waiting_start_at` | timestamp or null | The start that waits under the `queue` policy. |
| `created_at`, `updated_at` | timestamp | In UTC. |

Rules:

- `write_target = default-branch` is refused while the Sync API requires approval (R6).
- `left_out_set_by` and `left_out_set_at` are set when `write_when_left_out` changes from false to
  true, and cleared when it changes to false.
- `next_start_at` is computed from `cron` and `timezone` when the schedule is created, changed,
  resumed or taken over, and after each start. It is always later than the time of the
  computation.
- A change may set `registry_version`, `cron`, `timezone`, `write_target`, `write_when_left_out`,
  `overlap_policy` and `start_late_by_at_most_seconds`. It may not set `name`, `config_id` or
  `owner`.

State transitions:

| From | Action or cause | To |
| --- | --- | --- |
| (none) | create | `active` |
| `active` | pause | `paused` |
| `paused` | resume | `active` |
| `active` | the owner has no valid token at a start | `stopped`, reason `owner-token-invalid` |
| `active` | the Sync API requires approval and the write target is the default branch, at a start | `stopped`, reason `approval-required` |
| `stopped` (`owner-token-invalid`) | take over by an administrator | `active`, with the administrator as owner |
| `stopped` (`approval-required`) | change of the write target to `branch-review` | `active` |
| `active`, `paused`, `stopped` | delete | `deleted` |

A `deleted` schedule accepts no action. A paused or stopped schedule starts no run and records no
missed start.

## Schedule branch (new, stored)

Table `schedule_branches`. One row for each branch that a schedule's run created.

| Field | Type | Rule |
| --- | --- | --- |
| `schedule_id` | text | The schedule. |
| `run_id` | text, primary key | The run that created the branch. |
| `branch` | text | The branch name (R11). |
| `proposed_change_id` | text or null | Null when the run failed before it opened one. |
| `state` | text | `open`, `replaced` or `kept`. |
| `replaced_by_run_id` | text or null | The run whose proposed change replaced this one. |
| `created_at` | timestamp | In UTC. |

Rules:

- A row is written when the worker reports that it created the branch.
- `open`: the schedule may replace it at its next successful run.
- `replaced`: the schedule closed the proposed change, if any, and deleted the branch.
- `kept`: a person approved, merged or closed the proposed change, or the branch no longer
  exists. The schedule never touches it again.
- A schedule has at most one row in the state `open` (SC-004).

## Run (changed)

Two columns on `product_runs`, and the same two fields on `ProductRun`, `RunResource` and
`PublicRunResource`.

| Field | Type | Rule |
| --- | --- | --- |
| `trigger_type` | text | `manual` or `scheduled`. A run of `POST /runs` holds `manual`. |
| `schedule_id` | text or null | Set when `trigger_type` is `scheduled`. |

- `actor` of a scheduled run is the schedule's owner at the start time.
- `destination_branch` of a run with branch review is the generated branch name.
- `GET /runs` filters on both fields. Both are stored columns, as ADR 0032 requires.
- The two columns reach only a new product database (R12).

## Event (changed)

- `event_type` accepts the segment `schedule` (R9).
- `RelatedResource.kind` and `PrimaryResource.kind` accept `schedule`.
- The primary resource of an event is the run when `run_id` is set, else the schedule when the
  event names one, else the configuration.
- No new column.

## Mutation receipt (changed)

- `resource_kind` accepts `schedule`, for the mutations of the schedule routes.
- A scheduled run's receipt has the resource kind `run`, the owner as actor, and the key digest
  of `schedule:<schedule_id>:<start time in UTC>` (R2).

## Stage result of a scheduled `sync` (changed, in the run's results)

The result of the `sync` stage gains one optional entry, written only for a run with branch
review:

| Field | Type | Meaning |
| --- | --- | --- |
| `branch_review.branch` | text or null | The branch the run created. Null when the plan had no operation. |
| `branch_review.proposed_change_id` | text or null | The proposed change the run opened. |
| `branch_review.replaced_run_id` | text or null | The run whose branch and proposed change this run replaced. |
| `branch_review.kept_run_id` | text or null | The earlier run whose proposed change was left in place, with the cause in `kept_cause`. |
| `branch_review.kept_cause` | text or null | `approved`, `merged`, `closed` or `missing`. |

The store reads this entry in `commit_claimed_execution` and updates `schedule_branches` in the
same transaction.

## Flow parameters of a scheduled run (changed, in memory only)

The service adds these to the parameters of `service_sync_run`:

| Parameter | Meaning |
| --- | --- |
| `confirm_left_out` | Exists today. True when the schedule's left-out setting is on (R5). |
| `branch_review` | True for the write target `branch-review`. |
| `replace_branch`, `replace_proposed_change_id` | The schedule's row in the state `open`, if any. |

The flow reads them only after it claimed its execution, so only a run that the service started
can carry them.
