"""Configuration-scoped PostgreSQL advisory guard that serializes one configuration's writes.

The guard takes a **session-level** advisory lock on **one dedicated direct
PostgreSQL connection**. Neither property is a preference; each one is required
for the guard to exclude anything at all:

- session level, because a write is held across many destination operations and a
  transaction-scoped lock would be released at the first commit; and
- direct, because a transaction-mode connection pooler multiplexes one server
  session across clients, so the lock and the writer would no longer be the same
  session. **Transaction-mode PgBouncer is unsupported.** Session-mode pooling, or
  a direct connection, is required.

Every provider and cleanup boundary contains all exception classes, not only
`psycopg.Error`: a connection, cursor, or protocol implementation may raise
anything, and an interrupt can arrive mid-statement. A provider `Exception`
becomes a sanitized guard failure, a non-`Exception` `BaseException` is re-raised
unchanged once the session is retired, and a cleanup failure never replaces the
body exception or the primary failure already unwinding.

The guard is inert on its own: nothing in the supported write path calls it yet.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

import psycopg
from psycopg import conninfo
from typing_extensions import LiteralString

from infrahub_sync.execution import (
    MIN_SECRET_LENGTH,
    LiveSecretValues,
    collect_secret_values,
    sanitize_exception_chain,
)
from infrahub_sync.product_store.models import _IDENTIFIER_PATTERN

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from typing import Literal, NoReturn

ADVISORY_KEY_PREFIX = "infrahub-sync:apply:v1:"
DEFAULT_DEADLINE_SECONDS = 30.0
MIN_DEADLINE_SECONDS = 0.001
MAX_DEADLINE_SECONDS = 300.0

# The write-lock session label: this prefix followed by the holding run's ID. A label
# longer than 62 bytes is never written and never accepted, because PostgreSQL
# truncates `application_name` at `NAMEDATALEN - 1` (63 bytes in a standard build),
# so a 63-byte label may be the cut-off form of a longer one.
GUARD_LABEL_PREFIX = "infrahub-sync-guard:"
MAX_GUARD_LABEL_BYTES = 62
# The run records' identifier pattern. `fullmatch` rather than `match`, because `$`
# also matches before a trailing newline.
_RUN_ID = re.compile(_IDENTIFIER_PATTERN)

logger = logging.getLogger(__name__)

_LOCK_NOT_AVAILABLE = "55P03"
_OID_MASK = 0xFFFFFFFF

# `set_config(..., false)` rather than `SET`, because `SET` takes no parameters and
# the deadline must never be interpolated into the statement text.
_SET_LABEL = "SELECT set_config('application_name', %s, false)"
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
# The holder lookup after a lock timeout. It is bounded by its own `statement_timeout`, so
# a slow catalog read cannot extend a contended failure by more than two seconds.
_HOLDER_TIMEOUT = "SELECT set_config('statement_timeout', %s, false)"
HOLDER_LOOKUP_TIMEOUT_SECONDS = 2
_HOLDER_TIMEOUT_MILLISECONDS = str(HOLDER_LOOKUP_TIMEOUT_SECONDS * 1000)
# The label of the session that holds this exact key in this database. At most two rows are
# read: a candidate is taken only when exactly one session holds the key.
_HOLDER = """
SELECT a.application_name
FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.granted
  AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND l.classid::bigint = %s AND l.objid::bigint = %s AND l.objsubid = 1
  AND l.pid <> pg_backend_pid()
LIMIT 2
"""

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
    """The wait for the configuration's key ended before this writer could take it.

    `holder_run_id` is a candidate run ID parsed from the label of the session that held
    the key when the wait ended, or `None` when the label is absent, unreadable, or not
    well formed. It is not confirmed against the run records.

    `wait_started_at` is when this writer started waiting for the key, in UTC: the time the
    guard took immediately before it sent the lock request. `None` when the guard did not set it.
    """

    def __init__(
        self, message: str, *, holder_run_id: str | None = None, wait_started_at: datetime | None = None
    ) -> None:
        super().__init__(message)
        self.holder_run_id = holder_run_id
        self.wait_started_at = wait_started_at


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


def guard_label(run_id: str) -> str | None:
    """Return the session label that names `run_id`, or `None` when it cannot be written.

    The label is written only when the run ID matches the run records' identifier
    pattern and the whole label is at most `MAX_GUARD_LABEL_BYTES` bytes, so a label
    that is written can always be parsed back to the same run ID.
    """
    if _RUN_ID.fullmatch(run_id) is None:
        return None
    label = GUARD_LABEL_PREFIX + run_id
    if len(label.encode("utf-8")) > MAX_GUARD_LABEL_BYTES:
        return None
    return label


def _holder_run_id(label: object) -> str | None:
    """Parse the holding run's ID from one untrusted session label.

    Any session of the same database account can set its own label, so the label is
    accepted only when it is a `str` of at most `MAX_GUARD_LABEL_BYTES` UTF-8 bytes,
    starts with the exact prefix, and the rest matches the run ID pattern. The raw
    label never leaves this function, so it cannot reach a message or a record.
    """
    if not isinstance(label, str):
        return None
    if len(label.encode("utf-8", errors="surrogatepass")) > MAX_GUARD_LABEL_BYTES:
        return None
    if not label.startswith(GUARD_LABEL_PREFIX):
        return None
    run_id = label[len(GUARD_LABEL_PREFIX) :]
    if _RUN_ID.fullmatch(run_id) is None:
        return None
    return run_id


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
        # Kept by reference: `hold_apply_guard` passes a list that is read again at each use.
        self._secrets = secrets
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
            row = self._connection.execute(_OWNERSHIP, _lock_columns(self._key)).fetchone()
        # One provider boundary; every class is classified rather than escaping.
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            failure = self._failure(ApplyGuardOwnershipError, _SESSION_LOST.format(type(exc).__name__), exc)
            self.retire()
            _fail(failure)
        except BaseException:  # Contained, then re-raised unchanged.
            self.retire()
            raise
        if row != (self._backend_pid, True):
            self.retire()
            _fail(self._failure(ApplyGuardOwnershipError, _NOT_OWNED))

    def release(self) -> None:
        """Confirm the provider released exactly this key, then close the session.

        Callers may confirm release explicitly before publishing any success;
        leaving the `hold_apply_guard` block confirms it otherwise. Only a
        confirmed unlock followed by a successful close is a release, so repeating
        one is a no-op while every other outcome leaves the session retired.
        """
        if self._state == "released":
            return
        if self._state == "retired":
            _fail(self._failure(ApplyGuardReleaseError, _RETIRED))
        try:
            row = self._connection.execute(_RELEASE, (self._key,)).fetchone()
        # One provider boundary; every class is classified rather than escaping.
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            failure = self._failure(ApplyGuardReleaseError, _RELEASE_FAILED.format(type(exc).__name__), exc)
            self.retire()
            _fail(failure)
        except BaseException:  # Contained, then re-raised unchanged.
            self.retire()
            raise
        confirmed = row == (True, self._backend_pid)
        close_failure = self._close()
        if not confirmed:
            self._state = "retired"
            _fail(self._failure(ApplyGuardReleaseError, _UNCONFIRMED))
        if close_failure is not None:
            self._state = "retired"
            _fail(close_failure)
        self._state = "released"

    def retire(self) -> None:
        """Discard the session permanently; closing it releases any key it holds.

        Never raises, whatever the connection does, so a caller unwinding on its
        own failure keeps that failure rather than a cleanup one.
        """
        if self._state != "held":
            return
        self._state = "retired"
        self._close()

    def _close(self) -> ApplyGuardError | None:
        """Close the dedicated session, returning any failure rather than raising.

        A close failure is never allowed to replace the body exception or the
        primary provider failure that a caller is already unwinding on, so this
        boundary contains every exception class and reports the outcome instead.
        """
        try:
            self._connection.close()
        # One cleanup boundary; a close failure never replaces a primary failure.
        except BaseException as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return self._failure(ApplyGuardReleaseError, _CLOSE_FAILED.format(type(exc).__name__), exc)
        return None

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
    run_id: str | None = None,
) -> Iterator[ApplyGuard]:
    """Hold one configuration's advisory write key for exactly the body's duration.

    Refuses a rejected deadline before opening any session, waits at most the
    deadline for the key, and confirms release before returning. A body exception
    is preserved over any cleanup failure; a cleanup-only failure fails the use.

    `secrets` adds values — a keyword DSN's password, via `dsn_secret_values` —
    that the environment collector cannot see, so no driver message can carry
    them out through a failure's cause chain.

    `run_id`, when given and writable as a label (`guard_label`), labels the session
    so that a writer refused by contention can name this run as the holder.
    """
    milliseconds = deadline_milliseconds(deadline_seconds)
    # Read again at each failure: a value released to the run while the key is held is in the
    # run's redaction registry, and a list computed here would not hold it.
    collected = LiveSecretValues((*collect_secret_values(), *secrets))
    guard = _acquire(
        key=advisory_lock_key(configuration_id),
        connect=connect,
        milliseconds=milliseconds,
        secrets=collected,
        run_id=run_id,
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
    secrets: Sequence[str],
    run_id: str | None = None,
) -> ApplyGuard:
    """Label the session, bound the deadline, take the key, and prove the hold before returning it.

    The label is set before the lock request, so a failure to set it fails the acquisition
    as unavailable and never leaves an unlabelled holder. After a lock timeout, the same
    session reads the holder's label before it is closed; whatever that read returns, the
    failure stays a contention failure.
    """
    failure: ApplyGuardError
    connection: GuardConnection | None = None
    wait_started_at: datetime | None = None
    label = None if run_id is None else guard_label(run_id)
    try:
        connection = connect()
        if label is not None:
            connection.execute(_SET_LABEL, (label,))
        connection.execute(_SET_DEADLINE, (str(milliseconds),))
        # Taken before the request, so it is never later than the real start of the wait.
        wait_started_at = _utc_now()
        connection.execute(_ACQUIRE, (key,))
        row = connection.execute(_OWNERSHIP, _lock_columns(key)).fetchone()
    # One provider boundary; every class is classified rather than escaping.
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        failure = _acquisition_failure(exc, secrets)
    except BaseException:  # Contained, then re-raised unchanged.
        if connection is not None:
            _discard(connection)
        raise
    else:
        if row is not None and row[1] is True:
            return ApplyGuard(connection=connection, key=key, backend_pid=row[0], secrets=secrets)
        failure = _guard_failure(ApplyGuardUnavailableError, _UNPROVEN, None, secrets)
    if connection is not None:
        # Outside the handler, so an interrupt during the holder read still discards the
        # session and is re-raised unchanged.
        try:
            if isinstance(failure, ApplyGuardContentionError):
                failure.wait_started_at = wait_started_at
                failure.holder_run_id = _read_holder(connection, key)
        finally:
            _discard(connection)
    _fail(failure)


def _utc_now() -> datetime:
    """Return the current time in UTC. Tests replace it to record when the guard reads it."""
    return datetime.now(timezone.utc)


def _acquisition_failure(exc: Exception, secrets: Sequence[str]) -> ApplyGuardError:
    """Classify one acquisition failure. Only a driver lock timeout is contention."""
    if isinstance(exc, psycopg.Error) and exc.sqlstate == _LOCK_NOT_AVAILABLE:
        return _guard_failure(ApplyGuardContentionError, _CONTENDED, exc, secrets)
    return _guard_failure(ApplyGuardUnavailableError, _UNAVAILABLE.format(type(exc).__name__), exc, secrets)


def _read_holder(connection: GuardConnection, key: int) -> str | None:
    """Return the run ID the session holding `key` is labelled with, or `None`.

    Best effort: every `Exception` from the lookup gives `None`. A candidate is returned
    only when exactly one other session holds the key and its label parses
    (`_holder_run_id`). The debug line records only the outcome class, never the label,
    because any session of the same database account can set its own label.
    """
    try:
        connection.execute(_HOLDER_TIMEOUT, (_HOLDER_TIMEOUT_MILLISECONDS,))
        cursor = connection.execute(_HOLDER, _lock_columns(key))
        first = cursor.fetchone()
        if first is None:
            outcome, candidate = "no-row", None
        elif cursor.fetchone() is not None:
            outcome, candidate = "several-rows", None
        else:
            candidate = _holder_run_id(first[0])
            outcome = "label-rejected" if candidate is None else "named"
    # One provider boundary: a failed lookup only costs the candidate.
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        outcome, candidate = "lookup-failed", None
    logger.debug("write guard holder lookup: %s", outcome)
    return candidate


def _discard(connection: GuardConnection) -> None:
    """Close a session that never became a hold, keeping the primary failure.

    Closing releases any advisory lock the session took, so a discarded session
    cannot keep holding a configuration. A failure here is never reported: the
    failure that caused the discard is the one the caller needs.
    """
    with suppress(BaseException):
        connection.close()


def _lock_columns(key: int) -> tuple[int, int]:
    """Split the signed key into the `oid` pair `pg_locks` records."""
    return (key >> 32) & _OID_MASK, key & _OID_MASK


def _guard_failure(
    error_type: type[ApplyGuardError],
    message: str,
    cause: BaseException | None,
    secrets: Sequence[str],
    *,
    holder_run_id: str | None = None,
) -> ApplyGuardError:
    """Build one guard failure whose cause chain is a redacted, rebuilt copy.

    `holder_run_id` applies only to a contention failure; it is ignored otherwise.
    """
    error = (
        ApplyGuardContentionError(message, holder_run_id=holder_run_id)
        if error_type is ApplyGuardContentionError
        else error_type(message)
    )
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
