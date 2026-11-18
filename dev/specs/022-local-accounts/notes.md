# Notes: Sign in to Sync with a local account

Records made while the tasks of [tasks.md](tasks.md) run. Each heading names its task.

## T001: Unit tier before any change

Recorded on 2026-11-17, at commit `b53274d9` of the branch `022-local-accounts`, with
Python 3.11.8 on macOS. No source file had changed.

Commands, run from the worktree root:

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke tests.tests-unit
```

The task runs
`pytest -m "not integration and not smoke and not docker and not compose"`.

Both commands exited with 0. The last line of the pytest output:

```text
8844 passed, 4 skipped, 371 deselected, 2 xfailed, 15 warnings in 223.86s (0:03:43)
```

| Count      | Tests |
|------------|-------|
| Collected  | 9221  |
| Deselected | 371   |
| Selected   | 8850  |
| Passed     | 8844  |
| Skipped    | 4     |
| Xfailed    | 2     |
| Failed     | 0     |
| Errors     | 0     |

No test failed before any change. A test that fails after Phase 2 is caused by this feature.

The 4 skipped tests. A node ID is the file, then `::`, then the test name.

- In `tests/adapters/test_service_extra_sdk_imports.py`:
    - `test_ipfabric_adapter_imports_without_error_when_extra_installed`
    - `test_ipfabric_adapter_constructs_client_without_live_call`
    - `test_ipfabric_adapter_passes_verify_not_verify_ssl`
- In `tests/vendored_prefect_extras/test_executors.py`:
    - `test_executor_docs_disclose_local_engine_limitations`

The 2 expected failures are two parameter sets of one test in
`tests/plan/test_left_out_walk.py`. The test is
`test_each_problem_of_one_record_is_listed_and_the_record_is_left_out_once`, and the
parameter sets are:

- `wrong-value-type-and-dropdown-value`
- `failed-transform-and-dropdown-value`

`uv sync` changed no tracked file. `uv.lock` is unchanged.

## T002: The scrypt setting of research R2

Checked on 2026-11-17 against the OWASP Password Storage Cheat Sheet:
<https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html>

Result: confirmed. The setting of research R2 is one of the settings the cheat sheet lists.

| Parameter | Value of research R2 | Meaning                           |
|-----------|----------------------|-----------------------------------|
| N         | 2^15 (32768)         | CPU and memory cost, about 32 MiB |
| r         | 8                    | Block size, 1024 bytes            |
| p         | 3                    | Degree of parallelism             |

The section "scrypt" of the cheat sheet says "Use one of the following settings" and lists five:

- N=2^17 (128 MiB), r=8 (1024 bytes), p=1
- N=2^16 (64 MiB), r=8 (1024 bytes), p=2
- N=2^15 (32 MiB), r=8 (1024 bytes), p=3
- N=2^14 (16 MiB), r=8 (1024 bytes), p=5
- N=2^13 (8 MiB), r=8 (1024 bytes), p=10

The cheat sheet says of this list: "These configuration settings provide a similar minimal level
of defense, with the main trade-off between parallelism and RAM usage."

One difference in wording to know about. The summary at the top of the cheat sheet names only the
first setting: "use scrypt with a minimum CPU/memory cost parameter of (2^17), a minimum block
size of 8 (1024 bytes), and a parallelization parameter of 1". The section "scrypt" offers the
five settings as alternatives. Research R2 rejected N=2^17 because one verification needs
128 MiB. The cheat sheet does not say which of the two passages takes priority.

The cheat sheet recommends Argon2id first and scrypt "when the former is not available".
Research R2 rejected `argon2-cffi` because it is a new compiled dependency.

## T003: Files that name the removed variable or the removed resolver

Recorded on 2026-11-17 at commit `b53274d9`. This is the checklist for T021 to T025.

Command, run from the worktree root:

```bash
git grep -lE 'BEARER_TOKENS|EnvironmentPrincipalResolver' -- . ':!dev/specs'
```

The pattern `BEARER_TOKENS` also matches `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. The search is
case-sensitive and reads tracked files only, so `node_modules` and `.venv` are not read. A
search of the working tree with `grep -r`, with those directories excluded, returns the same
46 files.

| Group                  | Files |
|------------------------|-------|
| Source                 | 3     |
| Tests                  | 31    |
| Deployment files       | 3     |
| Documentation          | 6     |
| Changelog              | 1     |
| `tasks/`               | 1     |
| Decision records       | 1     |
| Total                  | 46    |

### Differences from research R11

- Research R11 counts 32 test files, 18 of them in `tests/service`. The search returns 31, and
  17 in `tests/service`. The other directories match R11.
- `tests/service/test_serve_site.py` is probably the 18th file. It sets the variable through
  the constant `PRINCIPALS_ENV` of `infrahub_sync/service/auth.py` and names none of the three
  searched terms. It is listed below, outside the 46.
- `dev/adr/0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md` names the
  variable. Research R11 does not list it. Whether an accepted decision record is edited is not
  decided in the spec.

### Matches that are not the full variable name

- `BEARER_TOKENS` alone is a separate Compose variable of the development stack. It supplies
  the value of `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. Four files name it:
  `development/docker-compose.yml`, `tasks/dev.py`, `docs/docs/development-stack.mdx` and
  `tests/dev_stack/test_dev_tasks.py`. The documentation page names only the short form.
- `tests/service/test_environment_names.py` also names `INFRAHUB_SYNC_MANAGED_BEARER_TOKENS`,
  an older name that the test checks is not read.

### Source (3)

- [ ] `infrahub_sync/service/auth.py` (defines the variable name and the resolver)
- [ ] `infrahub_sync/service/serve.py` (names the resolver only)
- [ ] `infrahub_sync/testcontainers/helpers.py`

### Tests in `tests/service` (17)

All name the resolver only, except `test_environment_names.py`, which names both.

- [ ] `tests/service/test_approval.py`
- [ ] `tests/service/test_approval_required.py`
- [ ] `tests/service/test_artifact_confidentiality.py`
- [ ] `tests/service/test_cancel_planned_run.py`
- [ ] `tests/service/test_config_routes.py`
- [ ] `tests/service/test_environment_names.py`
- [ ] `tests/service/test_events_route.py`
- [ ] `tests/service/test_failure_cause_credentials.py`
- [ ] `tests/service/test_http_api.py`
- [ ] `tests/service/test_http_errors.py`
- [ ] `tests/service/test_plan_report_routes.py`
- [ ] `tests/service/test_prefect_wording.py`
- [ ] `tests/service/test_run_logs_route.py`
- [ ] `tests/service/test_sync_store_policy.py`
- [ ] `tests/service/test_validation_secret_containment.py`
- [ ] `tests/service/test_web_site.py`
- [ ] `tests/service/test_worker_environment.py`

### Tests in `tests/compose` (9)

All name the variable only.

- [ ] `tests/compose/conftest.py`
- [ ] `tests/compose/lifecycle.py`
- [ ] `tests/compose/test_compose_contract.py`
- [ ] `tests/compose/test_configuration_free_startup.py`
- [ ] `tests/compose/test_container_cli.py`
- [ ] `tests/compose/test_credential_leaks.py`
- [ ] `tests/compose/test_documentation.py`
- [ ] `tests/compose/test_external_store.py`
- [ ] `tests/compose/test_lifecycle.py`

### Tests in `tests/dev_stack` (2)

- [ ] `tests/dev_stack/test_deps_mode.py`
- [ ] `tests/dev_stack/test_dev_tasks.py`

### Tests in `tests/image` (1)

- [ ] `tests/image/conftest.py`

### Tests in `tests/plan` (1)

- [ ] `tests/plan/test_format_5.py` (names the resolver only)

### Tests in `tests/product_store` (1)

- [ ] `tests/product_store/test_contract.py` (names the resolver only)

### Deployment files (3)

- [ ] `docker-compose.yml`
- [ ] `development/docker-compose.yml`
- [ ] `infrahub_sync/testcontainers/docker-compose.yml`

### Documentation (6)

- [ ] `docs/docs/compose-deployment.mdx`
- [ ] `docs/docs/development-stack.mdx`
- [ ] `docs/docs/operations/compose-troubleshooting.mdx`
- [ ] `docs/docs/operations/day-2-operations.mdx`
- [ ] `docs/docs/quickstart-compose.mdx`
- [ ] `docs/docs/reference/sync-http-api.mdx`

### Changelog (1)

- [ ] `changelog/421.added.md`

### `tasks/` (1)

- [ ] `tasks/dev.py`

### Decision records (1)

- [ ] `dev/adr/0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md`

### Not found by the search, and still affected (1)

- [ ] `tests/service/test_serve_site.py` (sets the variable through `PRINCIPALS_ENV`)

A search for `PRINCIPALS_ENV` returns 21 files. 20 of them are in the list of 46. Search for
`PRINCIPALS_ENV` again when T021 to T025 remove the constant.

## T046: Quickstart scenarios 1 to 4 on the development stack

Run on 2026-11-18, from the worktree of the branch `022-local-accounts`, on macOS with Docker
Engine 29.8.2, with the image `infrahub-sync:dev`. The checkout's `infrahub_sync/` is mounted
into the containers, so the code under test is this worktree's. All four scenarios passed.

### How the stack was started, and why not with `dev.start`

The development stack of another checkout was running under the fixed project name
`infrahub-sync-dev` on the default ports. `dev.start` and `dev.destroy` would have acted on
that stack. So the scenarios ran on a separate Compose project with other ports, started from
this worktree's `development/docker-compose.yml`:

```bash
export SYNC_API_PORT=18001 PREFECT_PORT=14230 STORAGE_POSTGRES_PORT=15440 MINIO_PORT=19010 INFRAHUB_PORT=18000
docker compose -p infrahub-sync-022 -f development/docker-compose.yml up -d --wait --wait-timeout 420 --no-build sync-api
```

This starts PostgreSQL, the object store, the Prefect server, `sync-bootstrap` and `sync-api`,
and not Infrahub or the worker, which the four scenarios do not use. The image was not rebuilt:
its build inputs (`Dockerfile`, `pyproject.toml`, `uv.lock`) are the same as in the checkout
that built it. At the end, the project was removed with `docker compose -p infrahub-sync-022
... down --volumes --remove-orphans`; `sync-api` and `sync-bootstrap` stayed after that
command and were removed with `docker rm -f`, then the project's network and volume.

### Adaptations to quickstart.md

- `INFRAHUB_SYNC_API_URL=http://127.0.0.1:18001`, for the port above.
- Every password was given with `--password-stdin`, because the run had no terminal for the
  prompts. `password --password-stdin` reads two lines: the current password, then the new one.
- The CLI ran as `.venv/bin/infrahub-sync` with `HOME` set to a scratch directory, so the
  session file was written there and not in the user's own application directory.
- `tokens create` was run as `TOKEN=$(infrahub-sync tokens create ci-nightly)`, which the
  command supports: the value is its only line on standard output.
- Scenario 2 and the "without the password" step of scenario 3 need environment that
  `development/docker-compose.yml` does not pass through. Each used a scratch override file
  given with a second `-f`, and `up -d --wait --no-build --no-deps sync-api`. The password
  variable was set to an empty value, because Compose cannot unset a variable that the file
  defines; the empty value takes the same path in the code.
- The password change of scenario 3 used `ADMIN_ACCOUNT_PASSWORD=<new value> docker compose
  ... up -d --wait --no-build --no-deps sync-api`, which recreates `sync-api` only.

### Scenario 1: add a person and give a script a token: pass

```text
$ infrahub-sync login --name admin --password-stdin        -> account: admin / administrator: true
$ infrahub-sync accounts create alice --password-stdin     -> account: alice / administrator: false / disabled: false
$ infrahub-sync logout                                     -> signed out
$ infrahub-sync login --name alice --password-stdin        -> account: alice / administrator: false
$ infrahub-sync whoami                                     -> actor: alice / administrator: false / approval_required: false
$ infrahub-sync password --password-stdin                  -> password changed
$ infrahub-sync runs list                                  -> exit 0, no run
$ TOKEN=$(infrahub-sync tokens create ci-nightly)          -> standard output: <token> (prefix ifs_pat_, 51 characters);
                                                              standard error: note: the token value is not shown again; store it now
$ INFRAHUB_SYNC_API_TOKEN=<token> infrahub-sync whoami     -> actor: alice
$ infrahub-sync tokens list                                -> ci-nightly, token ID, creation, expiry "-", last use
```

`sync-api` started at 09:40:06 UTC, before the first command at 09:40:28 UTC, and had not
restarted when the scenario ended. No file of the deployment changed.

### Scenario 2: the removed variable is ignored: pass

`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` was set on `sync-api` to one entry with a token that no
account has, and `sync-api` was recreated. It started and reported healthy, with the variable
present in its environment.

```text
$ INFRAHUB_SYNC_API_TOKEN=<token of the old variable> infrahub-sync whoami -> status: 401 / code: unauthenticated
GET /api/me with that token                                                -> HTTP 401
GET /api/me with the admin token                                           -> HTTP 200
```

### Scenario 3: the `admin` account: pass

```text
$ infrahub-sync login --name admin --password-stdin        -> account: admin
$ infrahub-sync password                                   -> status: 409 / code: environment-managed-account
$ infrahub-sync accounts disable admin                     -> status: 409 / code: environment-managed-account
$ infrahub-sync accounts set-password admin                -> status: 409 / code: environment-managed-account
$ infrahub-sync tokens create x   (as admin)               -> status: 409 / code: environment-managed-account
```

Each refusal printed: "the environment of the Sync API manages the admin account; change
INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD for its password or INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN for its
token, and restart the Sync API".

Then the password variable was changed and `sync-api` alone was recreated. PostgreSQL, the object
store and the Prefect server kept their start times.

```text
$ infrahub-sync whoami   (session saved before the change)  -> status: 401 / code: unauthenticated
                                                               hint: the Sync API no longer accepts the saved session, so it was removed; run infrahub-sync login
$ infrahub-sync login --name admin   (old password)         -> status: 401 / code: sign-in-refused
$ infrahub-sync login --name admin   (new password)         -> account: admin / administrator: true
```

`sync-api` with an empty password variable exited with code 1, and its log said
`INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD must be set: it is the password of the admin account`. An
additional check with a password of 8 characters exited the same way, with
`INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD must have at least 12 characters`; the log did not contain
the value.

### Scenario 4: repeated failures: pass

`sync-api` was first recreated with the development password again.

```text
attempts 1 to 5, wrong password for alice                  -> status: 401 / code: sign-in-refused / message: the account name or the password was not accepted
attempt 6, right password for alice                        -> the same three lines
$ INFRAHUB_SYNC_API_TOKEN=<token ci-nightly> infrahub-sync whoami -> actor: alice
$ infrahub-sync accounts list   (as admin)                 -> alice  not-administrator  active  <time 15 minutes after the 5th failure>
$ infrahub-sync accounts unlock alice   (as admin)         -> account alice unlocked
$ infrahub-sync login --name alice                         -> account: alice / administrator: false
```

### Checks beyond the four scenarios, for the documentation

- `accounts disable alice`, then `whoami` with her token: `401 unauthenticated`.
  `accounts enable alice`, then the same token: still `401`, and her password signs in.
- The session file was created with mode `0600` (`-rw-------`).
- On this machine, `typer.get_app_dir("infrahub-sync")` returns
  `~/Library/Application Support/infrahub-sync`.

No defect was found.

## T061: Quickstart scenarios 5 and 5a on the development stack

Run on 2026-11-18 from 10:31 to 10:36 UTC, from the worktree of the branch `022-local-accounts`
at commit `612e46b5`, on macOS with Docker Engine 29.8.2 and the image `infrahub-sync:dev`. The
checkout's `infrahub_sync/` is mounted into the containers, so the code under test is this
worktree's. Both scenarios passed. No defect of this chunk was found.

### How the stack was started

As for T046: the development stack of another checkout runs under `infrahub-sync-dev` on the
default ports, so the scenarios ran on the separate Compose project `infrahub-sync-022`, with
the same ports and the same command (`up -d --wait --no-build sync-api`). At the end, `down
--volumes --remove-orphans` removed the volumes; the containers that stayed and the project's
network and certificate volume were removed with `docker rm -f`, `docker network rm` and
`docker volume rm`. No container, network or volume of the project remains.

### Adaptations to quickstart.md

- The adaptations of T046: port `18001`, `--password-stdin`, the CLI as `.venv/bin/infrahub-sync`
  with `HOME` set to a scratch directory.
- Scenario 5 needs runs of `alice`. As `admin`, a configuration was registered from the valid
  package of `tests/configuration/validation_packages.py`; as `alice`, `plan --no-wait` created
  two runs. The project runs no worker, so the runs stay `accepted`.
- `accounts delete alice` ran without a terminal: without `--yes` it refused and named `--yes`
  (exit 2), then ran with `--yes`.
- "The new `alice` cannot apply or cancel": `runs cancel RUN --yes` and `apply RUN
  --expected-checksum <64 characters> --no-wait`, as the new account.
- "A search of the product database": `pg_dump --data-only infrahub_sync` in the project's
  PostgreSQL container, searched for `alice` in any letter case, column by column.

### Scenario 5a: the `admin` password cannot be tested through a reason: pass

```text
$ plan --config <id> --version 1 --no-wait --reason <admin development password>   (as alice)
    -> exit 0, run accepted
$ plan --config <id> --version 1 --no-wait --reason "pasted ifs_pat_abcdef"        (as alice)
    -> exit 1, status: 422 / code: secret-parameter-refused
```

### Scenario 5: disable, enable, delete: pass

```text
$ accounts disable alice                              -> disabled: true
$ INFRAHUB_SYNC_API_TOKEN=<ci-nightly> whoami         -> status: 401 / code: unauthenticated
$ accounts enable alice                               -> disabled: false
$ accounts delete alice      (no terminal, no --yes)  -> exit 2: "standard input is not a terminal, so no
                                                         confirmation can be asked; pass --yes to delete"
$ accounts delete alice --yes                         -> Deleted account 11c3aeb2
$ runs list                                           -> both runs of alice: actor "Deleted account 11c3aeb2"
$ accounts delete alice --yes   (again)               -> status: 404 / code: account-not-found
$ accounts create alice                               -> a new account alice
$ runs cancel RUN --yes         (as the new alice)    -> status: 403 / code: forbidden
$ apply RUN ... --no-wait       (as the new alice)    -> status: 403 / code: forbidden
$ runs list                     (as admin, after)     -> both runs still show "Deleted account 11c3aeb2"
```

The database after the scenario:

- The deleted row has short identifier `11c3aeb2`, no name, no lower-case name, no password hash
  and a deletion time. No token and no session of it remains.
- The new `alice` has another short identifier (`2621f81d`).
- The event `infrahub_sync.account.deleted` is recorded, between `enabled` and the `created` of
  the new account.
- The search of the dump finds `alice` in two columns only: `accounts.name` and
  `accounts.name_key` of the new account. No reason held the name, so no free-text column holds
  it either.

### An observation outside this chunk

`runs cancel RUN --yes` as `admin` passed the rights check (the Sync API reserved a mutation
receipt for it) and answered `503`, code `orchestration-unavailable`, which the service returns
when the Prefect step of a cancellation fails. The flow run had not been picked up, because the
project ran no worker; the cause was not investigated further. This is the cancellation path of
spec 012, not an account behavior, and the rights of an
administrator over the run of the deleted account are covered by
`test_a_run_in_progress_is_not_stopped_by_the_deletion_and_an_administrator_cancels_it`.

## T073: Gates after Phase 7

Run on 2026-11-18, Python 3.11.8, SQLite 3.45.1:

- `uv run invoke format`, `uv run invoke lint` and `uv run ruff check .`: exit 0. `lint` prints 25
  `unused-ignore-comment` warnings of ty, none in a file of Phase 7.
- `uv run invoke tests.tests-unit`: 9765 passed, 4 skipped, 469 deselected, 2 xfailed. The
  baseline of the chunk was 9761 passed; the 4 new cases are in
  `tests/service/test_no_secret_output.py`.
- `uv run invoke tests.check-310`: all three Python 3.10 legs passed (6926 passed, 103 skipped,
  409 deselected, 2 xfailed).
- `uv run infrahub-sync --help` and `uv run infrahub-sync accounts --help`: exit 0.
- `uv run invoke docs.docusaurus`: static files generated. `uv run invoke docs.rumdl` and
  `uv run rumdl check .`: no issues.

## T074: Notes for the pull request

Searched on 2026-11-18 with `git grep -n SERVICE_BEARER_TOKENS <branch> -- .`, keeping the files
that each branch changed since its merge base with `022-local-accounts`. No other branch was
edited.

### The Helm chart of spec 018 names the removed variable

`origin/018-helm-chart-kubernetes` (`dd800065`) holds no chart directory yet. Its values contract
and its operator page name `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`:

- `dev/specs/018-helm-chart-kubernetes/contracts/values.md:21`: the value
  `credentials.bearerTokens` sets the removed variable;
- `docs/docs/kubernetes-deployment.mdx:143` and `:154`: the variable table and the description of
  the principals;
- `dev/specs/018-helm-chart-kubernetes/contracts/rendered-release.md`, `data-model.md`,
  `tasks.md` and `verification.md`.

The branch that merges second replaces `credentials.bearerTokens` with values for
`INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` (required) and `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`
(optional), and updates the Kubernetes page. The same branch also changes
`infrahub_sync/service/serve.py`, `docker-compose.yml`, `development/docker-compose.yml`,
`infrahub_sync/testcontainers/docker-compose.yml` and `tasks/dev.py`, which this branch changes
for the `admin` variables, so the merge has conflicts in these files.

### Spec 021 adds a method to the resolver that this branch removes

`021-scheduled-runs` (`3987a12e`) adds `principal_of(actor)` to the `PrincipalResolver` protocol
and implements it on `EnvironmentPrincipalResolver`, which this branch removes
(`infrahub_sync/service/auth.py`). A schedule keeps the actor name of its owner
(`dev/specs/021-scheduled-runs/research.md`, R8). After both merge:

- `StoreAccountResolver` needs `principal_of`, and a schedule must hold the account identifier,
  not the name, so that ADR 0050 holds: a deleted account owns no schedule, and a new account with
  the same name does not take the schedule over.
- Its tests in `tests/service/` (`schedule_fixtures.py`, `test_schedule_bootstrap.py` and the
  existing tests it changes) build the removed resolver and move to `tests/service/accounts.py`.

### Spec 020 changes the same files

`020-product-store-failure-cause` (`e7b93f09`) changes `infrahub_sync/service/serve.py`,
`docs/docs/compose-deployment.mdx`, `docs/docs/development-stack.mdx` and
`docs/docs/operations/day-2-operations.mdx`, which name the removed variable on that branch.
`019-second-plan-real-changes` changes no file that names it.

### Decision record numbers

- This branch uses 0049 and 0050. 0048 is taken on `020-product-store-failure-cause`.
- 0045 is used twice already: on `018-helm-chart-kubernetes`
  (`0045-the-kubernetes-deployment-orders-its-start-with-a-hashed-job-and-a-bootstrap-record.md`)
  and on `origin/compose-default-internal-credentials`
  (`0045-the-compose-file-defaults-its-internal-credentials.md`).
- `docs/docs/development/adr-index.mdx` on this branch does not list 0043, 0046 or 0047, which
  `dev/adr/README.md` lists.

## T054: User Story 2 checks (2026-11-18)

### Frontend gates

`pnpm check` (Biome, 74 files, no finding), `pnpm typecheck` (`tsc --noEmit`, no error) and
`pnpm build` (Vite) pass in `frontend/`.

### Sign-in part of quickstart scenario 6: pass

Scenario 6 opens `http://127.0.0.1:8001/`. This check served this branch instead, on
`127.0.0.1:18761`, from a throw-away script outside the repository:

- `infrahub_sync.service.serve.build_service` with a SQLite product store in a scratch directory,
  the `admin` account applied from a test password and token through
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` and `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, and
  `INFRAHUB_SYNC_WEB_DIRECTORY` set to `frontend/dist`;
- the fake orchestration of `tests/service/test_http_api.py` in place of Prefect, and no liveness
  reconciler, so nothing reaches a Prefect API;
- seeded through the API: the account `alice` created by `admin`, one configuration registered by
  `admin` (registration needs an administrator), and one plan run started by `alice`.

A headless Chromium (the cached `chromium_headless_shell-1243`, driven by Playwright 1.65.2 for
Python through `uv run --no-project --with playwright`, no dependency added to the repository)
ran these steps. The final run, after the last frontend change, passed every step:

| Step | Result |
|---|---|
| Opening the root address shows the sign-in form and no page content | pass |
| Opening the run's address shows the sign-in form and no page content | pass |
| A wrong password shows "The account name or the password is not accepted." | pass |
| The password field is empty after the refusal | pass |
| The right password signs in; the header shows `alice` and **Sign out** | pass |
| The page asked for (the run) opens after sign-in | pass |
| The run's details show "Started by" `alice` | pass |
| The session token is in `sessionStorage` | pass |
| A reload keeps the session | pass |
| The runs list shows "Started by alice" | pass |
| The configuration page shows "Registered by admin" | pass |
| **Sign out** shows the sign-in form | pass |
| Sign-out removes the token from `sessionStorage` | pass |
| `GET /api/me` with the old session token answers `401` | pass |
| After a second sign-in, a session ended through `POST /api/auth/sign-out` and a reload show the sign-in form and remove the token | pass |

Not exercised in the browser: "approved by", because the fake orchestration produces no plan to
approve; the `503 sign-in-busy` message; the end of a session after the idle period (the
reload after a sign-out through the API checks the same `401` handling).
The second half of scenario 6 ("My account", "Accounts") belongs to User Story 4.

### Unit tier

`uv run invoke tests.tests-unit`: 9922 passed, 4 skipped, 2 xfailed, 0 failed. The baseline was
9923 passed: the two cases of the `dev.frontend` test that printed which token to enter became
one test of the sign-in reminder.

## T066: User Story 4 checks (2026-11-18)

### Frontend gates

`pnpm check` (Biome, 78 files, no finding), `pnpm typecheck` (`tsc --noEmit`, no error) and
`pnpm build` (Vite) pass in `frontend/`. `frontend/src/api/types.generated.ts`, regenerated from
the OpenAPI document of this branch written to a file, did not change.

### Second half of quickstart scenario 6: pass

The check served this branch on `127.0.0.1:18761` as the User Story 2 check did (SQLite product
store in a scratch directory, the fake orchestration, no liveness reconciler, `admin` from a test
password and token). The seed created, through the API, the administrator `alice` and the account
`bob`, which is not an administrator. A headless Chromium (`chromium_headless_shell-1243`,
Playwright 1.65.2 for Python through `uv run --no-project --with playwright`, no dependency added
to the repository) ran these steps. The final run, on the last build, passed all 44 steps:

| Step | Result |
|---|---|
| `bob`: the header links to **My account** and the main navigation has no **Accounts** | pass |
| `bob`: `/accounts` shows "Only an administrator can manage accounts" and no table | pass |
| `bob`: two different new passwords, then a 9-character password, are refused with no `PUT /api/me/password` request | pass |
| `bob`: the password change says that other sessions ended and API tokens still work; the three fields are empty; one request was sent | pass |
| `bob`: the old password is refused and the new one accepted through the API; the tab stays signed in | pass |
| `bob`: an expiry date two days ago is refused with no `POST /api/me/tokens` request | pass |
| `bob`: a token with an expiry date in 30 days shows its value once, read-only, with "This value is not shown again." and **Copy** | pass |
| `bob`: `GET /api/me` with the value answers `200` for `bob` | pass |
| `bob`: the value is in neither `sessionStorage`, `localStorage` nor the address, and is gone after a reload | pass |
| `bob`: the list shows the token with an expiry date and without its value | pass |
| `bob`: the token is deleted after its confirmation; the list is empty; the value answers `401` | pass |
| `alice`: **Accounts** lists `admin`, `alice` and `bob`; `admin` shows "Defined by the deployment's environment" and no action | pass |
| `alice`: the menu of her own row offers only **Disable**, **View API tokens** and **Delete** | pass |
| `alice`: two different initial passwords are refused; `carol` is created and signs in through the API | pass |
| `alice`: `carol` made an administrator, then not; focus returns to the **Actions** button of `carol` | pass |
| `alice`: a password set for `carol` signs in; the first password is refused | pass |
| `alice`: `carol` disabled, and her sign-in through the API is refused; the **Active** filter hides her, **Disabled** shows only her | pass |
| `alice`: `carol` enabled, and her sign-in is accepted | pass |
| `alice`: after five wrong sign-ins through the API, the row of `carol` shows "Sign-in refused until"; **Allow sign-in again** ends the refusal and she signs in | pass |
| `alice`: after five wrong sign-ins for `admin`, its only action is **Allow sign-in again**; after it, `admin` has no action | pass |
| `alice`: **View API tokens** of `carol` lists her token without its value; deleting it makes the value answer `401`; focus returns after **Close** | pass |
| `alice`: the delete dialog says the deletion cannot be reversed and that disabling can be; its button is refused until `carol` is typed | pass |
| `alice`: the deletion shows `Deleted account <short identifier>`; `carol` cannot sign in | pass |
| `admin`: **My account** shows that the environment defines the account, and offers no password or token action | pass |

At a width of 390 pixels the pages have no horizontal scroll; the main navigation scrolls instead.

Not exercised in the browser: the `503 sign-in-busy` message; disabling or deleting one's own
account (the page then shows the sign-in form; checked only by reading the code); the Clipboard
API refusal (the run granted the clipboard permission, so **Copy** took the Clipboard API path).

### Unit tier

`uv run invoke tests.tests-unit`: 9922 passed, 4 skipped, 2 xfailed, 0 failed, as before. No
Python file changed.
