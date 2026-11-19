# Data model: Manage schedules from the web interface

This feature stores nothing. Every value comes from the Sync API. This document describes the
resources the pages read, the values the pages derive from them, and the requests the forms
build. Field definitions are in the
[spec 021 HTTP contract](../archive/021-scheduled-runs/contracts/http-api.md) and in
`ScheduleResource`, `CreateScheduleRequest`, `ChangeScheduleRequest` and `RunResource` in
[`frontend/src/api/types.generated.ts`](../../../frontend/src/api/types.generated.ts).

## Resources read

### Schedule (`ScheduleResource`)

| Field | Shown as | Notes |
| --- | --- | --- |
| `schedule_id` | Not shown as text | Used in the address of the schedule page and in links. |
| `name` | Title of the schedule page, first column of the list | |
| `config_id`, `registry_version` | Configuration name and "version N", linked to the configuration page | The configuration name comes from the configurations list that the interface already reads. |
| `newer_version` | Notice "Version N of this configuration is registered" with an action to change the version | Shown when not null. |
| `cron`, `timezone` | The cron expression and the time zone name | |
| `write_target` | "Branch review" or "Default branch" | Any other value is shown as the API returns it. |
| `write_when_left_out`, `left_out_set_by`, `left_out_set_at` | "Write when records are left out: on, set by <account> on <time>" | `left_out_set_by` is an account name (FR-007). |
| `overlap_policy` | "When a run is in progress: skip the start" (`reject`) or "wait for it" (`queue`) | |
| `start_late_by_at_most_seconds` | "Start late by at most <duration>" | Null is shown as "not set". |
| `state`, `state_reason` | State badge; for `stopped`, the stop panel (see below) | |
| `owner` | Account name | Compared with the signed-in account's name (see "Permissions"). |
| `next_start_at`, `waiting_start_at` | Time in the schedule's time zone, the viewer's local time when it differs, and a relative time | `next_start_at` is null for a paused, stopped or deleted schedule. |
| `last_run_id` | Link to the run page, with the run's state badge | The run is read with the existing run query. |
| `prefect_synced` | Warning when false (FR-006) | |
| `consecutive_failures` | "Failed runs in a row: N" | |
| `open_branch` | Branch name, link to the run that created it, proposed change identifier | No link into Infrahub (out of scope). |
| `created_at`, `updated_at` | Local times | |

### Schedule page (`SchedulePage`)

`items` and `next_cursor`, read page by page as the runs list does. Query parameters: `state`,
`config_id`, `limit`, `cursor`.

### Run (`RunResource`, fields this feature reads)

| Field | Use |
| --- | --- |
| `trigger_type` | `scheduled` marks the run in the runs list and on the run page. |
| `schedule_id` | Link from the run to its schedule; runs-list filter. |
| `scheduled_start_at` | "Scheduled for <time>" on the run page. |

### Signed-in account (`GET /me`)

| Field | Use |
| --- | --- |
| `actor` | Account name, compared with `owner`. |
| `administrator` | Decides "Take over" and allows every other action. |
| `approval_required` | Makes the default-branch write target unavailable. |

## Derived values

### State and stop reason

| `state` | `state_reason` | Badge | Panel text | Action shown in the panel |
| --- | --- | --- | --- | --- |
| `active` | null | Active | None | None |
| `paused` | null | Paused | "This schedule starts no run until it is resumed." | Resume |
| `stopped` | `owner-not-found` | Stopped | "The account that owns this schedule is disabled or deleted." | "An administrator takes the schedule over, or enables the owner's account and resumes the schedule. A deleted account cannot be enabled again." Take over (administrators). |
| `stopped` | `approval-required` | Stopped | "This schedule writes to the default branch, and this Sync API requires an approved plan before every write to the default branch." | "Change the write target to branch review. The schedule is then active again." Change. |
| `stopped` | `repeated-failures` | Stopped | "N runs in a row failed, were interrupted or ended with an unknown outcome." | "Check the last runs of the schedule, then resume it." Links to the runs; Resume. |
| `stopped` | any other value | Stopped | The value as the API returns it | None |
| `deleted` | any | Deleted | "This schedule was deleted. Its runs are kept." | None |
| any other value | any | The value as the API returns it | None | None |

The panel texts follow `_STOPPED_ACTIONS` in `infrahub_sync/cli.py` and the table "A stopped
schedule" in `docs/docs/orchestration.mdx`.

### Permissions

Let `me` be the answer of `GET /me` and `s` the schedule.

| Action | Available when | Text when unavailable |
| --- | --- | --- |
| Create | Signed in | (never unavailable) |
| Change, Pause, Resume, Delete | `s.state != "deleted"` and (`me.administrator` or `me.actor == s.owner`) | "Only the owner of this schedule or an administrator can do this." |
| Pause | also `s.state == "active"` | Not shown otherwise |
| Resume | also `s.state` is `paused` or `stopped` | Not shown otherwise |
| Take over | `s.state != "deleted"` and `me.administrator` and `me.actor != s.owner` | Not shown to an account that is not an administrator |

`me.actor == s.owner` compares account names. A deleted owner is named
`Deleted account <short identifier>`, which no signed-in account has.

### Form values

#### Create (`CreateScheduleRequest`)

| Field | Input | Filled in with | Rule in the form |
| --- | --- | --- | --- |
| `name` | Text | Empty | Required. |
| `config_id` | Configuration choice | The configuration of the page the form was opened from | Required. |
| `registry_version` | Version choice | The configuration's latest version | Required. |
| `cron` | Text | Empty | Required; the Sync API checks it. |
| `timezone` | Time zone choice | The browser's time zone, or `UTC` when the browser does not give one | |
| `write_target` | Choice | `branch-review` | `default-branch` unavailable when `me.approval_required`. |
| `write_when_left_out` | Switch | Off | Unavailable when `write_target` is `branch-review`. |
| `overlap_policy` | Choice | `reject` | |
| `start_late_by_at_most_seconds` | Duration | 3600 | |
| `reason` | Text | Empty | Optional; sent only when not empty. |

#### Change (`ChangeScheduleRequest`)

The same fields except `name` and `config_id`, which are shown and cannot be changed. When the
form opens, it keeps a copy of the schedule's values (the opening values) and fills the fields
with them. The request holds only the fields whose value differs from the opening values, plus
`reason` when not empty. The request is never computed against a schedule read after the form
opened: a field that another person changed meanwhile, and that this person did not touch, would
otherwise be sent back with the old value. When a later read of the schedule differs from the
opening values, the form shows "This schedule changed since you opened this form" with a reload. When only `reason` or
nothing would be sent, the form states "Nothing changed" and sends no request (FR-018).

### Refusals mapped to a field

| Code | Field |
| --- | --- |
| `schedule-cron-invalid` | Cron expression |
| `schedule-timezone-invalid` | Time zone |
| `schedule-name-in-use` | Name |
| `schedule-exists-for-configuration` | Configuration; the message names the existing schedule, and the form links to the schedule list filtered by the configuration |
| `approval-required-for-schedule`, `schedule-branch-declared` | Write target |
| `schedule-left-out-not-applicable` | Write when records are left out |
| `configuration-version-not-found` | Version |

Every other refusal is shown above the form's actions with its title and detail, as the other
forms of the interface show refusals.
