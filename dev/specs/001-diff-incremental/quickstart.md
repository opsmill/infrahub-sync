# Validation Guide

Run from repository root with uv and Python available. All test fixtures are synthetic; no backend credentials or servers are needed.

```bash
uv sync --extra dev
uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py
uv run invoke format
uv run invoke lint
uv run ty check .
uv run pytest -q
```

The sequential regression must create a cursor-bearing sync baseline, write a newer cursorless diff, and verify the following incremental load replays the baseline and queries deltas without a full loader call. Fallback tests must assert reason, side and resource. Existing baseline diagnostics from main must not increase on touched files.
