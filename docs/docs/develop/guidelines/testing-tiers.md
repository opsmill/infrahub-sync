---
title: "Testing tiers"
---

## Testing tiers

> Part of: Develop > Guidelines | Related: [Testing](testing.md), [Quality gates](../knowledge/quality-gates.md), [Qualifying an internal candidate](../guides/qualifying-an-internal-candidate.md)

**Checked 2026-09-29 against source revision
[`d9ef147c569a42ec4471bba78ec270c343cdfa28`](https://github.com/opsmill/infrahub-sync/tree/d9ef147c569a42ec4471bba78ec270c343cdfa28).** The commands, markers,
settings and skip behavior below were read at that revision from
[`tasks/tests.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tasks/tests.py),
[`tasks/__init__.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tasks/__init__.py),
[`tasks/preview.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tasks/preview.py),
[`tasks/compose.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tasks/compose.py),
the modules under [`tests/integration/`](https://github.com/opsmill/infrahub-sync/tree/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/integration) and the
`[tool.pytest.ini_options]` markers in [`pyproject.toml`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/pyproject.toml). The unit tier
and `check-310` were run at that revision. The integration sequence below was not replayed
live for this revision; the nightly workflow runs a similar sequence.

The Redis rows and steps were removed afterward, because V3 refuses configured sync stores; that part of the guide describes the later test classification, not this revision.

Which test command to run, what each one needs before it can prove anything, and what it
writes. [Testing](testing.md) covers what makes an individual test worth having; this page
covers which suite it belongs in and which gate runs it.

### A skipped check is not a pass

Most of the suites below skip themselves when their prerequisite is missing, and a skip exits
zero. That is deliberate for the live tiers: a missing external service or a stack that is not
running leaves those tests skipped rather than blocking you. It does **not** extend to
everything — the offline default needs the `docker compose` CLI, and without it the unmarked
preview configuration test fails rather than skipping (see
[The offline default](#the-offline-default)).

Where a suite does skip, **a green result only proves what actually ran**. Before claiming a
tier passed, confirm it was collected and executed, not skipped. The two places this matters
most are the preview smoke suite, where every test skips when the stack is unreachable, and
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
`compose`. It needs no network, no credentials and no running stack.

**It does need the `docker compose` CLI on your PATH.** One unmarked module under
`tests/preview/` shells out to `docker compose … config --format json` to resolve the preview
Compose files, and it does so with `check=True` and no availability guard — so on a machine
without that CLI the offline default **fails** with `FileNotFoundError` rather than skipping.
That command resolves the Compose files and starts no service. Install Docker Desktop, or the
Compose plugin, before treating a red offline run as a real regression.

Below Python 3.11 the task adds two ignores, because the Sync service is unavailable there:

```text
--ignore=tests/service --ignore=tests/runtime_schema/test_worker_path.py
```

Prefer the task over a hand-written `pytest` invocation. A bare `pytest -q` collects the
preview and integration suites against whatever environment your shell happens to name.

### The tiers

| Tier | Command | Needs | Writes |
|---|---|---|---|
| Unit | `uv run invoke tests.tests-unit` | The installed extras, plus the `docker compose` CLI on your PATH | Nothing outside `tmp_path` |
| Integration | `uv run invoke tests.tests-integration` | Varies by family — see below; no single set of variables covers the tier | Varies by family: read-only, temporary local state, or a disposable live target — see below |
| Preview smoke | `uv run invoke preview.smoke` | The preview stack, started with `preview.up` | Seeds and writes to the disposable stack |
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

The two tables below are the complete reference: the first names each family and what it
needs, the second names each setting and where to get it.

| Family | Needs | Where |
|---|---|---|
| Infrahub destination | `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN` | destination schema read, keyed write, node conversion, replace-set shrink |
| Apply guard | A disposable PostgreSQL at `APPLY_GUARD_TEST_POSTGRESQL_DSN`, plus `psycopg` (and Prefect for the managed variant) | apply-guard and managed write-guard |
| Saved-plan apply | `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` **plus** `NETBOX_URL` and `NETBOX_TOKEN` | saved-plan apply |
| Remote run | `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`, `PREFECT_API_URL`, and a separately served deployment the test resolves | remote-run |
| Durable store | `INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL`, `_S3_BUCKET` and `_S3_ENDPOINT_URL`, plus `boto3` and `psycopg` | service storage, isolated worker handoff |
| Live stack | A running development stack, probed rather than configured | managed write-guard live |
| Prefect idempotency | The `prefect` and `opsmill_prefect_extras` imports only | [`tests/integration/test_service_prefect_idempotency.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/integration/test_service_prefect_idempotency.py) |
| Product store on PostgreSQL | A disposable PostgreSQL at `PRODUCT_STORE_TEST_POSTGRESQL_DSN`, plus `psycopg` | [`tests/product_store/test_contract.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/product_store/test_contract.py), [`test_configuration_baseline.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/product_store/test_configuration_baseline.py), [`test_write_admission.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/product_store/test_write_admission.py), [`tests/service/test_apply_versus_verify_race.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/service/test_apply_versus_verify_race.py) |

| Setting | Points to | How to get it from the local stacks |
|---|---|---|
| `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN` | A disposable Infrahub | The preview Infrahub: `http://localhost:8080`, and `INFRAHUB_INITIAL_ADMIN_TOKEN` from `development/preview.env` |
| `NETBOX_URL`, `NETBOX_TOKEN` | A NetBox with the `seed` dataset | Printed by `uv run invoke netbox.seed` |
| `PREFECT_API_URL` | A Prefect server that has the served `infrahub-sync` deployment | The preview Prefect: `http://localhost:4210/api`. Start the deployment separately, as the remote-run module docstring describes |
| `APPLY_GUARD_TEST_POSTGRESQL_DSN` | A disposable PostgreSQL database | A separate database on the preview PostgreSQL (port 5439). These tests terminate backends on it |
| `PRODUCT_STORE_TEST_POSTGRESQL_DSN` | A disposable PostgreSQL database | A second separate database on the preview PostgreSQL |
| `INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL` | A disposable PostgreSQL database | A third separate database on the preview PostgreSQL |
| `INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET`, `INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL` | An S3-compatible bucket | The preview MinIO: bucket `infrahub-sync-preview` at `http://127.0.0.1:9010`. `preview.up` creates the bucket |
| `INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_PREFIX`, `INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_REGION` | Optional; the key prefix and region | Defaults: `integration` for the storage test, `us-east-1` for the region |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Credentials for that bucket | `PREVIEW_MINIO_ACCESS_KEY` and `PREVIEW_MINIO_SECRET_KEY` from `development/preview.env` |

Use a separate, empty database for each of the three PostgreSQL settings. Do not reuse the
preview service's own `infrahub_sync` database: these tests lock, terminate, create, and drop
objects in the database they receive.

Three families need more detail than the tables give:

- **Saved-plan apply** needs a NetBox reachable at `NETBOX_URL`, seeded with the fixed dataset
  the test module's own docstring describes (sites `site-a`/`site-b`/`site-c`, racks
  `rack-site-<x>-<n>`, devices `dev-01`…`dev-40`, tags `tag-01`…`tag-10`). `development/netbox/`
  provisions exactly that NetBox, disposable and local, following the same pattern as the
  preview environment's `development/docker-compose.preview.yml`:

  ```bash
  uv run invoke netbox.seed        # starts NetBox, resets it, loads the `seed` dataset, and prints the URL and token
  ```

  `netbox.seed` starts NetBox itself and prints its URL and development token when it
  finishes — running `netbox.up` first is unnecessary and makes NetBox run its first
  migration twice; on a small host that first migration can take 15 minutes or more. Use
  `netbox.up` on its own only for an empty NetBox with nothing loaded, or to reprint the
  banner later without touching the already-running containers.
  `netbox.seed` accepts `--dataset`: `seed` (the default) for this test, or `demo` for
  [the `from-netbox` example check](#the-from-netbox-example-check). Each dataset replaces
  the whole NetBox database. Point
  `INFRAHUB_ADDRESS` and
  `INFRAHUB_API_TOKEN` at a disposable Infrahub with the schema library loaded (see step 4 of
  [Run the complete tier](#run-the-complete-tier)) — the test writes to it and does not clean up, so reset it
  (`invoke preview.down --volumes`, `preview.up`, reload the schema) between runs. Then:

  ```bash
  uv run pytest -m integration tests/integration/test_saved_plan_apply_integration.py
  ```

  `uv run invoke netbox.down` removes the NetBox containers and their data volumes.
- **Prefect idempotency** needs no external service. It skips unless `prefect` and
  `opsmill_prefect_extras` import, then starts Prefect's own isolated temporary API server with
  `PREFECT_HOME` and `PREFECT_LOCAL_STORAGE_PATH` redirected under `tmp_path`. It writes only
  that temporary state and tears it down.
- **Product store on PostgreSQL** is two different things under one DSN. Three modules —
  [`test_configuration_baseline.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/product_store/test_configuration_baseline.py),
  [`test_write_admission.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/product_store/test_write_admission.py) and
  [`tests/service/test_apply_versus_verify_race.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/service/test_apply_versus_verify_race.py)
  — parametrize their contracts over SQLite and PostgreSQL, and only the `postgresql`
  parameter carries the `integration` mark. Alongside them,
  [`test_contract.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/product_store/test_contract.py) contributes one standalone
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

V3 refuses configured sync stores, including Redis. The remaining Redis compatibility
checks are unit tests and need no Redis server:

```bash
uv run pytest tests/test_redis_store_compat.py tests/test_sync_store_policy.py tests/service/test_sync_store_policy.py
```

Most of the modules under [`tests/integration/`](https://github.com/opsmill/infrahub-sync/tree/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/integration)
also describe their setup in a module docstring, including the warnings about disposable
targets.

##### Run the complete tier

This sequence uses the preview stack and the local NetBox. Run it
from the repository root. The values in angle brackets come from `development/preview.env` or
from the `netbox.seed` output; do not commit them.

1. Start the services and create the three test databases:

   ```bash
   uv run invoke preview.up
   for database in apply_guard_test product_store_test storage_test; do
     docker exec infrahub-sync-preview-sync-postgres-1 createdb -U postgres "$database"
   done
   ```

2. Set the environment:

   ```bash
   export INFRAHUB_ADDRESS="http://localhost:8080"
   export INFRAHUB_API_TOKEN="<INFRAHUB_INITIAL_ADMIN_TOKEN>"
   export PREFECT_API_URL="http://localhost:4210/api"
   export APPLY_GUARD_TEST_POSTGRESQL_DSN="postgresql://postgres:postgres@127.0.0.1:5439/apply_guard_test"
   export PRODUCT_STORE_TEST_POSTGRESQL_DSN="postgresql://postgres:postgres@127.0.0.1:5439/product_store_test"
   export INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5439/storage_test"
   export INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET="infrahub-sync-preview"
   export INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL="http://127.0.0.1:9010"
   export AWS_ACCESS_KEY_ID="<PREVIEW_MINIO_ACCESS_KEY>"
   export AWS_SECRET_ACCESS_KEY="<PREVIEW_MINIO_SECRET_KEY>"
   ```

   For the remote-run test, also start the served deployment in a second terminal, as
   [`test_remote_run_live.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/integration/test_remote_run_live.py)
   describes. Without it, that test skips.

3. Run the tier with the skip report. Leave `NETBOX_URL` and `NETBOX_TOKEN` unset here, because
   the saved-plan family needs its own fresh Infrahub (step 4):

   ```bash
   uv run pytest -m integration -rs
   ```

   The default summary gives only a skip count. `-rs` adds one `SKIPPED` line for each skipped
   test, and the line names the missing setting or unreachable service. Check those lines before
   you report the tier as passed.

4. Run the saved-plan family on a fresh Infrahub, with the schema snapshot loaded:

   ```bash
   uv run invoke preview.down --volumes
   uv run invoke netbox.seed
   uv run invoke preview.up
   uv run infrahubctl schema load tests/data/nightly_schema --wait 120
   export NETBOX_URL="<URL printed by netbox.seed>"
   export NETBOX_TOKEN="<token printed by netbox.seed>"
   uv run pytest -m integration -rs tests/integration/test_saved_plan_apply_integration.py
   ```

   `preview.down --volumes` also removes the three test databases, so run step 4 after step 3.

5. Remove the services and their data:

   ```bash
   uv run invoke preview.down --volumes
   uv run invoke netbox.down
   ```

   Stop the served deployment if you started one.

The nightly workflow runs the same families through `.github/scripts/nightly_e2e.py`, which
derives the settings from the preview configuration.

What these tests do to their targets differs, and the difference matters when you choose what to
point them at:

- **Prefect idempotency** contacts nothing external. It writes only temporary local state under
  `tmp_path` and tears it down.
- [`test_destination_schema_live_read.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/integration/test_destination_schema_live_read.py)
  reads a live Infrahub's schema and performs no mutation.
- **Every other live-backed family** mutates, locks or writes the target it names — Infrahub
  branches and nodes, the guard and product-store databases, the durable store, or the
  development stack. Point each of those at something disposable.

##### The `from-netbox` example check

The `from-netbox` example check runs the shipped `examples/netbox_to_infrahub` package
through the Sync API: register it, then `diff` and `sync` into an Infrahub branch. It is not a
pytest test. Run it by hand when you change the example mapping, the NetBox adapter, or the
pinned NetBox image, and record the result in the pull request. The nightly
end-to-end workflow also runs it against the pinned local `demo` dataset.

It runs against the local `demo` dataset, never the public NetBox demo. The public demo is
shared, so other users change its data between runs. The `demo` dataset is the official
NetBox demo data from `netbox-community/netbox-demo-data`, restored from a SQL dump that is
pinned to one commit and one SHA-256 checksum. `netbox.seed --dataset demo` downloads the
dump into the `.netbox/` directory, which Git ignores. It stops before it touches the database
if the checksum does not match.

Run these commands from the repository root:

1. Load the `demo` dataset. The task prints the NetBox URL, `http://localhost:8082`, and the
   development token.

   ```bash
   uv run invoke netbox.seed --dataset demo
   ```

2. Start a fresh preview stack. The worker resolves the package's `netbox-token` credential
   from its own environment, so set `NETBOX_TOKEN` to the token that step 1 printed before
   `preview.up` starts the worker. `preview.up` sets `INFRAHUB_API_TOKEN` for the worker
   itself.

   ```bash
   uv run invoke preview.down --volumes
   NETBOX_TOKEN="<token printed by netbox.seed>" uv run invoke preview.up
   ```

3. Load the schema library into the preview Infrahub. The example maps onto the 16 schemas
   of the `infrahub/traditional-infrastructure-sot` Marketplace collection.
   `tests/data/nightly_schema/` contains a snapshot of that collection, and the nightly
   workflow loads the same snapshot. The token is the preview's development admin token from
   `development/preview.env`.

   ```bash
   export INFRAHUB_ADDRESS="http://localhost:8080"
   export INFRAHUB_API_TOKEN="06438eb2-8019-4776-878c-0941b1f1d1ec"
   uv run infrahubctl schema load tests/data/nightly_schema --wait 120
   ```

   To check the example against the current Marketplace version instead, download the
   collection and load that copy:

   ```bash
   uv run infrahubctl marketplace get infrahub/traditional-infrastructure-sot --collection --output-dir .netbox/schemas
   uv run infrahubctl schema load .netbox/schemas --wait 120
   ```

   `marketplace get` waits five seconds for each Marketplace response and has no timeout
   option. When the Marketplace answers more slowly, the command fails with
   `Marketplace request failed: ReadTimeout` and writes no files. Repeated attempts can fail
   the same way. In that case, load the snapshot. If the Marketplace version differs from the
   snapshot, record the difference in the pull request.

4. Write the local package and register it. The shipped package names the public NetBox
   demo and an Infrahub on port 8000. `netbox.demo-package` writes
   `.netbox/from-netbox.local.yml`, a copy that points at the local NetBox and the preview
   Infrahub. The mapping and the credential references stay the same. Pass
   `--infrahub-url` to use another Infrahub address.

   ```bash
   uv run invoke netbox.demo-package
   export INFRAHUB_SYNC_API_URL="http://localhost:8010"
   export INFRAHUB_SYNC_API_TOKEN="preview-tester-token-0001"
   uv run infrahub-sync configs register .netbox/from-netbox.local.yml --reason "register the local NetBox demo import"
   ```

5. Create the branch, then run `diff` and `sync` with the `config_id` and `registry_version`
   from the registration.

   ```bash
   uv run infrahubctl branch create netbox-import
   uv run infrahub-sync diff --config-id <config-id> --version <version> --branch netbox-import --reason "review the local NetBox demo import"
   uv run infrahub-sync sync --config-id <config-id> --version <version> --branch netbox-import --reason "import the local NetBox demo data"
   ```

On the pinned dataset and the current example mapping, the plan has 1,688 operations: 1,687
creates and one delete, which apply does not execute. After the sync, the object count of
each kind on the `netbox-import` branch equals that kind's planned operations. A different
count means that the mapping, the adapter, or the pinned data changed. Explain that change
in the pull request.

When you finish, remove both stacks and their data volumes:

```bash
uv run invoke preview.down --volumes
uv run invoke netbox.down
```

##### The saved-plan write-surface qualifications

Three live qualifications exercise the saved-plan write surface end to end.

`tests/integration/test_saved_plan_apply_integration.py` runs the full
NetBox → Infrahub path on a **keyed slice**: `BuiltinTag`, `LocationSite`,
`LocationRack`, `OrganizationManufacturer`, `DcimPlatform` and `DcimDeviceType`
are seeded, and `DcimDevice` is the kind under test. Every one declares
`human_friendly_id: ['name__value']`, so every planned write renders a key. It
needs the pinned schema library loaded, a NetBox carrying the deterministic
dataset (sites `site-a`/`site-b`/`site-c`, devices `dev-01`…`dev-40`, tags
`tag-01`…`tag-10`), and `NETBOX_URL` / `NETBOX_TOKEN` alongside the Infrahub
variables — see [Saved-plan apply](#integration) above for
provisioning. **Six tests must pass.** A seventh is optional: SC-016's ambiguous-peer
half skips when the destination schema admits no genuinely ambiguous peer, which
is the case on a keyed slice, since every kind is filtered on exactly the
component its uniqueness constraint pins. The skip message names the constraint
that establishes it.

**It needs a fresh disposable Infrahub for each run.** The suite writes and does
not clean up: it perturbs the destination with a per-run canary and then asserts
the derived plan carries the create, update and delete those perturbations imply.
A second run against the same instance sees the first run's canary and fails in
setup. Reset between runs.

Two further qualifications need no source system, only `INFRAHUB_ADDRESS` and
`INFRAHUB_API_TOKEN`:

- `tests/integration/test_infrahub_replace_set_shrink_integration.py` — keyed
  planned writes, applied and re-applied with the same result;
- `tests/integration/test_infrahub_keyed_write_integration.py` — a kind whose
  human-friendly ID crosses a relationship, created and then re-applied to prove it
  converges rather than duplicating; an update keyed by its recorded id, renaming in
  place; and a recorded id the destination cannot find, refused with nothing written.
  Each runs on a branch the test creates and deletes.

The interface kinds are covered there: they are written like any other kind now, and
what the test pins is that the destination converges them.

#### Nightly end-to-end report

`.github/workflows/workflow-nightly-e2e.yml` runs the integration tests, preview
smoke tests, saved-plan live tests, and `from-netbox` check against disposable
services. A scheduler outside GitHub can dispatch it each night. To dispatch one run
manually, use a full commit SHA already merged into `feature/v3-develop`:

```bash
gh workflow run workflow-nightly-e2e.yml --ref feature/v3-develop -f sha=<full-40-character-sha>
```

The NetBox suites load the bundled `traditional-infrastructure-sot` schema snapshot from
`tests/data/nightly_schema/` and verify its content digest. Review the schema and expected
demo counts before updating the snapshot and digest.

The requested commit must contain `.github/scripts/nightly_e2e.py` and this
workflow. An older commit cannot run these suites, even if the dispatch uses
the current workflow file.

The workflow checks out that exact commit and refuses one that is not an ancestor
of the current `feature/v3-develop` head. In the GitHub Actions run, open the job
summary for pass, fail, and skip counts and duration by suite. Download the
`nightly-e2e-junit` artifact for individual test results. A failed suite also
uploads its container and preview process logs in
`nightly-e2e-failed-container-logs`. The workflow reports failures but is not a
required pull-request check and does not publish a release candidate.

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

**Five modules under [`tests/preview/`](https://github.com/opsmill/infrahub-sync/tree/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/preview) are not part of that tier.** They
carry no `preview` marker, so they already run in the ordinary unit gate — 70 tests in total —
and they need no stack:

| Module | What it covers | Tests |
|---|---|---|
| [`test_evidence.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/preview/test_evidence.py) | The two helpers the live qualification rows capture evidence with, plus a read of [Contributing](../../contributing.mdx) that holds the documented setup command to the pinned package manager | 9 |
| [`test_preview_configuration.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/preview/test_preview_configuration.py) | Regression checks on the disposable preview environment, including the Compose resolution that needs the `docker compose` CLI | 25 |
| [`test_preview_legacy_state.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/preview/test_preview_legacy_state.py) | That the preview refuses retired-vocabulary state and resets it destructively | 9 |
| [`test_preview_worker_identity.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/preview/test_preview_worker_identity.py) | That the preview starts the supported service worker without static identity plumbing | 4 |
| [`test_smoke_request_shapes.py`](https://github.com/opsmill/infrahub-sync/blob/d9ef147c569a42ec4471bba78ec270c343cdfa28/tests/preview/test_smoke_request_shapes.py) | That the smoke suite's request bodies are the ones the shipped API accepts | 23 |

Directory is not marker: do not assume a module under `tests/preview/` is opt-in. If you touch
Contributing or the preview tasks, run the unmarked set directly as a fast pre-check:

```bash
uv run pytest -q -m "not preview" tests/preview
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

**When Python 3.10 is not installed, the task checks nothing and exits zero.** Its output
says that it skipped and names each leg it did not check. Install an interpreter with
`uv python install 3.10` and re-run before treating the task as evidence.

### Related

- [Testing](testing.md) — what makes a test worth having.
- [Quality gates](../knowledge/quality-gates.md) — what `invoke lint` and `invoke format` run.
- [Testing adapters](testing-adapters.md) — the coverage an adapter must ship.
- [Testing an adapter](../guides/testing-an-adapter.md) — how to write and run those tests.
- [Repository tour](../knowledge/repository-tour.md) — which part of the tree each suite covers.
