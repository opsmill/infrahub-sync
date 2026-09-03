# The configuration write guard

> Part of: `dev/knowledge/` | Related: [The shared execution surface](execution-surface.md), [Secret redaction](../guidelines/secret-redaction.md)

`infrahub_sync/service/apply_guard.py` serializes writes to one configuration across
processes. Once workers stop sharing a filesystem, a file lock can no longer exclude a
second writer, so the mechanism is a PostgreSQL session-level advisory lock instead.

The primitive is inert: no supported write path calls it yet.

## Deployment requirements

**The guard needs a direct PostgreSQL connection, or a session-mode pooler.** Two
properties make that a hard requirement rather than a preference:

- The lock is **session level**, not transaction level. A write is held across many
  destination operations, and a transaction-scoped lock would be released at the first
  commit.
- The lock and the writer must be **the same backend session**. A transaction-mode
  connection pooler multiplexes one server session across clients between transactions,
  so the session holding the lock and the session doing the next statement are not
  guaranteed to be the same one.

**Transaction-mode PgBouncer is therefore unsupported.** Point the guard at PostgreSQL
directly, or at a pooler in session mode. The guard opens its own dedicated connection
for each hold and never hands it to an adapter.

The connection is opened with autocommit on, so the session-level `lock_timeout` and the
advisory lock live outside any transaction. Holding the guard across a long write does
not leave a backend `idle in transaction`.

## The advisory key

Each configuration gets one signed 64-bit key: the first eight bytes of the SHA-256
digest of `infrahub-sync:apply:v1:<configuration-id>`, read in network byte order as a
signed integer. That is the range PostgreSQL accepts for a single-key advisory lock.

Every process that writes a configuration has to derive the identical value, so the
derivation is pinned by literal vectors in `tests/service/test_apply_guard.py` rather
than recomputed by the tests. Changing the prefix, the byte count, the byte order, or the
signed reading is a cross-process incompatibility, which is what the `v1` in the prefix
exists to version.

`pg_locks` records a single-`bigint` advisory key as the high and low halves of its two
`oid` columns, with `objsubid = 1`. The ownership query reverses that split.

## The deadline

The deadline defaults to 30 seconds. Accepted values run from 0.001 through 300 seconds;
zero, negatives, and non-finite values refuse before any connection is opened.

Conversion to provider milliseconds **rounds up**, giving 1 through 300,000. Rounding up
is not cosmetic: PostgreSQL reads `lock_timeout = 0` as "wait forever", so a
sub-millisecond deadline must reach the server as 1, never as 0.

The deadline is applied by setting `lock_timeout` on the guard session and then calling
`pg_advisory_lock`. A wait that exceeds it aborts with SQLSTATE `55P03`, which the guard
reports as contention. Every other driver failure is reported as unavailability, so a
broken deployment is not misread as a busy one.

## Proving ownership without reacquiring

`ApplyGuard.require_ownership` reads `pg_locks` and compares both the backend PID
recorded at acquisition and the exact key. It never calls `pg_advisory_lock` again:
session advisory locks are counted, so a proof that reacquired would stack a second hold
and mask the loss it was meant to detect — and one confirmed unlock would then leave the
key held.

A proof fails when the backend session changed, when the lock row is gone (an external
unlock), or when the statement itself fails (a terminated backend). Any of those retires
the session: the guard closes it and refuses every later use. Closing a session releases
the advisory locks it held, so retirement also frees the configuration.

## Confirmed release and cleanup precedence

Leaving the `hold_apply_guard` block confirms release, and callers may confirm it
explicitly before publishing any success. Confirmation means `pg_advisory_unlock`
returned true on the same backend session; a false result, a driver failure, or a failed
close all fail the guard use even though the body succeeded.

When the body raises, that exception is preserved and the cleanup detail is dropped. This
holds for `BaseException` as well as `Exception`, so a cancellation is not replaced by a
cleanup failure. A cleanup-only failure, with no body exception to preserve, is raised.

## Sanitizing the whole failure graph

Guard failures can reach Prefect logs and persisted results, so no driver text may travel
in them. Each failure carries a fixed message and, as its cause, the rebuilt redacted
copy that `sanitize_exception_chain` produces, which covers the original message, its
structured arguments, and its own cause and context chain, and which drops its notes.

`__context__` needs its own handling. Raising inside an `except` block binds the handled
driver exception to `__context__`, and `__suppress_context__` only keeps it out of a
*rendered* traceback — a serializer walking the graph still reaches it. Every guard
failure is therefore raised through one choke point that clears `__context__` after the
raise.

A keyword connection string (`host=… password=…`) is not URL-shaped, so
`collect_secret_values` does not see its password. `dsn_secret_values` extracts it, and
callers pass it in through `secrets`. It applies the shared `MIN_SECRET_LENGTH` floor for
the reason the shared collector does: redacting a short value shreds unrelated
diagnostics.

## Testing it

The unit suite drives the guard against a scripted session fake, so every failure path is
reachable. The provider facts that fake assumes are proven separately against a real
server in `tests/integration/test_apply_guard_integration.py`, which is opt-in with
`-m integration` and a reachable `APPLY_GUARD_TEST_POSTGRESQL_DSN`. Those tests terminate
backends, so the DSN must point at a disposable, single-purpose database.

Real PostgreSQL is a hard gate for this module. A skipped guard case is not evidence.

## See also

- [The shared execution surface](execution-surface.md) — the entry point to one run, and
  the local pipeline lock the guard replaces for distributed writes.
- [Secret redaction](../guidelines/secret-redaction.md) — the shared collector and cause-chain
  rules the guard reuses.
- [Testing](../guidelines/testing.md) — why a killed mutation, not a passing test, is the evidence.
