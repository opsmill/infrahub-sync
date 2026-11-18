# Contract: Sync HTTP API

All routes are under `/api`. The API stays `v3-unstable`. Every route except `POST /auth/sign-in`
requires `Authorization: Bearer <API token or session token>`. Resource shapes are in
[data-model.md](../data-model.md).

This contract was brought in line with the implementation on 2026-11-18. The route list matches
the paths that `tests/service/test_http_api.py` pins in the OpenAPI document.

## Existing routes

No change of path, request or response. `actor`, `approved_by` and `registered_by` in every
response hold an account name, or `Deleted account <short identifier>`. This is also true of an
answer that is replayed for an `Idempotency-Key`.

- The message of the `already-approved` refusal no longer names the account. The approval in its
  details does.
- Every `reason` has a maximum length of 512 characters. A longer one answers `422`.
- A `reason` that contains a secret value of the process, `ifs_pat_` or `ifs_ses_` answers
  `422`, code `secret-parameter-refused`.

| Case | Answer |
| --- | --- |
| No value, an unknown value, a deleted token, a disabled or deleted account | `401`, code `unauthenticated` |
| An API token after its expiry date | `401`, code `token-expired` |
| A session idle for longer than the idle period | `401`, code `unauthenticated`. The session is deleted. |

## Sign-in and sign-out

| Route | Who | Request | Answer |
| --- | --- | --- | --- |
| `POST /auth/sign-in` | anyone | `{"name", "password"}` | `200` `SessionResource` |
| `POST /auth/sign-out` | a session | none | `204`. The session token is refused afterwards. |

- A wrong name, a wrong password, a disabled account and a name refused after repeated failures
  all answer `401`, code `sign-in-refused`, with one fixed message. So do a name and a password
  that no account can have (an empty value, a name longer than 64 characters, a password longer
  than 1,024 characters), with no password check and no count.
- A sign-in that waits more than 5 seconds answers `503`, code `sign-in-busy`, and is not
  counted. The wait is for one of the two places of the password checks of the process, or for
  the end of another check of the same name in the process (research R6).
- `POST /auth/sign-out` with an API token answers `422`, code `not-a-session`.
- A body that does not match the schema answers `422`, code `request-invalid`.

Changed during implementation: the first text named only the wait for a place of the password
checks. The lock of one check for each name, added after the review, answers the same code.

## The caller's own account

| Route | Request | Answer |
| --- | --- | --- |
| `GET /me` | none | `200`, unchanged. `actor` is the account name. |
| `PUT /me/password` | `{"current_password", "new_password", "reason"?}` | `204`. Other sessions of the account end; a request made with an API token keeps no session. API tokens stay. |
| `GET /me/tokens` | none | `200` list of `TokenResource`, oldest first |
| `POST /me/tokens` | `{"name", "expires_at"?, "reason"?}` | `201` `CreatedTokenResource` |
| `DELETE /me/tokens/{token_id}` | query `reason`? | `204` |

- A wrong `current_password` answers `403`, code `current-password-refused`, and counts as a
  failed sign-in attempt. While the name is refused, the route answers the same code without a
  check.
- A `new_password` shorter than 12 characters answers `422`, code `password-too-short`; longer
  than 1,024 characters, `password-too-long`; with a character that UTF-8 cannot encode,
  `password-invalid`.
- A route that checks or hashes a password answers `503`, code `sign-in-busy`, when it waits
  more than 5 seconds, as a sign-in does.
- A token name has 1 to 64 printable characters with no space at either end. A name that
  contains a secret value of the process or a token prefix answers `422`, code
  `secret-parameter-refused`. A name the account already uses answers `409`, code
  `token-name-taken`.
- An `expires_at` must carry a time zone. One that is not in the future answers `422`, code
  `expiry-in-the-past`.
- A token ID that the account does not have answers `404`, code `token-not-found`.
- `admin` receives `409`, code `environment-managed-account`, on `PUT /me/password` (the message
  names `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`), `POST /me/tokens` and
  `DELETE /me/tokens/{token_id}` (the message names `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`).

## Accounts (administrator only)

| Route | Request | Answer |
| --- | --- | --- |
| `GET /accounts` | query `state=active\|disabled\|all` (default `all`) | `200` list of `AccountResource`, by name |
| `POST /accounts` | `{"name", "password", "administrator"?, "reason"?}` | `201` `AccountResource` |
| `GET /accounts/{name}` | none | `200` `AccountResource` |
| `PATCH /accounts/{name}` | `{"disabled"?, "administrator"?, "reason"?}`, at least one of the first two | `200` `AccountResource` |
| `PUT /accounts/{name}/password` | `{"new_password", "reason"?}` | `204`. Every session of the account ends, and its refusal ends. |
| `POST /accounts/{name}/unlock` | optional body `{"reason"?}` | `204`. Ends a refusal after repeated failures, whether or not one is in force. |
| `DELETE /accounts/{name}` | query `reason`? | `200` `DeletedAccountResource`: `{"placeholder": "Deleted account <short identifier>"}` |
| `GET /accounts/{name}/tokens` | none | `200` list of `TokenResource`, oldest first |
| `DELETE /accounts/{name}/tokens/{token_id}` | query `reason`? | `204` |

- A caller that is not an administrator receives `403`, code `forbidden`, on every route of this
  table, before any other check of the request.
- `{name}` matches an account in any letter case. An unknown or deleted name answers `404`, code
  `account-not-found`.
- A name that exists, without regard to case, answers `409`, code `account-name-taken`. This is
  checked before the password rules.
- A name that does not match the name rule, that the product reserves, or that contains a token
  prefix answers `422`, code `account-name-invalid` (research R8).
- The password rules and the `503 sign-in-busy` answer of `PUT /me/password` apply to
  `POST /accounts` and `PUT /accounts/{name}/password`.
- In `PATCH /accounts/{name}`, `disabled: true` disables the account and deletes its tokens and
  sessions, and `disabled: false` enables it. `administrator` sets the level, from the next
  request of the account. A field whose value the account already has changes nothing. A body
  with neither field answers `422`, code `request-invalid`.
- A token ID that the account does not have answers `404`, code `token-not-found`.
- No answer contains an account identifier (`acct_…`).
- A change to the account that the environment manages answers `409`, code
  `environment-managed-account`:
    - `PUT /accounts/admin/password`: the message names `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`;
    - `DELETE /accounts/admin/tokens/{token_id}`: the message names
      `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`;
    - `PATCH /accounts/admin` and `DELETE /accounts/admin`: the message says that `admin` is
      always an active administrator that the environment defines.

  `POST /accounts/admin/unlock` is accepted.
- Every mutation route of this document accepts an optional `reason` of 1 to 512 printable
  characters with no space at either end, in its body or, for `DELETE`, in the query. It is
  recorded in the audit event. A `reason` that contains a secret the process knows, or a token
  prefix, answers `422`, code `secret-parameter-refused`, as on the existing routes.

Changed during implementation: the first text said every refusal for `admin` names a variable,
and refused every change to `admin`. No variable disables, deletes or sets the level of `admin`,
so those refusals say why instead; unlock changes nothing that the environment defines, so it is
accepted. The codes `password-too-long`, `password-invalid`, `token-not-found` and
`request-invalid` (for a `PATCH` with no change), the `reason` fields of every body, and the check
of token names were added during the implementation.

## What no answer contains

- A password, in any form.
- A password hash, a token digest or a session digest.
- A token value, except in the `201` answer to `POST /me/tokens`.
- A session token, except in the `200` answer to `POST /auth/sign-in`.
