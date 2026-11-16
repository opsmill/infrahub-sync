# 44. The base install declares no Redis client, and no code imports the Redis store

**Status**: Accepted
**Date**: 2026-11-16
**Source**: `infrahub_sync/utils.py`, `infrahub_sync/execution.py`,
`infrahub_sync/configuration/storage.py`, `pyproject.toml`

## Context

[ADR 0008](0008-declare-redis-directly-instead-of-the-diffsync-extra.md) declared `redis>=4.3,<9`
as a direct base dependency for one reason: `infrahub_sync/utils.py` imported
`diffsync.store.redis.RedisStore` at module top, so every installation needed a Redis client to
import the package.

Since then V3 refuses a configured sync store:

- configuration validation refuses a package that has a `store` block;
- the Sync API refuses a run of such a configuration;
- `execute_run` refuses a `SyncInstance` with a `store` before it builds an adapter.

The two places in `utils.py` that built a `RedisStore` could therefore not run through the
execution surface. The dependency, the import and the import-compatibility test
(`tests/test_redis_store_compat.py`) existed only for each other.

## Decision

- `infrahub_sync/utils.py` no longer imports `RedisStore`. It builds an in-memory `LocalStore`
  for each adapter and raises `UnsupportedSyncStoreError`, with the fixed message that the other
  refusals use, when the configuration has a `store` block. A configured store is never replaced
  by an in-memory store without notice.
- `pyproject.toml` no longer declares `redis`. `diffsync` stays declared without its `[redis]`
  extra, because that extra caps `redis<5.0` and cannot be installed next to the `prefect` extra.
- `tests/test_redis_store_compat.py` is removed. The refusal tests
  (`tests/test_sync_store_policy.py`, `tests/service/test_sync_store_policy.py` and the
  configuration tests) stay.

## Consequences

- A base installation no longer installs a Redis client. An installation with the `prefect`
  extra, or with the `service` extra on Python 3.11–3.13, still gets one, because Prefect requires
  it. On Python 3.10 the `service` extra installs no package.
- A project that installs `diffsync[redis]` next to `infrahub-sync` resolves the Redis client
  from that extra alone. It cannot also install the `prefect` extra, for the reason ADR 0008
  records.
- The repository no longer makes a compatibility claim about the Redis store of `diffsync`.
- Supporting a configured store again needs a new decision: the import, the dependency and the
  refusals all have to change together.

## Alternatives considered

- **Keep the import and the dependency.** Rejected: every installation carried a client that no
  supported path uses, and a test that only proved the import works.
- **Import `RedisStore` only when a configuration asks for it.** Rejected: every route refuses
  such a configuration first, so the import could not run.
