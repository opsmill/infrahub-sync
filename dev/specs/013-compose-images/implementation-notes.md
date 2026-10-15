# Implementation notes: spec 013

## Baseline (T001, T002)

Starting commit: `bad4e0b3` on branch `013-compose-images`. No code or test changed for these
two tasks.

### T001: offline unit tier

- Environment: Python 3.11.8, full development profile
  (`uv sync --extra dev --extra prefect --extra service`).
- Command: `uv run invoke tests.tests-unit`
- Started: 2026-10-15T19:07:12Z
- Result: 5670 passed, 4 skipped, 203 deselected, 4 warnings in 138.49s. Exit code 0.

### T002: compose lifecycle tier

- Environment: Docker 29.4.0 on OrbStack (aarch64), Docker Compose 5.1.2.
- Image: `infrahub-sync:compose-test` (image ID `a5eb800a0a73`), built with
  `docker build -t infrahub-sync:compose-test .` at the starting commit.
- Command:
  `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -n 0`
- Started: 2026-10-15T20:43:18Z
- Result: 37 passed, 0 failed, 0 skipped, 216 deselected in 519.42s (8 min 39 s). Exit code 0.
- Failing tests: none.
- Scope: one local run on arm64 only. It does not measure the failure rate that T004 measures
  with ten repeated runs on amd64 and arm64.
