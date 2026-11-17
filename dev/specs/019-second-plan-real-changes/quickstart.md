# Quickstart: validate the second plan and the plan step timings

## 1. Offline unit tier (every pull request)

```bash
uv sync --extra dev --extra prefect --extra service
uv run pytest tests/plan/test_second_plan.py -q
uv run pytest tests/adapters -k peer_order -q
uv run pytest tests/service/test_plan_step.py tests/product_store/test_plan_step_column.py -q
uv run pytest tests/test_benchmark_runner.py -q
uv run invoke tests.tests-unit
```

Expected: all pass, with no NetBox and no Infrahub server.

## 2. Live checks (opt-in, development stack)

```bash
uv run invoke dev.start
uv run pytest -m integration tests/integration/test_infrahub_builtin_nodes_integration.py -q
uv run pytest -m integration tests/integration/test_infrahub_one_peer_clear_integration.py -q
```

Expected: the probe lists the nodes that a new Infrahub 1.11.9 branch holds and the mark that
identifies them; the emptied relationship reads back as empty.

## 3. The NetBox example, twice

Follow [The `from-netbox` example check](../../../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check),
then:

```bash
uv run infrahub-sync plan --config <config_id> --branch <branch>
uv run infrahub-sync apply <run_id> --checksum <checksum>
uv run infrahub-sync plan --config <config_id> --branch <branch>
uv run infrahub-sync runs plan <second_run_id>
uv run infrahub-sync runs show <second_run_id>
```

Expected:

- the second plan prints `operations: 0`, with no `IpamNamespace` delete;
- `runs show` prints `plan_waiting`, `plan_preparing`, `plan_comparing`, `plan_checking` and
  `plan_saving` with a duration each.

## 4. Benchmark machine (manual)

```bash
for tier in S M; do
  for scenario in cold warm changed; do
    uv run invoke bench.run --line v3 --tier "$tier" --scenario "$scenario" --repetitions 3
  done
done
```

Expected: 18 records with the fields of [benchmark-record.md](contracts/benchmark-record.md).
The `warm` records hold 0 operations, and the two `warm` repetitions of a tier hold the same
`operations_digest`. Write the results page from the records.
