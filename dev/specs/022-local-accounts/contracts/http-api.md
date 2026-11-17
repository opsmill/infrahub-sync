# Contract: Sync HTTP API

All routes are under `/api`. The API stays `v3-unstable`. Every route except `POST /auth/sign-in`
requires `Authorization: Bearer <API token or session token>`. Resource shapes are in
[data-model.md](../data-model.md).

## Existing routes

No change of path, request or response. `actor` in every response holds an account name, or
`Deleted account <identifier>`.

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
- `POST /auth/sign-out` with an API token answers `422`, code `not-a-session`.

## The caller's own account

| Route | Request | Answer |
| --- | --- | --- |
| `GET /me` | none | `200`, unchanged |
| `PUT /me/password` | `{"current_password", "new_password"}` | `204`. Other sessions of the account end. |
| `GET /me/tokens` | none | `200` list of `TokenResource` |
| `POST /me/tokens` | `{"name", "expires_at"?}` | `201` `CreatedTokenResource` |
| `DELETE /me/tokens/{token_id}` | none | `204` |

- A wrong `current_password` answers `403`, code `current-password-refused`.
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
| `DELETE /accounts/{name}` | none | `200` `{"placeholder": "Deleted account <identifier>"}` |
| `GET /accounts/{name}/tokens` | none | `200` list of `TokenResource` |
| `DELETE /accounts/{name}/tokens/{token_id}` | none | `204` |

- A caller that is not an administrator receives `403`, code `forbidden`, on every route of this
  table.
- An unknown name answers `404`, code `account-not-found`.
- A name that exists, without regard to case, answers `409`, code `account-name-taken`.
- A name that does not match the name rule answers `422`, code `account-name-invalid`.
- Any change to an account that the environment manages answers `409`, code
  `environment-managed-account`. The message names `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` or
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`. The same code answers `PUT /me/password`,
  `POST /me/tokens` and `DELETE /me/tokens/{token_id}` when the caller is `admin`.
- Every mutation route of this document accepts an optional `reason` in its body or, for
  `DELETE`, in the query. It is recorded in the audit event. A `reason` that contains a secret
  the process knows, or the value the request presented, answers `422`, as on the existing
  routes.

## What no answer contains

- A password, in any form.
- A password hash, a token digest or a session digest.
- A token value, except in the `201` answer to `POST /me/tokens`.
- A session token, except in the `200` answer to `POST /auth/sign-in`.
