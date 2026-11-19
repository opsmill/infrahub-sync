---
title: "Local accounts and sign-in"
---

## Local accounts and sign-in

<!-- Extracted from dev/specs/archive/022-local-accounts on 2026-11-18 -->

Every authenticated request to the Sync API acts as an account that the product store holds. A person signs
in with an account name and a password and gets a session token. A script presents an API
token that an account created for itself. The `admin` account is defined by the environment of
`sync-api`. This page describes how the code does this. For the steps an operator follows, see
[Manage accounts](../../guides/accounts.mdx); for the routes and the commands, see the
[Sync HTTP API reference](../../reference/sync-http-api.mdx) and the
[CLI reference](../../reference/cli.mdx).

The decisions behind this design are in
[ADR 0049](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md)
(accounts, passwords, tokens, sessions, the `admin` account and the refusal after failed
sign-ins) and
[ADR 0050](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0050-a-record-holds-an-account-identifier-and-a-deletion-empties-the-account-row.md)
(account identifiers in records, and the deletion of an account).

### Where the code is

- `infrahub_sync/product_store/accounts.py` holds the SQL statements of the four account
  tables. Each function takes the cursor of its caller. `store.py` declares the tables in its
  schema text and wraps each action in one transaction, together with its audit event.
- `infrahub_sync/service/auth.py` holds `Principal`, the `PrincipalResolver` protocol,
  `StoreAccountResolver` and `apply_admin_account`.
- `infrahub_sync/service/credentials.py` hashes and verifies passwords, makes token values,
  computes digests, and holds the two limits on password checks (`VerificationGate` and
  `one_check_per_name`).
- `infrahub_sync/service/settings.py` reads and checks the variables
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`, `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` and
  `INFRAHUB_SYNC_SESSION_IDLE_SECONDS`.
- `infrahub_sync/service/account_routes.py` declares the routes; each route calls one function
  of `infrahub_sync/service/account_actions.py`, which checks the request and the rights, calls
  the store and builds the answer.
- `infrahub_sync/service/presentation.py` holds `AccountNameMiddleware`, which replaces account
  identifiers with names in every JSON answer.
- `infrahub_sync/cli_accounts.py` holds the commands `login`, `logout`, `password`, and the
  `tokens` and `accounts` groups. `infrahub_sync/cli_session.py` reads and writes the session
  file of the CLI.
- In `frontend/src/`, `components/sign-in.tsx` is the sign-in form, `api/token.ts` keeps the
  session token, `routes/account.tsx` is the "My account" page, `routes/accounts.tsx` is the
  "Accounts" page for administrators, and `features/accounts/shared.tsx` holds the parts both
  pages use.

### The four tables

- `accounts`: one row for each account, deleted or not. A row holds the account identifier
  (`acct_` and 32 hexadecimal characters), a short identifier of 8 hexadecimal characters, the
  name as typed, a lower-case name key with a unique index, the password hash, the flags
  `administrator`, `disabled` and `environment_managed`, the time of the last password change,
  and the deletion time.
- `account_tokens`: the API tokens. A row holds the SHA-256 digest of the token value (unique
  index), the account, a name that is unique for the account, an optional expiry time, the time
  of last use, and the flag `environment_managed` for the token of `admin`.
- `account_sessions`: the sessions. A row holds the digest of the session token, the account and
  the time of last use.
- `sign_in_attempts`: the count of failed sign-ins for each lower-case name, the start of its
  15-minute window, and the time until which the name is refused.

The store never holds a token value or a password, only digests and hashes. The value of a new
API token is in the answer of its creation only.

### How a request is resolved

The dependency `authenticate` of `app.py` reads the header `Authorization: Bearer <value>` and
calls `StoreAccountResolver.resolve`:

1. It computes the SHA-256 digest of the value and calls `resolve_digest`, which looks for the
   digest among the API tokens first, then among the sessions. The table that holds the digest
   gives the kind of value. The prefixes `ifs_pat_` and `ifs_ses_` do not decide the kind,
   because the token of `admin` has neither.
2. `resolve_digest` records the time of use when the stored time is empty or more than one
   minute old, and returns the time stored before this request.
3. The resolver refuses a disabled or deleted account and an unknown value with
   `unauthenticated`, and an API token after its expiry time with `token-expired`.
4. A session not used for longer than `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` (default 28,800, from
   60 to 999,999,999,999) is deleted with a second store call, and the request is refused.

The resolver caches nothing, so the next request of a disabled account is refused. The accepted
`Principal` holds the account identifier in `actor`, the account name in `name`, the kind of
value, and the session identifier for a session. Every write records `actor`, and every rights
check compares identifiers.

### Passwords and the limit on password checks

`hash_password` uses `hashlib.scrypt` with N = 2^15, r = 8, p = 3, a 16-byte salt, a 32-byte key
and `maxmem` of 64 MiB, and stores `scrypt$<N>$<r>$<p>$<salt>$<key>`. `verify_password` reads
the parameters from the stored text, so a later change of the parameters keeps old hashes
working. A stored text that is not such a hash, or whose parameters need more than 64 MiB of
memory, a `p` above 16 or a key above 128 bytes, verifies no password, and
`account_actions.py` writes the log line
`account_password_hash_unreadable` with the account identifier.

A password has 1 to 1,024 characters that UTF-8 can encode. A new password also has at least 12
characters. These limits are checked before any hash is computed.

Two limits keep the memory of password hashing bounded:

- `PASSWORD_VERIFICATION_GATE` lets at most 2 `scrypt` computations run at the same time in a
  process: the check at sign-in, the check of a current password, and the hash of every new
  password. A request that waits more than 5 seconds for its turn answers `503 sign-in-busy`.
- `one_check_per_name` lets one password check for each lower-case name run at a time in a
  process. A request that waits more than 5 seconds answers `503 sign-in-busy`.

### Sign-in and the refusal after failed sign-ins

`sign_in` in `account_actions.py` runs these steps:

1. A name that the store cannot count (`is_countable_name`: empty, longer than 64 characters
   or 64 in lower case, a NUL character or a character that UTF-8 cannot encode) or a password
   that no account can have (empty, longer than 1,024 characters or a character that UTF-8
   cannot encode) is refused at once. Nothing is read, counted or recorded. The account-name
   rule is not applied here: a name with a character that no account name can have, such as
   `!`, goes through the next steps and its failures are counted.
2. A name refused in `sign_in_attempts` is refused before any password check. The attempt is not
   counted and the refusal time does not move.
3. Under the lock of the name and a place of the gate, the refusal is read again, and the
   password is checked. A name that has no account is checked against a fixed hash
   (`no_account_hash`), so the answer and its duration do not show whether the account exists.
   A failure is counted before the lock is released.
4. The store inserts the session in one transaction with its audit event, after it locks the
   account row and checks that the account is still active with the password that was checked.

Every sign-in refused for its credentials or for a refusal of its name gets the same answer,
`401` with one message, and writes an authentication refusal record without the name, except in
step 1. A sign-in that waits more than 5 seconds for the lock of its name or a place of the gate
answers `503 sign-in-busy` instead, and is not counted.

The constants are in `product_store/accounts.py`: 5 failures (`SIGN_IN_FAILURE_LIMIT`) within
15 minutes refuse the name for 15 minutes. A wrong current password in `PUT /me/password`
counts in the same way. A successful sign-in, a new password and the deletion of the account
delete the row. An administrator ends a refusal with `POST /accounts/{name}/unlock`, for
`admin` as well, and a start of `sync-api` ends the refusal of `admin`.

The table holds at most 10,000 rows (`SIGN_IN_ATTEMPTS_BOUND`). To add a row when it is full,
the store deletes rows whose window has passed, then rows with the oldest window, and never a
row whose refusal is in force. When only refusals in force remain, the new name is not
recorded. Because the lock of a name is held in the memory of one process, P processes can
check up to 5 + (P − 1) passwords for each refusal.

### The `admin` account

At each start, `build_app` in `serve.py` reads the `admin` variables, then
`apply_admin_account` makes the row `admin` match them in one transaction:

- it creates the row when it is missing, as an active administrator with
  `environment_managed` set;
- when the stored hash does not verify the password of the environment, it stores a new hash
  and deletes the sessions of `admin`;
- it keeps one environment-managed API token for `admin`, the digest of
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, or none when the variable is not set;
- it deletes the failed sign-ins of `admin`.

On PostgreSQL the transaction takes the advisory lock of schema initialization, and on SQLite
the write lock, so two processes that start together run it one after the other. `serve.py`
then removes `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` from the environment of the process before
it collects the secret values, so the password is in no free-text check. The token of `admin`
stays in `resolver.secret_values`.

`settings.py` stops the start when a variable breaks a rule: the password has 12 to 1,024
characters that UTF-8 can encode; the token has at least 16 printable ASCII characters, no space
at either end, and does not start with `ifs_pat_` or `ifs_ses_`. A request presents the token in
an HTTP header, which the Sync API reads as Latin-1 and without surrounding spaces, so no other
token could match. The message names the variable and never the value.

Every account route that would change the password, the tokens, the level or the state of an
`environment_managed` account, or delete it, answers `409 environment-managed-account`. Unlock
is accepted.

### Account names in answers

Records hold account identifiers. `AccountNameMiddleware` replaces them in every JSON answer of
the API application, including answers replayed for an `Idempotency-Key` from
`mutation_receipts`:

- the values of `actor`, `approved_by` and `registered_by`, at any depth;
- the `id` of a related resource of kind `account`, and of a primary resource of kind
  `account`.

An identifier of an active or disabled account becomes its name, and an identifier of a deleted
account becomes `Deleted account <short identifier>`. A value that is not an account identifier
is left as stored. `AccountNameMiddleware` reads all the accounts of one answer with one store
call. It passes on unchanged an answer that is not `application/json` or that carries a `Digest` header,
which is how an artifact is served.

`test_every_response_model_with_an_account_field_has_a_route_case` in
`tests/service/test_account_presentation.py` lists every response model that declares one of
these fields, and fails when a model has no route case in the test.

### Free text and token values

The store holds only token digests, so the service cannot list token values to look for them in
free text. `is_free_text` in `service.py` refuses a value that holds a secret value of the
process, or the prefix `ifs_pat_` or `ifs_ses_`. The refusal is `422 secret-parameter-refused`.
It applies to every `reason`, to the other free-text parameters of the run routes, and to the
name of a new API token. An account name that holds a token prefix is
refused as `422 account-name-invalid`.

### Audit events

Each accepted account action writes one audit event, of type `infrahub_sync.account.<action>`
(for example `signed_in`, `created`, `token_created` or `deleted`), in the store transaction of
the action. The related resources of kind `account` hold the identifiers of the account that
acted and of the account acted on. Three kinds of refusal write `infrahub_sync.account.refused`
on their own: a caller that is not an administrator (`refused-authorization`), a free text that
holds a secret or a token prefix (`refused-secret-parameter`), and a wrong current password
(`refused-current-password`). Other refusals, such as an invalid or taken name or a change to
the `admin` account, write no event. `update_account` writes a change of state and a change of
level in two transactions, so a failure of the second keeps the first and its event.

### The session file of the CLI

`login` saves the session token and the account name in `sessions.json`, in the directory that
`typer.get_app_dir("infrahub-sync")` returns. The directory has mode `0700`. A write goes to a
temporary file with mode `0600`, which then replaces the file. A write holds the lock file
`sessions.json.lock` from its read to its replacement, so two commands that write at the same
time do not lose each other's entry. Entries are keyed by the
normalized address of the Sync API (`normalize_address`). A file that is missing or cannot be
read counts as no saved session.

`cli.py` presents `INFRAHUB_SYNC_API_TOKEN` when it is set and not empty, and reads the file
only otherwise. `SyncClient.from_environment` reads no file, so a script that uses the client as
a library depends on the variable only. When the Sync API refuses a saved session with `401`,
the CLI removes the entry, but only while it still holds the refused token, so a session that a
`login` saved meanwhile stays.

### The web interface

The web interface sets no cookie. `api/token.ts` keeps the session token in `sessionStorage`
under the key `infrahub-sync.session-token`, so it ends when the browser tab closes. The
interface has no field for an API token.

- While the tab holds no session, every page shows the sign-in form of `sign-in.tsx`. It shows
  one message for every refused sign-in, and other messages for `503 sign-in-busy` and for a
  Sync API that does not answer.
- `api/client.ts` removes the token when the Sync API answers `401` to a request that carried
  it, and clears the cached answers, so the sign-in form opens. The header of
  `routes/__root.tsx` names the signed-in account from `GET /me` and offers sign-out, which calls
  `POST /auth/sign-out` and removes the token even when the call fails.
- The link to the "Accounts" page is shown only when `GET /me` answers that the account is an
  administrator. The Sync API refuses the account routes to other accounts.
- Run, approval and configuration version pages show the names that the Sync API returns.
  The rule for naming accounts in the interface is in `AGENTS.md`, section "Sync frontend".
