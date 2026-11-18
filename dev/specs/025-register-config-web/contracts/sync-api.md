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

`destination_read` is `not_requested` without `branch`; otherwise `read`, `timeout`,
`unreachable`, `unauthorized`, `forbidden`, `nobranch`, `unconfigured` or `credentials`. When it is not `read` or
`not_requested`, the findings include the existing `destination_schema_read_failed` finding.

**Refusals**:

| Status | Code | When |
|---|---|---|
| 400 | `request` | Both or neither of `package` and `document`. |
| 400 | `request`, reason `invalid_package` | `document` is not a YAML or JSON mapping with string keys, or is empty. |
| 422 | `validation`, reason `v2_configuration` | `document` or `package` has no `configuration` key and has top-level `source` and `destination` keys. The message names `infrahub-sync configs convert`. |
| 400 / 422 | as registration | Any refusal the registration gives for the same package, with the same status, code and reason (`config_routes.py`, `_call`). |
| 413 | — | The request is larger than the service or a proxy in front of it accepts. |

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

## Configuration version resources (changed, User Story 4)

Every response that carries a configuration version (registration, `GET` of a version, the version
list) gains:

```json
{"registered_without_validation": false}
```

## CLI (FR-006)

`infrahub-sync configs check PACKAGE [--branch BRANCH] [--offset N] [--limit N]` reads the file,
sends it as `package` to `POST /api/configs/validate`, prints the report in the format of
`configs validate`, plus `destination_read`, and exits 1 when the returned page has a finding.
