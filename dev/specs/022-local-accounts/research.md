# Research: Sign in to Sync with a local account

**Date**: 2026-11-17 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Each entry states a decision, the reason and the alternatives. File and line references are to
the branch `feature/v3-develop` at commit `7987596f`. The entries R1, R2, R4 to R7 and R9 were
revised after the [critique](critiques/critique-20261117.md).

## What the code does today

- **One resolver, one protocol.** `create_app` receives a `PrincipalResolver`
  (`infrahub_sync/service/auth.py:24`) with two members: `resolve(token)`, which returns a
  `Principal` or nothing, and `secret_values`. The dependency `authenticate`
  (`infrahub_sync/service/app.py:361`) reads the `Authorization` header and calls `resolve`.
  `build_app` (`infrahub_sync/service/serve.py:63`) builds the resolver from
  `EnvironmentPrincipalResolver.from_environment`.
- **A principal is a name and a flag.** `Principal(actor, administrator)`. Rights are checked in
  two places: `service.py:1775` (only the account that started a run, or an administrator, acts
  on it) and `config_routes.py:464` and `:485` (administrator only).
- **Records hold the principal's name as text**, and so do stored answers:
    - columns: `product_runs.actor`, `product_runs.approved_by`, `mutation_receipts.actor`,
      `audit_events.actor`, `audit_event_resources.resource_id` (rows with `kind = 'account'`)
      and `configuration_versions.registered_by`;
    - `mutation_receipts.response_body`, which keeps the full answer to a request for a replay
      (`service.py:1612`, `config_routes.py:376`), with `actor`, `approved_by` or
      `registered_by` inside;
    - the message of the `already-approved` refusal (`product_store/models.py:325`).
- **About 50 writes carry the name after it was resolved**: in `service.py`, in
  `liveness.py:245` and `:260`, in `resume_view.py:281`, and in the worker, which builds its
  event before the commit (`flow.py:1570`, `:1280`).
- **What does not hold the name**: `product_runs.audit_links` (event identifiers only), Prefect
  tags and flow parameters (`orchestration.py:205`), and the log lines of
  `infrahub_sync/service`.
- **The product store runs on two databases.** `_RelationalRunStore`
  (`infrahub_sync/product_store/store.py:796`) is used by `SQLiteRunStore` and
  `PostgreSQLRunStore`. The schema is one text of `CREATE TABLE IF NOT EXISTS` statements
  (`store.py:72`), which adds a new table to an existing database. The store opens one
  connection for each call and has no pool (`store.py:820`).
- **The free-text check.** `collect_secret_values` (`infrahub_sync/execution.py:144`) collects
  every environment value whose name contains `PASSWORD` or `TOKEN`. A `reason` that contains a
  collected value, or a value of `resolver.secret_values`, is refused with `422`
  (`service.py:2083`). Most `reason` fields have no maximum length (`client/models.py:123`).
- **The CLI and the client read one variable.** `SyncClient.from_environment`
  (`infrahub_sync/client/client.py:140`) reads `INFRAHUB_SYNC_API_TOKEN`.
- **The web interface keeps a bearer token for the browser tab** in `sessionStorage`
  (`frontend/src/api/token.ts`). Spec 017, decision D-2, states that the service keeps no
  browser session and sets no cookie.
- **The Compose `cli` service** is read-only, keeps no volume and always sets
  `INFRAHUB_SYNC_API_TOKEN` (`docker-compose.yml:360`).
- **The frontend has no test runner.** `frontend/package.json` has `lint`, `check`, `typecheck`
  and `build`.

## R1. Where accounts are stored, and what a record holds

- **Decision**:
    - Four new tables in the product store, declared in the existing `_SCHEMA` text: `accounts`,
      `account_tokens`, `account_sessions` and `sign_in_attempts`
      ([data-model.md](data-model.md)).
    - **A record holds an account identifier, not a name.** Each account gets an identifier of
      the form `acct_` followed by 32 hexadecimal characters when it is created. The existing
      text columns and the stored answers hold this identifier. No existing table changes.
    - **The API turns an identifier into a name when it answers** (R7).
- **Rationale**:
    - A name can then change state without any record changing: a deletion empties one row.
    - An approval is never rewritten, as
      [ADR 0037](../../adr/0037-an-approval-is-a-record-on-the-run.md) requires.
    - Rights compare identifiers, so a new account that takes an old name has no right over old
      runs (FR-032), with no extra rule.
    - A write that lands after a deletion carries the identifier of the deleted account. It
      displays as the placeholder and gives no right to anyone, so no write needs a check.
- **Records made before this feature** hold a principal's name in those columns. A value that is
  not an account identifier is displayed as it is stored, and gives no right to any account. The
  spec assumes no production deployment exists (decision D-3), so this concerns development
  databases only.
- **Alternatives considered**:
    - Keep the name in records and rewrite it when the account is deleted. This was the first
      design. Rejected after the critique: it rewrites six columns and stored JSON, it changes
      `approved_by`, and every request in progress can write the old name after the deletion.
    - A separate database for accounts. Rejected: a second connection profile for four tables.

## R2. How a password is stored

- **Decision**: `hashlib.scrypt` from the Python standard library, with a random 16-byte salt for
  each password, and an explicit `maxmem`. The stored text holds the algorithm name, the cost
  parameters, the salt and the derived key, so the parameters can be raised later and an old
  hash still verifies.
- **Cost parameters**: N = 2^15, r = 8, p = 3, which is one of the scrypt settings the OWASP
  Password Storage Cheat Sheet lists, and needs about 32 MiB for one verification. Implementation
  confirms the setting against the current cheat sheet, and a test fails if the parameters in
  the code fall below the chosen setting.
- **At most two verifications run at the same time** in one `sync-api` process. A sign-in that
  waits more than 5 seconds for its turn answers `503` with the code `sign-in-busy`. So password
  hashing uses at most about 64 MiB, whatever the number of sign-in requests.
- **Rationale**: no new dependency (constitution VII). scrypt is a memory-hard function made for
  passwords. Without `maxmem`, `hashlib.scrypt` refuses these parameters; the reviewer measured
  this. Sign-in routes are synchronous FastAPI routes, which run in the thread pool, so a
  verification does not block the event loop.
- **Alternatives considered**:
    - The strongest OWASP setting, N = 2^17. It needs 128 MiB for one verification, and
      `docker-compose.yml` sets no memory limit for `sync-api`. Rejected.
    - `argon2-cffi`. A new compiled dependency in the `service` extra. Rejected for now; the
      stored format names the algorithm, so a later change is possible.
    - `hashlib.pbkdf2_hmac`. Not memory-hard. Rejected.

## R3. What a request presents

- **Decision**: every request keeps presenting `Authorization: Bearer <value>`. The value is one
  of two kinds, told apart by a prefix:
    - an **API token** (`ifs_pat_…`), created by an account, valid until it expires or is deleted;
    - a **session token** (`ifs_ses_…`), returned by a sign-in with a password, valid until
      sign-out or until it has not been used for the idle period.

  Both are 32 random bytes from `secrets.token_urlsafe`. The store holds only the SHA-256 digest,
  with a unique index, so one lookup by digest finds the row.
- **Rationale**:
    - The CLI, scripts and the web interface already send a bearer value, so every route keeps
      its path, request and response (FR-005, FR-015, SC-006).
    - A random 256-bit value does not need a slow hash: it cannot be guessed.
    - Spec 017 decided against a cookie. A session token in `sessionStorage` follows that
      decision, and needs no protection against cross-site request forgery.
- **Alternatives considered**:
    - A session cookie for the web interface. Rejected: reverses spec 017 D-2.
    - A signed token that the API verifies without the store (JWT). Rejected: FR-027 requires
      that the next request of a disabled account is refused, which needs a read of the store.
- **The token of `admin`** is the value the operator sets in the environment, because the
  operator chooses it. It must not start with `ifs_pat_` or `ifs_ses_` (R4). It is stored as a
  digest like any other token.

## R4. How the caller of a request is resolved

- **Decision**: a new `StoreAccountResolver` replaces `EnvironmentPrincipalResolver` behind the
  `PrincipalResolver` protocol. The protocol changes in two ways:
    - `resolve(value)` returns a result that is either a `Principal` or a refusal code
      (`unauthenticated` or `token-expired`), because nothing cannot express an expired token;
    - `Principal` gains `name`, the kind of value presented (API token or session) and, for a
      session, its identifier. `Principal.actor` holds the account identifier, which is what
      every existing write records and what the rights checks compare.
- **One store call for each request**: it reads the token row or the session row with its
  account, refuses a disabled or deleted account, an expired token and a session idle for longer
  than the idle period, and records the time of use when the last recorded use is more than a
  minute old. The store has no connection pool, so this is one more connection for each request.
  No result is cached, because a cache would accept a request after an account is disabled
  (FR-027).
- **`GET /me`** keeps its fields: `actor` is the account name, taken from `Principal.name`.
- **Free text** (FR-024, [`secret-redaction.md`](../../../docs/docs/development/guidelines/secret-redaction.md)):
  the service cannot list tokens it stores as digests. So:
    - a free-text parameter that contains `ifs_pat_` or `ifs_ses_` is refused with the existing
      `422`, whoever the token belongs to;
    - the token of `admin` stays in `secret_values`, as a principal token is today, and must not
      start with either prefix;
    - the password of `admin` is in no check (R5);
    - every `reason` field gets a maximum length of 512 characters, as one of them has today
      (`client/models.py:194`).

  The check runs in `service.py` and in `config_routes.py`, which both receive the secrets when
  they are built.

## R5. The `admin` account

- **Decision**: two variables in the environment of `sync-api`:
    - `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`, required, at least 12 characters;
    - `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, optional, at least 16 characters, as a principal token
      today (`auth.py:36`).

  `build_app` applies them before it builds the resolver, in one transaction that takes the
  advisory lock of schema initialization on PostgreSQL (`store.py:858`), so two `sync-api`
  processes that start together do not conflict:
    - it creates the row `admin` when it is missing, as an active administrator marked
      `environment_managed`;
    - when the stored hash does not verify the password of the environment, it stores a new hash
      and deletes the sessions of `admin`;
    - it sets the one environment-managed token row of `admin` to the digest of the variable, or
      deletes that row when the variable is empty;
    - it deletes the `sign_in_attempts` row of `admin` (R6).
- **The process then removes the password variable from its own environment.** The password is
  in no list of secret values and in no free-text check. Otherwise any account could send
  candidate passwords in a `reason` and learn which one the service refuses, with no limit; the
  reviewer found this in the first design.
- **Every account route refuses a change to an `environment_managed` account** with `409` and
  the code `environment-managed-account`, and the message names the variable.
- **Rationale**: decision D-2 of the spec. Applying at the start of `sync-api`, and not in
  `sync-bootstrap`, means a restart of `sync-api` alone is enough to rotate or recover.
- **The names** include `ACCOUNT` because `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` already exists
  and names the database administrator.
- **Alternatives considered**: the names `INFRAHUB_SYNC_INITIAL_ADMIN_*`, as in Infrahub.
  Rejected: "initial" says the value applies once, which is the opposite of D-2.

## R6. The refusal after repeated failures

- **Decision**: a table `sign_in_attempts` keyed by the lower-case name.
    - A failed sign-in adds one to the count of the current 15-minute window. A wrong current
      password on `PUT /me/password` counts in the same way.
    - When the count reaches 5, the row gets a `refused_until` time 15 minutes later. While that
      time is in the future, a sign-in for the name is refused before the password is checked.
    - **An attempt made while the name is refused does not count and does not move
      `refused_until`.** When the time passes, the count starts again from zero.
    - A successful sign-in deletes the row.
- **A name that has no account** is counted in the same way, and its sign-in runs one password
  verification against a fixed hash. So the answer and its duration do not show whether the
  account exists (FR-003).
- **Ways to end a refusal early**:
    - an administrator clears it for another account (`POST /accounts/{name}/unlock`);
    - a restart of `sync-api` clears it for `admin` (R5);
    - requests with an API token are never affected.
- **Bounds**: a name longer than 64 characters is refused before any write. The table holds at
  most 10,000 rows: a write that would exceed this first deletes the rows with the oldest
  window. The limit on concurrent verifications (R2) bounds the memory.
- **A known limit**: a person who knows an account name can keep that account from signing in
  with its password, by failing 5 times in each 15-minute window. The rule is the one the
  maintainer chose in the interview. The alternative is a delay that grows with each failure and
  never refuses outright. It is recorded in the critique for the maintainer.
- **Alternatives considered**: a limit for each client address. Rejected: the Compose deployment
  is behind a proxy or bound to the loopback address, so the address the API sees is often the
  same for every person.

## R7. How an answer names an account, and what a deletion does

- **Decision, presentation**: one function at the HTTP boundary turns account identifiers into
  display names in every outgoing body, whether the body was just built or comes from a stored
  answer:
    - it looks at the fields `actor`, `approved_by` and `registered_by`, and at the related
      resources of kind `account`;
    - an identifier of an active or disabled account becomes its name;
    - an identifier of a deleted account becomes `Deleted account <short identifier>`;
    - a value that is not an account identifier is left as it is (R1).

  It reads the accounts it needs in one store call for each answer.
- **Decision, deletion**: one update of the account row. It removes the name, the lower-case key
  and the password hash, and sets the deletion time. The row keeps the identifier and the short
  identifier. The same transaction deletes the tokens, the sessions and the `sign_in_attempts`
  row, and writes the audit event. No other record changes.
- **The short identifier** is 8 hexadecimal characters, unique, chosen when the account is
  created. It is shown only after the deletion.
- **The `already-approved` refusal** no longer puts the account in its message. The approval in
  its details names it, through the presentation above.
- **Where the name of a deleted account can remain**:
    - free text that a person typed: `reason`, `approval_reason`, `registration_reason`;
    - log lines, if a later change logs a name; none does today;
    - the session file of a CLI that signed in as the account, which holds no name.

  The documentation says so, and the search of SC-007 excludes the free-text columns.
- **Alternatives considered**: resolve names in each route. Rejected: about ten routes and two
  replay paths would each need it, and a new route could forget it. One boundary function is
  tested once against every response model.

## R8. Account names

- **Decision**: a name matches `^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$`. The store keeps the name as
  typed, for display, and a lower-case key with a unique index that ignores deleted rows.
  Refused as names: `admin` (taken by the row that the environment manages), `unknown` and
  `unauthenticated` (values the code writes when no account applies), and anything that starts
  with `acct_`.
- **Rationale**: the documentation already shows an email address as a principal name
  (`docs/docs/compose-deployment.mdx:569`). No space keeps a name different from every
  placeholder and safe in a URL path.
- **A deleted name is free again**, because the deletion empties the key. A disabled account
  keeps its key, so its name stays taken.

## R9. Sessions, sign-out and password changes

- **Decision**:
    - A session ends at sign-out, after `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` without use (default
      28800, which is 8 hours), when its account is disabled or deleted, and when the password of
      its account changes, except the session that made the change.
    - Disabling an account deletes its tokens and its sessions. Enabling it therefore restores
      nothing (FR-029). The maintainer decided this in the interview.
    - Changing a password does not delete the tokens of the account.
- **The CLI keeps the session token in a file** `sessions.json` in the directory that
  `typer.get_app_dir("infrahub-sync")` returns, with mode `0600`, keyed by the address of the
  Sync API. Typer is a base dependency, so this works on Python 3.10 without the `service`
  extra.
- **The lookup of the file is in the CLI, not in `SyncClient.from_environment`.** A script that
  uses the client as a library reads no file.
- **`INFRAHUB_SYNC_API_TOKEN`, when set, is used and the file is not read.** `login` still
  signs in and saves the session, and prints a line that says the variable takes precedence.
  `logout` ends the saved session and prints the same line.
- **On the Compose deployment**, the `cli` service keeps no file. The documented first step
  there is to set `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` and to give the same value to
  `INFRAHUB_SYNC_API_TOKEN`. `login` is for a CLI installed on a workstation.
- **The web interface keeps the session token** where it keeps the token today.
- **Alternatives considered**: the operating system keyring for the CLI. Rejected: a new
  dependency, and no keyring exists in a container.

## R10. Routes and rights

- **Decision**: the routes of [contracts/http-api.md](contracts/http-api.md), all under `/api`.
- **No `Idempotency-Key`** on the account routes. Creating an account that exists answers `409`,
  and the other actions give the same result when repeated.
- **`reason` is optional** on the account routes, and is recorded in the audit event when given.
- **Audit**: each account action writes an `audit_events` row through the existing
  `record_audit` path, with an `event_type` of the form `infrahub_sync.account.<action>` and a
  related resource of kind `account` that holds the identifier of the account acted on. A refused
  sign-in writes the existing authentication refusal record, without the name that was tried.

## R11. What changes in the deployment and the tests

- **Removed**: `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` and `EnvironmentPrincipalResolver`, from
  `docker-compose.yml`, `development/docker-compose.yml`,
  `infrahub_sync/testcontainers/docker-compose.yml`, `infrahub_sync/testcontainers/helpers.py`,
  the `tasks/` that read it, six documentation pages and the unreleased fragment
  `changelog/421.added.md`, which names the variable.
- **Existing tests**: 32 test files name the variable or the resolver (18 in `tests/service`, 9
  in `tests/compose`, 2 in `tests/dev_stack`, and one each in `tests/image`, `tests/plan` and
  `tests/product_store`), and 20 call `create_app`. One shared fixture builds a store-backed
  resolver with seeded accounts and returns their tokens, so each file changes its set-up and
  not its assertions.
- **The development stack** sets the `admin` password and token to fixed development values, so
  `infrahub-sync-dev-token` keeps working.
- **Spec 018 (Helm chart)** is on another branch and names the removed variable. The branch that
  merges second updates the chart values.

## R12. Decision records and rules to write

- A new decision record: sign-in uses local accounts in the product store, and the `admin`
  account is defined by the environment.
- A new decision record: a record holds an account identifier, and a deletion empties the
  account row.
- `AGENTS.md`: the Sync frontend rule is replaced (FR-036), and the CLI command list gains the
  new commands.
- **Constitution principle VI** says credentials are "never committed, printed, or logged". This
  feature displays a token value once, to the account that created it. The feature amends the
  principle with one sentence that allows it (FR-040). The wider rework of principle VI, for
  credentials of other systems that Sync stores, belongs to the next specification.
