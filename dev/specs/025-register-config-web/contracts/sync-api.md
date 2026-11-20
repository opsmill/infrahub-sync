# Contract: Sync API changes

All addresses are under `/api` ([spec 017](../../017-serve-web-interface/spec.md)). Errors use the
existing Sync API error format.

## `POST /api/configs/validate` (new, User Story 1)

Validates a configuration package without registering it. Stores nothing, records no audit event,
takes no `Idempotency-Key`.

**Access**: an administrator token, as `POST /configs`. Any other token gets 403 `forbidden`
("administrator access is required"), no destination is contacted, and the refusal is recorded with
the operation `check-config-package`, as registration records `register-config`. Reason: with a
branch, the service connects to the address the package declares, with the destination credential
it holds (research R10).

**Query parameters**: `branch`, `offset`, `limit`, with the same parsing and bounds as the existing
validate route.

**Body**: exactly one of:

```json
{"package": {"format_version": 1, "configuration": {"name": "netbox-to-infrahub", "...": "..."}}}
```

```json
{"document": "format_version: 1\nconfiguration:\n  name: netbox-to-infrahub\n  ...\n"}
```

**200 response**:

```json
{
  "package_checksum": "9f2c…",
  "name": "netbox-to-infrahub",
  "declared_content": {"format_version": 1, "configuration": {"...": "..."}, "credentials": {}, "package_metadata": {"adapter_api_version": 1}, "omissions": []},
  "destination_read": "read",
  "destination_schema_fingerprint": "41ab…",
  "offset": 0,
  "limit": 256,
  "total_findings": 1,
  "next_offset": null,
  "findings": [
    {"code": "destination_attribute_missing", "severity": "error", "location": "/configuration/schema_mapping/2/fields/1", "message": "…"}
  ]
}
```

`destination_read` is `not_requested` without `branch`; otherwise `read`, `not_read`, `timeout`,
`unreachable`, `unauthorized`, `forbidden`, `nobranch`, `rejected`, `unconfigured` or `credentials`.
`not_read` means the destination adapter declares no schema read, so nothing was read. No current
adapter returns it: the Sync API refuses every destination adapter without a schema read before the
check runs. The value keeps `read` true for an adapter added later. For the other values that are not `read` or `not_requested`, the
findings include the existing `destination-schema-read-failed` finding.

**Refusals**:

| Status | Code | When |
|---|---|---|
| 400 | `request` | Both or neither of `package` and `document`. |
| 400 | `request`, reason `invalid_package` | `document` is not a YAML or JSON mapping with string keys, is empty, or holds more than 1,000,000 values once its YAML aliases are expanded. |
| 422 | `validation`, reason `v2_configuration` | `document` or `package` has no `configuration` key and has top-level `source` and `destination` keys. The message names `infrahub-sync configs convert`. |
| 400 / 422 | as registration | Any refusal the registration gives for the same package, with the same status, code and reason (`config_routes.py`, `_call`). |
| 413 | — | Not sent by the Sync API, which sets no size limit on the request body. A proxy in front of it may answer 413; the web interface shows a message for that answer. |
| 422 | `request-invalid` | The body is not valid JSON. Sent before the token check, as on every other route, and no destination read happens. |

**Invariant**: for the same package, branch and destination state, `findings` equals the findings of
validating the registered version (FR-002).

## `POST /api/configs` and `POST /api/configs/{config_id}/versions` (changed, User Story 4)

The request body accepts one new optional field:

```json
{"package": {"...": "..."}, "reason": "Add the VLAN mapping", "registered_without_validation": true}
```

Default `false`. It is part of the request fingerprint that the idempotency check compares, like
`reason`. It is recorded on a created version only. When the package is identical to the
newest version (`created: false`), the field is ignored and the existing version is returned as it
is.

## `POST /api/configs` and `POST /api/configs/{config_id}/versions` (changed, decision D-12)

The request body holds exactly one of `package`, a JSON object as before, and `document`, the text
of a YAML or JSON package file:

```json
{"document": "format_version: 1\nconfiguration:\n  name: netbox-to-infrahub\n  ...\n", "reason": "Add the VLAN mapping"}
```

The Sync API parses `document` as `POST /api/configs/validate` does (`config_routes.py`,
`_document_package`), so for the same text the registered version's `package_checksum` equals the
check's `package_checksum`. The web interface sends `document`; the CLI sends `package`. A request
with only `package` has the same body and the same request fingerprint as before.

| Status | `family` | When |
|---|---|---|
| 400 | `request` | Both or neither of `package` and `document`. Checked after the administrator check and the `reason` check. |
| 400 | `request`, reason `invalid_package` | `document` is not a YAML or JSON mapping with string keys, is empty, or holds more than 1,000,000 values once its YAML aliases are expanded. |
| 422 | `validation`, reason `v2_configuration` | `document` has no `configuration` key and has top-level `source` and `destination` keys. A `package` gets the refusals it got before. |

**Idempotency**: the request fingerprint holds the text of `document` instead of `package`. A retry
with the same key and the same text replays the stored response. The same key with different text,
or with the same package sent as `package`, gets 409 `idempotency-conflict`. A refused `document`
releases the key, as a refused `package` does.

## Configuration version resources (changed, User Story 4)

Every response that carries a configuration version (registration, `GET` of a version, the version
list) gains:

```json
{"registered_without_validation": false}
```

## CLI (FR-006)

`infrahub-sync configs check PACKAGE [--branch BRANCH] [--offset N] [--limit N]` reads the file,
sends it as `package` to `POST /api/configs/validate`, prints the report in the format of
`configs validate`, plus `destination_read`, and exits 1 when any page has an `error` finding. When the
printed page has none, it reads the following pages until it finds one or reaches the end, and then names
the offset to read. A `warning` finding does not change the exit code (spec decision D-10).
