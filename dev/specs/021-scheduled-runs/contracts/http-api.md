# Contract: Sync HTTP API

Paths are given without the `/api` prefix, as in `docs/docs/reference/sync-http-api.mdx`. Every
route below needs a bearer token. Every `POST` needs an `Idempotency-Key` header and follows the
existing rules for a repeated key. Errors use the existing error body.

## Schedule resource

```json
{
  "schedule_id": "s-3f2a…",
  "name": "from-netbox-nightly",
  "config_id": "from-netbox",
  "registry_version": 3,
  "newer_version": 4,
  "cron": "0 2 * * *",
  "timezone": "Europe/Paris",
  "write_target": "branch-review",
  "write_when_left_out": false,
  "left_out_set_by": null,
  "left_out_set_at": null,
  "overlap_policy": "reject",
  "start_late_by_at_most_seconds": 3600,
  "state": "active",
  "state_reason": null,
  "owner": "operator",
  "next_start_at": "2026-11-18T01:00:00Z",
  "waiting_start_at": null,
  "last_run_id": "…",
  "prefect_synced": true,
  "consecutive_failures": 0,
  "open_branch": {"run_id": "…", "branch": "sync/from-netbox-nightly-20261117-a1b2c3", "proposed_change_id": "…"},
  "created_at": "2026-11-17T09:12:00Z",
  "updated_at": "2026-11-17T09:12:00Z"
}
```

- `newer_version` is the highest registered version of the configuration when it is higher than
  `registry_version`, else null (user story 3).
- `newer_version`, `start_late_by_at_most_seconds` and `waiting_start_at` are absent before user
  story 3 is delivered.
- `state_reason` is `owner-not-found`, `approval-required` or `repeated-failures` for a stopped
  schedule, else null.
- `last_run_id` is the schedule's most recent run, else null. `GET /runs/{run_id}` holds its
  outcome.
- `open_branch` is the schedule's most recent branch in the state `open`, else null.
- All times are in UTC.

## `POST /schedules`

Creates a schedule. Answer `201` with the schedule resource.

| Field | Required | Default |
| --- | --- | --- |
| `name` | yes | |
| `config_id` | yes | |
| `registry_version` | yes | |
| `cron` | yes | |
| `timezone` | no | `UTC` |
| `write_target` | no | `branch-review` |
| `write_when_left_out` | no | `false` |
| `overlap_policy` | no | `reject` |
| `start_late_by_at_most_seconds` | no | `3600` |
| `reason` | no | |

Refusals:

| Status | Code | When |
| --- | --- | --- |
| 404 | `configuration-version-not-found` | The configuration or the version is not registered. |
| 409 | `schedule-name-in-use` | A schedule that is not deleted has that name. |
| 409 | `schedule-exists-for-configuration` | The configuration already has a schedule that is not deleted. The message names it. |
| 409 | `approval-required-for-schedule` | `write_target` is `default-branch` and the Sync API requires approval. The message names `INFRAHUB_SYNC_REQUIRE_APPROVAL`. |
| 409 | `schedule-branch-declared` | `write_target` is `branch-review` and the configuration version declares its own destination branch. |
| 422 | `schedule-left-out-not-applicable` | `write_when_left_out` is true and `write_target` is `branch-review`. A run with branch review always writes the remaining records. |
| 422 | `schedule-cron-invalid` | The cron expression is not valid, or does not have five fields. The message holds the reason. |
| 503 | `orchestration-unavailable` | The schedule is stored and the task manager could not be reached. The schedule starts no run until a later request, or a restart of the Sync API, hands it to the task manager. `GET /schedules/{schedule_id}` shows `prefect_synced: false`. |
| 422 | `schedule-timezone-invalid` | The time zone name is not known. |
| 422 | `secret-parameter-refused` | A request field holds a credential that the Sync API knows. The schedule is not stored. |
| 503 | `configuration-version-invalid` | The registered configuration version cannot be read. |
| 409 | `idempotency-conflict`, `idempotency-in-progress` | The existing rules for a repeated `Idempotency-Key`. |
| 422 | `schedule-setting-not-available` | `overlap_policy` is `queue` or `start_late_by_at_most_seconds` is set, before user story 3 is delivered. `write_target` is `default-branch`, before user story 2 is delivered. `write_target` is `branch-review`, or is absent, before the worker side of user story 1 is delivered. |

## `GET /schedules`

Lists schedules, newest first by `(created_at, schedule_id)`. Answer `200` with
`{"items": […], "next_cursor": …}`. Paging follows ADR 0032.

| Query | Meaning |
| --- | --- |
| `config_id` | Only schedules of that configuration. |
| `state` | Only schedules in that state. Without it, deleted schedules are left out. |
| `limit`, `cursor` | As for `GET /runs`. |

## `GET /schedules/{schedule_id}`

Answer `200` with the schedule resource, also for a deleted schedule. `404 schedule-not-found`
otherwise.

## `POST /schedules/{schedule_id}/change`

Changes one or more of `registry_version`, `cron`, `timezone`, `write_target`,
`write_when_left_out`, `overlap_policy` and `start_late_by_at_most_seconds`, with an optional
`reason`. A field that is absent keeps its value. Answer `200` with the schedule resource, which
holds the new `next_start_at`.

The refusals of `POST /schedules` apply, and:

| Status | Code | When |
| --- | --- | --- |
| 403 | `forbidden` | The caller is neither the owner nor an administrator. |
| 404 | `schedule-not-found` | |
| 409 | `schedule-deleted` | |
| 422 | `request-invalid` | The body names `name`, `config_id` or `owner`, or changes nothing. |
| 409 | `schedule-changed` | Another writer changed the schedule during each of three attempts to save the request. Send the request again. |

## `POST /schedules/{schedule_id}/pause` and `POST /schedules/{schedule_id}/resume`

Body: an optional `reason`. Answer `200` with the schedule resource. A pause of a paused schedule
and a resume of an active schedule answer `200` and change nothing.

| Status | Code | When |
| --- | --- | --- |
| 403 | `forbidden` | The caller is neither the owner nor an administrator. |
| 404 | `schedule-not-found` | |
| 409 | `schedule-deleted` | |
| 409 | `schedule-stopped` | A resume of a stopped schedule whose reason still holds. The message names the reason and the action it needs. |
| 409 | `schedule-changed` | As for a change. It also answers a deletion and a take-over. |

A resume of a stopped schedule whose reason no longer holds answers `200`, and the schedule is
active.

## `POST /schedules/{schedule_id}/delete`

Body: an optional `reason`. Answer `200` with the schedule resource in the state `deleted`. A
run in progress is not cancelled. The open proposed change and its branch stay in Infrahub, and
`open_branch` in the answer names them (FR-034). The same holds for a change of the write target
to `default-branch`. Refusals: `403
forbidden`, `404 schedule-not-found`. A second deletion answers `200`.

## `POST /schedules/{schedule_id}/take-over` (user story 3)

Body: an optional `reason`. The caller must be an administrator. The caller becomes the owner,
and a schedule stopped with the reason `owner-not-found` becomes active. Answer `200` with
the schedule resource. Refusals: `403 forbidden`, `404 schedule-not-found`, `409
schedule-deleted`.

## `GET /runs` and `GET /runs/{run_id}` (changed)

- The run resource gains `trigger_type` (`manual` or `scheduled`), `schedule_id` and
  `scheduled_start_at`.
- `GET /runs` gains the filters `trigger_type` and `schedule_id`.

## `POST /runs/{run_id}/verify`, `/apply` and `/resume` (changed)

Refused with `409 scheduled-run-not-appliable` for a scheduled run with branch review. The
message says that the schedule's next run replaces it.

## `GET /events` (changed)

- `event_types` accepts the types of [events.md](events.md).
- `related` accepts `schedule:<schedule_id>`.
- An event gains the optional field `details`.

## `POST /runs` (not changed)

Its runs record `trigger_type = "manual"`. It gets no new field.
