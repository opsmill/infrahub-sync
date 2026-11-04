# Data Model: Discoverable runs, configurations and versions

Only changes are listed. Existing fields are unchanged unless named.

## Stored tables (`infrahub_sync/product_store/store.py`)

### `product_runs` (existing)

| Change | Detail |
| --- | --- |
| New index `product_runs_started` | `(started_at, run_id)`. Serves the unfiltered run list. |
| New index `product_runs_binding_started` | `(config_id, registry_version, started_at, run_id)`. Serves filtered lists and the latest-run lookups. |

`started_at` values are compared as UTC ISO 8601 text; the store converts each inserted run's
`started_at` to UTC.

### `configuration_versions` (existing)

| Column | Type | Rule |
| --- | --- | --- |
| `registered_by` (new) | text, nullable | Actor of the registration request. `NULL` for versions registered before this change. |
| `registration_reason` (new) | text, nullable | Reason from the registration request, or `NULL`. |

Both are written in the transaction that inserts the version. A version is immutable, so they
are never updated.

### `configurations` (existing)

| Column | Type | Rule |
| --- | --- | --- |
| `name` (new) | text, nullable | `configuration.name` of the latest version's package. Updated in the transaction that registers a version. Filled at start-up for rows where it is `NULL`. Not unique. |

## Public resources (`infrahub_sync/client/models.py`)

### `RunPage` (new)

| Field | Type | Rule |
| --- | --- | --- |
| `items` | list of `PublicRunResource` | At most `limit`; newest first. `artifact_refs` and `prefect_executions` are empty in list items. |
| `next_cursor` | string or `null` | Present when more rows match. Opaque. |

### `ConfigurationSummaryResource` (existing)

| Field | Change |
| --- | --- |
| `name` | New, string or `null`. |
| `latest_version` | New, integer. The highest `registry_version`. |

### `ConfigurationVersionSummaryResource` (new)

| Field | Type |
| --- | --- |
| `config_id` | string |
| `registry_version` | integer ≥ 1 |
| `package_checksum` | 64-character SHA-256 hex |
| `created_at` | timezone-aware timestamp |
| `registered_by` | string or `null` |
| `registration_reason` | string or `null` |
| `latest_run` | `RunReference` or `null` |
| `latest_applied_run` | `RunReference` or `null` |

### `RunReference` (new)

| Field | Type |
| --- | --- |
| `run_id` | string |
| `phase` | string |
| `outcome` | string or `null` |
| `started_at` | timezone-aware timestamp |

### `ConfigurationPage` and `ConfigurationVersionPage` (new)

Same shape as `RunPage`, with `ConfigurationSummaryResource` and
`ConfigurationVersionSummaryResource` items.

### `PrincipalResource` (new)

| Field | Type |
| --- | --- |
| `actor` | string |
| `administrator` | boolean |

## Cursor

Opaque URL-safe base64 of a JSON object:

| Key | Meaning |
| --- | --- |
| `k` | Last row's sort key: `[started_at, run_id]` for runs, `[created_at, config_id]` for configurations, `registry_version` for versions. |
| `f` | First 16 hex characters of the SHA-256 of the canonical filter set the cursor was issued for. |

A cursor that fails to decode, or whose `f` differs from the request's filters, is refused
with `422 invalid-cursor`.

## Prefect flow-run tags

| Tag | Present when |
| --- | --- |
| Existing tags (`infrahub-sync`, `service`, and any tag added in Prefect) | Always. Sync writes the union of the flow run's current tags and the run tags, because Prefect replaces the tag list on update. |
| `run:<run_id>` | Always. |
| `stage:<plan\|verify\|apply\|sync>` | Always. |
| `config:<config_id>` | Registered runs. |
| `config-version:<config_id>@<registry_version>` | Registered runs. |

All values come from identifiers the run resource already publishes; none contains a secret,
actor, branch or work pool.
