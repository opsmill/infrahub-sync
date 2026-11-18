# Data model: Register a configuration package from the web interface

## Package check (new, not stored)

The result of validating a package that is not registered (User Story 1).

| Field | Type | Meaning |
|---|---|---|
| `package_checksum` | string | The checksum the package would have once registered. |
| `name` | string or null | The configuration name the package declares. |
| `declared_content` | object | The package as the store would keep it, defaults filled. |
| `findings`, `offset`, `limit`, `total_findings`, `next_offset` | as the existing validation report | The findings page, in the order of the existing report. |
| `destination_schema_fingerprint` | string or null | Set when the destination schema was read. |
| `destination_read` | `not_requested`, `read`, `timeout`, `unreachable`, `unauthorized`, `forbidden`, `nobranch`, `unconfigured`, `credentials` | Whether the destination was read, and why not. |

Rules:

- Exactly one of `package` or `document` is accepted as input.
- `document` is parsed with `yaml.safe_load`; JSON is valid YAML.
- A document without a `configuration` key and with top-level `source` and `destination` keys is
  refused as a v2 configuration.
- Every other refusal is the one the registration gives for the same package.

## Configuration version (existing, one field added)

| Field | Change |
|---|---|
| `registered_without_validation` | New, boolean, `false` by default. `true` only when the registration request said so (User Story 4). |

Stored as a row in the new table `configuration_versions_unvalidated (config_id, registry_version)`,
primary key on both columns, foreign key to `configuration_versions`. No row means `false`.

Rules:

- The row is written in the transaction that creates the version.
- A registration that returns an existing identical version (`created: false`) does not add or
  remove a row.
- A version is never unmarked.

## Configuration mutation request (existing, one field added)

| Field | Change |
|---|---|
| `registered_without_validation` | New, optional boolean, default `false`. Accepted by `POST /configs` and `POST /configs/{config_id}/versions`. |

## Review step (web interface only)

State held in the browser for one dropped file:

| Field | Source |
|---|---|
| target | "new configuration" or a `config_id` (from the page the file was dropped on) |
| check | the latest package check response |
| branch | `declared_content.configuration.destination.settings.branch`, or `main`, changed by the operator |
| compared version | the newest version's number when the review opened (new version only) |
| reason | entered by the operator, optional |
| idempotency key | generated once when the review opens |

State transitions:

```text
dropped -> checking -> valid           -> registering -> registered | unchanged
                    -> findings        (Register disabled)
                    -> unreachable     -> registering (with the mark, User Story 4 only)
                    -> refused         (message, no review)
valid | findings | unreachable -> checking   (branch changed)
registering -> stale (newer version found) -> checking
```
