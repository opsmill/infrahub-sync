# Contract: Sync HTTP API changes

Every route requires `Authorization: Bearer <token>` and refuses a missing or invalid token
with `401 unauthenticated`, recorded by the existing authentication dependency. Errors use the
existing envelope.

## `GET /runs` (new)

Query parameters:

| Name | Type | Default | Rule |
| --- | --- | --- | --- |
| `config_id` | identifier | none | Filter. Malformed (not matching the configuration ID pattern) → `422 request-invalid`. Unknown → `404 configuration-not-found`. |
| `registry_version` | integer ≥ 1 | none | Requires `config_id` (else `422 request-invalid`). Unknown → `404 configuration-version-not-found`. |
| `limit` | integer 1–200 | 50 | Out of range → `422`. |
| `cursor` | string | none | From a previous `next_cursor` with the same filters, else `422 invalid-cursor`. |

Response `200`:

```json
{
  "items": [
    {
      "run_id": "20261104T0930-0a1b2c3d",
      "operation": "plan",
      "configuration_reference": "cfg-netbox@2",
      "config_id": "cfg-netbox",
      "registry_version": 2,
      "package_checksum": "<64 hex>",
      "destination_branch": "main",
      "actor": "automation@example.com",
      "started_at": "2026-11-04T09:30:12+00:00",
      "finished_at": "2026-11-04T09:31:40+00:00",
      "phase": "planned",
      "outcome": "planned",
      "reconciliation_required": false,
      "summary": {"sync_name": "netbox-inventory"},
      "results": {},
      "audit_links": [],
      "artifact_refs": [],
      "prefect_executions": []
    }
  ],
  "next_cursor": "eyJrIjpbIjIwMjYtMTEtMDRUMDk6MzA6MTIrMDA6MDAiLCIyMDI2MTEwNFQwOTMwLTBhMWIyYzNkIl0sImYiOiIuLi4ifQ"
}
```

- Order: `started_at` descending, then `run_id` descending.
- `artifact_refs` and `prefect_executions` are always empty in list items; read
  `GET /runs/{run_id}` for them.
- Without filters, legacy runs (no `config_id`) are included with null binding fields.
- `destination_branch` is the branch the run writes to; it is `null` for runs created before
  this change. `GET /runs/{run_id}` returns the same field.
- `next_cursor` is `null` on the last page. No total count.

## `GET /me` (new)

Response `200`: `{"actor": "automation@example.com", "administrator": false}`. Performs no
other action and records no audit event on success.

## `GET /configs` (changed)

| Name | Type | Default | Rule |
| --- | --- | --- | --- |
| `name` | string | none | Exact match on the configuration's current name. |
| `limit` | integer 1–200 | 50 | |
| `cursor` | string | none | Replaces `offset`. |

Response `200`: `{"items": [ConfigurationSummary], "next_cursor": ...}`, ordered by
`created_at` descending, then `config_id` descending. Each item:
`{"config_id", "name", "latest_version", "created_at"}`.

**Breaking**: the response was a bare array and `offset` is removed.

## `GET /configs/{config_id}` (changed)

Adds `name` and `latest_version` to the response.

## `GET /configs/{config_id}/versions` (changed)

`limit` and `cursor` as above. Response `200`:
`{"items": [ConfigurationVersionSummary], "next_cursor": ...}`, ordered by
`registry_version` descending. Items contain no `declared_content`; see the data model.

**Breaking**: the response was a bare array of full versions, and `offset` is removed.
`GET /configs/{config_id}/versions/{registry_version}` still returns the full version.

## Registration routes (changed behavior only)

`POST /configs` and `POST /configs/{config_id}/versions` record the request's actor and
reason on the created version and update the configuration's `name`. Request and response
shapes are unchanged.

## Unchanged

`POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/plan|results|artifacts[/{id}]`,
`POST /runs/{id}/verify|apply|cancel`, `GET /status`, `GET /version`.
