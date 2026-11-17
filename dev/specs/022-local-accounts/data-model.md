# Data Model: Sign in to Sync with a local account

**Date**: 2026-11-17 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

Four new tables in the product store. No existing table changes. Times are ISO 8601 text in UTC,
as in the existing tables.

## `accounts`

| Field | Type | Rule |
| --- | --- | --- |
| `account_id` | text, primary key | `acct_` and 32 hexadecimal characters. This is the value every record holds (research R1). |
| `short_id` | text, unique | 8 hexadecimal characters, chosen at creation. Shown only in `Deleted account <short_id>`. |
| `name` | text, nullable | As typed. Matches the name rule (research R8). Empty after a deletion. |
| `name_key` | text, nullable, unique | The lower-case name. Empty after a deletion, so the name is free again. |
| `password_hash` | text, nullable | Algorithm, cost parameters, salt and derived key (research R2). Never returned. Empty after a deletion. |
| `administrator` | boolean | The level. |
| `disabled` | boolean | `false` when created. |
| `environment_managed` | boolean | `true` only for `admin`. |
| `created_at`, `updated_at`, `password_changed_at` | text | Times. |
| `deleted_at` | text, nullable | Set by the deletion. |

States and transitions:

- **active → disabled** (administrator): deletes the tokens and the sessions of the account.
- **disabled → active** (administrator): restores sign-in with the password only.
- **active or disabled → deleted** (administrator): empties `name`, `name_key` and
  `password_hash`, sets `deleted_at`, and deletes the tokens, the sessions and the
  `sign_in_attempts` row. Not reversible.
- An `environment_managed` account has no transition.

## `account_tokens`

| Field | Type | Rule |
| --- | --- | --- |
| `token_id` | text, primary key | A random UUID. Used to delete a token. |
| `account_id` | text | The owner. |
| `name` | text | Unique for one account. 1 to 64 characters. |
| `digest` | text, unique | SHA-256 of the token value (research R3). |
| `created_at` | text | |
| `expires_at` | text, nullable | The optional expiry date. |
| `last_used_at` | text, nullable | Written when the last recorded use is more than a minute old. |
| `environment_managed` | boolean | `true` for the one token of `admin` that the environment sets. |

## `account_sessions`

| Field | Type | Rule |
| --- | --- | --- |
| `session_id` | text, primary key | A random UUID. |
| `account_id` | text | The account that signed in. |
| `digest` | text, unique | SHA-256 of the session token. |
| `created_at` | text | |
| `last_used_at` | text | Written when the last recorded use is more than a minute old. A session whose last use is older than the idle period is refused and deleted. |

## `sign_in_attempts`

| Field | Type | Rule |
| --- | --- | --- |
| `name_key` | text, primary key | The lower-case name that was tried, whether or not an account has it. At most 64 characters. |
| `failed_count` | integer | Failures in the current window. |
| `window_started_at` | text | Start of the 15-minute window. |
| `refused_until` | text, nullable | Set when the count reaches 5. Not moved by an attempt made while the name is refused. |

The table holds at most 10,000 rows (research R6).

## Existing columns that hold an account

No change of type or of name. From this feature on, each holds an account identifier. A record
made earlier holds a principal's name, which is displayed as stored.

| Table | Column |
| --- | --- |
| `product_runs` | `actor`, `approved_by` |
| `mutation_receipts` | `actor`, and the fields `actor`, `approved_by`, `registered_by` inside `response_body` |
| `audit_events` | `actor` |
| `audit_event_resources` | `resource_id`, for rows with `kind = 'account'` |
| `configuration_versions` | `registered_by` |

## What the service passes between its parts

- **`Principal`**: `actor` (the account identifier), `name`, `administrator`, the kind of value
  presented (API token or session) and, for a session, `session_id`.
- **The result of `resolve`**: a `Principal`, or a refusal code (`unauthenticated`,
  `token-expired`).

## What the API returns

In every answer, the fields `actor`, `approved_by` and `registered_by`, and the related
resources of kind `account`, hold a display name: the account name, or
`Deleted account <short_id>` (research R7).

- **`PrincipalResource`** (`GET /me`, existing): `actor`, `administrator`, `approval_required`.
  No change.
- **`AccountResource`**: `name`, `administrator`, `disabled`, `environment_managed`,
  `created_at`, `sign_in_refused_until` (a time, or nothing). Never a password hash, and never
  the account identifier.
- **`TokenResource`**: `token_id`, `name`, `created_at`, `expires_at`, `last_used_at`. Never the
  value or the digest.
- **`CreatedTokenResource`**: the fields of `TokenResource` and `token`, the value. Returned
  once, by the creation.
- **`SessionResource`**: `token`, the session token, `account` (an `AccountResource`) and
  `idle_seconds`. Returned once, by the sign-in.
