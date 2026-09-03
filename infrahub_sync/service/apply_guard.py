"""Configuration-scoped PostgreSQL advisory guard that serializes one configuration's writes.

The guard takes a **session-level** advisory lock on **one dedicated direct
PostgreSQL connection**. Both properties are load-bearing:

- session level, because a write is held across many destination operations and a
  transaction-scoped lock would be released at the first commit; and
- direct, because a transaction-mode connection pooler multiplexes one server
  session across clients, so the lock and the writer would no longer be the same
  session. **Transaction-mode PgBouncer is unsupported.** Session-mode pooling, or
  a direct connection, is required.

The guard is inert on its own: nothing in the supported write path calls it yet.
"""

from __future__ import annotations

import hashlib
import math
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, Protocol

import psycopg
from psycopg import conninfo
from typing_extensions import LiteralString

from infrahub_sync.execution import MIN_SECRET_LENGTH, collect_secret_values, sanitize_exception_chain

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from typing import Literal, NoReturn

ADVISORY_KEY_PREFIX = "infrahub-sync:apply:v1:"
DEFAULT_DEADLINE_SECONDS = 30.0
MIN_DEADLINE_SECONDS = 0.001
MAX_DEADLINE_SECONDS = 300.0

_LOCK_NOT_AVAILABLE = "55P03"
_OID_MASK = 0xFFFFFFFF

# `set_config(..., false)` rather than `SET`, because `SET` takes no parameters and
# the deadline must never be interpolated into the statement text.
_SET_DEADLINE = "SELECT set_config('lock_timeout', %s, false)"
_ACQUIRE = "SELECT pg_advisory_lock(%s)"
# Reads the holder; it never reacquires. `pg_locks` records a single-`bigint`
# advisory key as the high and low halves of its two `oid` columns, with
# `objsubid = 1`.
_OWNERSHIP = """
SELECT pg_backend_pid(), EXISTS (
    SELECT 1 FROM pg_locks
    WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid()
      AND classid::bigint = %s AND objid::bigint = %s AND objsubid = 1
)
"""
_RELEASE = "SELECT pg_advisory_unlock(%s), pg_backend_pid()"

_DEADLINE_REFUSED = (
    "the configuration write guard deadline must be a finite value from "
    f"{MIN_DEADLINE_SECONDS} through {MAX_DEADLINE_SECONDS} seconds"
)
_CONTENDED = "another writer holds this configuration's write guard"
_UNAVAILABLE = "the configuration write guard could not acquire a direct PostgreSQL session ({0})"
_UNPROVEN = "the configuration write guard could not prove its own advisory lock"
_SESSION_LOST = "the configuration write guard lost its PostgreSQL session ({0})"
_NOT_OWNED = "the configuration write guard no longer owns its advisory key"
_RETIRED = "the configuration write guard session was retired"
_RELEASE_FAILED = "the configuration write guard could not release its advisory key ({0})"
_UNCONFIRMED = "the configuration write guard could not confirm its advisory unlock"
_CLOSE_FAILED = "the configuration write guard could not close its dedicated session ({0})"


class ApplyGuardError(Exception):
    """Base guard failure. Its message and cause chain are already sanitized."""


class ApplyGuardDeadlineError(ApplyGuardError):
    """The requested deadline is outside the accepted range."""


class ApplyGuardUnavailableError(ApplyGuardError):
    """No usable dedicated PostgreSQL session could hold the key."""


class ApplyGuardContentionError(ApplyGuardError):
    """Another writer held the configuration's key for the whole deadline."""


class ApplyGuardOwnershipError(ApplyGuardError):
    """The same live session could not be proven to still own the exact key."""


class ApplyGuardReleaseError(ApplyGuardError):
    """The release of the key could not be confirmed."""


class GuardCursor(Protocol):
    """The single-row read surface the guard needs from a cursor."""

    def fetchone(self) -> Any: ...


class GuardConnection(Protocol):
    """The narrow direct-PostgreSQL session surface the guard uses."""

    def execute(self, query: LiteralString, params: Sequence[Any] | None = None) -> GuardCursor: ...

    def close(self) -> None: ...


def advisory_lock_key(configuration_id: str) -> int:
    """Derive one configuration's signed 64-bit advisory key.

    The first eight SHA-256 bytes of `infrahub-sync:apply:v1:<configuration-id>`,
    read in network byte order as a signed integer — the range PostgreSQL accepts
    for a single-key advisory lock. Every process that writes one configuration
    must derive exactly this value, so the derivation is pinned by literal vectors
    in the tests.
    """
    digest = hashlib.sha256((ADVISORY_KEY_PREFIX + configuration_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def deadline_milliseconds(seconds: float) -> int:
    """Convert one accepted deadline to `lock_timeout` milliseconds, rounding up.

    Rounding up matters: PostgreSQL reads `lock_timeout = 0` as "wait forever", so
    a sub-millisecond deadline must become 1 rather than 0. The range comparison
    also rejects every non-finite value, since `nan`, `inf`, and `-inf` all fail it.
    """
    if not MIN_DEADLINE_SECONDS <= seconds <= MAX_DEADLINE_SECONDS:
        _fail(ApplyGuardDeadlineError(_DEADLINE_REFUSED))
    return math.ceil(seconds * 1000)


def dsn_secret_values(dsn: str) -> tuple[str, ...]:
    """Return the password a connection string carries, for guard redaction.

    `collect_secret_values` scans every environment value for URL userinfo, but a
    keyword connection string (`host=… password=…`) is not URL-shaped, so its
    password would reach a driver message uncollected. Values below
    `MIN_SECRET_LENGTH` are dropped for the reason the shared collector drops
    them: redacting a short value shreds unrelated diagnostics.
    """
    try:
        parsed = conninfo.conninfo_to_dict(dsn)
    except psycopg.Error:
        return ()
    password = parsed.get("password")
    if isinstance(password, str) and len(password) >= MIN_SECRET_LENGTH:
        return (password,)
    return ()


def connect_guard_session(dsn: str) -> psycopg.Connection[Any]:
    """Open one dedicated direct session for exactly one guard hold.

    Autocommit keeps the session-level `lock_timeout` and the advisory lock outside
    any transaction, so holding the guard across a long write does not leave a
    backend idle in a transaction.
    """
    return psycopg.connect(dsn, autocommit=True)


class ApplyGuard:
    """One live hold of one configuration's advisory write key."""

    def __init__(
        self,
        *,
        connection: GuardConnection,
        key: int,
        backend_pid: int,
        secrets: Sequence[str],
    ) -> None:
        self._connection = connection
        self._key = key
        self._backend_pid = backend_pid
        self._secrets = tuple(secrets)
        self._state: Literal["held", "released", "retired"] = "held"

    @property
    def key(self) -> int:
        """The signed 64-bit advisory key this hold owns."""
        return self._key

    @property
    def retired(self) -> bool:
        """Whether the session was discarded as lost, broken, or uncertain."""
        return self._state == "retired"

    def require_ownership(self) -> None:
        """Prove the same live backend session still holds exactly this key.

        Reads `pg_locks`; it never reacquires, so a key lost to session death or to
        a stray unlock cannot be silently restored by the proof itself.
        """
        if self._state != "held":
            _fail(self._failure(ApplyGuardOwnershipError, _RETIRED))
        try:
            row = self._connection.execute(_OWNERSHIP, self._lock_columns()).fetchone()
        except psycopg.Error as exc:
            failure = self._failure(ApplyGuardOwnershipError, _SESSION_LOST.format(type(exc).__name__), exc)
            self.retire()
            _fail(failure)
        if row != (self._backend_pid, True):
            self.retire()
            _fail(self._failure(ApplyGuardOwnershipError, _NOT_OWNED))

    def release(self) -> None:
        """Confirm the provider released exactly this key, then close the session.

        Callers may confirm release explicitly before publishing any success;
        leaving the `hold_apply_guard` block confirms it otherwise. Repeating a
        confirmed release is a no-op.
        """
        if self._state == "released":
            return
        if self._state == "retired":
            _fail(self._failure(ApplyGuardReleaseError, _RETIRED))
        try:
            row = self._connection.execute(_RELEASE, (self._key,)).fetchone()
        except psycopg.Error as exc:
            failure = self._failure(ApplyGuardReleaseError, _RELEASE_FAILED.format(type(exc).__name__), exc)
            self.retire()
            _fail(failure)
        confirmed = row == (True, self._backend_pid)
        self._state = "released" if confirmed else "retired"
        close_failure = self._close()
        if not confirmed:
            _fail(self._failure(ApplyGuardReleaseError, _UNCONFIRMED))
        if close_failure is not None:
            _fail(close_failure)

    def retire(self) -> None:
        """Discard the session permanently; closing it releases any key it holds.

        Never raises, so a caller unwinding on its own exception keeps that
        exception rather than a cleanup failure.
        """
        if self._state != "held":
            return
        self._state = "retired"
        self._close()

    def _close(self) -> ApplyGuardError | None:
        """Close the dedicated session, returning a failure rather than raising."""
        try:
            self._connection.close()
        except psycopg.Error as exc:
            return self._failure(ApplyGuardReleaseError, _CLOSE_FAILED.format(type(exc).__name__), exc)
        return None

    def _lock_columns(self) -> tuple[int, int]:
        """Split the signed key into the `oid` pair `pg_locks` records."""
        return (self._key >> 32) & _OID_MASK, self._key & _OID_MASK

    def _failure(
        self, error_type: type[ApplyGuardError], message: str, cause: BaseException | None = None
    ) -> ApplyGuardError:
        return _guard_failure(error_type, message, cause, self._secrets)


@contextmanager
def hold_apply_guard(
    configuration_id: str,
    *,
    connect: Callable[[], GuardConnection],
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    secrets: Sequence[str] = (),
) -> Iterator[ApplyGuard]:
    """Hold one configuration's advisory write key for exactly the body's duration.

    Refuses a rejected deadline before opening any session, waits at most the
    deadline for the key, and confirms release before returning. A body exception
    is preserved over any cleanup failure; a cleanup-only failure fails the use.

    `secrets` adds values — a keyword DSN's password, via `dsn_secret_values` —
    that the environment collector cannot see, so no driver message can carry
    them out through a failure's cause chain.
    """
    milliseconds = deadline_milliseconds(deadline_seconds)
    collected = tuple(dict.fromkeys((*collect_secret_values(), *secrets)))
    guard = _acquire(
        key=advisory_lock_key(configuration_id),
        connect=connect,
        milliseconds=milliseconds,
        secrets=collected,
    )
    try:
        yield guard
    except BaseException:
        guard.retire()
        raise
    guard.release()


def _acquire(
    *,
    key: int,
    connect: Callable[[], GuardConnection],
    milliseconds: int,
    secrets: tuple[str, ...],
) -> ApplyGuard:
    """Bound the deadline, take the key, and prove the hold before returning it."""
    failure: ApplyGuardError
    connection: GuardConnection | None = None
    try:
        connection = connect()
        connection.execute(_SET_DEADLINE, (str(milliseconds),))
        connection.execute(_ACQUIRE, (key,))
        row = connection.execute(_OWNERSHIP, ((key >> 32) & _OID_MASK, key & _OID_MASK)).fetchone()
    except psycopg.Error as exc:
        if exc.sqlstate == _LOCK_NOT_AVAILABLE:
            failure = _guard_failure(ApplyGuardContentionError, _CONTENDED, exc, secrets)
        else:
            failure = _guard_failure(ApplyGuardUnavailableError, _UNAVAILABLE.format(type(exc).__name__), exc, secrets)
    else:
        if row is not None and row[1] is True:
            return ApplyGuard(connection=connection, key=key, backend_pid=row[0], secrets=secrets)
        failure = _guard_failure(ApplyGuardUnavailableError, _UNPROVEN, None, secrets)
    if connection is not None:
        with suppress(psycopg.Error):
            connection.close()
    _fail(failure)


def _guard_failure(
    error_type: type[ApplyGuardError],
    message: str,
    cause: BaseException | None,
    secrets: Sequence[str],
) -> ApplyGuardError:
    """Build one guard failure whose cause chain is a redacted, rebuilt copy."""
    error = error_type(message)
    error.__cause__ = None if cause is None else sanitize_exception_chain(cause, secrets)
    error.__suppress_context__ = True
    return error


def _fail(error: ApplyGuardError) -> NoReturn:
    """Raise `error` with no unsanitized exception reachable from its graph.

    Raising inside an `except` block binds the handled driver exception to
    `__context__`, which a result serializer can still walk even though
    `__suppress_context__` keeps it out of a rendered traceback. Clearing it after
    the raise is the one place where every guard failure is context-free.
    """
    try:
        raise error
    except ApplyGuardError:
        error.__context__ = None
        raise
