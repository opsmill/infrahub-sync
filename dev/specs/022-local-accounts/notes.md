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
