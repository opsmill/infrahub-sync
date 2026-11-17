# Data Model: Sign in to Sync with a local account

**Date**: 2026-11-17 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

Five new tables in the product store. No existing table changes. Times are ISO 8601 text in UTC,
as in the existing tables.

## `accounts`

| Field | Type | Rule |
| --- | --- | --- |
| `account_id` | text, primary key | A random UUID. Never shown to a person. |
| `name` | text | As typed. Matches `^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$` (research R8). This is the value records hold in `actor`. |
| `name_key` | text, unique | The lower-case name. Makes names unique without regard to case (FR-012). |
| `password_hash` | text | Algorithm, cost parameters, salt and derived key (research R2). Never returned. |
| `administrator` | boolean | The level (FR-010). |
| `disabled` | boolean | The state. `false` when created. |
| `environment_managed` | boolean | `true` only for `admin` (FR-006 to FR-008). |
| `created_at`, `updated_at`, `password_changed_at` | text | Times. |

State transitions:

- **active → disabled** (administrator): deletes the tokens and the sessions of the account.
- **disabled → active** (administrator): restores sign-in with the password only.
- **active or disabled → deleted** (administrator): removes the row (see "Deletion").
- An `environment_managed` account has no transition.

## `account_tokens`

| Field | Type | Rule |
| --- | --- | --- |
| `token_id` | text, primary key | A random UUID. Used to delete a token. |
| `account_id` | text | The owner. |
| `name` | text | Unique for one account. 1 to 64 characters. |
| `digest` | text, unique | SHA-256 of the token value (research R3). |
| `created_at` | text | |
| `expires_at` | text, nullable | The optional expiry date (FR-014). |
| `last_used_at` | text, nullable | Written at most once a minute (FR-017). |
| `environment_managed` | boolean | `true` for the one token of `admin` that the environment sets. |

## `account_sessions`

| Field | Type | Rule |
| --- | --- | --- |
| `session_id` | text, primary key | A random UUID. |
| `account_id` | text | The account that signed in. |
| `digest` | text, unique | SHA-256 of the session token. |
| `created_at` | text | |
| `last_used_at` | text | Written at most once a minute. A session whose last use is older than the idle period is refused and deleted (FR-022). |

## `sign_in_attempts`

| Field | Type | Rule |
| --- | --- | --- |
| `name_key` | text, primary key | The lower-case name that was tried, whether or not an account has it. |
| `failed_count` | integer | Failures in the current window. |
| `window_started_at` | text | Start of the 15-minute window. |
| `refused_until` | text, nullable | Set when the count reaches 5 (FR-021). |

## `deleted_accounts`

| Field | Type | Rule |
| --- | --- | --- |
| `identifier` | text, primary key | 8 hexadecimal characters. Part of the placeholder `Deleted account <identifier>`. |
| `deleted_at` | text | |

The table holds no name. It exists so that two deleted accounts never get the same identifier.

## Existing columns that hold an account name

No change of type or of name. Each holds an account name, or a placeholder after a deletion.

| Table | Column |
| --- | --- |
| `product_runs` | `actor`, `approved_by` |
| `mutation_receipts` | `actor` |
| `audit_events` | `actor` |
| `audit_event_resources` | `resource_id`, for rows with `kind = 'account'` |
| `configuration_versions` | `registered_by` |

## Deletion

One transaction (research R7):

1. Choose an identifier that `deleted_accounts` does not hold.
2. Replace the account name with `Deleted account <identifier>` in each column of the table
   above.
3. Delete the rows of the account in `account_tokens`, `account_sessions` and
   `sign_in_attempts`, then the row in `accounts`.
4. Insert the identifier into `deleted_accounts`.

The audit event of the deletion is written in the same transaction. Its related resource is the
placeholder, and its `actor` is the administrator who deleted the account.

## What the API returns

- **`Principal`** (`GET /me`, existing): `actor`, `administrator`, `approval_required`. No change.
- **`AccountResource`**: `name`, `administrator`, `disabled`, `environment_managed`,
  `created_at`. Never a password hash.
- **`TokenResource`**: `token_id`, `name`, `created_at`, `expires_at`, `last_used_at`. Never the
  value or the digest.
- **`CreatedTokenResource`**: the fields of `TokenResource` and `token`, the value. Returned once,
  by the creation.
- **`SessionResource`**: `token`, the session token, `account` (an `AccountResource`) and
  `idle_seconds`. Returned once, by the sign-in.
