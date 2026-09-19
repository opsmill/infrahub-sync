---
title: "The configuration write guard"
---

## The configuration write guard

The Sync service serializes registered `apply` and `sync` writes for each configuration
with a PostgreSQL advisory lock. Both stages acquire this guard before reading the live
destination schema and hold it until their write work finishes.

Each worker stage uses a private scratch directory, so a filesystem lock in that directory
cannot exclude another worker. The guard serializes processes that use the same
configuration key in the same PostgreSQL database. It does not lock destination objects
against other configurations or external tools.

The implementation is [`service/apply_guard.py`][guard-source]; the registered callers are
in [`service/flow.py`][flow-source]. The
[execution module](execution-surface.md#the-lock-and-the-already-locked-caller) also has a
local pipeline lock for callers that share a cache root.

### Registered write stages

The service requires a configuration ID, registry version and package checksum for a write.
It checks that binding against the product run and registered package before executing the
stage. The guard key uses the configuration ID, so different versions of that configuration
contend for the same lock.

| Stage | Before acquisition | While holding the guard |
| --- | --- | --- |
| `apply` | Retrieve the retained plan into scratch; verify its configuration binding and approved checksum. | Read the live destination schema, compare it with the plan's schema fingerprint, construct the destination and apply the saved operations. |
| `sync` | Validate the registered binding and write confirmation. | Read the schema, build runtime models, plan, publish the plan, verify it and apply it. |

A service `sync` calls `execute_run` separately for `plan`, `verify` and `apply`. It never
calls the refused core `operation="sync"` path. Keeping those stages under one guard prevents
another participating writer for the configuration from writing between planning and apply.
Standalone `plan` and `verify` stages do not acquire the configuration write guard.

The service connects `guard.require_ownership` to the engine through
`ProvenWriteOwnership`. The engine checks ownership immediately before each destination
operation it dispatches and once after the final operation. The engine does not dispatch delete operations; it records their identifiers as skipped.
If a proof fails, the engine raises before dispatching the next operation; earlier writes
are not rolled back. See [planned writes and apply](planned-write-and-apply.md) for the
operation records and destination methods.

After the guarded block exits successfully, the service publishes the final checkpoint
and then commits product success. A release failure therefore prevents a success result,
even if the destination operations completed. The service retains the completed or partial
`ApplyRecord` for its failure report. An uncertain write needs destination inspection;
follow the [uncertain-write procedure](../../compose-deployment.mdx#when-the-outcome-of-a-write-is-uncertain).

### Deployment requirements

**The guard requires a direct PostgreSQL connection or a session-mode pooler.** A
session-level advisory lock remains held across transactions until it is explicitly
released or the session ends. A transaction-level lock would end with its transaction.
These are [PostgreSQL advisory-lock semantics](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS).

The service opens a dedicated guard connection using `INFRAHUB_SYNC_DATABASE_URL`.
Acquisition, ownership checks and release must use the same PostgreSQL backend session.
**Transaction-mode PgBouncer is unsupported:** it can assign different backend sessions
between transactions. The guard connection is never passed to an adapter or the product
store; destination writes use the adapter's own client.

The connection uses autocommit, so setting `lock_timeout` and holding the advisory lock
leave no transaction open during destination work. The guard does not establish
multi-host or high-availability support; see the
[qualified deployment limits](../../operations/supported-platforms-and-limits.mdx).

### The advisory key

Each configuration gets one signed 64-bit key: the first eight bytes of the SHA-256
digest of `infrahub-sync:apply:v1:<configuration-id>`, read in network byte order as a
signed integer. Every participating process must derive the same key.

`tests/service/test_apply_guard.py` checks literal expected values. Changing the prefix,
byte count, byte order or signed interpretation would change which writers exclude each
other; the `v1` prefix identifies this derivation.

PostgreSQL records a single-`bigint` advisory key in `pg_locks` as high and low 32-bit
halves in `classid` and `objid`, with `objsubid = 1`. The ownership query compares those
halves with the derived key.

### The deadline

The acquisition deadline defaults to 30 seconds. `hold_apply_guard` accepts values from
0.001 through 300 seconds and rejects values outside that range, including non-finite
values, before opening a connection. This limits the lock wait, not the duration of the
destination write.

`deadline_milliseconds` rounds up to an integer from 1 through 300,000. For example,
0.0015 seconds becomes 2 milliseconds. PostgreSQL interprets `lock_timeout = 0` as an
unlimited wait, so the conversion must never produce zero for an accepted deadline.

The guard sets `lock_timeout` on its session, then calls `pg_advisory_lock`. A Psycopg
failure with SQLSTATE `55P03` becomes `ApplyGuardContentionError`. Other provider `Exception`
instances during acquisition become `ApplyGuardUnavailableError`, distinguishing lock
contention from a connection or provider failure.

### Proving ownership without reacquiring

`ApplyGuard.require_ownership` queries `pg_locks` and checks both the backend process ID
recorded at acquisition and the exact advisory key. It does not reacquire the lock.
PostgreSQL counts repeated acquisitions by the same session, so reacquiring would require
an extra unlock and could conceal a lost lock.

A changed backend, a missing lock row or a failed query retires the guard: it refuses
later use and attempts to close its session. PostgreSQL releases session locks when the
session ends. If closing fails, the guard cannot confirm release.

### Confirmed release and cleanup precedence

Leaving a successful `hold_apply_guard` block calls `ApplyGuard.release`. Callers can also
release explicitly; repeating a confirmed release does nothing.

Release succeeds only when `pg_advisory_unlock` returns true from the recorded backend
and the dedicated connection closes successfully. A false result, provider exception or
failed close retires the guard and raises `ApplyGuardReleaseError`. Catching that error
inside the block does not make its later exit successful: a retired guard refuses release.

If the block body raises, its exception takes precedence over cleanup failures. The guard
attempts to close the session, then re-raises the original exception. A cleanup-only
failure raises when there is no body exception to preserve.

### Containing every exception class

The guard accepts an injected connection, so provider calls can raise exceptions other
than `psycopg.Error`. Its exception boundaries handle them as follows:

- Provider `Exception` instances become sanitized guard failures. Only a Psycopg error
  with SQLSTATE `55P03` counts as contention during acquisition.
- A `BaseException` outside `Exception`, such as cancellation or an interrupt, propagates
  unchanged after the session is retired or discarded.
- The private `_close` helper returns a cleanup failure instead of raising it. It catches
  every exception class, including an interrupt during close, so cleanup cannot replace
  the primary failure.

### Sanitizing the whole failure graph

Guard failures use fixed messages and a rebuilt, redacted cause chain from
`sanitize_exception_chain`. The rebuilt exceptions contain the original type names and
redacted message text; they do not copy the original structured arguments or notes.
See [secret redaction](../guidelines/secret-redaction.md) for the shared collector.

The guard's `_fail` helper also clears `__context__` after raising. Suppressing context
only hides it in a rendered traceback; clearing it prevents a serializer from finding the
original provider exception through that attribute.

A keyword connection string such as `host=… password=…` is not URL-shaped.
`dsn_secret_values` extracts its password, and the service supplies it through the guard's
`secrets` argument. Values shorter than the shared `MIN_SECRET_LENGTH` are excluded to
avoid replacing unrelated diagnostic text.

### Testing it

`tests/service/test_apply_guard.py` uses a scripted session fake to exercise acquisition,
ownership, release and exception handling. The opt-in
`tests/integration/test_apply_guard_integration.py` checks the PostgreSQL behavior that the
fake assumes, using `-m integration` and `APPLY_GUARD_TEST_POSTGRESQL_DSN`.

Those integration tests terminate backends. Their connection string must identify a
disposable, single-purpose database. Changes to the guard implementation require real
PostgreSQL evidence; a skipped integration case does not verify provider behavior.

### See also

- [The shared execution surface](execution-surface.md) — operation inputs, result types and local locking.
- [Sync architecture](sync-architecture.md) — registered execution and durable records.
- [Testing](../guidelines/testing.md) — behavioral checks and test isolation.

[guard-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/apply_guard.py
[flow-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/flow.py#L545-L668
