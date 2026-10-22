# Data Model: Lineage of the data an import writes

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

## Configuration: Infrahub destination settings

The Infrahub adapter's allowed settings (`infrahub_sync/configuration/capabilities.py:525`) gain
`protect`. `source` and `owner` change form.

| Setting | Type | Default | Rule |
|---|---|---|---|
| `source` | `{kind, name}` or absent | absent: the import account | `kind` is one of the allowed kinds; `name` is a non-empty string |
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
| `import_account` | account name and `id` | `client.get_user()`, always |
| `source` | named node with `id` | the named source, or the import account |
| `owner` | named node with `id`, or none | the named owner |
| `protect` | boolean | the setting |

- A named node that does not exist stops the run, in plan (FR-004) and in apply (FR-011). The
  cause names the setting, the kind and the name. Apply stops before any write.
- In apply, the id from `client.get_user()` must equal the plan's `lineage.import_account.id`.
  Otherwise apply refuses before any write, and the cause names both accounts (FR-012).
- A plan made before this feature has no `lineage.import_account`. Apply refuses it before any
  write, and the cause asks for a new plan (FR-012).

## Stored plan: manifest fields (additive)

Added to `PlanManifest` (`infrahub_sync/plan/models.py:377-407`) without a format version change;
absent on older plans; covered by the checksum (`infrahub_sync/plan/checksum.py:59-67`).

`lineage`:

| Field | Type | Notes |
|---|---|---|
| `import_account` | `{name, id}` | the account from `client.get_user()`; apply compares `id` (FR-012) |
| `source` | `{kind, name}` | `kind` is `CoreAccount` when the source is the import account |
| `owner` | `{kind, name}` or `null` | `null` when the package names no owner |
| `protect` | boolean | |

`last_changes`:

| Field | Type | Notes |
|---|---|---|
| `path` | string | always `last_changes.jsonl`, relative to the plan directory |
| `sha256` | string | digest of the file's bytes |
| `count` | integer | number of records |
| `metadata_read` | boolean | `false` when the server is older than 1.7.0 or its version cannot be parsed (research R-1) |

## Stored plan: `plan/last_changes.jsonl`

One JSON record for each proposed update to an attribute or a relationship of a node that already
exists (an `update` operation). A `create` has no last-change record (FR-007).

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
