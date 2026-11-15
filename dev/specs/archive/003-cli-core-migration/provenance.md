# DB-004 combined-tree provenance and coverage

Base: `5c335f648af3f2674fca206116e47345401c0c05` (PR #160 reconciliation).

The shared surface owns four operations (`plan`, `verify`, `sync`, and `apply`)
for four entry paths: CLI `diff`, CLI `sync`, CLI `apply`, and the packaged
Prefect flow. `sync` covers both serial and tier-parallel execution.

| CLI path | Baseline provenance | DB-004 ownership proof |
| --- | --- | --- |
| `diff` live plan | PR #160 `execute_run(operation="plan")` mapping | The command only parses/options-renders; `execute_run` owns the plan lock, run sidecar, engine lifecycle, and post-lock committed-plan guard. |
| `diff --from-plan` review | Saved-plan review shipped in the reconciled base | The command renders the `execute_run(operation="verify")` result; review remains lock-free and adapter-free. |
| `sync --no-parallel` | PR #160 serial mapping | Preserved through `execute_run(operation="sync", parallel=False)`. |
| `sync --parallel` | Reconciled base's tier behavior | `execute_run` constructs once, selects tiers, holds the lock, updates `run.json`, and keeps the explicit-`order` fallback. |
| `apply` | Saved-plan apply shipped in the reconciled base | `execute_run(operation="apply")` owns prechecks, lock, destination-only assembly, verification/apply, and all `run.json` transitions; the CLI renders compatible outcomes. |

Focused coverage: `tests/test_execution_surface.py`,
`tests/test_cli_execution_mapping.py`, `tests/test_cli_parallel.py`,
`tests/test_execution_cli_parity.py`, `tests/test_cli_plan_review.py`,
`tests/test_cli_adapter_load_failures.py`, and `tests/test_no_prefect_import.py`.
The concurrency mapping test uses a sentinel running sidecar and asserts the bounded refusal
names its active Sync run; the CLI mapping test also asserts a credential-shaped environment
value is redacted before the error logger writes it.
