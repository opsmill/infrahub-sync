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
APPLY_GUARD_TEST_POSTGRESQL_DSN="host=127.0.0.1 port=55433 user=postgres password=probe dbname=probe" \
  uv run pytest -m integration tests/integration/test_apply_guard_integration.py -q
PRODUCT_STORE_TEST_POSTGRESQL_DSN="host=127.0.0.1 port=55433 user=postgres password=probe dbname=probe" \
  uv run pytest tests/product_store/test_write_admission.py -q
docker stop guard-pg16
```

Expected:

- Run A holds the lock, runs B and C wait: C's contention error names A, never B.
- A holder without a label, or with a 63-byte label: the candidate is `None`.
- A holder connected with another account: the candidate is `None` or A, never another value. The test output
  records which one the server returned.

Point the DSN only at a disposable database: the tests terminate backends on it.

## Gates

```bash
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
```
