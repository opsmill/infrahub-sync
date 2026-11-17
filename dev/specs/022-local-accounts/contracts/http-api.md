# Contract: Sync HTTP API

All routes are under `/api`. The API stays `v3-unstable`. Every route except `POST /auth/sign-in`
requires `Authorization: Bearer <API token or session token>`. Resource shapes are in
[data-model.md](../data-model.md).

## Existing routes

No change of path, request or response. `actor`, `approved_by` and `registered_by` in every
response hold an account name, or `Deleted account <short identifier>`. This is also true of an
answer that is replayed for an `Idempotency-Key`.

- The message of the `already-approved` refusal no longer names the account. The approval in its
  details does.
- Every `reason` has a maximum length of 512 characters. A longer one answers `422`.
- A `reason` that contains `ifs_pat_` or `ifs_ses_` answers `422`, code
  `secret-parameter-refused`.

| Case | Answer |
| --- | --- |
| No value, an unknown value, a deleted token, a disabled account | `401`, code `unauthenticated` |
| An API token after its expiry date | `401`, code `token-expired` |
| A session idle for longer than the idle period | `401`, code `unauthenticated` |

## Sign-in and sign-out

| Route | Who | Request | Answer |
| --- | --- | --- | --- |
| `POST /auth/sign-in` | anyone | `{"name", "password"}` | `200` `SessionResource` |
| `POST /auth/sign-out` | a session | none | `204`. The session token is refused afterwards. |

- A wrong name, a wrong password, a disabled account and a name refused after repeated failures
  all answer `401`, code `sign-in-refused`, with one fixed message.
- A sign-in that waits more than 5 seconds for a password check answers `503`, code
  `sign-in-busy`.
- `POST /auth/sign-out` with an API token answers `422`, code `not-a-session`.

## The caller's own account

| Route | Request | Answer |
| --- | --- | --- |
| `GET /me` | none | `200`, unchanged |
| `PUT /me/password` | `{"current_password", "new_password"}` | `204`. Other sessions of the account end. |
| `GET /me/tokens` | none | `200` list of `TokenResource` |
| `POST /me/tokens` | `{"name", "expires_at"?}` | `201` `CreatedTokenResource` |
| `DELETE /me/tokens/{token_id}` | none | `204` |

- A wrong `current_password` answers `403`, code `current-password-refused`, and counts as a
  failed sign-in attempt. While the name is refused, the route answers the same code.
- A `new_password` shorter than 12 characters answers `422`, code `password-too-short`.
- A token name the account already uses answers `409`, code `token-name-taken`.
- An `expires_at` in the past answers `422`, code `expiry-in-the-past`.

## Accounts (administrator only)

| Route | Request | Answer |
| --- | --- | --- |
| `GET /accounts` | query `state=active\|disabled\|all` (default `all`) | `200` list of `AccountResource` |
| `POST /accounts` | `{"name", "password", "administrator"?}` | `201` `AccountResource` |
| `GET /accounts/{name}` | none | `200` `AccountResource` |
| `PATCH /accounts/{name}` | `{"administrator"?, "disabled"?}` | `200` `AccountResource` |
| `PUT /accounts/{name}/password` | `{"new_password"}` | `204`. Every session of the account ends. |
| `POST /accounts/{name}/unlock` | none | `204`. Ends a refusal after repeated failures. |
| `DELETE /accounts/{name}` | none | `200` `{"placeholder": "Deleted account <short identifier>"}` |
| `GET /accounts/{name}/tokens` | none | `200` list of `TokenResource` |
| `DELETE /accounts/{name}/tokens/{token_id}` | none | `204` |

- A caller that is not an administrator receives `403`, code `forbidden`, on every route of this
  table.
- An unknown name answers `404`, code `account-not-found`.
- A name that exists, without regard to case, answers `409`, code `account-name-taken`.
- A name that does not match the name rule, or that the product reserves, answers `422`, code
  `account-name-invalid`.
- No answer contains an account identifier (`acct_…`).
- Any change to an account that the environment manages answers `409`, code
  `environment-managed-account`. The message names `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` or
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`. The same code answers `PUT /me/password`,
  `POST /me/tokens` and `DELETE /me/tokens/{token_id}` when the caller is `admin`.
- Every mutation route of this document accepts an optional `reason` in its body or, for
  `DELETE`, in the query. It is recorded in the audit event. A `reason` that contains a secret
  the process knows, or a token prefix, answers `422`, as on the existing routes.

## What no answer contains

- A password, in any form.
- A password hash, a token digest or a session digest.
- A token value, except in the `201` answer to `POST /me/tokens`.
- A session token, except in the `200` answer to `POST /auth/sign-in`.
