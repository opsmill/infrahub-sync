# HTTP API contract: stored credentials

All routes are under `/api`, keep the API version `v3-unstable`, and use the existing error envelope
`{"error": {"code", "message", "status", …}}`. No response of these routes contains a value, except the
worker route.

## Credential resource

```json
{
  "name": "netbox-lab",
  "description": "NetBox lab, read-only token",
  "fields": ["token"],
  "revision": 3,
  "created_by": "alice",
  "created_at": "2026-11-20T10:00:00Z",
  "updated_by": "bob",
  "updated_at": "2026-11-21T09:00:00Z"
}
```

`created_by` and `updated_by` are account names, as the Sync API returns them elsewhere, including
`Deleted account <short identifier>`.

## Routes for accounts

| Method and path | Who | Request | Success | Refusals |
| --- | --- | --- | --- | --- |
| `GET /credentials` | every signed-in account | `?offset&limit` | `200 {"items": [Credential], "next_offset"}` sorted by name | `401` |
| `GET /credentials/{name}` | every signed-in account | | `200 Credential` | `401`, `404 credential-not-found` |
| `POST /credentials` | administrator | `{"name", "description"?, "username"?, "password"?, "token"?}` | `201 Credential` (revision 1) | `403 forbidden`, `409 credential-exists`, `422 credential-fields-invalid`, `422 request-invalid` |
| `PUT /credentials/{name}/value` | administrator | `{"username"?, "password"?, "token"?}` (the whole new set) | `200 Credential` (revision + 1) | `403`, `404`, `422 credential-fields-invalid` |
| `PATCH /credentials/{name}` | administrator | `{"description"}` | `200 Credential` (same revision) | `403`, `404`, `422` |
| `DELETE /credentials/{name}` | administrator | | `204` | `403`, `404`, `409 credential-in-use` with `details: {"configurations": [config_id], "schedules": [schedule name]}` |

- Mutating routes take `Idempotency-Key`, as the schedule routes do. The stored replay answer is the
  credential resource, which holds no value.
- The administrator check runs before the body is validated, as the account routes do
  (`account_routes.py:105-120`).
- A 422 answer never repeats the submitted body (`app.py:459-464`).
- Each mutating route writes an audit event (`credential.created`, `credential.value_changed`,
  `credential.updated`, `credential.deleted`) with the name and revision, never a value.
- The checks name the permission `manage-credentials`, held by administrators (FR-015).

## Worker route

`POST /worker/runs/{run_id}/credentials`

- Accepts only the `worker` account. Every other account, including an administrator, gets
  `403 forbidden` (FR-005).
- Request: `{"flow_run_id": "<uuid>"}`.
- Success `200`:

```json
{
  "run_id": "run_…",
  "blocks": {
    "destination": {"name": "infrahub-netbox-sync", "revision": 4, "fields": {"token": "…"}},
    "source": {"name": "netbox-lab", "revision": 3, "fields": {"token": "…"}}
  }
}
```

- Only the blocks the stage needs (research R5). A block that names no credential is absent.
- Refusals, each with no value:
    - `404 run-not-found`
    - `409 execution-not-in-progress`: no claimed, non-terminal execution `(run_id, flow_run_id)`
    - `422 credential-not-found`, `422 credential-field-missing` (with the block and the field names)
    - `422 credential-format-removed`: the version uses the removed `credentials` block
    - `500 credential-undecryptable`: the stored value cannot be decrypted with the current key
- No `Idempotency-Key`, no `mutation_receipts` row.
- Each success writes `run_credential_uses` rows and one audit event `credential.released`.

## The worker account on other routes

Every route outside `/worker/` answers `403 forbidden` to the worker account.

## Changed routes

- `GET /runs/{run_id}` gains `credentials_used: [{"stage", "block", "name", "revision"}]`.
- `POST /configs`, `POST /configs/{config_id}/versions` and `POST /configs/{config_id}/versions/{v}/validate`
  report the findings `credential-not-found`, `credential-field-missing`, `credential-not-used`
  (a block that names a credential its method does not use) and `credential-format-removed`.
  `credential-not-set-in-worker` and `worker-environment-unknown` are removed.
- The run-starting routes (plan, sync, apply, verify, approve, resume) refuse with `422` and the same
  codes before they start a run (FR-008).
- The message of `credential-not-found` names the command `infrahub-sync credentials create <name>` and
  says that an administrator runs it.
