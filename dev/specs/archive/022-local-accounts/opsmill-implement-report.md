# Implementation report: Sign in to Sync with a local account

| Item | Value |
| --- | --- |
| Spec directory | `dev/specs/022-local-accounts` |
| Branch | `022-local-accounts` (worktree `.worktrees/022-local-accounts`), not pushed |
| Base commit | `b53274d9` (2026-11-17 22:30 +01:00) |
| Head commit before this report | `052e8c5d` (2026-11-18 15:04 +01:00) |
| Commits | 40 |
| Diff | 112 files, 16,790 insertions, 743 deletions |
| Status | Complete for User Stories 1 and 3. User Stories 2 and 4 (web interface) not started, on purpose. |

## 1. Summary

A person now signs in to Sync with a local account stored in the product store. The changes:

- **Removed:** `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` and the resolver that read it.
- **The `admin` account:** defined by `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` and the optional `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, and applied at each start of `sync-api`.
- **Accounts and tokens:** an administrator creates, disables, enables, unlocks and deletes accounts, and sets their password and level. Each account changes its own password and manages its API tokens, through the API and the CLI.
- **Records:** a record holds an account identifier, and every answer shows the account name, or `Deleted account <short identifier>` after a deletion.

The offline unit tier passes with 9,838 tests, 0 failures and 2 xfailed. The 2 xfailed tests predate this work. The Python 3.10 legs also pass, and quickstart scenarios 1 to 5a passed on a development stack.

## 2. Chunk ledger

Each chunk ran in a subagent with a clean context, one at a time.

| # | Chunk | Tasks | ✅ / ⚠️ / ❌ | Commits | Points flagged |
| --- | --- | --- | --- | --- | --- |
| 1 | Setup | T001–T003 | 3 / 0 / 0 | `05eddabc` | The baseline was 8,844 passed. The scrypt setting was confirmed against OWASP. The removed variable was named by 46 files. |
| 2 | The store | T004–T009 | 6 / 0 / 0 | `c44450f6` | `resolve_digest` records a use before the caller decides, so the resolver must delete an idle session. |
| 3 | Passwords, tokens, resolver, `admin` | T010–T018 | 8 / 1 / 0 | `8922f449` | T015 was left partial on purpose: the old resolver stayed so the tests stayed green, and chunk 5 removed it. The password variable is removed from the process environment after it is applied. |
| 4 | Names in answers | T019–T020 | 2 / 0 / 0 | `c1f8bb4c`, `92d4d672` | One ASGI middleware covers every JSON answer, including answers replayed for an `Idempotency-Key`. The event filter by account name broke; chunk 7 fixed it. |
| 5 | Removal of the old variable | T015, T021–T026 | 7 / 0 / 0 | `ba1f5675`, `feded5cd`, `76766dc9`, `fa87bcfa`, `3d57104f`, `83b26ca1` | 31 test files moved to a shared account helper, and 35 store assertions changed from a name to an identifier. `76766dc9` was committed while ty failed; `fa87bcfa` fixed it. Five strict xfails were added until the documentation was written. |
| 6 | Sign-in and sign-out | T027, T033, T034 | 3 / 0 / 0 | `5fad406a`, `4e0b75f0` | Event types use underscores. A NUL character in a name raised an error on PostgreSQL; it is now refused before any store call. |
| 7 | Account and token routes, events, free text | T028–T031, T035–T038 | 8 / 0 / 0 | `472f9b51`, `c5babdc4`, `d4b01aa0` | Stopped by a usage limit and resumed. The earlier check never examined `reason`; a check now runs on every mutation route. |
| 8 | Client and CLI | T032, T039–T042 | 5 / 0 / 0 | `d09a7d7f`, `7f7e1b7b`, `4e0faa4b` | Stopped by a usage limit and finished by a new subagent on Opus 5.5. `--json` is a new option, because the contract named an output option that does not exist. Tracebacks no longer show local variables. |
| 9 | Documentation and quickstart | T043–T046 | 4 / 0 / 0 | `3b6b6c61`, `901ce910`, `5ed76ebc`, `1f4810f2`, `4fe3d931` | Quickstart 1–4 ran on a separate Compose project, because another session's stack used the default project name. `reference/sync-http-api.mdx` is written by hand, not generated. |
| 10 | Delete an account, manage another account | T055–T061 | 7 / 0 / 0 | `ad7f84e8`, `93594bdb`, `612e46b5`, `decec9da`, `dd5e6910` | The deletion is written in one transaction with its event. Quickstart 5 and 5a passed. |
| 11 | Polish | T067–T074 | 8 / 0 / 0 | `22c065b9`, `84f7dfb0`, `9d33e73d`, `06f97d8d`, `c9babc5f`, `1bb6ebfb`, `81ef6025` | Stopped by a usage limit after its last commit. The orchestrator committed the last test change (`81ef6025`). The decision records are numbered 0049 and 0050. |
| Review | Fixes | 11 findings | 11 / 0 / 0 | `b3d3b353`, `577fcdbb`, `5dbc25d1`, `052e8c5d` | See section 5. |

## 3. Tasks not completed

T047–T054 (User Story 2) and T062–T066 (User Story 4), 13 tasks in all, cover the web interface. They were not dispatched. `tasks.md` requires the agreement of the colleague who builds the web interface before they start, and that agreement has not been given. Until then, the web interface's existing token field accepts an API token of an account (FR-015).

Note added on 2026-11-18: the maintainer instructed that User Stories 2 and 4 are built now, to replace the token field from the start. They no longer wait for the colleague's agreement; `tasks.md` and `spec.md` (Assumptions) say so.

All other tasks are ticked (61 of 74).

## 4. Local-pass evidence

The table has one row per test file. Each chunk's report named every test function inside these files, with its case count and its runner line. Those lists live in the chunk reports, not in this file, so this table is the record that the files passed. All runs used Python 3.11.8 on macOS. SQLite means the `local` profile, and pg-emu means the PostgreSQL store class over SQLite. A real PostgreSQL run used a disposable `postgres:16-alpine` container, which was removed afterwards.

| Test file | Type | Run command | Passed at (UTC) | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/product_store/test_accounts.py` | unit, integration | `uv run pytest tests/product_store/test_accounts.py -q`; PostgreSQL: `PRODUCT_STORE_TEST_POSTGRESQL_DSN=… uv run pytest … -m integration` | 2026-11-18T13:42:37Z; PostgreSQL 13:41:45Z | SQLite, pg-emu, PostgreSQL 16 | `45 passed, 20 skipped` (group B); PostgreSQL `127 passed, 571 deselected` |
| `tests/product_store/test_sign_in_attempts.py` | unit, integration | as above | 2026-11-18T13:28:27Z; PostgreSQL 13:28:12Z | SQLite, pg-emu, PostgreSQL 16 | `14 passed, 4 skipped` (group A); PostgreSQL `26 passed, 51 deselected` |
| `tests/product_store/test_account_deletion.py` | unit, integration | `uv run pytest -p no:cacheprovider -v <7 files>`; PostgreSQL `-m integration` | 2026-11-18T10:44:25Z; PostgreSQL 13:41:45Z | SQLite, pg-emu, PostgreSQL 16 | `508 passed, 5 skipped`; PostgreSQL included in `127 passed` |
| `tests/product_store/test_contract.py` (modified) | unit | group B command | 2026-11-18T13:42:37Z | n/a | `45 passed, 20 skipped` |
| `tests/service/test_credentials.py` | unit | group A and D commands | 2026-11-18T14:04:41Z | n/a | `23 passed, 230 deselected` |
| `tests/service/test_account_resolver.py` | unit, integration | chunk 3 run A; group B | 2026-11-18T14:11:08Z (PostgreSQL case) | SQLite, PostgreSQL 16 | `1 passed, 51 deselected` |
| `tests/service/test_admin_account.py` | unit, integration | group C command | 2026-11-18T13:49:54Z | SQLite | `29 passed, 302 deselected` |
| `tests/service/test_account_presentation.py` | unit | chunk 7 command (6 files) | 2026-11-18T08:04:20Z | SQLite | `437 passed` |
| `tests/service/test_sign_in.py` | unit | groups A–C; separate run | 2026-11-18T14:12:02Z | SQLite | `1 passed, 53 deselected` (last case); groups as above |
| `tests/service/test_account_routes.py` | unit | groups B–D | 2026-11-18T14:04:41Z | SQLite | `23 passed, 230 deselected` |
| `tests/service/test_token_routes.py` | unit | groups A–C | 2026-11-18T13:49:54Z | SQLite | `29 passed, 302 deselected` |
| `tests/service/test_free_text.py` | unit | chunk 7 command | 2026-11-18T08:04:20Z | SQLite | `437 passed` |
| `tests/service/test_account_events.py` | unit | chunk 10 command | 2026-11-18T10:44:25Z | SQLite | `508 passed, 5 skipped` |
| `tests/service/test_no_secret_output.py` | unit | `uv run pytest tests/service/test_no_secret_output.py -q -p no:cacheprovider` | 2026-11-18T13:06:19Z | SQLite | `4 passed, 1 warning in 3.73s` |
| `tests/service/accounts.py`, `account_api.py` (helpers) | n/a | unit tier | 2026-11-18 | SQLite | covered by the unit tier |
| 31 existing files moved to the account helper (`tests/service/`, `tests/compose/`, `tests/dev_stack/`, `tests/plan/`, `tests/product_store/`, `tests/image/`) | unit | `uv run invoke tests.tests-unit` | 2026-11-17T23:09:56Z | SQLite | `9094 passed, 4 skipped, 442 deselected, 7 xfailed` |
| `tests/client/test_account_client.py` | unit | chunk 8 run A; chunk 10 | 2026-11-18T10:44:25Z | n/a | `508 passed, 5 skipped` |
| `tests/cli/test_login_commands.py`, `test_account_commands.py`, `test_cli_session.py` | unit | chunk 8 runs A and B; group D | 2026-11-18T09:14:17Z | n/a, Python 3.11 and 3.10 | `720 passed in 5.35s` |
| `tests/compose/test_documentation.py` (xfails removed) | unit | `uv run pytest tests/cli/test_parity_and_closure.py tests/compose/test_documentation.py` | 2026-11-18T09:58:44Z | n/a | `170 passed` |
| `tests/compose/test_configuration_free_startup.py` | compose | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s022-check uv run pytest -m compose …` | 2026-11-17T23:22:35Z | Docker, root `docker-compose.yml`, overlay image | `5 passed in 125.46s` |
| `tests/compose/test_lifecycle.py`, `test_credential_leaks.py`, `test_external_store.py`, compose part of `test_container_cli.py`; `tests/dev_stack/smoke/conftest.py`; `tests/integration/test_managed_write_guard_live.py` | compose, e2e | `docker build -t infrahub-sync:compose-test . && INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose`; `uv run invoke dev.smoke` | deferred: local E2E not run | needs a full image build (frontend submodule missing in the worktree) or the development stack | none |
| Whole offline unit tier | unit | `uv run invoke tests.tests-unit` | 2026-11-18 (after the fixes) | SQLite | `9838 passed, 4 skipped, 491 deselected, 2 xfailed, 0 failed` |
| Python 3.10 legs | unit | `uv run invoke tests.check-310` | 2026-11-18 (after the fixes) | CPython 3.10 | `passed all three Python 3.10 legs` |

## 5. Review findings

Six reviewers ran in parallel, read-only: code, tests, error handling, comments, types and simplification.

| Severity | File | Finding | Outcome |
| --- | --- | --- | --- |
| High | `service/account_actions.py` | Parallel sign-ins passed the refusal check before the password check counted them, allowing tens of guesses for each refusal | Fixed in `b3d3b353`. The refusal is read again inside the gate, and each process runs one check per name at a time. With P processes the limit is 5 + (P − 1). |
| High | `account_actions.py`, `product_store/accounts.py` | A session or token created during a disable or a password reset survived it; a token created during a disable became valid after enable | Fixed in `577fcdbb`. The store creates a session or token only for an active account whose password has not changed, in one transaction. |
| High | `account_actions.py` | An account action and its audit event were written in separate transactions | Fixed in `577fcdbb` for every accepted action. Refusal events stay separate. |
| High | `credentials.py`, `account_actions.py` | A password that UTF-8 cannot encode answered `503` | Fixed in `5dbc25d1`: answers `422 password-invalid`. |
| High | `service/settings.py` | An `admin` password over 1,024 characters was accepted, but no sign-in with it could succeed | Fixed in `5dbc25d1`: refused at start. |
| High | `credentials.py` | A damaged stored hash was treated as a wrong password with no trace | Fixed in `5dbc25d1`: same answer, plus one log line with the account identifier. |
| Medium | `product_store/accounts.py` | A flood of unknown names could delete a refusal in force | Fixed in `b3d3b353`. |
| Medium | `dev/adr/0049`, `reference/sync-http-api.mdx`, `AGENTS.md` | The documents said `admin` cannot be unlocked; the code allows it | Fixed in `052e8c5d`. |
| Low | `sync-http-api.mdx`, `account_actions.py`, `serve.py`, `cli.py`, `credentials.py` | Missing error codes; an `admin` refusal that named the wrong variable; stale comments | Fixed in `052e8c5d`. |
| Medium | `cli_session.py` | A session file that cannot be read is treated as empty | Deferred. Chunk 8's brief chose this behavior for a damaged file; an unreadable file could be told apart. |
| Medium | `presentation.py` | An identifier with no account is shown as it is | Deferred. It cannot happen without a damaged store. |
| Medium | `service/account_actions.py` | Server request models hide secrets in `__repr__` only, not with `Field(repr=False)` | Deferred. No current code path prints them. |
| Low | several | Type invariants not enforced at construction (`Account`, `ResolvedCredential`, `Principal`, `AdminAccountSettings`) | Deferred. |
| Low | `serve.py`, `cli_accounts.py`, `cli.py` | A raw traceback for a store error at start; `login` leaves an open session when it cannot save it; the hint after a failed file change | Deferred. |
| Advisory | several | Eight simplifications, such as a shared token-deletion helper, removing re-exports, and constants written twice | Deferred. |
| Tests, medium | several | Distinct placeholders for two deletions; boundary cases of expiry and idle time; route tests on SQLite only | Deferred. |

## 6. Decisions taken without the maintainer

- **Chunk 3 and T015:** the old resolver was kept for two chunks so that each commit left the tests green.
- **Chunk 8 after the Fable limit:** a new subagent on Opus 5.5 checked and finished the files on disk, instead of resuming the stopped one.
- **Commit trailers:** every commit carries only `Co-Authored-By`, with no `Claude-Session` line, following the maintainer's memory note about session trailers. 0 commits on the branch have one.
- **Quickstart:** it ran on a separate Compose project with other ports, so that another session's development stack was not touched.
- **Review:** six reviewers ran in parallel instead of one after another, to save time. Every high finding and two medium findings that weaken the same protection were fixed; the rest are deferred.
- **Item 1 of the fixes:** the per-name lock is per process, so with several `sync-api` processes the limit is 5 + (P − 1) checks for each refusal, not exactly 5.
- **Decided earlier, still to confirm:** records hold an account identifier (D-10), and the rule of 5 failures in 15 minutes stays as it is, instead of a delay that grows.

## 7. Next steps

1. Confirm decision D-10 and the rule of FR-021.
2. Run the compose E2E tier with a full image build, and the development-stack smoke test: `docker build -t infrahub-sync:compose-test . && INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose`, then `uv run invoke dev.smoke`.
3. Ask the colleague who builds the web interface about User Stories 2 and 4, then run `speckit-opsmill-implement` for T047–T054 and T062–T066.
   Note added on 2026-11-18: no question to the colleague is needed any more. The maintainer instructed that User Stories 2 and 4 are built now; run `speckit-opsmill-implement` for T047–T054 and T062–T066.
4. Plan the merges. The Helm chart of spec 018 and the scheduled runs of spec 021 conflict with this branch: see `notes.md`, section "T074". Spec 021 adds `principal_of` to the resolver that this branch removes, and a schedule must hold an account identifier.
5. Update `research.md`. It still says every `admin` refusal names a variable, and R6 says the rows with the oldest window are deleted.
6. Consider the deferred review findings in section 5.
7. Open a pull request into `feature/v3-develop` when the points above are settled. It needs one `changes/*` label; the changelog fragments are in `changelog/`.
