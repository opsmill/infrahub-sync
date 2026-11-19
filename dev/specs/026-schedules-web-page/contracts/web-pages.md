# Contract: Schedule pages of the web interface

The addresses, search parameters and Sync API calls of each page. The Sync API routes are those of
the [spec 021 HTTP contract](../../archive/021-scheduled-runs/contracts/http-api.md), and the two
additions of [http-api.md](http-api.md).

## Navigation

| Entry | Address | Shown to |
| --- | --- | --- |
| Schedules | `/schedules` | Every signed-in account. Placed after "Runs". |

## `/schedules` — schedules list

| Search parameter | Values | Effect |
| --- | --- | --- |
| `state` | `active`, `paused`, `stopped`, `deleted` | Only schedules in that state. Absent: every state except `deleted`. A value that is not one of these is dropped. |
| `config` | A configuration identifier | Only that configuration's schedules. A value that does not match the identifier pattern is dropped. |

| Sync API call | When |
| --- | --- |
| `GET /schedules?limit=50[&state=…][&config_id=…][&cursor=…]` | On load, every 10 seconds, and for "Load older schedules". |
| `GET /configs` | On load, for configuration names in the rows and in the configuration filter. |
| `GET /me` | On load, as every page. |

Columns: name, configuration and version, state, cadence (cron expression and time zone), next
start, owner. A row links to `/schedules/$scheduleId`. "Create schedule" opens the create dialog
with no configuration chosen.

Empty states:

- No schedule and no filter: "No schedule exists." with "Create schedule". When no configuration is
  registered: "Register a configuration first, then create its schedule."
- No schedule matches the filters: "No schedule matches these filters." with a way to clear them.

## `/schedules/$scheduleId` — schedule page

| Sync API call | When |
| --- | --- |
| `GET /schedules/{schedule_id}` | On load and every 10 seconds. `404 schedule-not-found` shows "Schedule not found" with a link to `/schedules`. |
| `GET /runs/{last_run_id}` | When `last_run_id` is set, for the state of the last run. |
| `GET /configs/{config_id}/versions` | When the change dialog opens, for the version choice. |
| `POST /schedules/{schedule_id}/pause` | Pause dialog. |
| `POST /schedules/{schedule_id}/resume` | Resume dialog. |
| `POST /schedules/{schedule_id}/change` | Change dialog. Sends only the fields that changed. |
| `POST /schedules/{schedule_id}/delete` | Delete dialog, after confirmation. |
| `POST /schedules/{schedule_id}/take-over` | Take-over dialog, after confirmation. Administrators only. |
| `GET /schedules/next-starts?cron=…&timezone=…&count=5` | For an active schedule, its next five starts. |
| `GET /events?related=schedule:<schedule_id>&order=desc&…` | The "History" section: first page on load and every 10 seconds (without `until`), older pages on demand (with `until`). See http-api.md. |

Every `POST` sends `Idempotency-Key`. The key is kept while the dialog is open and its fields are
unchanged, so a second press after a lost answer resends the same key. Every `POST` body may hold
`reason`, sent only when not empty.

Sections, in order:

1. Header: name, state badge, configuration and version, owner; the actions the signed-in account
   may take (data-model "Permissions").
2. Notices: the stop panel for a stopped schedule; "starts no run until it is handed to the task
   manager" when `prefect_synced` is false; the newer version notice.
3. Next start and last run: next start (local time and relative time), waiting start when set,
   last run with its state, failed runs in a row, "View all runs of this schedule" linking to
   `/runs?schedule=<schedule_id>`.
4. Settings: cron expression, time zone, write target, "write when records are left out" with who
   set it and when, overlap policy, late start.
5. Next starts: the next five start times of an active schedule.
6. Review branch: the open branch, the run that created it, the proposed change identifier.
7. History: the schedule's events, newest first, with the switch "Include run events" and "Load
   older events".
8. Record: created and updated times.

## Create dialog

Opened from `/schedules` (no configuration chosen) or from a configuration page (that
configuration and its latest version chosen).

| Sync API call | When |
| --- | --- |
| `GET /configs` | When no configuration is chosen, for the configuration choice. |
| `GET /configs/{config_id}/versions` | When a configuration is chosen, for the version choice. |
| `GET /schedules/next-starts?cron=…&timezone=…&count=5` | 400 ms after the cron expression or the time zone last changed, when the expression has five fields. Only the answer to the latest request is shown. |
| `POST /schedules` | On save. A `201` answer opens `/schedules/$scheduleId` of the returned schedule. |
| `GET /schedules?config_id=…&limit=1` | After a `503 orchestration-unavailable` refusal, whose body holds no schedule: finds the stored schedule and opens its page, which shows the task-manager warning. |

The change dialog uses the same cadence helper and preview as the create dialog.

## Changes to existing pages

| Page | Change |
| --- | --- |
| `/runs` | New search parameter `schedule` (a schedule identifier). Sent as `schedule_id` to `GET /runs`. Shown as a removable filter "Schedule: <name>". A run with `trigger_type` `scheduled` has a "Scheduled" mark linking to its schedule. |
| `/runs/$runId` | For a scheduled run: "Started by schedule <name>, scheduled for <time>", linking to the schedule. Reads `GET /schedules/{schedule_id}` for the name. |
| `/configs/$configId` | A "Schedule" line: the schedule's name, state and next start with a link, or "No schedule" and "Create schedule". Reads `GET /schedules?config_id=…&limit=1`. |
