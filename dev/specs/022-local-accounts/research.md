# Research: Sign in to Sync with a local account

**Date**: 2026-11-17 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Each entry states a decision, the reason and the alternatives. File and line references are to
the branch `feature/v3-develop` at commit `7987596f`. The entries R1, R2, R4 to R7 and R9 were
revised after the [critique](critiques/critique-20261117.md).

On 2026-11-18, after the implementation and its review, every entry was brought in line with the
code, which is the reference. Where a decision changed, the entry states the final decision, and a
line "Changed during implementation:" gives what the first text said and why it changed.

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
  each password, a 32-byte derived key, and `maxmem` set to 64 MiB. The stored text is
  `scrypt$<N>$<r>$<p>$<salt>$<derived key>`, with the salt and the key in base64 without padding,
  so the parameters can be raised later and an old hash still verifies.
- **Cost parameters**: N = 2^15, r = 8, p = 3, which is one of the scrypt settings the OWASP
  Password Storage Cheat Sheet lists, and needs about 32 MiB for one verification. The setting
  was confirmed against the cheat sheet on 2026-11-17 ([notes.md](notes.md), T002), and a test
  fails if the parameters in the code fall below it.
- **A stored hash is verified only when its parameters are within ceilings**: at most 64 MiB of
  memory, `p` at most 16 and a derived key of at most 128 bytes. A hash that cannot be read
  verifies no password, gets the answer of a wrong password, and writes one log line,
  `account_password_hash_unreadable`, with the account identifier and never the hash.
- **At most two scrypt computations run at the same time** in one `sync-api` process: the
  verifications of sign-in and of a current password, and the hash of every new password
  (`POST /accounts`, `PUT /accounts/{name}/password`, `PUT /me/password`). A request that waits
  more than 5 seconds for its turn answers `503` with the code `sign-in-busy`. So password
  hashing uses at most about 64 MiB, whatever the number of requests.
- **A password has 1 to 1,024 characters that UTF-8 can encode.** A longer password is refused
  before any hash is computed, so the size of a request does not set the cost of a check. A new
  password longer than 1,024 characters answers `422 password-too-long`; one that holds a
  character UTF-8 cannot encode, such as a lone surrogate, answers `422 password-invalid`. At
  sign-in, such a password gets the one sign-in refusal, with no check.
- Changed during implementation: the first text limited sign-ins only. Hashing a new password
  costs as much memory as a check, so it shares the same two places. The maximum length and the
  UTF-8 rule were added after the review found that such a password answered `503`, and that an
  `admin` password longer than 1,024 characters could never sign in.
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
  of two kinds:
    - an **API token** (`ifs_pat_…`), created by an account, valid until it expires or is deleted;
    - a **session token** (`ifs_ses_…`), returned by a sign-in with a password, valid until
      sign-out or until it has not been used for the idle period.

  Both are the prefix and 32 random bytes from `secrets.token_urlsafe`. The store holds only the
  SHA-256 digest, with a unique index. The resolver looks for the digest among the API tokens
  first, then among the sessions, and the table that holds it gives the kind.
- Changed during implementation: the first text said the prefix tells the two kinds apart. The
  token of `admin` has no prefix (it must not start with either), so the kind comes from the
  table. The prefixes still serve the free-text check (R4).
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
  A session found idle costs a second call, which deletes it. No result is cached, because a
  cache would accept a request after an account is disabled (FR-027).
- **`GET /me`** keeps its fields: `actor` is the account name, taken from `Principal.name`.
- **Free text** (FR-024, [`secret-redaction.md`](../../../docs/docs/development/guidelines/secret-redaction.md)):
  the service cannot list tokens it stores as digests. So:
    - a free-text parameter that contains `ifs_pat_` or `ifs_ses_` is refused with the existing
      `422`, code `secret-parameter-refused`, whoever the token belongs to;
    - the token of `admin` stays in `secret_values`, as a principal token is today, and must not
      start with either prefix;
    - the password of `admin` is in no check (R5);
    - every `reason` field gets a maximum length of 512 characters, as one of them has today
      (`client/models.py:194`). On the account routes, a `reason` must also be printable and
      trimmed, as a configuration request requires.

  The check runs in `service.py`, in `config_routes.py` and in the account routes
  (`account_actions.py`), on every `reason` and on the name of a new API token. On an account
  route, the refusal also writes the event `infrahub_sync.account.refused` with the outcome
  `refused-secret-parameter`, and without the refused text. An account name that contains a
  token prefix is refused as `422 account-name-invalid` (R8).
- Changed during implementation: the first text named `reason` only, on the existing routes. The
  name of an API token is shown in every list of tokens, so it gets the same check, and the
  account routes needed the check too.

## R5. The `admin` account

- **Decision**: two variables in the environment of `sync-api`:
    - `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`, required, 12 to 1,024 characters of valid UTF-8
      text;
    - `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, optional, at least 16 characters, as a principal token
      today (`auth.py:36`), and not starting with `ifs_pat_` or `ifs_ses_`.

  A value that breaks a rule stops the start with a message that names the variable and never
  the value. `build_app` applies them before it builds the resolver, in one transaction that
  takes the advisory lock of schema initialization on PostgreSQL (`store.py:858`), and the write
  lock of the database on SQLite, so two `sync-api` processes that start together do not
  conflict:
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
  the code `environment-managed-account`:
    - a change of its password (`PUT /accounts/admin/password`, `PUT /me/password`): the message
      names `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`;
    - the creation or the deletion of one of its tokens: the message names
      `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`;
    - a change of its state or its level, or its deletion: the message says that `admin` is
      always an active administrator that the environment defines, because no variable changes
      that.

  `POST /accounts/admin/unlock` is accepted: it ends a refusal and changes nothing that the
  environment defines. The CLI prints one fixed message for the code, which says all of this.
- Changed during implementation: the first text said that every refusal names the variable. No
  variable disables, deletes or sets the level of `admin`, so naming one there would point the
  operator to an edit that does nothing.
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
      `refused_until`.** When the time passes, the next failure starts a new window with a count
      of one.
    - A successful sign-in deletes the row. So do a new password, set by an administrator or by
      the account, and the deletion of the account.
- **A name that has no account** is counted in the same way, and its sign-in runs one password
  verification against a fixed hash. So the answer and its duration do not show whether the
  account exists (FR-003).
- **A name or a password that no account can have** gets the same refusal with no check, no
  count and no record: a name that is empty, longer than 64 characters (also in lower case),
  holds a NUL character or a character UTF-8 cannot encode, and a password that is empty or
  longer than 1,024 characters or holds a character UTF-8 cannot encode.
- **One check at a time for each name in a process.** A sign-in or a password change holds a
  lock for the lower-case name, then a place of the gate (R2), then reads the refusal again
  immediately before the check, and counts a failure before it lets the lock go. A request that
  finds the name refused at that point checks nothing and counts nothing. A request that waits
  more than 5 seconds for the lock answers `503 sign-in-busy`. The lock is held in the memory of
  the process, so with P `sync-api` processes at most 5 + (P − 1) passwords are checked for each
  refusal.
- Changed during implementation: the first text read the refusal once, before the check. The
  review found that parallel sign-ins all passed that read before the first failure was counted,
  so tens of passwords could be checked for each refusal. The lock and the second read close
  this within one process.
- **Ways to end a refusal early**:
    - an administrator clears it for any account, `admin` included
      (`POST /accounts/{name}/unlock`);
    - a restart of `sync-api` clears it for `admin` (R5);
    - requests with an API token are never affected.
- Changed during implementation: the first text said "for another account". Unlock changes
  nothing that the environment defines, so it is accepted for `admin` too.
- **Bounds**: the limit on concurrent verifications (R2) bounds the memory. The table holds at
  most 10,000 rows. To add a row for a new name when the table is full, the store deletes rows
  in this order, and never a row whose refusal is in force:
    1. the rows whose window started more than 15 minutes ago, which count nothing any more;
    2. then the rows with the oldest window.

  When too few rows can be deleted, because the others hold a refusal in force, the new name is
  not recorded and its sign-in gets the same refusal.
- Changed during implementation: the first text deleted the rows with the oldest window, which
  could include a refusal in force. The review found that a flood of failed sign-ins for unknown
  names could then end the refusal of a real account.
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

  It reads the accounts it needs in one store call for each answer. It is an ASGI middleware,
  `AccountNameMiddleware` (`presentation.py`), so it covers every JSON answer of the API
  application, including an answer replayed for an `Idempotency-Key`, and the `id` of a primary
  resource of kind `account`.
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
    - the session file of a CLI that signed in as the account, on the workstation of that
      person. It holds the account name next to the session token, until `logout` removes the
      entry, or until the CLI removes it because the Sync API refuses the session.

  The documentation says so, and the search of SC-007 excludes the free-text columns.
- Changed during implementation: the first text said the session file holds no name. The CLI
  saves the account name with the session token, to print it.
- **Alternatives considered**: resolve names in each route. Rejected: about ten routes and two
  replay paths would each need it, and a new route could forget it. One boundary function is
  tested once against every response model.

## R8. Account names

- **Decision**: a name matches `^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$`. The store keeps the name as
  typed, for display, and a lower-case key with a unique index. A deleted row has an empty
  (`NULL`) key, and both databases treat two empty values as different, so the index does not
  count deleted rows. Refused as names, in any letter case, with `422 account-name-invalid`:
  `admin` (taken by the row that the environment manages), `unknown` and `unauthenticated`
  (values the code writes when no account applies), anything that starts with `acct_`, and
  anything that contains `ifs_pat_` or `ifs_ses_`. A name that is taken answers
  `409 account-name-taken`.
- Changed during implementation: the token prefixes were added to the refused names. A token
  pasted as a name would otherwise be stored and shown as it was sent.
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
    - Changing a password does not delete the tokens of the account. A password change made with
      an API token keeps no session.
    - A sign-in or a new API token is written only while the account is active and, for a
      sign-in, still has the password that was checked: the store locks the account row in the
      transaction that inserts the session or the token. A disable, a deletion or a password
      change that runs at the same time either comes first and the insertion is refused, or
      comes after and deletes what was inserted.
- Changed during implementation: the last point was added after the review found that a session
  or a token created during a disable or a password reset outlived it.
- **The CLI keeps the session token in a file** `sessions.json` in the directory that
  `typer.get_app_dir("infrahub-sync")` returns. Typer is a base dependency, so this works on
  Python 3.10 without the `service` extra.
    - The directory is created with mode `0700`. A write goes to a temporary file with mode
      `0600` in the same directory, which then replaces the file, so the token is never readable
      by another user and an interrupted write leaves the previous file whole.
    - Each entry holds the session token and the account name, keyed by the normalized address
      of the Sync API: the scheme and the host in lower case, trailing slashes and one trailing
      `/api` removed, the query and the fragment dropped, the port kept as written. An address
      without `http` or `https`, without a host, or with a user name or a password has no entry.
    - A file that is missing or cannot be read as JSON of that shape counts as no saved session.
- **The lookup of the file is in the CLI, not in `SyncClient.from_environment`.** A script that
  uses the client as a library reads no file.
- **`INFRAHUB_SYNC_API_TOKEN`, when set and not empty, is used and the file is not read.**
  `login` still signs in and saves the session, and prints a line that says the variable takes
  precedence. `logout` ends the saved session and prints the same line.
- **A saved session that the Sync API refuses** with `401 unauthenticated` is removed from the
  file, and the CLI prints the `login` hint.
- **`login` reads the password from a prompt, or with `--password-stdin` from standard input.**
  Without a terminal, or with `--password-stdin`, it requires `--name`.
- **The account and token commands print a table by default, and JSON with `--json`.**
- Changed during implementation: the contract named "the existing output option", which does not
  exist; `--json` is a new option of these commands.
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
- **Audit**: each accepted account action writes one `audit_events` row in the transaction of
  the action, so a failure of either writes neither. Its `event_type` is
  `infrahub_sync.account.<action>`, with underscores as in every other event type: `signed_in`,
  `signed_out`, `created`, `disabled`, `enabled`, `level_set`, `password_set` (by an
  administrator), `password_changed` (by the account), `unlocked`, `token_created`,
  `token_deleted` and `deleted`. Its related resources of kind `account` hold the identifiers of
  the account that acted and of the account acted on.
    - A field of `PATCH /accounts/{name}` that the account already has changes nothing and
      writes no event. When both fields change, each is written with its own event.
    - A refused account action writes `infrahub_sync.account.refused`, on its own, with an
      outcome that says why: `refused-authorization` (not an administrator),
      `refused-secret-parameter` (R4) or `refused-current-password`.
    - A refused sign-in writes the existing authentication refusal record, with one reason for
      every cause and without the name that was tried. A sign-in with a name or a password that
      no account can have (R6) writes no record.
- Changed during implementation: the first text wrote the events through the existing
  `record_audit` path, after the action. The review found an action could be stored without its
  event, so the store now writes both in one transaction. The action part of an event type uses
  underscores, as the existing event types do.

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
  The implementation moved 31 test files to the shared helper `tests/service/accounts.py`.
- **The development stack** sets the `admin` password to `infrahub-sync-dev-password` and its
  token to `infrahub-sync-dev-token`, so `infrahub-sync-dev-token` keeps working. The variables
  `ADMIN_ACCOUNT_PASSWORD` and `ADMIN_ACCOUNT_TOKEN` of the developer's shell replace them.
- **Spec 018 (Helm chart)** was merged into this branch. The chart itself is in the repository
  `opsmill/infrahub-helm`. Its values `credentials.adminAccountPassword` (required when the chart
  creates the Secret) and `credentials.adminAccountToken` (optional) replace
  `credentials.bearerTokens`, and `settings.sessionIdleSeconds` sets
  `INFRAHUB_SYNC_SESSION_IDLE_SECONDS`. This branch describes them on
  `docs/docs/kubernetes-deployment.mdx`.
- Changed during implementation: the first text left the chart to the branch that merges
  second. This branch merged second, so it updated the Kubernetes page.

## R12. Decision records and rules to write

- A new decision record: sign-in uses local accounts in the product store, and the `admin`
  account is defined by the environment. Written as
  [ADR 0049](../../adr/0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md).
- A new decision record: a record holds an account identifier, and a deletion empties the
  account row. Written as
  [ADR 0050](../../adr/0050-a-record-holds-an-account-identifier-and-a-deletion-empties-the-account-row.md).
- `AGENTS.md`: the CLI command list gains the new commands. The Sync frontend rule is replaced
  (FR-036) with User Story 2, which is not implemented yet.
- **Constitution principle VI** says credentials are "never committed, printed, or logged". This
  feature displays a token value once, to the account that created it. The feature amends the
  principle with one sentence that allows it (FR-040). The wider rework of principle VI, for
  credentials of other systems that Sync stores, belongs to the next specification.
