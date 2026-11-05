# Implementation report: Prefect run observability and the missing Sync API address

## Baseline

Measured on branch `016-prefect-run-observability` at `8bc54ce9`, at 2026-11-05T15:30:17Z, before any
implementation:

- `uv run invoke tests.tests-unit`: `6422 passed, 7 skipped, 239 deselected, 1 xfailed, 5 warnings in 156.25s`
- `uv run ruff check .`: `All checks passed!`

## Gates

Measured on `defa1951` plus the T030–T033 working-tree changes, from 2026-11-05T16:06:59Z:

- `uv run invoke format`: exit 0. `413 files left unchanged`, `All checks passed!`,
  `All formatters have been executed!`. It changed no file.
- `uv run invoke lint`: exit 0.
  - rumdl: `Success: No issues found in 140 files`.
  - ruff: `413 files already formatted`, `All checks passed!`.
  - Pylint: `Pylint passed (0 diagnostics, score 10.0)`.
  - ty: `Found 18 diagnostics`. The chunk rules give a baseline of 22, so ty reports no new diagnostic.
- `uv run ruff check .`: `All checks passed!`
- `uv run invoke tests.tests-unit`, first run at 2026-11-05T16:10:16Z: exit 1,
  `3 failed, 6515 passed, 4 skipped, 246 deselected, 1 xfailed, 7 warnings in 159.89s`. The three failures
  (`tests/test_release_pin.py::test_the_repository_files_validate_together_at_the_project_version` and two
  tests in `tests/testcontainers/test_packaging.py`) came from `defa1951`: it added
  `INFRAHUB_SYNC_PREFECT_UI_URL` to the root `docker-compose.yml` but not to the packaged copy
  `infrahub_sync/testcontainers/docker-compose.yml`. The root file was copied over the packaged copy.
- `uv run invoke tests.tests-unit`, second run at 2026-11-05T16:13:04Z: exit 0,
  `6518 passed, 4 skipped, 246 deselected, 1 xfailed, 7 warnings in 142.83s (0:02:22)`.

## Live check

Run on the development stack (project `infrahub-sync-dev`) after `uv run invoke dev.build` and
`uv run invoke dev.start`, both exit 0, at 2026-11-05T16:07:32Z. Only `plan` was run; no `sync` or `apply`.

- Plan run ID: `20261105T1607-44f997d8`. It planned 1 operation (`by_action: delete=1`,
  `by_kind: IpamNamespace=1`).
- `infrahub-sync runs show 20261105T1607-44f997d8` printed:

  ```text
  phase: planned
  outcome: planned
  execution_state: completed
  flow_run_id: 5dddef3a-f6d5-46e0-b8a3-f7b0155d70ce
  prefect_flow_run_url: http://127.0.0.1:4230/runs/flow-run/5dddef3a-f6d5-46e0-b8a3-f7b0155d70ce
  ```

- Opening the link: a `GET` with httpx's default `Accept: */*` returned HTTP 404 with the JSON body
  `{"detail":"Not Found"}`. The same `GET` with `Accept: text/html,application/xhtml+xml`, as a browser sends
  for a navigation, returned HTTP 200 with the Prefect UI's `text/html` page. Prefect 3.8.6 serves its
  single-page UI on a deep path only for an HTML navigation request. A 200 shows that the UI page loads at that
  address; it does not prove that the UI's router shows the flow run. No browser was opened.
- `POST http://127.0.0.1:4230/api/artifacts/filter` for the flow run returned one artifact, key
  `sync-run-20261105t1607-44f997d8-plan`, type `markdown`:

  ```markdown
  # Sync run 20261105T1607-44f997d8

  - Stage: plan
  - Outcome: planned
  - Configuration: 20261105T1514-9d8722ad@1
  - Destination branch: netbox-import
  - Operations by action: delete 1
  - Operations by kind: IpamNamespace 1

  ## Next steps

  - `infrahub-sync runs show 20261105T1607-44f997d8`
  - `infrahub-sync runs plan 20261105T1607-44f997d8 --detail`
  ```

- `POST /api/flow_runs/filter`: state `COMPLETED` (`Completed`), name `Plan sync of from-netbox`.
- `POST /api/logs/filter`, 65 lines, sorted by time. The last two:

  ```text
  INFO 2026-11-05T16:08:02.163892Z service run 20261105T1607-44f997d8 stage=plan outcome=planned; next step: `infrahub-sync runs show 20261105T1607-44f997d8`
  INFO 2026-11-05T16:08:02.622602Z Finished in state Completed()
  ```

  The worker's line is its last one. Prefect's own final-state line follows it, so the quickstart's "last line
  in the Logs tab" is the line before last. `docs/docs/orchestration.mdx` says so.
- Hint check:

  ```text
  $ env -u INFRAHUB_SYNC_API_URL uv run infrahub-sync runs list; echo "exit $?"
  error: client-input
  argument: service_url
  hint: set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API
  exit 2
  $ INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 env -u INFRAHUB_SYNC_API_TOKEN uv run infrahub-sync runs list; echo "exit $?"
  error: client-input
  argument: token
  hint: set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token
  exit 2
  ```
