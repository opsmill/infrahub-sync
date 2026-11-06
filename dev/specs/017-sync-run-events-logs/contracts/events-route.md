# Contract: `GET /events`

Authentication: a valid bearer token, as for `GET /runs` (FR-008). A missing or invalid token gets HTTP 401 and
creates no event (FR-009).

## Query parameters

| Name | Type | Meaning |
| --- | --- | --- |
| `run_id` | string | Events whose primary resource is this run. |
| `config_id` | string | Events whose primary resource is this configuration, or whose related resources include it. |
| `event_type` | string, repeatable | Events of these types. |
| `related` | string `<kind>:<id>`, repeatable | Events with this related resource, for example `account:alice` or `branch:main`. |
| `since` | datetime with time zone | Events at or after this time. |
| `until` | datetime with time zone | Events before this time. |
| `limit` | integer | Page size; the same default and maximum as `GET /runs`. |
| `offset` | integer, default 0 | Number of matching events to skip. |

A value that fails validation gets HTTP 422 with the existing error body.

## Response 200

```json
{
  "items": [
    {
      "event_id": "a-3f0c...",
      "event_type": "infrahub_sync.run.planned",
      "occurred_at": "2026-11-06T10:15:02Z",
      "actor": "alice",
      "operation": "plan",
      "outcome": "succeeded",
      "reason": null,
      "primary_resource": {"kind": "run", "id": "r-..."},
      "related": [
        {"kind": "account", "id": "alice"},
        {"kind": "config", "id": "c-..."},
        {"kind": "config_version", "id": "c-...@3"},
        {"kind": "branch", "id": "main"}
      ]
    }
  ],
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```

Order: `occurred_at`, then `event_id`, oldest first.
