# Contract: Sync HTTP API additions

Paths are given without the `/api` prefix, as in `docs/docs/reference/sync-http-api.mdx`. Both
additions need a bearer token, store nothing and write no event. Errors use the existing error
body.

## `POST /schedules/next-starts` (FR-031)

Returns the next start times of a cron expression in a time zone, as the scheduler computes them.
It is a `POST` with a JSON body, not a `GET` with query values, because uvicorn's access log writes
the path and its query string (for example `"GET /api/configs/… HTTP/1.1" 200`). A credential
typed into the cron field by mistake would reach the log before the route refuses it, which spec
021 FR-025 forbids. Like `POST /configs/{config_id}/versions/{registry_version}/validate`, it
stores nothing and takes no `Idempotency-Key`.

| Body field | Required | Meaning |
| --- | --- | --- |
| `cron` | yes | A cron expression with five fields. |
| `timezone` | no, default `UTC` | A time zone name. |
| `count` | no, default `5` | The number of start times, from 1 to 20. |

Answer `200`:

```json
{
  "cron": "0 2 * * *",
  "timezone": "Europe/Paris",
  "starts": ["2026-11-20T01:00:00Z", "2026-11-21T01:00:00Z"],
  "searched_until": "2031-11-23T21:40:00Z"
}
```

- `starts` are in UTC, in increasing order, later than the time of the request. They are computed
  with `schedule_cadence.starts_between(cron, timezone, now, now + 1830 days, count)` (five years of 366 days,
  `NEXT_STARTS_SEARCH_WINDOW`; the example answers a request made at 2026-11-19T21:40:00Z), the module
  that computes a schedule's `next_start_at`.
- `starts` holds fewer than `count` times when the expression has fewer starts before
  `searched_until`, for example `0 0 29 2 *`. The web interface then states that there is no
  further start in the next 5 years.
- An expression that has no start at all, for example `0 0 30 2 *`, is refused with
  `schedule-cron-invalid`, as `POST /schedules` refuses it (`schedule_cadence.validate`).

Refusals:

| Status | Code | When |
| --- | --- | --- |
| 422 | `schedule-cron-invalid` | `schedule_cadence` raises `InvalidCadenceError` with the field `cron`. Same message as `POST /schedules`. |
| 422 | `schedule-timezone-invalid` | `schedule_cadence` raises `InvalidCadenceError` with the field `timezone`. Same message as `POST /schedules`. |
| 422 | `request-invalid` | `cron` is absent, or `count` is not an integer from 1 to 20 (the request model's validation). |

- No refusal of this route records an event. A refusal of `POST /schedules` records
  `infrahub_sync.schedule.refused`; this route is called while a person types, so it must not.
- The values are checked for a known credential like every other request field; a credential is
  refused with `secret-parameter-refused` and is not repeated in the message.

## `GET /events` (changed, FR-032)

| Query | Values | Meaning |
| --- | --- | --- |
| `order` | `asc` (default), `desc` | `asc`: oldest first, by `created_at` then `event_id`, as today. `desc`: newest first, by `created_at` then `event_id`, both descending. |

Every other parameter is unchanged. A value of `order` other than `asc` or `desc` is refused with
`422 request-invalid`. A request without `order` gets the same answer as before this change.

The web interface reads the history of a schedule with:

```text
GET /events?related=schedule:<schedule_id>&order=desc&limit=50&offset=0
GET /events?related=schedule:<schedule_id>&order=desc&until=<until>&limit=50&offset=<n>
```

The first page is read without `until`. Every older page sends `until` set to one millisecond after
the `occurred_at` of the first page's newest event (`occurred_at` is the stored `created_at`, and
the store keeps `created_at < until`). Every event of the first page is then before `until`, and
an event recorded later is after it, so events recorded later do not shift the offset of older
pages (FR-034). The time comes from the stored events, not from the browser's clock, which can
differ from the Sync API's clock. `occurred_at` is cut to milliseconds before the millisecond is
added, so `until` is still later than the event. The newest events are read again with a request
without `until`, and added above the list when their `event_id` is not shown yet. The list shows
each `event_id` once, also when two pages repeat an event; the offset counts every event the pages
returned. When the newest read has `has_more` and none of its events is shown, the pages are read
again, which moves `until` forward, so no event is left out between the newest read and the pages.

Without "Show all events", the request adds `event_type=<type>` for each schedule event type
of the spec 021 events contract except `infrahub_sync.schedule.replayed`. With it, the request has
no `event_type` filter.
