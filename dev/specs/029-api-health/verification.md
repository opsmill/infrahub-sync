# Verification of spec 029

## T022: User Story 1 gate (2026-11-23)

Python 3.11, with the dev, prefect and service extras.

| Command | Result |
| --- | --- |
| `uv run pytest tests/service tests/client -q` | 3364 passed, 32 skipped, at 2026-11-23T22:00:52Z |
| `uv run pytest tests/dev_stack -m "not smoke" -q` | 297 passed, 1 skipped, 26 deselected |
| `uv run invoke format` | No finding left |
| `uv run invoke lint` | Exit 0. ty reports 31 `unused-ignore-comment` warnings, all in files this spec does not change |
| `uv run ruff check .` | All checks passed |

The two tests that need real services ran separately:

- `tests/service/test_health_postgresql.py`: 3 passed at 2026-11-23T22:03:55Z, with
  `PRODUCT_STORE_TEST_POSTGRESQL_DSN` set to the development stack's database on
  `127.0.0.1:5440`. Without that variable, the file is skipped.
- `tests/dev_stack/smoke/test_health.py`: 1 passed at 2026-11-23T21:50:21Z. The running
  development stack was started from another worktree, and its `sync-api` has no `/api/health`
  route yet. To test this branch's route, the Sync API of this worktree ran on the host, on port
  8011, with the environment of `dev.api` (`tasks.dev.host_environment`). That run used the stack's
  PostgreSQL, object store, Prefect server and worker. A pytest plugin pointed the suite's Sync API
  address at port 8011. On a stack started from this branch, the command is
  `uv run pytest tests/dev_stack/smoke/test_health.py -m smoke`.

The offline unit tier (`uv run invoke tests.tests-unit`) then passed, 11235 passed and 12 skipped at 2026-11-23T22:18:12Z. Its first run found five failures that the new
Compose variables caused:

- `infrahub_sync/testcontainers/docker-compose.yml` must be a byte-for-byte copy of
  `docker-compose.yml`. The root file was copied over it.
- `docs/docs/compose-deployment.mdx` must name every variable that `docker-compose.yml` reads. The
  two health settings were added to its table of settings and to its list of start-up refusals.
  T044 rewrites the status section of that page.
