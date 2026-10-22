# Data Model: Lineage of the data an import writes

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

## Configuration: Infrahub destination settings

The Infrahub adapter's allowed settings (`infrahub_sync/configuration/capabilities.py:525`) gain
`protect`. `source` and `owner` change form.

| Setting | Type | Default | Rule |
|---|---|---|---|
| `source` | `{kind, name}` or absent | absent: the token's account | `kind` is one of the allowed kinds; `name` is a non-empty string |
| `owner` | `{kind, name}` or absent | absent: no owner | same as `source` |
| `protect` | boolean | `false` | only `true` or `false` |

- **Named node** (`{kind, name}`): `kind` is one of `CoreAccount`, `CoreAccountGroup`,
  `CoreRepository` and `CoreReadOnlyRepository` (spec FR-003). No other key is allowed.
- Registration refuses a bare string, an unknown kind, an empty name, extra keys, and a
  non-boolean `protect`. Each refusal is one validation finding at
  `/configuration/destination/settings/<setting>` (the pointer form of
  `infrahub_sync/configuration/validation.py:231`).
- The checks run through the adapter's `validator` hook (`AdapterConfigurationCapabilities`,
  `infrahub_sync/configuration/capabilities.py:55`), which the Infrahub entry does not use today.

## Run state: resolved lineage

Resolved once at the start of a plan and once at the start of an apply, before the source system is
read.

| Field | Type | Origin |
|---|---|---|
| `source` | named node with `id` | the named source, or the account from `client.get_user()` |
| `owner` | named node with `id`, or none | the named owner |
| `protect` | boolean | the setting |

- A named node that does not exist stops the run. The cause names the setting, the kind and the
  name (FR-004).

## Stored plan: manifest fields (additive)

Added to `PlanManifest` (`infrahub_sync/plan/models.py:377-407`) without a format version change;
absent on older plans; covered by the checksum (`infrahub_sync/plan/checksum.py:59-67`).

`lineage`:

| Field | Type | Notes |
|---|---|---|
| `source` | `{kind, name}` | `kind` is `CoreAccount` for the token's account |
| `owner` | `{kind, name}` or `null` | `null` when the package names no owner |
| `protect` | boolean | |
| `owner_applies_to_created_values_only` | boolean | `true` when `owner` is not `null`; drives the summary notice (FR-009) |

`last_changes`:

| Field | Type | Notes |
|---|---|---|
| `path` | string | always `last_changes.jsonl`, relative to the plan directory |
| `sha256` | string | digest of the file's bytes |
| `count` | integer | number of records |
| `metadata_read` | boolean | `false` when the server is older than 1.7.0 or its version cannot be parsed (research R-1) |

## Stored plan: `plan/last_changes.jsonl`

One JSON record for each proposed change to an attribute or a relationship on an existing node
(an `update` operation). A `create` has no earlier change to show, so it has no record.

| Field | Type | Notes |
|---|---|---|
| `operation_id` | string | the `PlannedOperation.operation_id` the change belongs to |
| `field` | string | attribute or relationship name |
| `field_type` | `attribute` or `relationship` | |
| `updated_by` | `{id, display_label}` or `null` | `null` is shown as "not recorded" |
| `updated_at` | ISO 8601 string or `null` | `null` is shown as "not recorded" |

- Records are sorted by `operation_id`, then `field`, and written in canonical JSON, so the same
  plan inputs give the same bytes.
- Which fields of an update count as "proposed changes" is decided by the same diff that builds the
  operation's payload (`infrahub_sync/plan/derive.py:590-682`).

## Sync API: `PlanResource` (optional additions)

`PlanResource` (`infrahub_sync/client/models.py:341`) gains:

- `last_changes`: the records of `last_changes.jsonl`, or absent for an older plan;
- `lineage`: the manifest's `lineage` object, or absent for an older plan.

## Adapter state (not stored)

- Map from `(destination node id, field name)` to `(updated_by, updated_at)`, filled while the
  destination is read with `include_metadata=True`. It is not part of the DiffSync models, so it
  does not take part in the diff.

## State changes on written values

| Value | Create | Update, value changed | Update, edge added | Update, nothing changed |
|---|---|---|---|---|
| source | set | set | set | no write |
| owner | set if named | not sent | set if named | no write |
| protection | sent only if `protect` | sent only if `protect` | sent only if `protect` | no write |
