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

## Polish checks

Run on branch `018-plan-read-progress` at `af5f422c` plus the T024–T027 changes, in the uv virtual environment with
the dev, Prefect and service extras (re-synced after `towncrier create`), on macOS, on 2026-11-09.

- `uv run invoke format`: exit 0, `431 files left unchanged`.
- `uv run invoke lint`: exit 0. rumdl `No issues found in 140 files`; Ruff `All checks passed!`; Pylint
  `0 diagnostics, score 10.0`; yamllint passed; ty reported 20 `unused-ignore-comment` warnings and no error. The
  warnings are in files this feature does not change (for example `infrahub_sync/adapters/nautobot.py` and
  `tests/product_store/test_contract.py`): their `ty: ignore[unresolved-import]` comments are for optional
  dependencies that this profile installs.
- `uv run ruff check .`: `All checks passed!`
- `uv run invoke docs.lint`: exit 0, `No issues found in 140 files`.
- `uv run invoke tests.tests-unit`, from 16:54:35Z to 16:57:20Z: `6865 passed, 4 skipped, 267 deselected,
  1 xfailed, 7 warnings in 161.83s`. The baseline was 6815 passed; the 50 more are the tests of T002–T022.
- `uv run infrahub-sync --help` and `uv run infrahub-sync runs --help`: exit 0.
- The `runs show` help changed: its docstring now names `source_read` and `destination_read`. `uv run invoke
  docs.generate` regenerated `docs/docs/reference/cli.mdx`, which gained those three lines and nothing else.

## Live checks

Checked on 2026-11-09. `docker ps` listed only the `hivemind` containers: no Sync API, worker, Prefect, Infrahub or
NetBox container of the preview stack was running. `INFRAHUB_SYNC_API_URL` was unset, and nothing answered on
`127.0.0.1:8000`. The containers were not started, because this chunk does not start containers it does not own.

- Section 2, a Plan that succeeds: not run, because no Sync API or worker was running.
- Section 3, a destination that cannot be read: not run, for the same reason.
- Section 4, an interrupted read: not run, for the same reason; it also needs a worker container to stop.
- Section 5, response shape: not run against a live Sync API, for the same reason. The shape is covered offline by
  the T014 tests in `tests/service/test_http_api.py`, which pass in the unit tier above.
