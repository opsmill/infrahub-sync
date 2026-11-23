# Quickstart: validate that a contended write names the run that holds the lock

## Prerequisites

- Python 3.11–3.13 and the full profile: `uv sync --extra dev --extra prefect --extra service`.
- Docker, for a disposable PostgreSQL server.

## Unit tier

```bash
uv run pytest tests/service/test_apply_guard.py tests/service/test_managed_write_guard.py \
  tests/service/test_failure_cause.py tests/cli/test_failure_output.py tests/product_store/test_write_admission.py -q
```

Expected: all pass, including the label parsing cases (exact prefix, wrong prefix, 63 bytes, invalid run ID),
the "lookup raised" case and the three template variants.

## Real PostgreSQL tier

```bash
docker run -d --rm --name guard-pg16 -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=probe \
  -p 127.0.0.1:55433:5432 postgres:16-alpine
export APPLY_GUARD_TEST_POSTGRESQL_DSN="host=127.0.0.1 port=55433 user=postgres password=probe dbname=probe"
export PRODUCT_STORE_TEST_POSTGRESQL_DSN="$APPLY_GUARD_TEST_POSTGRESQL_DSN"
# The same task the pull-request CI job runs; it fails on any skip, an unset DSN or an empty selection.
uv run invoke tests.tests-product-store-postgresql
docker stop guard-pg16
```

Expected:

- Run A holds the lock, runs B and C wait: C's contention error names A, never B.
- A holder without a label, or with a 63-byte label: the candidate is `None`.
- A holder connected with another account: the candidate is `None` or A, never another value. The test output
  records which one the server returned.

Point the DSN only at a disposable database: the tests terminate backends on it.

In CI, the "Product store on PostgreSQL" job of `.github/workflows/workflow-tests.yml` runs the same task on every
pull request that changes Sync files. Check that its log lists `tests/integration/test_apply_guard_integration.py`
and reports no skipped test.

## Web interface

```bash
cd frontend && pnpm test
```

Expected: `failureDetail` sets `activeRunId` for a failure record with a run ID, and leaves it unset when the
field is `null`, absent or not a string. In the running web interface, a failed `sync` that names another run
shows an "Open run" link to that run's page in its failure notice.

## Gates

```bash
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
```
