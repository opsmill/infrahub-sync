# Implementation Plan: Sign in to Sync with a local account

**Branch**: `022-local-accounts` | **Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/022-local-accounts/spec.md`

## Summary

A team gives a person access to Sync with one command, and each record names the account that
acted:

- **Accounts are stored in the product store.** Four new tables hold accounts, API tokens,
  sessions and failed sign-in attempts. No existing table changes (research R1,
  [data-model.md](data-model.md)).
- **A record holds an account identifier, not a name.** The existing text columns hold it. One
  function at the HTTP boundary turns identifiers into names in every answer (R1, R7).
- **A request still presents a bearer value.** It is an API token that an account created, or a
  session token from a sign-in with a password. A new resolver behind the existing
  `PrincipalResolver` protocol reads it from the store, so no existing route changes its path,
  request or response (R3, R4).
- **The `admin` account comes from the environment.** `sync-api` applies its password and its
  optional token at each start, and refuses any other change to it (R5).
- **`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is removed** from the code, the deployment files, the
  tests and the documentation (R11).
- **A deletion empties the account row.** Past records do not change, and display
  `Deleted account <short identifier>` (R7).
- **New routes and commands** let a person sign in and out, change a password, manage tokens
  and, as an administrator, manage accounts ([contracts](contracts/)).
- **The web interface** gains a sign-in form, the names of accounts on records, and two pages
  for accounts and tokens. This part needs the agreement of the colleague who builds it, and is
  not implemented yet.

This plan was brought in line with the implementation on 2026-11-18. Where a part changed, a line
"Changed during implementation:" gives the reason.

## Delivery order

Each step is releasable alone and matches one user story of the spec.

| Step | User story | Content | Depends on |
| --- | --- | --- | --- |
| 1 | US1 (P1) | Store tables, password and token primitives, resolver, account identifiers in records and names in answers, `admin` from the environment, removal of the old variable, sign-in and sign-out, the refusal after repeated failures and its unlock, account creation and listing, own password change, set password, disable and enable, own tokens, the matching CLI commands, deployment files, documentation | none |
| 2 | US3 (P3) | Delete, set level, administrator actions on another account's tokens, the matching CLI commands | step 1 |
| 3 | US2 (P2) | Sign-in form, signed-in account and sign-out, accounts on records, the `AGENTS.md` rule | step 1, and the colleague's agreement |
| 4 | US4 (P4) | "Accounts" and "My account" pages | steps 2 and 3 |

The spec ranks the web sign-in (US2, P2) above the deletion (US3, P3), as the maintainer
confirmed. The plan delivers US3 first because it is in the API and the CLI, which need no
agreement, while US2 waits for one. If the agreement comes early, steps 2 and 3 can swap: neither
depends on the other.

Step 1 holds everything a team needs to operate accounts safely: every sign-in rule, a way to
replace a lost password, and a way to remove a person's access.

Status on 2026-11-18: steps 1 and 2 are implemented
([implementation report](opsmill-implement-report.md)). Steps 3 and 4 are not started: they wait
for the agreement of the colleague who builds the web interface. Until then, the token field of
the web interface accepts an API token of an account (FR-015).

## Technical Context

**Language/Version**: Python 3.11–3.13 for the service; the CLI and the client also run on
Python 3.10. TypeScript for the web interface (`frontend/`).

**Primary Dependencies**: FastAPI, Pydantic, Typer, httpx, psycopg, the Python standard library
(`hashlib.scrypt`, `secrets`, `hmac`). React and TanStack Router in `frontend/`. No new
dependency.

**Storage**: the product store on SQLite (tests, local) and PostgreSQL (service). Four new
tables declared in the existing `_SCHEMA` text with `CREATE TABLE IF NOT EXISTS`. No change to an
existing table, so a database that an earlier build created gains the tables at start-up. The
existing text columns that held a principal's name hold an account identifier from this feature
on; no stored value is converted.

**Testing**: pytest offline unit tier (`uv run invoke tests.tests-unit`): `tests/product_store/`,
`tests/service/`, `tests/client/`, `tests/cli/`. Store tests run on SQLite, and on PostgreSQL
where the existing store tests do. One shared fixture builds a store-backed resolver with seeded
accounts, for the 32 existing test files that name the removed variable or resolver (research
R11). Tests the critique asked for: the search for a deleted name on both databases, a write
that lands after a deletion, the `admin` password sent in a reason, and two processes that apply
the `admin` variables together. The Compose and development-stack tiers
(`tests/compose/`, `tests/dev_stack/`) are updated for the new variables. The frontend has no
test runner: `pnpm check`, `pnpm typecheck`, `pnpm build` and the manual scenarios of
[quickstart.md](quickstart.md).

**Target Platform**: Linux server (Sync API); CLI on any platform; a current browser.

**Project Type**: web service with a CLI client and a web interface.

**Performance Goals**:

- A request with a token or a session adds one store call, which is one database connection,
  with one indexed read and at most one write a minute for each token or session. A session
  found idle adds a second call, which deletes it.
- An answer that names accounts adds one store call to read their names.
- A sign-in with a password takes under one second when no other sign-in is waiting. Password
  checks and the hashes of new passwords use at most about 64 MiB in one `sync-api` process
  (research R2).
- One password check for each name runs at a time in one process (research R6). With P
  `sync-api` processes, at most 5 + (P − 1) passwords are checked for each refusal after
  repeated failures, not exactly 5.

**Constraints**:

- The API stays `v3-unstable`. No existing route changes its path, request or response.
- No password, token value or digest in an API response, a CLI output, a log line or an audit
  event, except a token value in the answer to its creation
  ([`secret-redaction.md`](../../../docs/docs/development/guidelines/secret-redaction.md)).
- The base install stays free of the `service` extra: the store, the routes and the resolver are
  in the service profile; the client and the CLI use only the base dependencies.
- The vendored `opsmill_prefect_extras` stays byte-identical.
- The worker does not use accounts. No change to the worker's environment.

**Scale/Scope**: about 16 source files in Python, 14 new test files and 32 existing test files
that change their set-up, 3 deployment files, 8 documentation pages, 2 decision records, one
sentence of the constitution, and about 10 files in `frontend/`. Tens of accounts for
one deployment, not thousands. The implementation of steps 1 and 2 added 7 source modules and
20 test files (three of them helpers or fixtures), and moved 31 existing test files to the shared
helper.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only by default | The new commands change accounts, not a system of record. `accounts delete` asks for confirmation and says it cannot be reversed. `plan`, `runs show` and the other read commands are unchanged. | Pass |
| II. Idempotency and safety | Applying the `admin` variables gives the same result at each start. Disable, enable and delete give the same result when repeated. A deletion is one transaction. A run in progress is never stopped by an account action. | Pass |
| III. Adapter symmetry | No adapter changes. | Pass |
| IV. Type safety | New resources are Pydantic models. New errors are specific exceptions and API error codes. | Pass |
| V. Test discipline | Unit tests for each requirement on both store dialects; parametrized tests for names, password lengths and refusal cases. | Pass |
| VI. Secrets | Passwords are stored as scrypt hashes and tokens as digests. The `admin` password and token come from the environment. No password is logged, printed or returned. A token value and a session token are returned once, to the account that created them, and `tokens create` prints the token value once. The principle says credentials are "never printed", so this is a deviation: see "Complexity Tracking". | Deviation, justified |
| VII. Simplicity | The existing resolver protocol, bearer scheme, audit path and text columns are reused. No new dependency. One new module for accounts in the service, one in the store. | Pass |

Post-design re-check: one justified deviation, on principle VI. The feature amends the principle
(FR-040).

## Project Structure

### Documentation (this feature)

```text
dev/specs/022-local-accounts/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── cli.md
│   ├── environment.md
│   └── web-interface.md
├── checklists/requirements.md
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── product_store/
│   ├── store.py              # four tables in _SCHEMA; projection methods for accounts
│   ├── accounts.py           # new: account, token, session and attempt statements, the deletion
│   └── models.py             # new records; the already-approved message no longer names an account
├── service/
│   ├── auth.py               # Principal gains name, kind and session; resolve returns a result;
│   │                         #   StoreAccountResolver replaces EnvironmentPrincipalResolver;
│   │                         #   apply the admin variables
│   ├── presentation.py       # new: identifiers to display names in every outgoing body
│   ├── credentials.py        # new: password hash and verify, token and session values, digests,
│   │                         #   the gate of two scrypt computations, one check for each name
│   ├── account_routes.py     # new: declares /auth, /me/password, /me/tokens, /accounts
│   ├── account_actions.py    # new: the requests, answers, rights, refusals and events of each route
│   ├── app.py                # mount the account routes; the new refusal code; present answers
│   ├── serve.py              # build the new resolver; apply the admin variables at start
│   ├── service.py            # the free-text check refuses token prefixes
│   ├── config_routes.py      # the same check; present stored answers
│   ├── events.py             # account event types
│   └── settings.py           # the three new variables
├── client/
│   ├── client.py             # account, token and session methods
│   └── models.py             # the new resources; a maximum length for every reason
├── cli_session.py            # new: read, write and remove a saved session (Python 3.10)
├── cli_accounts.py           # new: login, logout, password, tokens, accounts; fixed error messages
├── cli.py                    # registers the account commands; session file lookup
└── testcontainers/           # docker-compose.yml and helpers.py: the admin variables

docker-compose.yml            # sync-api: the admin variables; the old variable removed
development/docker-compose.yml
tasks/                        # dev tasks that read BEARER_TOKENS

frontend/src/                 # not changed yet: User Stories 2 and 4
├── api/token.ts, client.ts, queries.ts, actions.ts, types.generated.ts
├── components/sign-in.tsx    # replaces token-entry.tsx
├── routes/__root.tsx         # signed-in account, sign out
├── routes/accounts.tsx, account.tsx
└── features/runs/, features/configs/   # accounts on records

tests/
├── service/accounts.py, service/account_api.py   # the shared helpers: seeded accounts and their tokens
├── product_store/test_accounts.py, test_account_deletion.py, test_sign_in_attempts.py
├── service/test_account_resolver.py, test_admin_account.py, test_sign_in.py,
│   test_account_routes.py, test_token_routes.py, test_account_events.py,
│   test_account_presentation.py, test_credentials.py, test_free_text.py,
│   test_no_secret_output.py
├── client/test_account_client.py
├── cli/conftest.py, test_account_commands.py, test_login_commands.py, test_cli_session.py
├── compose/, dev_stack/, image/            # the new variables
└── 31 existing files                       # set-up moves to the shared helper

docs/docs/
├── compose-deployment.mdx, quickstart-compose.mdx, development-stack.mdx
├── kubernetes-deployment.mdx # the chart values of the admin account (spec 018, merged)
├── operations/day-2-operations.mdx, operations/compose-troubleshooting.mdx
├── reference/sync-http-api.mdx, reference/ (generated CLI pages)
└── guides/accounts.mdx       # new: add a person, give a script a token, disable or delete

dev/adr/                      # 0049 and 0050 (research R12)
docs/docs/development/constitution.md   # one sentence of principle VI (FR-040)
AGENTS.md                     # the frontend rule, the CLI command list
changelog/                    # one `added` fragment, one `removed` fragment; 421.added.md
                              #   no longer names the removed variable
```

**Structure Decision**: the feature follows the existing layout. Store statements for accounts
go in a new module beside `store.py`, which already has 4,900 lines. Account routes go in a new
module beside `config_routes.py`, which is the existing example of a route group built outside
`app.py`. The CLI commands go in two new Typer groups, `accounts` and `tokens`, beside `configs`
and `runs`.

Changed during implementation:

- `account_routes.py` only declares the routes; each route calls one function of a new module,
  `account_actions.py`, which holds the requests, the answers and the actions. The documents of
  the implementation do not record why the routes were split in two modules.
- The commands `login`, `logout`, `password`, `tokens` and `accounts` are in a new module,
  `cli_accounts.py`. `cli.py` calls its `register` once, and `cli_accounts.py` imports nothing
  from `cli.py`. `cli.py` keeps the lookup of the session file.
- No file in `frontend/` changed: steps 3 and 4 are not started.

## Risks

- **A request in progress during a deletion** writes the identifier of the deleted account. It
  displays the placeholder and gives no right (R1). A test covers it.
- **An answer that is not passed through the presentation function** would show an identifier.
  A test builds every response model that has an account field and checks its answer.
- **One more database connection for each request**, because the store has no pool. If a
  deployment shows a slow API, a pool is the remedy; a cache of resolved accounts is not, because
  it would accept a request after an account is disabled.
- **The refusal after repeated failures** can be caused on purpose by a person who knows a name
  (R6). The limit is stated in the spec.
- **Several `sync-api` processes** each run one password check for a name at a time, so with P
  processes up to 5 + (P − 1) passwords are checked for each refusal (R6).
- **A table of failed sign-ins full of refusals in force** does not record a new name, so a
  wrong password for that name is not counted until a row can be deleted (R6).
- **The web interface steps need an agreement** that was not given. Steps 1 and 2 do not depend
  on it, and the token field keeps working with an account token.
- **Spec 018 (Helm chart)** named the removed variable on another branch. It was merged into this
  branch, which updated the Kubernetes page for the chart values of the `admin` account; the
  chart itself is in `opsmill/infrahub-helm`.
- **A production deployment of V3**, if one exists at release time, loses its tokens at the
  upgrade. The upgrade note says to set the `admin` variables first.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| Principle VI, "never printed": a token value is displayed once | A person must receive the token to give it to a script, and Sync keeps only its digest | Never displaying it would mean an administrator writes tokens into the environment, which is the problem this feature removes. The feature amends the principle (FR-040). |

The implementation amended principle VI in `docs/docs/development/constitution.md` (task T069),
so this deviation is now allowed by the principle. No other deviation was added.
