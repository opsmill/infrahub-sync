# Quickstart: check the read state of each side of a Plan

## Prerequisites

- The development profile: `uv sync --extra dev --extra prefect --extra service`.
- For the live check: the preview stack and a registered configuration, as in
  [testing tiers](../../../docs/docs/development/guidelines/testing-tiers.md).

## 1. Unit tier

```bash
uv run pytest tests/test_potenda_read_progress.py tests/product_store/test_side_read_columns.py \
  tests/service/test_read_progress.py -q
uv run invoke tests.tests-unit
```

Expected: every test passes.

## 2. Live check: a Plan that succeeds

1. Start a Plan: `uv run infrahub-sync plan --config <config_id> --no-wait`.
2. While it runs, read the run every few seconds: `uv run infrahub-sync runs show <run_id>`.
3. Expected while the reads run: `source_read` and `destination_read` show `state=waiting`, then
   `state=reading` with `started_at`.
4. Expected after the stage ends: both show `state=succeeded`, a `mode`, both times and `nodes` (see
   [cli-output.md](./contracts/cli-output.md)).
5. `uv run infrahub-sync runs logs <run_id>` shows one `Load: ... read succeeded` line for each side.

## 3. Live check: a destination that cannot be read

1. Stop the destination, or point the configuration at an unreachable address, and start a Plan.
2. Expected: the run fails, `destination_read` shows `state=failed` and an `error=` class, and `source_read` shows
   its own state.

## 4. Live check: an interrupted read

1. Start a Plan on a large configuration and stop the worker container while a side shows `state=reading`.
2. Wait until the Sync API marks the execution ended (`execution_state` is `interrupted`).
3. Expected: that side shows `state=interrupted` with no `ended_at`.

## 5. Response shape

`GET /runs/<run_id>` returns `reads` in each orchestration entry, as in
[run-response.md](./contracts/run-response.md). An execution recorded before this feature returns `reads: null`.
