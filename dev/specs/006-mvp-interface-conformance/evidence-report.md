# DB-006 evidence report

## Read-only inventory

The exact starting revision was
`8ba0c57a7601bd0ffc75431488d6b59717217de6` on
`feature/db-006-mvp-interface-conformance`, with a clean worktree.

- CLI, public Python, and managed worker calls converge on
  `infrahub_sync.execution.execute_run`.
- All three consume the v2 saved-plan reader/writer contract.
- Only `infrahub_sync.managed.flow` created/finished DB-003 `ProductRun` records and
  published `plan-review`. Configured standalone paths retained only the legacy run
  directory and sidecar.
- The pre-edit focused inventory supplied by the orchestrator passed 142 local/core/store
  tests and 35 managed/HTTP/real-Prefect-idempotency tests.

## Oracle red/green

Initial command:

```bash
uv run pytest -q tests/conformance/test_standalone_projection.py
```

Result: collection failed exactly at the missing integration seam with
`ModuleNotFoundError: No module named 'infrahub_sync.product_store.standalone'`.

After the repair:

```text
uv run pytest -q tests/conformance
11 passed
```

The suite includes a mutation-sensitive oracle check: changing the named
`configuration_reference` field causes canonical comparison to fail.

## VAL-1 88k evidence

The orchestrator ran the existing harness from an isolated scratch copy at
`/tmp/db006-val1.bFOaRH`; the planning repository remained read-only. Source result:
`/tmp/db006-val1.bFOaRH/harness/results.json`.

Environment: macOS 26.5.2 arm64, Python 3.12.2, seed 42; 88,117 baseline and 87,868
current records.

| Subject | Load | Diff | Peak memory | Summary |
|---|---:|---:|---:|---|
| A / DiffSync | 1.7558 s | 3.3236 s | 503,185,408 B (0.469 GiB) | create 81, update 1,426, delete 330, no-change 86,361 |
| B / DuckDB | 1.5854 s | 0.1484 s | 313,950,208 B (0.292 GiB) | same semantic summary |

Both satisfy the existing thresholds of at most one hour and at most 8 GiB. DB-006 did
not optimize or alter the harness.

## Environmental safety

- Docker client/server 29.4.3 was available.
- Two pre-existing containers (`infrahub-task-worker-1` and
  `infrahub-task-worker-2`) were observed and left untouched.
- An isolated PostgreSQL 16 plus MinIO
  `RELEASE.2025-04-22T22-12-26Z` profile passed create, immutable artifact
  publication, finish, fresh-provider reconstruction, record lookup, and artifact lookup:

  ```text
  DB006_LIVE_PROVIDER_PASS run=1 artifacts=1 restart_lookup=pass
  DB006_DOCKER_TEARDOWN_PASS containers=2 remaining=0
  ```

  Temporary `psycopg[binary]` and `boto3` clients were supplied with `uv run --with`;
  project dependencies and the lockfile were unchanged. Two preliminary client-shim probes
  exposed incorrect `copy` and `put(if_absent=...)` method signatures in the probe itself;
  both exited through the teardown trap with zero DB-006 containers remaining. The final
  probe implemented the documented `S3Client` protocol and passed.
- No Infrahub or NetBox service/token variables were present, and no corresponding service
  container was available. The shipped NetBox-to-Infrahub plan/apply qualification therefore
  stopped at its documented external setup precondition.
- A fresh Python 3.10 virtual environment installed the base project without the `prefect`
  or `managed` extras. `prefect`, `fastapi`, `uvicorn`, and `opsmill_prefect_extras` were
  absent; importing the v1 API, CLI, and standalone projector loaded neither Prefect nor
  `infrahub_sync.managed`. The temporary environment was removed:

  ```text
  DB006_BASE_INSTALL_PASS python=3.10 optional_managed_modules=absent
  DB006_BASE_INSTALL_TEARDOWN_PASS remaining=0
  ```
- No external write, package publication, push, merge, or promotion occurred.

## Final verification

The development environment was resolved twice, first with the required base development
extras and then with the already-declared managed extra for the direct envelope comparison:

```text
uv sync --extra dev --extra prefect
pass
uv sync --extra dev --extra prefect --extra managed
pass; existing exact Prefect Extras Git pin retained; no dependency or lockfile change
```

Behavior gates:

```text
uv run pytest -q tests/conformance tests/api/test_v1.py tests/test_execution_cli_parity.py tests/product_store tests/managed tests/orchestration/test_flow.py
221 passed, 1 warning in 27.20s

uv run pytest -q
1304 passed, 14 skipped, 1 xfailed, 4 warnings in 77.07s
```

After adding the CLI review-refusal case, the first implementation's focused rerun passed
222 tests with one inherited warning in 23.44 seconds. Independent review then required the
bounded correction evidence below; those later totals supersede the initial conformance
count.

Formatting and static checks:

```text
uv run invoke linter.format
pass; 151 files unchanged; Ruff checks passed

uv run ruff format --check . && uv run ruff check .
pass; 151 files formatted; all checks passed

uv run ty check .
exit 0; four inherited unused-ignore warnings

uv run invoke linter.lint-yaml
pass

uv run invoke docs.rumdl
pass; 69 files

uv run invoke lint
exit 28 at the inherited pylint baseline; rating 9.94/10 unchanged. The aggregate
short-circuited before YAML and ty, so those legs were run independently above. No new
message code or occurrence was introduced by DB-006.
```

Documentation and CLI checks:

```text
uv run invoke docs.generate
pass
uv run invoke docs.docusaurus
pass; optimized static site generated
uv run infrahub-sync --help
pass
uv run infrahub-sync list --directory examples/
pass; 14 configurations listed
uv run infrahub-sync diff --help
pass; --product-cache-location present
uv run infrahub-sync sync --help
pass; --product-cache-location present
uv run infrahub-sync apply --help
pass; --product-cache-location present
uv run infrahub-sync generate --name from-netbox --directory examples/
stopped at documented external precondition: localhost Infrahub unavailable; no file changed
```

## Independent-review correction pass

The lossless oracle now normalizes only exact schema paths. Three mutation cases plant
`run_id` or `created_at` under semantic payloads and prove that each disagreement fails.

`tests/conformance/test_interface_matrix.py` executes, rather than fabricates, these
paths:

- actual Typer CLI, public Python functions, and managed worker for plan, apply, and
  confirmed sync, with actual returned common fields, measured destination effects, and
  complete actor-free ProductRun/reference/artifact equality;
- public Python and managed worker independent verify common-return fields plus durable
  verification evidence (the managed verify return has no action-count summary);
- CLI plan writing a real v2 manifest, public Python verifying those exact bytes, and CLI
  review reading the unchanged bytes;
- managed HTTP admission through captured Prefect parameters into worker plan, verify,
  reviewed apply, and confirmed sync completion.

The HTTP matrix scans success results, a checksum-refusal error, logs, submitted Prefect
parameters, retained records, and artifacts for the recognizable configuration sentinel
and HTTP bearer token. HTTP-owned actor, audit, and Prefect-link fields remain present and
are reported separately rather than normalized away. The CLI has saved-plan review but no
independent verify operation; the report does not claim otherwise.

The expanded matrix exposed and repaired four additional concrete lifecycle discrepancies:
managed confirmed sync retained `operation=apply`; standalone apply failures dropped
`ApplyRecord` partial-write fields; direct CLI sync projected its review artifact after
writes; and configured CLI review treated a read-only SavedPlan as a sync result. It also
proved duplicate configured plans now produce the typed one-line refusal.

Pre-final correction command:

```text
uv run pytest -q tests/conformance tests/managed/test_flow_and_prefect.py tests/test_execution_surface.py tests/test_potenda_parallel.py tests/test_potenda_plan_artifact.py
217 passed, 1 warning in 3.32s

uv run pytest -q tests/conformance/test_interface_matrix.py
6 passed, 1 inherited warning
```

Final correction-pass behavior gates:

```text
uv run pytest -q tests/conformance tests/api/test_v1.py tests/test_execution_cli_parity.py tests/test_execution_surface.py tests/test_potenda_parallel.py tests/test_potenda_plan_artifact.py tests/product_store tests/managed tests/orchestration/test_flow.py
424 passed, 2 inherited warnings in 23.26s

uv run pytest -q
1320 passed, 14 skipped, 1 xfailed, 4 inherited warnings in 68.24s

uv run ruff format --check . && uv run ruff check .
pass; 153 files formatted; all checks passed

uv run pylint infrahub_sync/product_store/standalone.py --score=no
pass; the correction introduced no local pylint finding

uv run ty check .
exit 0; four inherited unused-ignore warnings

uv run invoke docs.rumdl && uv run invoke linter.lint-yaml
pass; 69 Markdown/MDX files and all YAML files

uv run invoke docs.generate && uv run invoke docs.docusaurus
pass; CLI reference regenerated and optimized static site built
```

## Boundary-derived adapter correction

The final evidence-only correction removes every synthesized operation, count, outcome,
and destination-effect input from the interface adapter. The executable fixture now has
one create operation and observes these producing boundaries directly:

- CLI: captured core `RunResult`, successful Typer exit, and CLI/log rendering;
- Python: the actual public v1 `RunResult` returned by plan/apply/sync;
- managed worker and HTTP-to-Prefect completion: the actual worker result dictionary;
- destination effects: an instrumented isolated destination probe, measured after each
  interface call (zero for plan, one create for apply and confirmed sync).

Four mutation cases change one actual public returned count, worker outcome, worker
operation, or measured destination state. Each now reaches the canonical envelope and
causes comparison failure. Full verify-envelope equality is not claimed: Python and
managed verify compare only their actually returned common run ID/operation/outcome,
durable verification result, and review artifact because the managed verify result has no
summary/counts field.

```text
uv run pytest -q tests/conformance/test_interface_matrix.py
10 passed, 1 inherited warning

uv run pytest -q tests/conformance
29 passed, 1 inherited warning in 1.75s

uv run pytest -q tests/conformance tests/api/test_v1.py tests/test_execution_cli_parity.py tests/test_execution_surface.py tests/test_potenda_parallel.py tests/test_potenda_plan_artifact.py tests/product_store tests/managed tests/orchestration/test_flow.py
428 passed, 2 inherited warnings in 24.94s

uv run ruff format --check tests/conformance && uv run ruff check tests/conformance
pass; seven files formatted; all checks passed

uv run ty check tests/conformance/interface_adapters.py tests/conformance/test_interface_matrix.py
pass; no diagnostics
```
