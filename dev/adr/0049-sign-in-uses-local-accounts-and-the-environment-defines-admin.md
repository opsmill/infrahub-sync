# 49. Sign-in uses local accounts in the product store, and the environment defines the `admin` account

**Status**: Accepted
**Date**: 2026-11-18
**Source**: `dev/specs/022-local-accounts/spec.md` (decisions D-1 to D-5 and D-9, FR-020 to
FR-023, Assumptions), `dev/specs/022-local-accounts/research.md` (R2 to R6, R9),
`dev/specs/022-local-accounts/plan.md` (Risks, Complexity Tracking)

## Context

The Sync API resolved each request from the variable `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, a
list of names and tokens in the environment of `sync-api`. To add a person, remove a person or
replace a lost token, an operator edited a deployment file and restarted the service. Each token
was written in that file in clear text, and a person signed in with no password.

A team needs to add and remove people without a change to the deployment, to give a script its
own token, and to recover access when every other administrator is locked out.

## Decision

- **Accounts are stored in the product store.** Four tables hold them: `accounts`,
  `account_tokens`, `account_sessions` and `sign_in_attempts`. An account has a name, a
  password, a level (administrator or not) and a state (active or disabled). An administrator
  creates, disables, enables and deletes accounts through the Sync API and the CLI.
- **The environment defines the `admin` account.** `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` is
  required and has at least 12 characters. `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` is optional, has
  at least 16 characters and must not start with `ifs_pat_` or `ifs_ses_`.
    - At each start, `sync-api` makes the row `admin` match these variables in one transaction:
      it creates the row when it is missing, stores a new password hash and deletes the sessions
      of `admin` when the password changed, sets or deletes the one token that the environment
      manages, and deletes the failed sign-ins counted for `admin`. On PostgreSQL the
      transaction takes the advisory lock of schema initialization, so two processes that start
      together do not conflict.
    - The process then removes the password variable from its own environment, so the password
      is in no list of secret values that a free-text check compares a `reason` with.
    - Every account route refuses a change to `admin` with `409 environment-managed-account`.
      To rotate the password or to recover access, an operator edits the variable and restarts
      `sync-api`.
- **A request presents a bearer value.** Each request keeps the header
  `Authorization: Bearer <value>`. The value is one of two kinds:
    - an API token (`ifs_pat_` and 32 random bytes), which an account creates for itself and
      which is valid until it expires or is deleted;
    - a session token (`ifs_ses_` and 32 random bytes), which a sign-in with a password
      returns, and which ends at sign-out, after `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` without
      use (default 28,800 seconds, 8 hours), when the account is disabled or deleted, and when
      its password changes (except the session that made the change).

  The store holds only the SHA-256 digest of each value, with a unique index. Each request reads
  the store once to resolve its value, and no result is cached, so the next request of a
  disabled account is refused. The value of an API token is returned once, in the answer of its
  creation.
- **Passwords are hashed with scrypt** from the Python standard library
  (`hashlib.scrypt`): N = 2^15, r = 8, p = 3, a random 16-byte salt and a 32-byte derived key,
  with `maxmem` set to 64 MiB. The stored text names the algorithm and its parameters, so the
  parameters can be raised later and an old hash still verifies. At most two password checks
  run at the same time in one `sync-api` process; a sign-in that waits more than 5 seconds for
  its turn gets `503 sign-in-busy`. Password hashing therefore uses at most about 64 MiB,
  whatever the number of sign-in requests.
- **Repeated failed sign-ins refuse the name for a period.** After 5 failed sign-ins for one name
  within 15 minutes, a sign-in for that name is refused for 15 minutes before the password is
  checked, and the account is not disabled. A wrong current password in a password change counts
  as a failure. An attempt made during the refusal does not count and does not extend it. A name
  that has no account is counted in the same way and runs one password check against a fixed
  hash, so the answer does not show whether the account exists. An administrator ends the refusal
  for another account; a restart of `sync-api` ends it for `admin`. Requests with a token are not
  affected. The table holds at most 10,000 rows.
- **No cookie.** The service sets no cookie. The CLI keeps its session token in `sessions.json`
  in its application directory, with mode `0600`. The web interface keeps the bearer value it
  uses in `sessionStorage`, for the browser tab only
  ([ADR 0043](0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md),
  `dev/specs/017-serve-web-interface/`); its sign-in with a password (spec User Story 2, not
  delivered yet) is to keep the session token in the same place.
- **`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is removed in the same release, with no transition
  period.** The service no longer reads it. This rests on the assumption that V3 has no
  production deployment (`spec.md`, Assumptions).

## Consequences

- A team adds a person, replaces a lost password or disables an account with one command, with
  no edit to a deployment file and no restart.
- The only sign-in secrets in a deployment file are the password and the optional token of
  `admin`. An operator who can edit the environment of `sync-api` can always recover
  administrator access.
- Scripts keep presenting a token in `INFRAHUB_SYNC_API_TOKEN`; only its value changes.
- Every request opens one more connection to the product store, because the store has no
  connection pool.
- A person who knows an account name can keep that account from signing in with its password,
  by failing 5 times in each 15-minute window. Requests with the account's API tokens still
  work, and an administrator can end the refusal.
- A deployment that set only the removed variable has no working token after the upgrade until
  it sets the `admin` variables.
- Sync displays an API token value once, to the account that created it. Principle VI of the
  constitution is amended to allow this.

## Alternatives considered

- **A session cookie for the web interface.** Rejected: it reverses the decision of spec 017 that
  the service sets no cookie, and it would need protection against cross-site request forgery.
- **Signed tokens that the API verifies without reading the store (for example JWT).**
  Rejected: the next request of a disabled or deleted account must be refused, which needs a
  read of the store.
- **argon2 (`argon2-cffi`).** Rejected for now: a new compiled dependency in the `service` extra.
  The stored hash names its algorithm, so a later change is possible.
- **The strongest scrypt setting of the OWASP Password Storage Cheat Sheet, N = 2^17.**
  Rejected: one check needs 128 MiB, and `docker-compose.yml` sets no memory limit for
  `sync-api`.
- **A limit for each client address.** Rejected: the Compose deployment is behind a proxy or
  bound to the loopback address, so the API often sees the same address for every person.
- **An "initial" password, applied once (`INFRAHUB_SYNC_INITIAL_ADMIN_*`).** Rejected: the
  `admin` account must follow the environment at every start, so that an operator can rotate the
  password and recover access by a restart.
