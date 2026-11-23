# Quickstart: check automatic resume

## Unit tier

```bash
uv run invoke tests.tests-unit
```

The new tests are listed in [plan.md](./plan.md). They use the in-memory and SQLite stores, a fake orchestration
with a scripted pool snapshot, and the existing fakes of `tests/service/test_resume_run.py`.

## PostgreSQL tier

```bash
PRODUCT_STORE_TEST_POSTGRESQL_DSN=postgresql://… uv run pytest tests/product_store/test_postgresql_isolation.py -k automatic_resume
```

Two Sync API reconcilers step the same row at the same time; exactly one submission happens (SC-005).

## Preview stack (SC-001)

1. Start the development stack with `uv run invoke dev.preview`, with `INFRAHUB_SYNC_AUTOMATIC_RESUME=true` on the
   Sync API service.
2. Register a configuration whose plan has at least 10,000 creates and updates (the tier M benchmark dataset).
3. Run `infrahub-sync plan`, then `infrahub-sync apply RUN_ID --expected-checksum CHECKSUM --no-wait`.
4. When `runs show RUN_ID` reports a progress marker above 0, stop the worker container:
   `docker compose -f development/docker-compose.yml kill sync-worker`.
5. Start it again: `docker compose -f development/docker-compose.yml up -d sync-worker`.
6. Wait for `runs show RUN_ID` to report the phase `applied`. Expected: one block "Automatic resume 1: started" with
   the rule 8 cause, and no command run in between.
7. Run `infrahub-sync plan` on the same configuration version. Expected: 0 creates and 0 updates.

## Refusal cases (SC-002)

Repeat steps 3 and 4 with each condition below, and check that `runs show` reports "Automatic resume 1: not started"
with the matching reason:

| Condition | Expected reason |
| --- | --- |
| `INFRAHUB_SYNC_AUTOMATIC_RESUME=false` | automatic resume is turned off |
| Change one node of the plan in Infrahub after step 4 | the check found 1 node that differs from the plan |
| `INFRAHUB_SYNC_REQUIRE_APPROVAL=true` and no `approve` | approval is missing |
| `infrahub-sync runs cancel RUN_ID` instead of killing the worker | no row; cancellations are not considered |
