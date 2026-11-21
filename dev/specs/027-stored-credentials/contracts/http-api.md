# HTTP API contract: stored credentials

All routes are under `/api`, keep the API version `v3-unstable`, and use the existing error envelope
`{"error": {"code", "message", "status", …}}`. No response of these routes contains a value, except the
worker route that releases the values of a run.

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

## Worker route that releases the values of a run

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
    - `422 credential-format-removed`: the version uses the removed `credentials` block or
      `$credential` references. The message says to register a new version.
    - `500 credential-undecryptable`: the stored value cannot be decrypted with the current key
- No `Idempotency-Key`, no `mutation_receipts` row.
- Each success writes `run_credential_uses` rows and one audit event `credential.released`.

## Worker route that returns the states of a schedule's earlier proposed changes

`POST /worker/schedules/{schedule_id}/proposed-change-states`

A scheduled start with branch review reads the state of the schedule's earlier proposed changes before
its run exists. The first worker route releases values only to a claimed execution of a run, so the
worker cannot get the destination token for this read. The Sync API does the read itself.

- Accepts only the `worker` account. Every other account, including an administrator, gets
  `403 forbidden` (FR-005).
- Request: no body. The route reads nothing from a request body.
- The Sync API takes every input of the read from its own store:
    - the destination address and the credential name, from the schedule's stored configuration version
    - the proposed-change identifiers, from the schedule's branch rows in the states `pending` and `open`
      that have a proposed change
- The Sync API decrypts the destination credential inside the redaction registry of the request, and
  reads each proposed change from Infrahub on `main`.
- Success `200`. The answer holds only the state of each proposed change, by the run of its branch row.
  It holds no value, no address, no token and no proposed-change identifier:

```json
{"states": {"run_…": "approved", "run_…": "merged"}}
```

- A state is `open`, `approved`, `merging`, `merged`, `closed`, `canceled`, or `missing` for a proposed
  change that Infrahub no longer holds.
- Refusals, each with no value:
    - `404 schedule-not-found`
    - `422 destination-credential-not-named`: the destination of the schedule's version names no stored
      credential. The worker reads such a destination itself.
    - `422 credential-not-found`, `422 credential-field-missing`, `422 credential-method-unknown` (with
      the block and the field names), as on the first worker route
    - `500 credential-undecryptable`, `503 credential-key-not-configured`, as on the first worker route
    - `422 credential-format-removed`: the schedule's stored version uses the removed `credentials`
      block or `$credential` references
    - `500 configuration-version-unavailable`: the schedule's stored version cannot be read
    - `424 proposed-change-read-failed`: Infrahub could not be read. `details.failure` is the failure
      class of the read, for example `system_unreachable` or `token_rejected`; the answer never holds the
      text of the error. The worker records that class as the cause of the failed start. The worker does
      not repeat this request.
- No `Idempotency-Key`, no `mutation_receipts` row.
- No audit event and no `run_credential_uses` row: no run exists, and no value leaves the Sync API. This
  matches the destination schema read that the Sync API does to validate a package. Each success that
  read Infrahub writes one log line with the schedule, the credential name and its revision. A schedule
  with no such branch row gets `{"states": {}}`; the Sync API then decrypts nothing and writes no log
  line.
- The worker checks that the answer holds a state for every branch row that its admission read. When a
  state is missing, the start fails with the cause `proposed-change-states-incomplete` and creates no
  run.

## The worker account on other routes

Every route outside the two worker routes answers `403 forbidden` to the worker account.

## Changed routes

- `GET /runs/{run_id}` gains `credentials_used: [{"stage", "block", "name", "revision"}]`.
- `POST /configs` and `POST /configs/{config_id}/versions` refuse a package that has a credential finding
  with `422 configs-validation`. The `reason` field of the error names the finding:
    - `credential-not-found` and `credential-field-missing`: tests of the registration cover both
      (`tests/product_store/test_credential_admission.py`).
    - `credential-not-used`: a block names a credential that its authentication method does not use.
      Configuration validation reports it as an error finding. No test of the registration route checks
      its `reason` yet; it is not known whether the `reason` is this code.
    - `credential-format-removed`: the package holds a `credentials` block, empty or not, or a
      `$credential` reference. The `message` field names `credential: <name>`
      (`tests/configuration/test_removed_credential_format.py`).
- `POST /configs/{config_id}/versions/{v}/validate` returns the same findings in the `findings` list of
  its report, each with its code, its location and its message.
- The findings `credential-not-set-in-worker` and `worker-environment-unknown` are removed.
- The run-starting routes (plan, sync, apply, verify, approve, resume) refuse with `422` and the same
  codes before they start a run (FR-008).
- The message of `credential-not-found` names the command `infrahub-sync credentials create <name>` and
  says that an administrator runs it.
