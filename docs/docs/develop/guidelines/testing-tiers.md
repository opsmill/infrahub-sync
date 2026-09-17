---
title: "Testing tiers"
---

## Testing tiers

> Part of: Develop > Guidelines | Related: [Testing](testing.md), [Quality gates](../knowledge/quality-gates.md), [Qualifying an internal candidate](../guides/qualifying-an-internal-candidate.md)

**Verified 2026-09-16 against source revision
[`61b6a1b9dccae637b522084f563858dfcd5e31a9`](https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9).** The commands, markers
and skip behavior below were read at that exact revision from
[`tasks/tests.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tasks/tests.py),
[`tasks/__init__.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tasks/__init__.py),
[`tasks/preview.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tasks/preview.py),
[`tasks/compose.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tasks/compose.py),
the modules under [`tests/integration/`](https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/integration) and the
`[tool.pytest.ini_options]` markers in [`pyproject.toml`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/pyproject.toml). Nothing here was
established by a live replay.

Which test command to run, what each one needs before it can prove anything, and what it
writes. [Testing](testing.md) covers what makes an individual test worth having; this page
covers which suite it belongs in and which gate runs it.

### A skipped check is not a pass

Most of the suites below skip themselves when their prerequisite is missing, and a skip exits
zero. That is deliberate — it keeps a contributor without Docker from being blocked — but it
means **a green result only proves what actually ran**. Before claiming a tier passed, confirm
the suite was collected and executed, not skipped. The two places this matters most are the
preview smoke suite, where every test skips when the stack is unreachable, and
`uv run invoke check-310`, which skips all three of its legs at once.

### The offline default

```bash
uv run invoke tests.tests-unit
```

This is the single offline default, and it is what a change has to keep green. It runs:

```text
pytest -m "not integration and not preview and not docker and not builder and not compose"
```

So it excludes five marker families: `integration`, `preview`, `docker`, `builder` and
`compose`. It needs no network, no credentials, no Docker daemon and no running stack.

Below Python 3.11 the task adds two ignores, because the Sync service is unavailable there:

```text
--ignore=tests/service --ignore=tests/runtime_schema/test_worker_path.py
```

Prefer the task over a hand-written `pytest` invocation. A bare `pytest -q` collects the
preview and integration suites against whatever environment your shell happens to name.

### The tiers

| Tier | Command | Needs | Writes |
|---|---|---|---|
| Unit | `uv run invoke tests.tests-unit` | Nothing beyond the installed extras | Nothing outside `tmp_path` |
| Integration | `uv run invoke tests.tests-integration` | Varies by family — see below; no single set of variables covers the tier | Whatever live target each family names |
| Preview smoke | `uv run invoke preview.smoke` | The development stack, started with `preview.up` | Seeds and writes to the disposable stack |
| Compose lifecycle | `uv run invoke compose.lifecycle` | An already built and loaded candidate image, and a Docker daemon | A real container stack it brings up and tears down |
| Clean-host qualification | See the candidate guide | A checkout-free host holding only the artifact | A real deployment |

#### Integration

```bash
uv run invoke tests.tests-integration
```

Runs `pytest -m integration`, which is repository-wide. **This tier is not homogeneous, and it
is not confined to `tests/integration/`.** It selects several families with different
prerequisites and different live targets, each skipping on its own, so configuring one family
leaves the others green and unproven. Route by family rather than assuming one setup covers the
tier:

| Family | Needs | Where |
|---|---|---|
| Infrahub destination | `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN` | destination schema read, keyed write, node conversion, replace-set shrink |
| Apply guard | A disposable PostgreSQL at `APPLY_GUARD_TEST_POSTGRESQL_DSN`, plus `psycopg` (and Prefect for the managed variant) | apply-guard and managed write-guard |
| Saved-plan apply | `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` **plus** `NETBOX_URL` and `NETBOX_TOKEN` | saved-plan apply |
| Remote run | `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`, `PREFECT_API_URL`, and a separately served deployment the test resolves | remote-run |
| Durable store | `INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL`, `_S3_BUCKET` and `_S3_ENDPOINT_URL`, plus `boto3` and `psycopg` | service storage, isolated worker handoff |
| Live stack | A running development stack, probed rather than configured | managed write-guard live |
| Prefect idempotency | The `prefect` and `opsmill_prefect_extras` imports only | [`tests/integration/test_service_prefect_idempotency.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/integration/test_service_prefect_idempotency.py) |
| Product store on PostgreSQL | A disposable PostgreSQL at `PRODUCT_STORE_TEST_POSTGRESQL_DSN`, plus `psycopg` | [`tests/product_store/test_contract.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/product_store/test_contract.py), [`test_configuration_baseline.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/product_store/test_configuration_baseline.py), [`test_write_admission.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/product_store/test_write_admission.py), [`tests/service/test_apply_versus_verify_race.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/service/test_apply_versus_verify_race.py) |
| Redis store compatibility | A reachable `REDIS_URL` | [`tests/test_redis_store_compat.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/test_redis_store_compat.py) |

Three of those deserve their setup stated here, because they are the ones a reader will not find
by looking in `tests/integration/`:

- **Prefect idempotency** needs no external service. It skips unless `prefect` and
  `opsmill_prefect_extras` import, then starts Prefect's own isolated temporary API server with
  `PREFECT_HOME` and `PREFECT_LOCAL_STORAGE_PATH` redirected under `tmp_path`. It writes only
  that temporary state and tears it down.
- **Product store on PostgreSQL** is two different things under one DSN. Three modules —
  [`test_configuration_baseline.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/product_store/test_configuration_baseline.py),
  [`test_write_admission.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/product_store/test_write_admission.py) and
  [`tests/service/test_apply_versus_verify_race.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/service/test_apply_versus_verify_race.py)
  — parametrize their contracts over SQLite and PostgreSQL, and only the `postgresql`
  parameter carries the `integration` mark. Alongside them,
  [`test_contract.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/product_store/test_contract.py) contributes one standalone
  marked test, `test_postgresql_run_store_initializes_against_a_real_server`, which is not
  parametrized: it is a schema-bootstrap check that only a real server can make.

  Set `PRODUCT_STORE_TEST_POSTGRESQL_DSN` and install `psycopg` for both. Each creates one
  generated schema and drops only that schema, and its scoped `search_path` deliberately
  excludes `public`, so a DSN aimed at the wrong database cannot reach another schema's
  tables:

  ```bash
  PRODUCT_STORE_TEST_POSTGRESQL_DSN="postgresql://postgres:probe@127.0.0.1:55433/storeprobe" \
    uv run --with 'psycopg[binary]' pytest -m integration tests/product_store tests/service
  ```

- **Redis store compatibility** runs one functional round trip against a live server. Set
  `REDIS_URL`; the test pings it first and skips when it is unset or unreachable. It writes
  adapter state under its own store identifiers.

  ```bash
  REDIS_URL="redis://127.0.0.1:6379/0" uv run pytest -m integration tests/test_redis_store_compat.py
  ```

Most of the modules under [`tests/integration/`](https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/integration)
carry their own exact setup in a module docstring, including the disposable-target warnings —
read it rather than copying variables between families. The guard DSN, the product-store DSN and
the durable-store settings must all point at single-purpose throwaway databases.

What these tests do to their targets differs, and the difference matters when you choose what to
point them at:

- **Prefect idempotency** contacts nothing external. It writes only temporary local state under
  `tmp_path` and tears it down.
- [`test_destination_schema_live_read.py`](https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests/integration/test_destination_schema_live_read.py)
  reads a live Infrahub's schema and performs no mutation.
- **Every other live-backed family** mutates, locks or writes the target it names — Infrahub
  branches and nodes, the guard and product-store databases, the durable store, Redis, or the
  development stack. Point each of those at something disposable.

#### Preview smoke

```bash
uv run invoke preview.up
uv run invoke preview.smoke
```

`preview.smoke` seeds the smoke dataset and then runs `pytest -m preview tests/preview -q`
against the running stack. It writes: the seed creates a device on `main` and forks the
`preview-smoke` branch, and the suite drives real plan and apply runs against that branch.
Every test in it skips rather than fails when the stack is not reachable.

The suite runs in a single process by design. Its modules share one Infrahub branch and one
Prefect deployment, and a collection hook orders them against each other; a distributed run
would split that ordering across workers.

One module under `tests/preview/` is not part of that tier.
`tests/preview/test_evidence.py` carries no `preview` marker, so it already runs in the
ordinary unit gate. It covers the evidence helpers offline and reads
[Contributing](../../contributing.mdx) to hold the documented setup command to the pinned
package manager. If you touch that page, run it directly as a fast pre-check:

```bash
uv run pytest -q tests/preview/test_evidence.py
```

[Local development stack](../../development-stack.mdx) is the full procedure for the stack
itself.

#### Compose lifecycle

```bash
uv run invoke compose.lifecycle
```

This one does **not** build anything. It consumes the candidate image the image gate already
built and loaded, addressed by the configuration digest that build recorded, and refuses to
run when the daemon does not hold it — building here would qualify a different artifact.

It enforces its own zero-skip policy internally: the task passes `--compose-zero-skip` and
runs single-process, so under it a skipped `compose`-marked case is a failed one. Running
`pytest tests/compose -m compose` directly keeps the ordinary Docker and platform skips
instead, which is useful while developing a case and useless as a qualification claim.

#### Clean-host qualification

The checkout-free driver and the full qualification route are already explained by
[Qualifying an internal candidate](../guides/qualifying-an-internal-candidate.md). That
procedure deliberately uses no interpreter, package manager or checkout on the host, because
the claim being made is that a host holding the artifact alone can run it. Do not reproduce
its steps here.

### `check-310`

```bash
uv run invoke check-310
```

This reproduces the three CI legs your active environment cannot reach:

1. `ty` with the Python 3.10 exclusions — `--exclude infrahub_sync/service --exclude tests/service`;
2. the unit tests on Python 3.10 with the `dev` and `prefect` extras;
3. the unit tests on Python 3.10 with the `dev` extra alone, which is the base install that
   has no service dependencies.

It builds its environments under `.preview/check-310-venv` and leaves your active `.venv`
alone, and it stops at the first failing leg.

**When Python 3.10 is not installed it skips loudly and exits zero, leaving all three legs
unqualified.** The output names each leg it did not check. Install an interpreter with
`uv python install 3.10` and re-run before treating the task as evidence.

### Related

- [Testing](testing.md) — what makes a test worth having.
- [Quality gates](../knowledge/quality-gates.md) — what `invoke lint` and `invoke format` run.
- [Testing adapters](testing-adapters.md) — the coverage an adapter must ship.
- [Testing an adapter](../guides/testing-an-adapter.md) — how to write and run those tests.
- [Repository tour](../knowledge/repository-tour.md) — which part of the tree each suite covers.
