# Implementation report: Read state for each side of a Plan

## Baseline

Run on branch `018-plan-read-progress` at `90178305`, in the uv virtual environment with the dev, Prefect and
service extras, on macOS, from 2026-11-09T16:10:40Z to 16:13:59Z.

- `uv run invoke tests.tests-unit`: `6815 passed, 4 skipped, 267 deselected, 1 xfailed, 7 warnings in 191.57s`
- `uv run ruff check .`: `All checks passed!`

## Node counting

Measured on 2026-11-09T16:20:24Z at `0300de42` plus the T002–T009 changes, in the uv virtual environment, on
macOS. The measurement script is not committed.

- Largest example configuration: `examples/nautobot-v1_to_infrahub/config.yml`, with 30 schema mappings over 23
  distinct kinds. It sets no `store`, so it uses DiffSync's in-memory store. `execute_run` refuses a configured
  store, so no example uses the Redis store on this path.
- No live Nautobot or Infrahub was available, so the side's real read time is not known. In its place, the script
  measured the time to add the nodes to an in-memory adapter with the example's 23 kinds. That time is a lower
  bound of a real read, which also queries the system over the network.
- The counting is `_nodes_by_kind` in `infrahub_sync/potenda/__init__.py`, averaged over 20 calls.

| Nodes | Time to add the nodes | Counting time | Counting as a share of adding |
| --- | --- | --- | --- |
| 7,659 | 0.0211 s | 0.000005 s | 0.026% |
| 76,659 | 0.2626 s | 0.000005 s | 0.002% |

With the in-memory store, `count(model)` is one `len()` for each kind, so the counting time does not grow with the
number of nodes. It stays far below 5% of the read, so the counting was not changed to a single pass.

Not measured: the Redis store, where `count(model)` scans the store's keys for each kind. No Redis server was
reachable on `localhost:6379`.
