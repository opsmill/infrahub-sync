# Quickstart: validate `GET /api/health`

These steps prove the acceptance scenarios of [spec.md](./spec.md) on the development stack. The
response format is in [contracts/health-api.md](./contracts/health-api.md).

## Prerequisites

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke dev.start          # preset `sync`: PostgreSQL, object store, Prefect, sync-api, sync-worker
alias dc='docker compose -p infrahub-sync-dev -f development/docker-compose.yml --profile sync'
health() { curl -s -o /tmp/health.json -w '%{http_code} %{time_total}s\n' http://127.0.0.1:8001/api/health; jq -c '{status, deps: [.dependencies[] | "\(.name)=\(.status)/\(.error)"], workers: .workers | {state, live_workers, items: [.items[] | .state]}}' /tmp/health.json; }
```

## Offline checks

```bash
uv run invoke tests.tests-unit
uv run pytest tests/service -k health
```

## Scenario 1: all up (FR-001, SC-005)

```bash
health
```

Expected: `200`, every dependency `up/none`, `workers.state` `known`, at least one `live` item.

## Scenario 2: worker paused and resumed (US1-1, SC-001, SC-003)

```bash
dc pause sync-worker
for i in $(seq 1 14); do sleep 5; health; done   # 70 s
dc unpause sync-worker
for i in $(seq 1 8); do sleep 5; health; done    # 40 s
```

Expected: `503` within 60 s of the pause, the worker listed as `stale`; `200` within 30 s of the
unpause. `dc ps sync-worker` shows `unhealthy` while paused, after the healthcheck retries (US2;
Docker Desktop reports a paused container `unhealthy`). On a Linux engine, also test US2 by
stopping only the Python process: `dc exec sync-worker sh -c 'kill -STOP $(pgrep -f infrahub_sync.service.worker)'`,
then `kill -CONT` the same way.

## Scenario 3: worker restart leaves a stale record (US1-4, SC-005)

```bash
dc restart sync-worker
sleep 30; health
```

Expected: `200`; `workers.items` holds one `live` and one `stale` worker.

## Scenario 4: each component stopped, then started (US1-2, US1-3, SC-002, SC-003)

```bash
for service in postgres object-store prefect-server; do
  dc stop "$service"; sleep 1; health        # 503 within 5 s, that component down
  dc start "$service"; sleep 15; health      # 200 again
done
dc pause postgres; health; dc unpause postgres   # 503 after about 3 s, database down/timeout
```

Expected: the stopped component is `down` with `name_resolution_failed` (or
`connection_refused`), the paused one with `timeout`. With `prefect-server` stopped,
`task_manager` and `task_manager_db` are `down` and `workers.state` is `unknown`. With `postgres`
stopped, `database` and `task_manager_db` are `down` (one PostgreSQL holds both databases), and
after 30 seconds the workers turn `stale`; `dc ps sync-worker` stays `healthy`. Record the measured
recovery times; the documentation states them. For PostgreSQL, wait 30 s instead of 15 s before
the second `health`.

## Scenario 5: minimum of two workers (US1-5)

```bash
INFRAHUB_SYNC_HEALTH_MIN_WORKERS=2 dc up -d --wait sync-api
health                                   # 503: one live worker, minimum 2
dc up -d --scale sync-worker=2 --wait
sleep 20; health                         # 200
```

## Scenario 6: no credential, nothing private (US1-6, FR-008)

```bash
curl -si http://127.0.0.1:8001/api/health | grep -i cache-control   # no-store
grep -Eic 'postgres|prefect-server|object-store|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|password|token' /tmp/health.json   # 0
```

## Scenario 7: concurrent callers (SC-006)

```bash
seq 1 600 | xargs -P 50 -I{} curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/api/health | sort | uniq -c
```

Expected: 600 answers of `200`.

## Scenario 8: `/status` is gone, readers moved (US3)

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/api/status      # 404
curl -s -o /dev/null -w '%{redirect_url}\n' http://127.0.0.1:8001/api          # …/api/health
```

Open `http://127.0.0.1:8001/`, pause `sync-worker`, and check that the page frame shows the same
"no live worker" message as before this change.

## Scenario 9: settings out of range

```bash
INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT=0 dc up -d sync-api; dc logs --tail 5 sync-api
```

Expected: the Sync API does not start, and the log names `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT`.

## Clean up

```bash
uv run invoke dev.destroy
```
