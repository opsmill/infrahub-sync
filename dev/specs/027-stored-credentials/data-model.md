# Data model: Stored credentials managed through the Sync API

**Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

All tables are new and declared in `_SCHEMA` of `infrahub_sync/product_store/store.py` with
`CREATE TABLE IF NOT EXISTS`, so a database from an earlier build gains them at start-up. No column is
added to an existing table (research, "What the code does today"). The SQL lives in a new module
`infrahub_sync/product_store/stored_credentials.py`, called by the store in the way
`product_store/accounts.py` is.

## stored_credentials

One row per credential. A deletion removes the row and its revisions.

| Column | Type | Rules |
| --- | --- | --- |
| `credential_id` | TEXT, primary key | `cred_` and a random identifier |
| `name` | TEXT, unique | Matches `^[a-z][a-z0-9-]{0,62}$`. Cannot change (FR-011). |
| `description` | TEXT | At most 500 characters; checked by the free-text check that refuses token prefixes |
| `current_revision` | INTEGER | The latest revision number |
| `created_by`, `updated_by` | TEXT | Account identifiers, shown as names at the HTTP boundary |
| `created_at`, `updated_at` | TEXT | ISO 8601 |

## stored_credential_revisions

One row per value of a credential (FR-014).

| Column | Type | Rules |
| --- | --- | --- |
| `credential_id` | TEXT | References `stored_credentials`; part of the primary key |
| `revision` | INTEGER | Starts at 1; part of the primary key |
| `fields` | TEXT | Names of the set fields, sorted, comma-separated: a subset of `password,token,username` |
| `key_id` | TEXT | 16 hex characters identifying the key (research R3) |
| `nonce` | BLOB / BYTEA | 12 bytes |
| `ciphertext` | BLOB / BYTEA | AES-256-GCM of the JSON object of the set fields |
| `created_by`, `created_at` | TEXT | Account identifier and time |

Field rules (FR-013): the allowed sets are `{token}`, `{username, password}` and `{username, token}`;
every other set, including all three fields, is refused. Each value is 1 to 4096 characters with no
control character. A change replaces the whole set of fields and creates the next revision.

## configuration_version_credentials

The credentials each registered version names (research R8). Written with the version, in the same
transaction.

| Column | Type | Rules |
| --- | --- | --- |
| `config_id`, `registry_version` | TEXT, INTEGER | The version |
| `block` | TEXT | `source` or `destination` |
| `credential_name` | TEXT | The name, not the identifier, so that a deleted and recreated credential with the same name is found again |

Primary key `(config_id, registry_version, block)`. Index on `credential_name`.

## run_credential_uses

The credentials a stage execution received (FR-003, FR-016, research R9).

| Column | Type | Rules |
| --- | --- | --- |
| `run_id` | TEXT | References `product_runs` |
| `flow_run_id` | TEXT | The stage execution in `prefect_executions` |
| `stage` | TEXT | `plan`, `verify`, `apply`, `sync` or `resume` |
| `block` | TEXT | `source` or `destination` |
| `credential_id` | TEXT | Keeps the revision unambiguous after a deletion and a new credential with the same name. No foreign key, so the row survives the deletion. |
| `credential_name` | TEXT | |
| `revision` | INTEGER | |
| `released_at` | TEXT | |

Primary key `(run_id, flow_run_id, block)`. A second release for the same execution and block, after a
worker retry, replaces the row.

## Mutation receipts (existing table, changed check)

Added during implementation (overnight log A1). The check on `mutation_receipts.resource_kind` accepts
`credential`. It has a new name, `mutation_receipts_resource_kinds` (SQLite triggers
`mutation_receipts_resource_kinds_insert` and `_update`). At start-up, the store drops the check of an
earlier build by its old name, only when the catalog holds it, and creates the new one. No column
changes. The receipt of a credential request stores an HMAC fingerprint keyed from a derivation of the
encryption key, and an answer that holds no value.

## Accounts (existing tables, new row)

- The `worker` account: an `accounts` row with `name = 'worker'`, `environment_managed = TRUE`, no
  password, `administrator = FALSE`. Applied at each `sync-api` start from
  `INFRAHUB_SYNC_WORKER_TOKEN` (research R4).
- Its token: an `account_tokens` row with `environment_managed = TRUE`.

## Removed

- `worker_environments`: its statement leaves `_SCHEMA`, and no code reads or writes it. An existing
  table on an earlier database is left in place.
- `WorkerEnvironment` (`product_store/models.py:461`).

## Package format (changed)

```yaml
source:
  adapter: netbox
  credential: netbox-lab        # new, optional
  settings:
    url: https://netbox.example.com
destination:
  adapter: infrahub
  credential: infrahub-netbox-sync
  settings:
    url: http://infrahub-server:8000
```

- `credential` matches the credential name pattern.
- The block's credential settings (research R2) are refused in `settings`.
- `credentials` at the package level and `{"$credential": …}` anywhere are refused at parse time.

## State of a credential

```text
created (revision 1) ──change value──▶ revision n+1 ──change value──▶ …
        │                                    │
        └──────────────delete (only when not in use, FR-010)──────────────▶ removed
```

A change of description keeps the revision. No other transition exists: no rename, no disable.
