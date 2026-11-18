# Tasks: Sign in to Sync with a local account

**Input**: Design documents from `dev/specs/022-local-accounts/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: included. The constitution's principle V requires a test for each new behavior.

**Format**: `- [ ] [ID] [P?] [Story] Description`. `[P]` means the task touches different files
from the other `[P]` tasks of its group and depends on no unfinished task of that group.

**Rules for every task**:

- No password, token value, session token or digest in a log line, an error message, an audit
  event or a test's printed output
  ([`secret-redaction.md`](../../../../docs/docs/development/guidelines/secret-redaction.md)).
- Store code runs on SQLite and PostgreSQL. Write SQL with `?` placeholders and pass it through
  `_sql`, as `infrahub_sync/product_store/store.py` does.
- Before each commit: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`.

## Phase 1: Setup

**Purpose**: a known starting point.

- [X] T001 Run `uv sync --extra dev --extra prefect --extra service` and
  `uv run invoke tests.tests-unit` in the worktree; record the number of passing tests in
  `dev/specs/022-local-accounts/notes.md`, to compare after Phase 2.
- [X] T002 [P] Confirm the scrypt setting of research R2 against the current OWASP Password
  Storage Cheat Sheet; record the setting and the date in `dev/specs/022-local-accounts/notes.md`.
- [X] T003 [P] List every file that names `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`,
  `BEARER_TOKENS` or `EnvironmentPrincipalResolver` (exclude `dev/specs`, `node_modules`,
  `.venv`) into `dev/specs/022-local-accounts/notes.md`; this is the checklist for T021 to T025.

## Phase 2: Foundational (blocks every user story)

**Purpose**: accounts exist in the store, a request is resolved from the store, records hold an
account identifier, answers display names, and the removed variable is gone.

**Checkpoint**: the whole unit tier passes with the store-backed resolver, and the only way to
sign in is the `admin` token from the environment.

### The store

- [X] T004 Add the tables `accounts`, `account_tokens`, `account_sessions` and
  `sign_in_attempts`, with their unique indexes, to `_SCHEMA` in
  `infrahub_sync/product_store/store.py`, as [data-model.md](data-model.md) defines them. The
  unique index on `accounts.name_key` must allow several empty values on both databases.
- [X] T005 [P] Add the records `Account`, `AccountToken`, `AccountSession` and `SignInAttempt`
  as frozen Pydantic models in `infrahub_sync/product_store/models.py`, and export them from
  `infrahub_sync/product_store/__init__.py`.
- [X] T006 Create `infrahub_sync/product_store/accounts.py` with the statements and functions
  for accounts: create (with a new `acct_` identifier and a unique `short_id`), read by
  identifier, by lower-case name and by a list of identifiers, list by state, set level, set
  password hash, disable (deletes tokens and sessions), enable. Each takes a cursor so the
  caller owns the transaction.
- [X] T007 In `infrahub_sync/product_store/accounts.py`, add the functions for tokens and
  sessions: create, list for an account, delete, delete all for an account, and one function
  `resolve_digest` that reads the token or session row with its account and writes
  `last_used_at` when the stored value is more than a minute old, in one transaction.
- [X] T008 Expose the functions of T006 and T007 as methods of `_RelationalRunStore` and of
  `ProductProjection` in `infrahub_sync/product_store/store.py`, following how the configuration
  methods are exposed, and add them to the `_RunStore` protocol.
- [X] T009 [P] Write `tests/product_store/test_accounts.py`: creation, uniqueness of the name
  without regard to case, a freed name after the key is emptied, level, disable and enable,
  token and session rows, `resolve_digest` for a token, a session, an expired token, a disabled
  account and an unknown digest, and the one-minute rule of `last_used_at`. Parametrize over the
  store dialects the existing store tests use.

### Passwords, tokens and the resolver

- [X] T010 [P] Create `infrahub_sync/service/credentials.py`: `hash_password` and
  `verify_password` with `hashlib.scrypt`, an explicit `maxmem` and the stored format of
  research R2; `new_api_token` and `new_session_token` with the prefixes `ifs_pat_` and
  `ifs_ses_`; `digest`; `contains_token_prefix`; a fixed hash for names with no account; and a
  gate that lets at most two verifications run at once and raises a specific exception after 5
  seconds of waiting.
- [X] T011 [P] Write `tests/service/test_credentials.py`: a hash verifies its password and no
  other, two hashes of one password differ, a hash made with lower parameters still verifies,
  the parameters in the code are not below the setting recorded in T002, token values have the
  prefix and 32 random bytes, and the gate refuses a third concurrent verification after its
  wait.
- [X] T012 In `infrahub_sync/service/auth.py`: give `Principal` the fields `name`, the kind of
  value presented and `session_id`, with `actor` holding the account identifier; make
  `PrincipalResolver.resolve` return a result that is a `Principal` or a refusal code; add
  `StoreAccountResolver`, which calls `resolve_digest` and applies the idle period; keep
  `secret_values` and return the `admin` token only.
- [X] T013 Add the three variables of [contracts/environment.md](contracts/environment.md) to
  `infrahub_sync/service/settings.py`, with their validation and messages that name the
  variable and never the value.
- [X] T014 In `infrahub_sync/service/auth.py`, add `apply_admin_account(projection, settings)`:
  the transaction of research R5 (create or update `admin`, replace its environment-managed
  token, delete its sessions when the password changed, delete its `sign_in_attempts` row),
  under the schema initialization lock on PostgreSQL. After it returns, remove the password
  variable from `os.environ`.
- [X] T015 In `infrahub_sync/service/serve.py`, call `apply_admin_account` in `build_app` before
  the resolver is built, build `StoreAccountResolver`, and delete
  `EnvironmentPrincipalResolver` and `PRINCIPALS_ENV` from `infrahub_sync/service/auth.py`.
- [X] T016 In `infrahub_sync/service/app.py`, make `authenticate` use the result of `resolve`:
  `401 unauthenticated` as today, and `401 token-expired` for an expired token. Make `GET /me`
  return `Principal.name` in `actor`.
- [X] T017 [P] Write `tests/service/test_account_resolver.py`: a token and a session resolve to
  the account identifier and name; an unknown value, a disabled account, a deleted account, an
  expired token and an idle session are refused with the right code; a request after a disable
  is refused with no restart.
- [X] T018 [P] Write `tests/service/test_admin_account.py`: first start creates `admin`; a
  second start changes nothing; a changed password replaces the hash and ends the sessions; a
  removed token variable removes the token; a missing or short password and a token with a
  reserved prefix stop the start with a message that names the variable; the password is absent
  from `os.environ` afterwards; two threads that apply the variables together both succeed.

### Names in answers

- [X] T019 Create `infrahub_sync/service/presentation.py`: one function that, given an outgoing
  body and a way to read accounts by identifier, replaces account identifiers in the fields
  `actor`, `approved_by` and `registered_by` and in related resources of kind `account` with
  the name, or `Deleted account <short_id>`, and leaves any other value as it is. Apply it in
  `infrahub_sync/service/app.py` to every answer, including an answer replayed from a receipt
  (`infrahub_sync/service/service.py`, `infrahub_sync/service/config_routes.py`). Change
  `already_approved_body` in `infrahub_sync/product_store/models.py` so its message names no
  account.
- [X] T020 [P] Write `tests/service/test_account_presentation.py`: for every response model in
  `infrahub_sync/client/models.py` that has `actor`, `approved_by`, `registered_by` or a related
  account resource, the answer shows a name and never a value that starts with `acct_`; a
  replayed answer shows a name; a value that is not an identifier is unchanged; a deleted
  account shows the placeholder.

### Removal of the old variable

- [X] T021 Create the shared fixture in `tests/service/accounts.py` (and register it in
  `tests/conftest.py` if other test packages need it): a store with seeded accounts (one
  administrator, one non-administrator), their API tokens, and a `StoreAccountResolver`.
- [X] T022 Move the 18 files of `tests/service/` that build `EnvironmentPrincipalResolver` or
  set the removed variable to the fixture of T021. Change set-up only; an assertion on `actor`
  keeps expecting the account name.
- [X] T023 [P] Do the same for `tests/plan/test_format_5.py`,
  `tests/product_store/test_contract.py`, `tests/image/conftest.py` and
  `tests/service/test_environment_names.py`.
- [X] T024 [P] Replace the removed variable with the `admin` variables in
  `infrahub_sync/testcontainers/docker-compose.yml` and
  `infrahub_sync/testcontainers/helpers.py`, keeping `TEST_API_TOKEN` as the token of `admin`;
  update the 9 files of `tests/compose/` and the 2 files of `tests/dev_stack/`.
- [X] T025 [P] Replace the removed variable in `docker-compose.yml` (required password with the
  `${NAME:?message}` form, optional token, optional idle period), in
  `development/docker-compose.yml` (fixed development password, token
  `infrahub-sync-dev-token`) and in the `tasks/` modules that read `BEARER_TOKENS`.
- [X] T026 Run `uv run invoke tests.tests-unit`; compare with the count of T001; fix every
  failure that the change of resolver caused.

## Phase 3: User Story 1 - Sign in with a local account through the CLI and the API (P1)

**Goal**: an administrator gives a person access in one command; the person signs in, changes
their password and creates a token for a script; the administrator replaces a lost password and
disables the account.

**Independent Test**: [quickstart.md](quickstart.md), scenarios 1 to 4 and the disable and enable
lines of scenario 5.

### Tests for User Story 1

- [X] T027 [P] [US1] Write `tests/service/test_sign_in.py`: sign-in returns a session token that
  resolves; a wrong name, a wrong password and a disabled account get one identical answer;
  the sixth attempt after five failures is refused with the right password; an attempt during
  the refusal does not move `refused_until`; a success clears the count; a name with no account
  is counted; a name over 64 characters writes nothing; the table never exceeds its bound;
  a waiting sign-in answers `503 sign-in-busy`; sign-out ends the session; sign-out with an API
  token answers `422`.
- [X] T028 [P] [US1] Write `tests/service/test_account_routes.py` for User Story 1: create,
  list by state, read; a taken name, an invalid name and each reserved name are refused; set
  password ends every session; disable refuses the next request of the password, the session
  and the token, and keeps the name on past runs; enable restores the password and no token;
  unlock ends a refusal; every route answers `403` to a non-administrator; every change to
  `admin` answers `409 environment-managed-account` and names the variable.
- [X] T029 [P] [US1] Write `tests/service/test_token_routes.py`: create returns the value once;
  list never shows it; a taken name and a past expiry are refused; a deleted token and an
  expired token are refused with their codes; own password change needs the current password,
  ends the other sessions, keeps the tokens, and a wrong current password counts as a failed
  attempt.
- [X] T030 [P] [US1] Write `tests/service/test_free_text.py`: a `reason` that contains
  `ifs_pat_` or `ifs_ses_` is refused on a run route and a configuration route; a `reason` of
  513 characters is refused; a `reason` equal to the `admin` password is accepted like any other
  text; a `reason` that contains the `admin` token is refused.
- [X] T031 [P] [US1] Write `tests/service/test_account_events.py`: each account action writes
  one audit event with the type `infrahub_sync.account.<action>`, the identifier of the account
  acted on as a related resource, and no password or token value; a refused sign-in writes no
  name.
- [X] T032 [P] [US1] Write `tests/client/test_account_client.py` and
  `tests/cli/test_login_commands.py`, `tests/cli/test_account_commands.py`,
  `tests/cli/test_cli_session.py`: each client method calls its route; the session file is
  created with mode `0600` and keyed by address; `INFRAHUB_SYNC_API_TOKEN` takes precedence and
  `login` prints the notice; a refused saved session is removed; a password is never accepted
  as an argument; `tokens create` prints the value once; the commands run on Python 3.10 without
  the `service` extra.

### Implementation for User Story 1

- [X] T033 [US1] In `infrahub_sync/product_store/accounts.py` and
  `infrahub_sync/product_store/store.py`, add the functions for `sign_in_attempts`: record a
  failure, read the refusal, clear a name, and the bound of 10,000 rows (research R6).
- [X] T034 [US1] Create `infrahub_sync/service/account_routes.py` with `POST /auth/sign-in` and
  `POST /auth/sign-out` as [contracts/http-api.md](contracts/http-api.md) defines them,
  including the fixed-hash verification for a name with no account and the gate of T010; mount
  it in `infrahub_sync/service/app.py`.
- [X] T035 [US1] In `infrahub_sync/service/account_routes.py`, add `GET /accounts`,
  `POST /accounts`, `GET /accounts/{name}`, `PATCH /accounts/{name}` for `disabled` only,
  `PUT /accounts/{name}/password` and `POST /accounts/{name}/unlock`, with the name rule and the
  reserved names of research R8 and the refusal for an environment-managed account.
- [X] T036 [US1] In `infrahub_sync/service/account_routes.py`, add `PUT /me/password`,
  `GET /me/tokens`, `POST /me/tokens` and `DELETE /me/tokens/{token_id}`.
- [X] T037 [US1] Add the account event types to `infrahub_sync/service/events.py` and write
  each account action's audit event through the existing `record_audit` path, in the
  transaction of the action where the store method allows it.
- [X] T038 [US1] In `infrahub_sync/service/service.py` and
  `infrahub_sync/service/config_routes.py`, make the free-text check refuse a value for which
  `contains_token_prefix` is true; give every `reason` field in `infrahub_sync/client/models.py`
  and `infrahub_sync/service/models.py` a maximum length of 512.
- [X] T039 [P] [US1] Add `AccountResource`, `TokenResource`, `CreatedTokenResource` and
  `SessionResource` to `infrahub_sync/client/models.py`, and the client methods for every route
  of T034 to T036 to `infrahub_sync/client/client.py`. `SyncClient.from_environment` keeps
  reading the variable only.
- [X] T040 [P] [US1] Create `infrahub_sync/cli_session.py`: read, write and remove a saved
  session in `typer.get_app_dir("infrahub-sync")/sessions.json`, mode `0600`, with only base
  dependencies.
- [X] T041 [US1] In `infrahub_sync/cli.py`, make the client factory use
  `INFRAHUB_SYNC_API_TOKEN` when set and the saved session otherwise, update the "no token"
  hint, and add `login`, `logout` and `password` as [contracts/cli.md](contracts/cli.md)
  defines them.
- [X] T042 [US1] In `infrahub_sync/cli.py`, add the `tokens` group (`list`, `create`, `delete`)
  and the `accounts` group with `list`, `create`, `set-password`, `unlock`, `disable` and
  `enable`. Passwords come from a prompt or `--password-stdin`.
- [X] T043 [P] [US1] Update `docs/docs/compose-deployment.mdx`, `docs/docs/quickstart-compose.mdx`,
  `docs/docs/development-stack.mdx`, `docs/docs/operations/day-2-operations.mdx` and
  `docs/docs/operations/compose-troubleshooting.mdx`: the `admin` variables, the first sign-in
  on a workstation and on Compose, the upgrade note that says to remove the old variable, and
  how to recover `admin`.
- [X] T044 [P] [US1] Write `docs/docs/guides/accounts.mdx`: add a person, give a script a
  token, replace a lost password, end a refusal after repeated failures, disable an account, and
  what a person who knows a name can and cannot do to that account's sign-in.
- [X] T045 [US1] Regenerate `docs/docs/reference/sync-http-api.mdx` and the CLI reference with
  `uv run invoke docs.generate`; run `uv run invoke docs.rumdl`.
- [X] T046 [US1] Run quickstart scenarios 1 to 4 on the development stack
  (`uv run invoke dev.start`) and record the result in `dev/specs/022-local-accounts/notes.md`.
  Done on a separate Compose project of `development/docker-compose.yml`, without the worker or
  Infrahub, because another checkout's stack held the project name of `dev.start`; see notes.md.

**Checkpoint**: User Story 1 is releasable. The web interface works with an account token in its
existing token field.

## Phase 4: User Story 2 - Sign in through the web interface (P2)

**Goal**: a person signs in to the web interface with a name and a password, and the pages name
accounts.

**Before starting**: on 2026-11-18 the maintainer instructed that User Stories 2 and 4 are built
now, to replace the token field from the start (spec "Assumptions"). This replaces the earlier
condition of the agreement of the colleague who builds the web interface. Phase 5 does not depend
on this phase.

**Independent Test**: [quickstart.md](quickstart.md), scenario 6, first half.

- [X] T047 [US2] Regenerate `frontend/src/api/types.generated.ts` from the OpenAPI document of
  the Sync API.
- [X] T048 [US2] In `frontend/src/api/token.ts` and `frontend/src/api/client.ts`, keep the
  session token where the token is kept today, and add the calls to sign in and sign out.
- [X] T049 [US2] Replace `frontend/src/components/token-entry.tsx` with
  `frontend/src/components/sign-in.tsx`: a name, a password, one refusal message for `401`, and
  a "try again" message for `503 sign-in-busy`.
- [X] T050 [US2] In `frontend/src/routes/__root.tsx`, display the signed-in account from
  `GET /api/me` and a "Sign out" action.
- [X] T051 [P] [US2] Display the account that started a run and the account that approved its
  plan in `frontend/src/features/runs/` (`RunPage.tsx`, `RunsPage.tsx`, `run-sections.tsx`).
- [X] T052 [P] [US2] Display the account that registered a version on the configuration page
  (`frontend/src/routes/configs.$configId.tsx` and its feature files).
- [X] T053 [US2] Replace the "Sync frontend" rule in `AGENTS.md` with the rule of
  [contracts/web-interface.md](contracts/web-interface.md), and update `frontend/README.md`,
  section "Calling the Sync API".
- [X] T054 [US2] Run `pnpm check`, `pnpm typecheck` and `pnpm build` in `frontend/`, then the
  sign-in part of quickstart scenario 6; record the result in
  `dev/specs/022-local-accounts/notes.md`.

**Checkpoint**: a person uses the web interface without a token.

## Phase 5: User Story 3 - Delete an account, and manage another account (P3)

**Goal**: an administrator deletes an account, changes a level, and manages another account's
tokens.

**Independent Test**: [quickstart.md](quickstart.md), scenario 5.

### Tests for User Story 3

- [X] T055 [P] [US3] Write `tests/product_store/test_account_deletion.py`: the deletion empties
  the name, the key and the hash, deletes tokens, sessions and attempts, and keeps the
  identifier and the short identifier; after it, a search of every table and of
  `mutation_receipts.response_body` finds the name only in the free-text columns; no run,
  approval or audit row changed; the name can be created again with a new identifier. Run on
  both store dialects.
- [X] T056 [P] [US3] Extend `tests/service/test_account_routes.py`: delete returns the
  placeholder; past runs, approvals, audit events and configuration versions display it with
  one short identifier; an approval of the deleted account stays valid and an administrator
  applies the plan; a new account with the same name cannot apply, approve, cancel or resume
  the old run; a write made with a `Principal` resolved before the deletion displays the
  placeholder; a run in progress is not stopped; set level takes effect on the next request;
  an administrator lists and deletes another account's tokens; `admin` cannot be deleted or
  changed.

### Implementation for User Story 3

- [X] T057 [US3] Add the deletion of research R7 to `infrahub_sync/product_store/accounts.py`
  and expose it through `infrahub_sync/product_store/store.py`, as one transaction that also
  writes the audit event.
- [X] T058 [US3] In `infrahub_sync/service/account_routes.py`, add `DELETE /accounts/{name}`,
  `administrator` in `PATCH /accounts/{name}`, `GET /accounts/{name}/tokens` and
  `DELETE /accounts/{name}/tokens/{token_id}`.
- [X] T059 [P] [US3] Add the matching methods to `infrahub_sync/client/client.py` and the
  commands `accounts delete` (confirmation, `--yes`), `accounts set-level`, `accounts tokens`
  and `accounts delete-token` to `infrahub_sync/cli.py`; extend
  `tests/client/test_account_client.py` and `tests/cli/test_account_commands.py`.
- [X] T060 [P] [US3] Extend `docs/docs/guides/accounts.mdx`: the difference between disabling and
  deleting, what a deletion removes and what it does not (reasons, log lines), and that it
  cannot be reversed. Regenerate the references with `uv run invoke docs.generate`.
- [X] T061 [US3] Run quickstart scenarios 5 and 5a on the development stack and record the
  result in `dev/specs/022-local-accounts/notes.md`.

**Checkpoint**: every account action exists in the API and the CLI.

## Phase 6: User Story 4 - Manage accounts and tokens in the web interface (P4)

**Goal**: the actions of Phases 3 and 5, in the web interface.

**Independent Test**: [quickstart.md](quickstart.md), scenario 6, second half.

**Updated 2026-11-18**: the "Accounts" page of T064 offers unlock for an account whose
`sign_in_refused_until` is in the future, and offers unlock as the only action for `admin`
([contracts/web-interface.md](contracts/web-interface.md)).

- [X] T062 [US4] Regenerate `frontend/src/api/types.generated.ts`, and add the queries and
  actions for accounts and tokens to `frontend/src/api/queries.ts` and
  `frontend/src/api/actions.ts`.
- [X] T063 [P] [US4] Create the "My account" page in `frontend/src/routes/account.tsx`: change
  the password, list tokens, create a token with its value displayed once, delete a token.
- [X] T064 [P] [US4] Create the "Accounts" page in `frontend/src/routes/accounts.tsx`: the list
  with level and state, the filter on active accounts, and create, set level, set password,
  unlock, disable, enable and delete with its confirmation. Display `admin` with the note that
  the environment defines it, and offer none of its actions.
- [X] T065 [US4] In `frontend/src/routes/__root.tsx`, link to "My account" for every account and
  to "Accounts" for administrators only.
- [X] T066 [US4] Run `pnpm check`, `pnpm typecheck` and `pnpm build` in `frontend/`, then the
  second half of quickstart scenario 6; record the result in
  `dev/specs/022-local-accounts/notes.md`.

## Phase 7: Polish and cross-cutting work

- [X] T067 [P] Write the decision record "Sign-in uses local accounts in the product store, and
  the environment defines the `admin` account" in `dev/adr/`, with the next free number at the
  time of writing, and add it to `dev/adr/README.md` and `docs/docs/development/adr-index.mdx`.
  Cite no internal source.
- [X] T068 [P] Write the decision record "A record holds an account identifier, and a deletion
  empties the account row" in `dev/adr/`, and index it in the same two files.
- [X] T069 [P] Add the sentence of FR-040 to principle VI in
  `docs/docs/development/constitution.md`, and update its version line and date as its
  "Governance" section requires.
- [X] T070 [P] Update `AGENTS.md`: the new commands in "CLI Commands", and
  `docs/docs/development/knowledge/repository-tour.md` for the new modules.
- [X] T071 [P] Add `changelog/+local-accounts.added.md` and
  `changelog/+bearer-tokens-variable.removed.md` with
  `uv run --extra dev towncrier create`, and edit `changelog/421.added.md` so it no longer names
  the removed variable.
- [X] T072 Write the search of SC-005 as a test in `tests/service/test_no_secret_output.py`: run
  the User Story 1 flow against a test app with captured logs and search every answer, log line
  and audit row for the passwords, the token values after creation and the digests.
- [X] T073 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`,
  `uv run invoke tests.tests-unit`, `uv run invoke tests.check-310`,
  `uv run infrahub-sync --help`, `uv run infrahub-sync accounts --help` and
  `uv run invoke docs.docusaurus`.
- [X] T074 Note for the pull request, in `dev/specs/022-local-accounts/notes.md`: the Helm chart
  of spec 018 names the removed variable; the branch that merges second updates the chart
  values to the `admin` variables.

## Dependencies

- Phase 1 → Phase 2 → Phase 3.
- Phase 4 (US2) needs Phase 3. The maintainer instructed on 2026-11-18 that it is built now.
- Phase 5 (US3) needs Phase 3 only. Phases 4 and 5 do not depend on each other; Phase 5 was
  delivered first.
- Phase 6 (US4) needs Phases 4 and 5.
- Phase 7: T067 to T071 can start after Phase 3; T072 to T074 come last.

Inside Phase 2: T004 → T006 → T007 → T008; T010 and T012 → T014 → T015 → T016; T019 after
T008; T021 after T015; T022 to T025 after T021; T026 last.

Inside Phase 3: the tests T027 to T032 can be written first and fail; T033 → T034; T035 and
T036 after T034; T039 before T041; T040 before T041; T041 → T042; T045 after T042.

## Parallel examples

- Phase 2: T005, T009, T010, T011 together after T004; T017, T018 and T020 together; T023, T024
  and T025 together after T021.
- Phase 3: T027 to T032 together; T039, T040, T043 and T044 together.
- Phase 5: T055 and T056 together; T059 and T060 together after T058.

## Implementation strategy

1. **First release: Phases 1 to 3.** A team operates accounts with the API and the CLI: add a
   person, sign in, tokens for scripts, lost passwords, disable. The web interface keeps its
   token field.
2. **Then Phase 5**: delete, levels, another account's tokens.
3. **Then Phases 4 and 6**, which the maintainer instructed on 2026-11-18 to build now, so that
   the sign-in form replaces the token field from the start.
4. **Phase 7** closes each release: the decision records, the constitution sentence and the
   changelog fragments go with the first release.
