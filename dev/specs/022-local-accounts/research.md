# Research: Sign in to Sync with a local account

**Date**: 2026-11-17 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Each entry states a decision, the reason and the alternatives. File and line references are to
the branch `feature/v3-develop` at commit `7987596f`.

## What the code does today

- **One resolver, one protocol.** `create_app` receives a `PrincipalResolver`
  (`infrahub_sync/service/auth.py:24`) with two members: `resolve(token)` and `secret_values`.
  The dependency `authenticate` (`infrahub_sync/service/app.py:361`) reads the `Authorization`
  header and calls `resolve`. `build_app` (`infrahub_sync/service/serve.py:63`) builds the
  resolver from `EnvironmentPrincipalResolver.from_environment`.
- **A principal is a name and a flag.** `Principal(actor, administrator)`. Every route receives
  it. Rights are checked in two places: `service.py:1776` (only the account that started a run,
  or an administrator, acts on it) and `config_routes.py:484` and `:505` (administrator only).
- **Records hold the name as text.** The product store has no account table. The name is in
  `product_runs.actor`, `product_runs.approved_by`, `mutation_receipts.actor`,
  `audit_events.actor`, `audit_event_resources` (rows with `kind = 'account'`) and
  `configuration_versions.registered_by`.
- **The product store runs on two databases.** `_RelationalRunStore`
  (`infrahub_sync/product_store/store.py:796`) is used by `SQLiteRunStore` (tests and local use)
  and `PostgreSQLRunStore` (the service). The schema is one text of `CREATE TABLE IF NOT EXISTS`
  statements (`store.py:72`). Start-up creates missing tables and does not alter existing ones.
- **The CLI and the client read one variable.** `SyncClient.from_environment`
  (`infrahub_sync/client/client.py:140`) reads `INFRAHUB_SYNC_API_TOKEN`.
- **The web interface keeps a bearer token for the browser tab** in `sessionStorage`
  (`frontend/src/api/token.ts`) and checks it with `GET /me`
  (`frontend/src/components/token-entry.tsx`). Spec 017, decision D-2, states that the service
  keeps no browser session and sets no cookie.
- **The frontend has no test runner.** `frontend/package.json` has `lint`, `check`, `typecheck`
  and `build`.

## R1. Where accounts are stored

- **Decision**: five new tables in the product store, declared in the existing `_SCHEMA` text:
  `accounts`, `account_tokens`, `account_sessions`, `sign_in_attempts` and `deleted_accounts`
  ([data-model.md](data-model.md)).
- **Rationale**: the product store is the one database the Sync API already owns, on both
  SQLite and PostgreSQL. New tables are created by `CREATE TABLE IF NOT EXISTS` on a new and on an
  existing database, so no migration step is needed. No existing table changes.
- **Alternatives considered**:
    - A separate database for accounts. Rejected: a second connection profile and a second
      bootstrap step for five small tables.
    - An account identifier in each record in place of the name. Rejected for this feature: it
      changes six columns of existing tables, and start-up does not alter existing tables. See R7.

## R2. How a password is stored

- **Decision**: `hashlib.scrypt` from the Python standard library, with a random 16-byte salt for
  each password. The stored text holds the algorithm name, the cost parameters, the salt and the
  derived key, so the parameters can be raised later and an old hash still verifies. The cost
  parameters are taken from the scrypt recommendation of the OWASP Password Storage Cheat Sheet
  at implementation time, and a test fails if they fall below it.
- **Rationale**: no new dependency (constitution VII). scrypt is a memory-hard function made for
  passwords, and it is available on Python 3.11 to 3.13 with the OpenSSL that CPython ships.
- **Alternatives considered**:
    - `argon2-cffi`. A stronger default, but a new compiled dependency in the `service` extra.
      Rejected for now; the stored format names the algorithm, so a later change is possible.
    - `hashlib.pbkdf2_hmac`. Not memory-hard. Rejected.
- **Consequence**: one verification takes tens of milliseconds and tens of megabytes. Sign-in
  routes are synchronous FastAPI routes, which run in the thread pool, so a verification does not
  block the event loop. The refusal after repeated failures (R6) bounds the number of
  verifications an attacker can cause for one name.

## R3. What a request presents

- **Decision**: every request keeps presenting `Authorization: Bearer <value>`. The value is one
  of two kinds, told apart by a prefix:
    - an **API token** (`ifs_pat_…`), created by an account, valid until it expires or is deleted;
    - a **session token** (`ifs_ses_…`), returned by a sign-in with a password, valid until
      sign-out or until it has not been used for the idle period.

  Both are 32 random bytes from `secrets.token_urlsafe`. The store holds only the SHA-256 digest,
  with a unique index, so one lookup by digest finds the row.
- **Rationale**:
    - The CLI, scripts and the web interface already send a bearer value, so `authenticate` and
      every route stay as they are (FR-005, FR-015, SC-006).
    - A random 256-bit value does not need a slow hash: it cannot be guessed, and a fast digest
      keeps the cost of each request to one indexed read.
    - Spec 017 decided against a cookie. A session token in `sessionStorage` follows that
      decision, and needs no protection against cross-site request forgery.
- **Alternatives considered**:
    - A session cookie for the web interface. Rejected: reverses spec 017 D-2 and adds
      cross-site request forgery protection to every mutation route.
    - A signed token that the API verifies without the store (JWT). Rejected: FR-027 requires
      that the next request of a disabled account is refused, which needs a read of the store in
      any case.
- **The token of `admin`** is the value the operator sets in the environment, with no prefix,
  because the operator chooses it. It is stored as a digest like any other token.

## R4. How the caller of a request is resolved

- **Decision**: a new `StoreAccountResolver` implements the existing `PrincipalResolver`
  protocol. `resolve(value)`:
    1. computes the digest and reads the token row or the session row with its account;
    2. refuses a disabled account, an expired token and a session idle for longer than the idle
       period;
    3. records the time of use, at most once a minute for each token or session;
    4. returns `Principal(actor=<account name>, administrator=<level>)`.

  `secret_values` returns the password and the token of `admin` from the environment, which are
  the only sign-in secrets the process knows in clear text.
- **Rationale**: the protocol is the boundary that FR-005 asks to keep. Routes, rights checks and
  records keep using `Principal`. A later sign-in method adds a resolver, not a route change.
- **Redaction**: today `secret_values` lists every bearer token, and the service refuses a
  `reason` that contains one. With stored digests the process cannot list the tokens. The
  dependency `authenticate` therefore passes the presented value to the request scope, and the
  existing check of free-text parameters also refuses a parameter that contains the presented
  value. See [`secret-redaction.md`](../../../docs/docs/development/guidelines/secret-redaction.md).
- **Expired token**: the answer is `401` with the code `token-expired`, as the spec's edge case
  asks. Only a caller that holds the value receives it.

## R5. The `admin` account

- **Decision**: two variables in the environment of `sync-api`:
    - `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`, required, at least 12 characters;
    - `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, optional, at least 16 characters, as a principal token
      today (`auth.py:36`).

  `build_app` applies them before it builds the resolver:
    - it creates the row `admin` when it is missing, as an active administrator marked
      `environment_managed`;
    - when the stored hash does not verify the password of the environment, it stores a new hash
      and deletes the sessions of `admin`;
    - it replaces the one environment-managed token row of `admin` with the digest of the
      variable, or deletes that row when the variable is empty.

  Every account route refuses a change to an `environment_managed` account with `409` and the
  code `environment-managed-account`, and the message names the variable.
- **Rationale**: decision D-2 of the spec. Applying at the start of `sync-api`, and not in
  `sync-bootstrap`, means a restart of `sync-api` alone is enough to rotate or recover. The
  operation gives the same result when two `sync-api` processes start together.
- **The names** include `ACCOUNT` because `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` already exists
  and names the database administrator.
- **Alternatives considered**: the names `INFRAHUB_SYNC_INITIAL_ADMIN_*`, as in Infrahub.
  Rejected: "initial" says the value applies once, which is the opposite of D-2.

## R6. The refusal after repeated failures

- **Decision**: a table `sign_in_attempts` keyed by the lower-case name. A failed sign-in adds
  one to the count of the current 15-minute window. When the count reaches 5, the row gets a
  `refused_until` time 15 minutes later. While that time is in the future, a sign-in for the name
  is refused before the password is checked. A successful sign-in deletes the row.
- **A name that has no account** is counted in the same way, and its sign-in runs one password
  verification against a fixed hash. So the answer and its duration do not show whether the
  account exists (FR-003).
- **Bound on the table**: a name longer than 64 characters is refused before any write. Each
  failed sign-in also deletes the rows whose window ended more than a day ago.
- **Rationale**: the count must survive a restart and be shared by several `sync-api` processes,
  so it is in the store and not in memory.
- **Alternatives considered**: a limit for each client address. Rejected: the Compose deployment
  is behind a proxy or bound to the loopback address, so the address the API sees is often the
  same for every person.

## R7. How a deletion removes the name from past records

- **Decision**: the deletion is one transaction of the store that:
    1. chooses a new identifier of 8 hexadecimal characters that `deleted_accounts` does not hold,
       and forms the placeholder `Deleted account <identifier>`;
    2. replaces the name with the placeholder in `product_runs.actor`,
       `product_runs.approved_by`, `mutation_receipts.actor`, `audit_events.actor`,
       `audit_event_resources.resource_id` (rows with `kind = 'account'`) and
       `configuration_versions.registered_by`;
    3. deletes the tokens, the sessions, the `sign_in_attempts` row and the account row;
    4. inserts the identifier and the time into `deleted_accounts`, which holds no name.
- **Rationale**:
    - Records keep one text column for the account, as today, so no existing table changes and no
      read path changes: the API returns the placeholder in `actor` with no extra code (FR-031).
    - An account name cannot contain a space (R8), so a placeholder is never equal to a name.
      A new account that takes the old name does not match the placeholder, so the existing
      rights check `run.actor != principal.actor` refuses it (FR-032) with no extra rule.
    - The identifier is the same on every record of the account, because one statement set
      writes it (FR-031).
- **Alternatives considered**:
    - Keep an account identifier in each record and resolve the name when reading. Cleaner in the
      long term, but it changes six existing columns and every read path, and start-up does not
      alter existing tables. Rejected for this feature.
    - Keep the name and mark the account deleted. Rejected: the name stays in the store, against
      FR-030 and SC-007.
- **Risks to verify in implementation**:
    - **A run in progress.** The worker writes an event when a stage ends, and takes the name
      from the request's receipt or from the run (`service/flow.py:1592`). If it read the name
      before the deletion, it would write the name after the deletion. The store methods that
      save a stage result must take the actor from the stored receipt or run row inside their own
      transaction. A test deletes the account between the claim and the commit of a stage and
      searches the store for the name.
    - **The SQLite update trigger on `mutation_receipts`** (`store.py:242`) guards the resource a
      receipt points to. The replacement of `actor` must pass it. A test covers both databases.
    - **Free text.** A `reason` or a `registration_reason` that a person typed can contain the
      name. The deletion does not rewrite free text. The quickstart and the documentation say so,
      and the search of SC-007 excludes the free-text columns.
    - **`product_runs.audit_links`** is a JSON text. Implementation checks whether it can hold an
      account name; if it can, the deletion rewrites it too.

## R8. Account names

- **Decision**: a name matches `^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$`. The store keeps the name as
  typed, for display and for records, and a lower-case key with a unique index. `admin` is
  reserved by the row that the environment manages.
- **Rationale**: the documentation already shows an email address as a principal name
  (`docs/docs/compose-deployment.mdx:570`). No space keeps a name different from every
  placeholder (R7) and safe in a URL path.
- **A deleted name is free again**, because the deletion removes the row. A disabled account
  keeps its row, so its name stays taken.

## R9. Sessions, sign-out and password changes

- **Decision**:
    - A session ends at sign-out, after `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` without use (default
      28800, which is 8 hours), when its account is disabled or deleted, and when the password of
      its account changes, except the session that made the change.
    - Disabling an account deletes its tokens and its sessions. Enabling it therefore restores
      nothing (FR-029).
    - Changing a password does not delete the tokens of the account (User Story 3, scenario 1).
- **The CLI keeps the session token in a file** `sessions.json` in the user configuration
  directory (`$XDG_CONFIG_HOME/infrahub-sync/`, or the platform equivalent), with mode `0600`,
  keyed by the address of the Sync API. `INFRAHUB_SYNC_API_TOKEN`, when set, is used instead and
  the file is not read. This keeps every script unchanged.
- **The web interface keeps the session token** where it keeps the token today, in
  `sessionStorage` for the tab.
- **Alternatives considered**: the operating system keyring for the CLI. Rejected: a new
  dependency, and no keyring exists in a container.

## R10. Routes and rights

- **Decision**: the routes of [contracts/http-api.md](contracts/http-api.md), all under `/api`:
  `POST /auth/sign-in`, `POST /auth/sign-out`, `/me/password`, `/me/tokens`, `/accounts` and
  `/accounts/{name}` with their sub-resources.
- **No `Idempotency-Key`** on the account routes. Creating an account that exists answers `409`,
  and the other actions give the same result when repeated. The header stays required where it
  is today.
- **`reason` is optional** on the account routes, and is recorded in the audit event when given.
- **Audit**: each account action writes an `audit_events` row through the existing
  `record_audit` path, with an `event_type` of the form `infrahub_sync.account.<action>` and a
  related resource of kind `account`. A refused sign-in writes the existing authentication
  refusal record, without the name that was tried.

## R11. What changes in the deployment and the tests

- **Removed**: `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` and `EnvironmentPrincipalResolver`, from
  `docker-compose.yml`, `development/docker-compose.yml`,
  `infrahub_sync/testcontainers/docker-compose.yml`, `infrahub_sync/testcontainers/helpers.py`,
  the `tasks/` that read it, and six documentation pages.
- **The Compose `cli` service** keeps `INFRAHUB_SYNC_API_TOKEN`, which now must equal
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, or hold a token that an account created.
- **The development stack** sets the `admin` password and token to fixed development values, so
  `infrahub-sync-dev-token` keeps working.
- **Spec 018 (Helm chart)** is on another branch and names the removed variable. The branch that
  merges second updates the chart values. This is recorded as a task, not done here.

## R12. Decision records and rules to write

- A new decision record: sign-in uses local accounts in the product store, and the `admin`
  account is defined by the environment.
- A new decision record: deleting an account replaces its name in past records.
- `AGENTS.md`: the Sync frontend rule is replaced (FR-036), and the CLI command list gains the
  new commands.
- Constitution principle VI is not changed by this feature. It governs the credentials Sync uses
  for other systems. A password hash of a person who signs in is not such a credential. The
  rework of principle VI belongs to the specification of stored credentials.
