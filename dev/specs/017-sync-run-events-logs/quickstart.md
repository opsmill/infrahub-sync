# Quickstart: check run events and logs on the development stack

## Prerequisites

- The development stack from `development/docker-compose.yml`, with Infrahub and a registered configuration whose
  destination is that Infrahub.
- `INFRAHUB_SYNC_API_URL` and `INFRAHUB_SYNC_API_TOKEN` set for the CLI.

## Unit tier

```bash
uv run invoke tests.tests-unit
uv run ruff check .
uv run invoke lint
```

## Scenario 1: missing destination branch (User Story 3)

1. `infrahub-sync plan --config <config> --branch demo-test` on a branch that does not exist.
2. `infrahub-sync runs show <run_id>`: the `plan_failure` names `demo-test`; the `hint:` line names the fix.
3. `infrahub-sync runs logs <run_id>`: one ERROR line with the cause and the next step, then the last log line. No
   line contains `Traceback`, `prefect` or the Prefect address.

## Scenario 2: timeline (User Story 1)

1. Run a plan that succeeds, then `infrahub-sync apply <run_id> --checksum <checksum>`.
2. `infrahub-sync events --run-id <run_id>`: `run.accepted`, `run.planned`, `run.accepted`, `run.applied`, in order.
3. As the frontend: `GET /events?run_id=<run_id>` returns the same four events with their related resources.

## Scenario 3: polling (User Story 2 and SC-003)

1. Start a plan and, at once, `infrahub-sync runs logs <run_id> --follow`.
2. Each line appears once; the command exits when the run ends.
3. Compare each line's `timestamp` with the time it was printed: the gap is under 5 seconds.
4. For the largest available sync (for example the `from-netbox` example), record the number of lines that
   `GET /runs/<run_id>/logs` reads from Prefect and the time one request takes (critique E6).

## Scenario 4: Prefect cannot be reached (SC-006)

1. Stop the `prefect-server` service.
2. `GET /runs/<run_id>/logs` returns 503 `log-unavailable`; `GET /runs/<run_id>` and `GET /events` return 200.
3. Start `prefect-server` again.
