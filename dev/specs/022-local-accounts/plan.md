# Implementation Plan: Sign in to Sync with a local account

**Branch**: `022-local-accounts` | **Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/022-local-accounts/spec.md`

## Summary

A team gives a person access to Sync with one command, and each record names the account that
acted:

- **Accounts are stored in the product store.** Five new tables hold accounts, API tokens,
  sessions, failed sign-in attempts and the identifiers of deleted accounts. No existing table
  changes (research R1, [data-model.md](data-model.md)).
- **A request still presents a bearer value.** It is an API token that an account created, or a
  session token from a sign-in with a password. A new resolver behind the existing
  `PrincipalResolver` protocol reads it from the store, so no existing route changes (R3, R4).
- **The `admin` account comes from the environment.** `sync-api` applies its password and its
  optional token at each start, and refuses any other change to it (R5).
- **`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is removed** from the code, the deployment files, the
  tests and the documentation (R11).
- **A deletion replaces the name in past records** with `Deleted account <identifier>`, in one
  transaction (R7).
- **New routes and commands** let a person sign in and out, change a password, manage tokens
  and, as an administrator, manage accounts ([contracts](contracts/)).
- **The web interface** gains a sign-in form, the names of accounts on records, and two pages
  for accounts and tokens. This part needs the agreement of the colleague who builds it.

## Delivery order

Each step is releasable alone and matches one user story of the spec.

| Step | User story | Content | Depends on |
| --- | --- | --- | --- |
| 1 | US1 (P1) | Store tables, password and token primitives, resolver, `admin` from the environment, removal of the old variable, sign-in and sign-out routes, account creation and listing, own tokens, CLI `login`, `logout`, `accounts create`, `accounts list`, `tokens`, deployment files, documentation | none |
| 2 | US3 (P3) | Own password change, set password, set level, disable, enable, delete with the replacement of the name, sign-in refusal after repeated failures, administrator actions on another account's tokens, the matching CLI commands | step 1 |
| 3 | US2 (P2) | Sign-in form, signed-in account and sign-out, accounts on records, the `AGENTS.md` rule | step 1, and the colleague's agreement |
| 4 | US4 (P4) | "Accounts" and "My account" pages | steps 2 and 3 |

Step 2 comes before step 3 because it is in the API and the CLI, which need no agreement, and
because a team cannot remove a person's access without it. The session idle period and the
password length rule are part of step 1, because sign-in is not safe to release without them.

## Technical Context

**Language/Version**: Python 3.11–3.13 for the service; the CLI and the client also run on
Python 3.10. TypeScript for the web interface (`frontend/`).

**Primary Dependencies**: FastAPI, Pydantic, Typer, httpx, psycopg, the Python standard library
(`hashlib.scrypt`, `secrets`, `hmac`). React and TanStack Router in `frontend/`. No new
dependency.

**Storage**: the product store on SQLite (tests, local) and PostgreSQL (service). Five new
tables declared in the existing `_SCHEMA` text with `CREATE TABLE IF NOT EXISTS`. No change to an
existing table, so a database that an earlier build created gains the tables at start-up.

**Testing**: pytest offline unit tier (`uv run invoke tests.tests-unit`): `tests/product_store/`,
`tests/service/`, `tests/client/`, `tests/cli/`. Store tests run on SQLite, and on PostgreSQL
where the existing store tests do. The Compose and development-stack tiers
(`tests/compose/`, `tests/dev_stack/`) are updated for the new variables. The frontend has no
test runner: `pnpm check`, `pnpm typecheck`, `pnpm build` and the manual scenarios of
[quickstart.md](quickstart.md).

**Target Platform**: Linux server (Sync API); CLI on any platform; a current browser.

**Project Type**: web service with a CLI client and a web interface.

**Performance Goals**:

- A request with a token or a session adds one indexed read to the store, and at most one write
  a minute for each token or session.
- A sign-in with a password takes under one second on the deployment's API container.

**Constraints**:

- The API stays `v3-unstable`. No existing route changes its path, request or response.
- No password, token value or digest in an API response, a CLI output, a log line or an audit
  event, except a token value in the answer to its creation
  ([`secret-redaction.md`](../../../docs/docs/development/guidelines/secret-redaction.md)).
- The base install stays free of the `service` extra: the store, the routes and the resolver are
  in the service profile; the client and the CLI use only the base dependencies.
- The vendored `opsmill_prefect_extras` stays byte-identical.
- The worker does not use accounts. No change to the worker's environment.

**Scale/Scope**: about 12 source files and 14 test files in Python, 3 deployment files, 8
documentation pages, 2 decision records, and about 10 files in `frontend/`. Tens of accounts for
one deployment, not thousands.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only by default | The new commands change accounts, not a system of record. `accounts delete` asks for confirmation and says it cannot be reversed. `plan`, `runs show` and the other read commands are unchanged. | Pass |
| II. Idempotency and safety | Applying the `admin` variables gives the same result at each start. Disable, enable and delete give the same result when repeated. A deletion is one transaction. A run in progress is never stopped by an account action. | Pass |
| III. Adapter symmetry | No adapter changes. | Pass |
| IV. Type safety | New resources are Pydantic models. New errors are specific exceptions and API error codes. | Pass |
| V. Test discipline | Unit tests for each requirement on both store dialects; parametrized tests for names, password lengths and refusal cases. | Pass |
| VI. Secrets | Passwords are stored as scrypt hashes and tokens as digests. The `admin` password and token come from the environment. No value is logged or returned. The principle governs the credentials Sync uses for other systems, which this feature does not touch (research R12). | Pass |
| VII. Simplicity | The existing resolver protocol, bearer scheme, audit path and text columns are reused. No new dependency. One new module for accounts in the service, one in the store. | Pass |

Post-design re-check: pass. No violation, so "Complexity Tracking" is empty.

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
│   ├── store.py              # five tables in _SCHEMA; projection methods for accounts
│   ├── accounts.py           # new: account, token, session and attempt statements, the deletion
│   └── models.py             # new records: Account, AccountToken, AccountSession
├── service/
│   ├── auth.py               # Principal and PrincipalResolver stay; StoreAccountResolver replaces
│   │                         #   EnvironmentPrincipalResolver; apply the admin variables
│   ├── credentials.py        # new: password hash and verify, token and session values, digests
│   ├── account_routes.py     # new: /auth, /me/password, /me/tokens, /accounts
│   ├── app.py                # mount the account routes; pass the presented value to the scope
│   ├── serve.py              # build the new resolver; apply the admin variables at start
│   ├── service.py            # the free-text check also refuses the presented value
│   ├── flow.py               # a stage result takes its actor from the stored row
│   ├── events.py             # account event types
│   └── settings.py           # the three new variables
├── client/
│   ├── client.py             # account, token and session methods; session file lookup
│   ├── models.py             # the new resources
│   └── session_file.py       # new: read, write and remove a saved session (Python 3.10)
├── cli.py                    # login, logout, password, tokens, accounts
└── testcontainers/           # docker-compose.yml and helpers.py: the admin variables

docker-compose.yml            # sync-api: the admin variables; the old variable removed
development/docker-compose.yml
tasks/                        # dev tasks that read BEARER_TOKENS

frontend/src/
├── api/token.ts, client.ts, queries.ts, actions.ts, types.generated.ts
├── components/sign-in.tsx    # replaces token-entry.tsx
├── routes/__root.tsx         # signed-in account, sign out
├── routes/accounts.tsx, account.tsx
└── features/runs/, features/configs/   # accounts on records

tests/
├── product_store/test_accounts.py, test_account_deletion.py
├── service/test_account_resolver.py, test_admin_account.py, test_sign_in.py,
│   test_account_routes.py, test_token_routes.py, test_account_events.py
├── client/test_account_client.py, test_session_file.py
├── cli/test_account_commands.py, test_login_commands.py
└── compose/, dev_stack/      # the new variables

docs/docs/
├── compose-deployment.mdx, quickstart-compose.mdx, development-stack.mdx
├── operations/day-2-operations.mdx, operations/compose-troubleshooting.mdx
├── reference/sync-http-api.mdx, reference/ (generated CLI pages)
└── guides/accounts.mdx       # new: add a person, give a script a token, disable or delete

dev/adr/                      # two records (research R12)
AGENTS.md                     # the frontend rule, the CLI command list
changelog/                    # one `added` fragment, one `removed` fragment
```

**Structure Decision**: the feature follows the existing layout. Store statements for accounts
go in a new module beside `store.py`, which already has 4,900 lines. Account routes go in a new
module beside `config_routes.py`, which is the existing example of a route group built outside
`app.py`. The CLI commands go in two new Typer groups, `accounts` and `tokens`, beside `configs`
and `runs`.

## Risks

- **A run in progress during a deletion** can write the old name after the deletion (R7). The
  store takes the actor from the stored row inside the commit, and a test covers it.
- **The web interface steps need an agreement** that was not given. Steps 1 and 2 do not depend
  on it, and the token field keeps working with an account token.
- **Spec 018 (Helm chart)** names the removed variable on another branch. The branch that merges
  second updates the chart.
- **A production deployment of V3**, if one exists at release time, loses its tokens at the
  upgrade. The upgrade note says to set the `admin` variables first.

## Complexity Tracking

No violation to justify.
