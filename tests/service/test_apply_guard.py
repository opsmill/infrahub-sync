"""Contract for the configuration-scoped PostgreSQL advisory write guard.

These tests drive the guard against one scripted direct-session fake. The provider
facts that fake assumes — that a session-level `lock_timeout` bounds
`pg_advisory_lock`, that `pg_locks` reports the holding backend, and that
`pg_advisory_unlock` confirms release — are proven against a real server in
`tests/integration/test_apply_guard_integration.py`.
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import pytest

pytest.importorskip("psycopg")

import psycopg

from infrahub_sync.service.apply_guard import (
    DEFAULT_DEADLINE_SECONDS,
    MAX_DEADLINE_SECONDS,
    MIN_DEADLINE_SECONDS,
    ApplyGuard,
    ApplyGuardContentionError,
    ApplyGuardDeadlineError,
    ApplyGuardError,
    ApplyGuardOwnershipError,
    ApplyGuardReleaseError,
    ApplyGuardUnavailableError,
    advisory_lock_key,
    deadline_milliseconds,
    dsn_secret_values,
    hold_apply_guard,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_BACKEND_PID = 4242
_OTHER_BACKEND_PID = 9797
_CONFIGURATION = "alpha"
_ALPHA_KEY = -8187121688807872226

# One canary per exception-graph carrier, each registered under a credential-shaped
# environment variable so `collect_secret_values` treats it as a secret VALUE.
_CANARY_ENVIRONMENT = {
    "GUARD_CANARY_MESSAGE_PASSWORD": "canary-message-8a55",
    "GUARD_CANARY_ARGUMENT_PASSWORD": "canary-argument-2f7a",
    "GUARD_CANARY_NOTE_PASSWORD": "canary-note-9b41",
    "GUARD_CANARY_CAUSE_PASSWORD": "canary-cause-4d08",
    "GUARD_CANARY_CONTEXT_PASSWORD": "canary-context-6e2c",
}


class _Statement(NamedTuple):
    sql: str
    parameters: tuple[Any, ...]


class _FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeSession:
    """One scripted direct-PostgreSQL session, recording every statement."""

    def __init__(
        self,
        *,
        held: bool = True,
        released: bool = True,
        ownership_pid: int = _BACKEND_PID,
        release_pid: int = _BACKEND_PID,
    ) -> None:
        self.statements: list[_Statement] = []
        self.close_calls = 0
        self.held = held
        self.released = released
        self.ownership_pid = ownership_pid
        self.release_pid = release_pid
        self.failures: dict[str, BaseException] = {}
        self.close_failure: BaseException | None = None

    def execute(self, query: str, params: Sequence[Any] | None = None) -> _FakeCursor:
        self.statements.append(_Statement(query, tuple(params or ())))
        for fragment, error in self.failures.items():
            if fragment in query:
                raise error
        return _FakeCursor(self._row(query))

    def _row(self, query: str) -> tuple[Any, ...] | None:
        if "pg_locks" in query:
            return (self.ownership_pid, self.held)
        if "pg_advisory_unlock" in query:
            return (self.released, self.release_pid)
        if "pg_advisory_lock" in query:
            return (None,)
        return ("configured",)

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failure is not None:
            raise self.close_failure

    def executed(self, fragment: str) -> list[_Statement]:
        return [statement for statement in self.statements if fragment in statement.sql]


class _Hold:
    """One complete guard hold, driven as a single statement inside `pytest.raises`.

    Records the connect calls and the yielded guard so a test can assert what
    happened after the hold failed, without nesting a context manager inside the
    `pytest.raises` block.
    """

    def __init__(
        self,
        session: _FakeSession,
        *,
        configuration_id: str = _CONFIGURATION,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        secrets: Sequence[str] = (),
        connect_failure: BaseException | None = None,
    ) -> None:
        self.session = session
        self.connect_calls: list[int] = []
        self.guard: ApplyGuard | None = None
        self._configuration_id = configuration_id
        self._deadline_seconds = deadline_seconds
        self._secrets = secrets
        self._connect_failure = connect_failure

    def run(self, action: Callable[[ApplyGuard], object] | None = None) -> None:
        """Acquire, optionally run `action` under the hold, then release."""

        def connect() -> _FakeSession:
            self.connect_calls.append(1)
            if self._connect_failure is not None:
                raise self._connect_failure
            return self.session

        with hold_apply_guard(
            self._configuration_id,
            connect=connect,
            deadline_seconds=self._deadline_seconds,
            secrets=self._secrets,
        ) as guard:
            self.guard = guard
            if action is not None:
                action(guard)


def _reachable_text(error: BaseException) -> str:
    """Return every string reachable from an exception graph.

    Covers what a traceback renderer shows AND what a result serializer can walk:
    each link's text, its structured arguments, its notes, and both its `__cause__`
    and `__context__` edges regardless of `__suppress_context__`.
    """
    parts: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        parts.extend((str(current), repr(current.args)))
        parts.extend(getattr(current, "__notes__", None) or [])
        parts.extend(traceback.format_exception(type(current), current, current.__traceback__))
        pending.extend(link for link in (current.__cause__, current.__context__) if link is not None)
    return "\n".join(parts)


def _hostile_error(error_type: type[BaseException]) -> BaseException:
    """Return an `error_type` carrying a distinct canary in every graph carrier.

    All four carriers reach a rendered traceback: `str(error)` prints the whole
    argument tuple, and `__notes__` prints after the message line.
    """
    context = RuntimeError(_CANARY_ENVIRONMENT["GUARD_CANARY_CONTEXT_PASSWORD"])
    cause = ValueError(_CANARY_ENVIRONMENT["GUARD_CANARY_CAUSE_PASSWORD"])
    cause.__context__ = context
    error = error_type(_CANARY_ENVIRONMENT["GUARD_CANARY_MESSAGE_PASSWORD"])
    error.args = (*error.args, {"password": _CANARY_ENVIRONMENT["GUARD_CANARY_ARGUMENT_PASSWORD"]})
    # `BaseException.add_note` exists from Python 3.11; the type gate targets 3.10.
    cast("Any", error).add_note(_CANARY_ENVIRONMENT["GUARD_CANARY_NOTE_PASSWORD"])
    error.__cause__ = cause
    return error


def _inject(session: _FakeSession, site: str, error: BaseException) -> _Hold:
    """Return one hold with `error` injected at one provider site."""
    if site == "connect":
        return _Hold(session, connect_failure=error)
    session.failures[site] = error
    return _Hold(session)


def _leaked_canaries(error: BaseException) -> list[str]:
    rendered = _reachable_text(error)
    return [name for name, value in _CANARY_ENVIRONMENT.items() if value in rendered]


# --------------------------------------------------------------------------- #
# Advisory key derivation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("configuration_id", "expected"),
    [
        ("", 3890624961464930541),
        ("alpha", -8187121688807872226),
        ("beta", 2691780931179434137),
        ("netbox-to-infrahub", -5804123439392766355),
        ("0", -5102868248911540112),
        ("01931f5c-6f4e-7a3b-9c2d-8e1f0a2b3c4d", -6484595175589399321),
        ("é-uniçode", 1950900642927055419),
    ],
)
def test_advisory_lock_key_matches_its_pinned_vectors(configuration_id: str, expected: int) -> None:
    """Independent processes must derive the same signed 64-bit key.

    The negative vectors pin the signed reading of the first eight SHA-256 bytes in
    network byte order; an unsigned, little-endian, or differently namespaced
    derivation fails here.
    """
    assert advisory_lock_key(configuration_id) == expected


def test_advisory_lock_key_separates_configurations() -> None:
    """Two configurations, including near-identical ones, must never share a key."""
    identifiers = ("", "alpha", "beta", "netbox-to-infrahub", "0", "alpha ", "Alpha")

    keys = [advisory_lock_key(identifier) for identifier in identifiers]

    assert len(set(keys)) == len(identifiers)


# --------------------------------------------------------------------------- #
# Deadline bounds and conversion
# --------------------------------------------------------------------------- #


def test_the_deadline_default_and_accepted_bounds_are_the_documented_ones() -> None:
    assert (DEFAULT_DEADLINE_SECONDS, MIN_DEADLINE_SECONDS, MAX_DEADLINE_SECONDS) == (30.0, 0.001, 300.0)
    assert deadline_milliseconds(DEFAULT_DEADLINE_SECONDS) == 30_000


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.001, 1),
        (0.0010001, 2),
        (0.029, 29),
        (0.5, 500),
        (1, 1000),
        (1.0001, 1001),
        (29.9999, 30_000),
        (300.0, 300_000),
    ],
)
def test_accepted_deadlines_convert_to_provider_milliseconds_by_ceiling(seconds: float, expected: int) -> None:
    """Rounding must never reach zero, which PostgreSQL reads as "wait forever"."""
    assert deadline_milliseconds(seconds) == expected


@pytest.mark.parametrize("seconds", [0, -1, 0.0009, 300.001, float("inf"), float("-inf"), float("nan")])
def test_rejected_deadlines_refuse_conversion(seconds: float) -> None:
    """Zero, negatives, sub-millisecond, over-range, and non-finite values all refuse."""
    with pytest.raises(ApplyGuardDeadlineError):
        deadline_milliseconds(seconds)


def test_a_rejected_deadline_refuses_before_any_session_is_opened() -> None:
    """Deadline validation is deterministic and must precede provider contact."""
    session = _FakeSession()
    hold = _Hold(session, deadline_seconds=0)

    with pytest.raises(ApplyGuardDeadlineError):
        hold.run()

    assert hold.connect_calls == []
    assert session.statements == []


# --------------------------------------------------------------------------- #
# Acquisition on one dedicated session
# --------------------------------------------------------------------------- #


def test_acquisition_bounds_the_deadline_then_locks_then_proves_ownership() -> None:
    """The exact statement order, arguments, and session count the provider receives."""
    session = _FakeSession()
    hold = _Hold(session, deadline_seconds=0.3)
    acquisition: list[_Statement] = []

    hold.run(lambda _: acquisition.extend(session.statements))

    assert hold.connect_calls == [1]
    assert hold.guard is not None
    assert hold.guard.key == _ALPHA_KEY
    assert len(acquisition) == 3
    assert "set_config" in acquisition[0].sql
    assert acquisition[0].parameters == ("300",)
    assert "pg_advisory_lock" in acquisition[1].sql
    assert acquisition[1].parameters == (_ALPHA_KEY,)
    assert "pg_locks" in acquisition[2].sql
    assert session.close_calls == 1


def test_a_lock_timeout_is_reported_as_contention() -> None:
    """`lock_timeout` aborts with SQLSTATE 55P03; that is contention, not unavailability."""
    session = _FakeSession()
    session.failures["pg_advisory_lock"] = psycopg.errors.LockNotAvailable("timed out")
    hold = _Hold(session)

    with pytest.raises(ApplyGuardContentionError):
        hold.run()

    assert hold.guard is None
    assert session.close_calls == 1


def test_a_driver_failure_that_is_not_a_lock_timeout_is_reported_as_unavailable() -> None:
    """Mapping every driver error to contention would hide a broken deployment."""
    session = _FakeSession()
    session.failures["pg_advisory_lock"] = psycopg.OperationalError("server gone")
    hold = _Hold(session)

    with pytest.raises(ApplyGuardUnavailableError) as caught:
        hold.run()

    assert not isinstance(caught.value, ApplyGuardContentionError)
    assert hold.guard is None
    assert session.close_calls == 1


def test_a_session_that_cannot_open_reports_unavailable_without_a_hold() -> None:
    """A session that never opened is not a session to close."""
    session = _FakeSession()
    hold = _Hold(session, connect_failure=psycopg.OperationalError("connection refused"))

    with pytest.raises(ApplyGuardUnavailableError):
        hold.run()

    assert hold.guard is None
    assert session.close_calls == 0


def test_acquisition_that_cannot_prove_its_own_lock_refuses_and_retires_the_session() -> None:
    """A lock the guard cannot see in `pg_locks` is not a hold it may report."""
    session = _FakeSession(held=False)
    hold = _Hold(session)

    with pytest.raises(ApplyGuardUnavailableError):
        hold.run()

    assert hold.guard is None
    assert session.close_calls == 1


# --------------------------------------------------------------------------- #
# Ownership proof
# --------------------------------------------------------------------------- #


def test_the_ownership_proof_reads_pg_locks_for_the_exact_key_without_reacquiring() -> None:
    """Reacquiring would stack a second session lock and mask a lost hold."""
    session = _FakeSession()
    hold = _Hold(session, configuration_id="beta")
    beta_key = advisory_lock_key("beta")
    proof: list[_Statement] = []

    def prove(guard: ApplyGuard) -> None:
        before = len(session.statements)
        guard.require_ownership()
        proof.extend(session.statements[before:])

    hold.run(prove)

    assert len(proof) == 1
    assert "pg_locks" in proof[0].sql
    assert "pg_backend_pid()" in proof[0].sql
    assert "advisory_lock" not in proof[0].sql
    assert proof[0].parameters == ((beta_key >> 32) & 0xFFFFFFFF, beta_key & 0xFFFFFFFF)


@pytest.mark.parametrize("loss", ["backend-session", "lock-row", "driver"])
def test_a_lost_or_uncertain_hold_refuses_ownership_and_retires_the_session(loss: str) -> None:
    """A different backend, a missing lock row, and a dead session all refuse."""
    session = _FakeSession()
    hold = _Hold(session)

    def lose_then_prove(guard: ApplyGuard) -> None:
        if loss == "backend-session":
            session.ownership_pid = _OTHER_BACKEND_PID
        elif loss == "lock-row":
            session.held = False
        else:
            session.failures["pg_locks"] = psycopg.OperationalError("session terminated")
        guard.require_ownership()

    with pytest.raises(ApplyGuardOwnershipError):
        hold.run(lose_then_prove)

    assert hold.guard is not None
    assert hold.guard.retired is True
    assert session.close_calls == 1
    assert session.executed("pg_advisory_unlock") == []


@pytest.mark.parametrize(
    ("injected", "raised"),
    [
        pytest.param(None, ApplyGuardOwnershipError, id="lock-row"),
        pytest.param(psycopg.OperationalError("driver"), ApplyGuardOwnershipError, id="driver-error"),
        pytest.param(RuntimeError("ordinary"), ApplyGuardOwnershipError, id="ordinary-exception"),
        pytest.param(KeyboardInterrupt("interrupt"), KeyboardInterrupt, id="base-exception"),
    ],
)
def test_a_failed_ownership_proof_retires_the_session_immediately(
    injected: BaseException | None, raised: type[BaseException]
) -> None:
    """A caller that catches the refusal must not be able to keep using the session.

    Retiring only while the hold unwinds would leave a caught refusal followed by
    another destination operation on a session that no longer owns the key. That
    has to hold for every failure class, including a contained interrupt.
    """
    session = _FakeSession()
    hold = _Hold(session)

    def prove_twice(guard: ApplyGuard) -> None:
        if injected is None:
            session.held = False
        else:
            session.failures["pg_locks"] = injected
        with pytest.raises(raised):
            guard.require_ownership()
        assert guard.retired is True
        assert session.close_calls == 1
        quiescent = len(session.statements)
        with pytest.raises(ApplyGuardOwnershipError):
            guard.require_ownership()
        assert len(session.statements) == quiescent

    with pytest.raises(ApplyGuardReleaseError):
        hold.run(prove_twice)

    assert session.close_calls == 1


def test_a_retired_guard_refuses_ownership_and_release_without_touching_the_session() -> None:
    """A retired session is never consulted again, and can never report success."""
    session = _FakeSession()
    guard = ApplyGuard(connection=session, key=_ALPHA_KEY, backend_pid=_BACKEND_PID, secrets=())

    guard.retire()
    with pytest.raises(ApplyGuardOwnershipError):
        guard.require_ownership()
    with pytest.raises(ApplyGuardReleaseError):
        guard.release()

    assert guard.retired is True
    assert session.close_calls == 1
    assert session.statements == []


# --------------------------------------------------------------------------- #
# Confirmed release
# --------------------------------------------------------------------------- #


def test_a_successful_hold_confirms_the_unlock_and_closes_the_session() -> None:
    session = _FakeSession()

    _Hold(session).run()

    release = session.executed("pg_advisory_unlock")
    assert len(release) == 1
    assert release[0].parameters == (_ALPHA_KEY,)
    assert session.close_calls == 1


@pytest.mark.parametrize(
    "session",
    [
        pytest.param(_FakeSession(released=False), id="unlock-returned-false"),
        pytest.param(_FakeSession(release_pid=_OTHER_BACKEND_PID), id="different-backend-session"),
    ],
)
def test_an_unconfirmed_unlock_fails_the_guard_use_and_retires_the_guard(session: _FakeSession) -> None:
    """A body that succeeded is still a failed guard use without a confirmed release.

    The guard must also report itself retired, so a caller that catches the failure
    cannot read it as a usable hold or drive another unlock at the closed session.
    """
    hold = _Hold(session)

    with pytest.raises(ApplyGuardReleaseError):
        hold.run()

    assert hold.guard is not None
    assert hold.guard.retired is True
    assert session.close_calls == 1
    unlocks = len(session.executed("pg_advisory_unlock"))
    with pytest.raises(ApplyGuardReleaseError):
        hold.guard.release()
    assert len(session.executed("pg_advisory_unlock")) == unlocks


def test_a_release_driver_failure_fails_the_guard_use_and_retires_the_session() -> None:
    session = _FakeSession()
    hold = _Hold(session)

    with pytest.raises(ApplyGuardReleaseError):
        hold.run(lambda _: session.failures.update({"pg_advisory_unlock": psycopg.OperationalError("gone")}))

    assert hold.guard is not None
    assert hold.guard.retired is True
    assert session.close_calls == 1


def test_a_close_failure_after_a_confirmed_unlock_still_fails_the_guard_use() -> None:
    """Cleanup is part of the guarantee: an unclosed dedicated session is a failure."""
    session = _FakeSession()
    session.close_failure = psycopg.OperationalError("close failed")
    hold = _Hold(session)

    with pytest.raises(ApplyGuardReleaseError):
        hold.run()

    assert len(session.executed("pg_advisory_unlock")) == 1


def test_an_explicit_release_inside_the_hold_is_confirmed_exactly_once() -> None:
    """Callers must be able to confirm release before publishing any success."""
    session = _FakeSession()
    released_inside: list[int] = []

    def release_early(guard: ApplyGuard) -> None:
        guard.release()
        released_inside.append(len(session.executed("pg_advisory_unlock")))

    _Hold(session).run(release_early)

    assert released_inside == [1]
    assert len(session.executed("pg_advisory_unlock")) == 1
    assert session.close_calls == 1


# --------------------------------------------------------------------------- #
# Body exception precedence over cleanup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("body_error_type", [ValueError, KeyboardInterrupt])
def test_a_body_exception_wins_over_a_cleanup_failure(body_error_type: type[BaseException]) -> None:
    """Cancellation arrives as `BaseException`; neither kind may be replaced."""
    session = _FakeSession(released=False)
    session.close_failure = psycopg.OperationalError("close-canary")
    message = "body-canary"

    def fail_body(guard: ApplyGuard) -> None:
        del guard
        raise body_error_type(message)

    with pytest.raises(body_error_type) as caught:
        _Hold(session).run(fail_body)

    assert not isinstance(caught.value, ApplyGuardError)
    assert message in _reachable_text(caught.value)
    assert "close-canary" not in _reachable_text(caught.value)
    assert session.close_calls == 1


def test_a_body_exception_retires_the_session_without_attempting_an_unlock() -> None:
    """An interrupted hold's session is uncertain; closing it releases the lock."""
    session = _FakeSession()
    message = "body failed"

    def fail_body(guard: ApplyGuard) -> None:
        del guard
        raise ValueError(message)

    with pytest.raises(ValueError, match=message):
        _Hold(session).run(fail_body)

    assert session.executed("pg_advisory_unlock") == []
    assert session.close_calls == 1


# --------------------------------------------------------------------------- #
# Exception-graph sanitization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("site", "error_type", "expected"),
    [
        ("pg_advisory_lock", psycopg.OperationalError, ApplyGuardUnavailableError),
        ("pg_advisory_lock", psycopg.errors.LockNotAvailable, ApplyGuardContentionError),
        ("pg_locks", psycopg.OperationalError, ApplyGuardUnavailableError),
        ("pg_advisory_unlock", psycopg.OperationalError, ApplyGuardReleaseError),
    ],
)
def test_no_guard_failure_graph_reaches_a_driver_canary(
    monkeypatch: pytest.MonkeyPatch, site: str, error_type: type[BaseException], expected: type[ApplyGuardError]
) -> None:
    """Arguments, notes, cause, and context must all be sanitized or unreachable."""
    for name, value in _CANARY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    session = _FakeSession()
    session.failures[site] = _hostile_error(error_type)

    with pytest.raises(expected) as caught:
        _Hold(session).run()

    assert _leaked_canaries(caught.value) == []


def test_an_ownership_failure_graph_reaches_no_driver_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _CANARY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    session = _FakeSession()

    def lose_then_prove(guard: ApplyGuard) -> None:
        session.failures["pg_locks"] = _hostile_error(psycopg.OperationalError)
        guard.require_ownership()

    with pytest.raises(ApplyGuardOwnershipError) as caught:
        _Hold(session).run(lose_then_prove)

    assert _leaked_canaries(caught.value) == []


def test_a_guard_failure_exposes_a_sanitized_cause_and_no_reachable_context() -> None:
    """`__suppress_context__` hides a raw context from a traceback but not from a walker."""
    session = _FakeSession()
    session.failures["pg_advisory_lock"] = psycopg.OperationalError("driver detail")

    with pytest.raises(ApplyGuardUnavailableError) as caught:
        _Hold(session).run()

    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True
    assert isinstance(caught.value.__cause__, Exception)
    assert not isinstance(caught.value.__cause__, psycopg.Error)


def test_explicitly_supplied_secret_values_are_redacted_from_a_guard_failure() -> None:
    """A keyword DSN's password is not in the environment; the caller supplies it."""
    supplied = "supplied-guard-password-canary"
    session = _FakeSession()
    session.failures["pg_advisory_lock"] = psycopg.OperationalError(f"auth failed for {supplied}")

    with pytest.raises(ApplyGuardUnavailableError) as caught:
        _Hold(session, secrets=(supplied,)).run()

    assert supplied not in _reachable_text(caught.value)


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("host=127.0.0.1 dbname=sync password=guard-secret-value", ("guard-secret-value",)),
        ("postgresql://sync:guard-secret-value@127.0.0.1/sync", ("guard-secret-value",)),
        ("host=127.0.0.1 dbname=sync password=tiny", ()),
        ("host=127.0.0.1 dbname=sync", ()),
        ("not a dsn at all =", ()),
    ],
)
def test_dsn_secret_values_collects_only_a_usable_password(dsn: str, expected: tuple[str, ...]) -> None:
    """Short values are dropped: redacting them would shred unrelated diagnostics."""
    assert dsn_secret_values(dsn) == expected


# --------------------------------------------------------------------------- #
# Provider and cleanup boundary containment
# --------------------------------------------------------------------------- #

_PROVIDER_SITES = ("connect", "pg_advisory_lock", "pg_locks", "pg_advisory_unlock")
_CLEANUP_FAILURES = [
    pytest.param(psycopg.OperationalError("close-canary"), id="driver-error"),
    pytest.param(RuntimeError("close-canary"), id="ordinary-exception"),
    pytest.param(KeyboardInterrupt("close-canary"), id="base-exception"),
]


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("connect", ApplyGuardUnavailableError),
        ("pg_advisory_lock", ApplyGuardUnavailableError),
        ("pg_locks", ApplyGuardUnavailableError),
        ("pg_advisory_unlock", ApplyGuardReleaseError),
    ],
)
def test_a_non_driver_provider_exception_becomes_a_sanitized_guard_failure(
    monkeypatch: pytest.MonkeyPatch, site: str, expected: type[ApplyGuardError]
) -> None:
    """A connection, cursor, or protocol implementation may raise more than `psycopg.Error`."""
    for name, value in _CANARY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    injected = _hostile_error(RuntimeError)
    session = _FakeSession()
    hold = _inject(session, site, injected)

    with pytest.raises(expected) as caught:
        hold.run()

    assert caught.value.__cause__ is not injected
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True
    assert _leaked_canaries(caught.value) == []
    assert session.close_calls == (0 if site == "connect" else 1)


@pytest.mark.parametrize("site", _PROVIDER_SITES)
def test_a_provider_base_exception_propagates_and_still_closes_the_session(site: str) -> None:
    """Cancellation must not be converted, and must not leave the session uncertain."""
    injected = KeyboardInterrupt("interrupt-canary")
    session = _FakeSession()
    hold = _inject(session, site, injected)

    with pytest.raises(KeyboardInterrupt) as caught:
        hold.run()

    assert caught.value is injected
    assert session.close_calls == (0 if site == "connect" else 1)


def test_only_a_driver_lock_timeout_counts_as_contention() -> None:
    """A non-driver error that merely carries a 55P03 attribute is not contention."""

    class _ImpostorError(RuntimeError):
        sqlstate = "55P03"

    session = _FakeSession()
    hold = _inject(session, "pg_advisory_lock", _ImpostorError("not a driver error"))

    with pytest.raises(ApplyGuardUnavailableError) as caught:
        hold.run()

    assert not isinstance(caught.value, ApplyGuardContentionError)


@pytest.mark.parametrize(
    ("injected", "expected"),
    [
        pytest.param(psycopg.OperationalError("driver"), ApplyGuardOwnershipError, id="driver-error"),
        pytest.param(RuntimeError("ordinary"), ApplyGuardOwnershipError, id="ordinary-exception"),
        pytest.param(KeyboardInterrupt("interrupt"), KeyboardInterrupt, id="base-exception"),
    ],
)
def test_an_ownership_proof_failure_of_any_class_retires_the_session(
    injected: BaseException, expected: type[BaseException]
) -> None:
    """A proof that cannot complete leaves no usable session behind."""
    session = _FakeSession()
    hold = _Hold(session)

    def prove(guard: ApplyGuard) -> None:
        session.failures["pg_locks"] = injected
        guard.require_ownership()

    with pytest.raises(expected):
        hold.run(prove)

    assert hold.guard is not None
    assert hold.guard.retired is True
    assert session.close_calls == 1


@pytest.mark.parametrize("close_error", _CLEANUP_FAILURES)
def test_a_close_failure_of_any_class_never_replaces_the_body_exception(close_error: BaseException) -> None:
    """Retiring a session must not be able to lose the caller's own failure."""
    session = _FakeSession()
    session.close_failure = close_error
    message = "body-canary"

    def fail_body(guard: ApplyGuard) -> None:
        del guard
        raise ValueError(message)

    with pytest.raises(ValueError, match=message) as caught:
        _Hold(session).run(fail_body)

    assert "close-canary" not in _reachable_text(caught.value)
    assert session.close_calls == 1


@pytest.mark.parametrize("close_error", _CLEANUP_FAILURES)
@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("pg_advisory_lock", ApplyGuardUnavailableError),
        ("pg_locks", ApplyGuardUnavailableError),
        ("pg_advisory_unlock", ApplyGuardReleaseError),
    ],
)
def test_a_close_failure_never_replaces_a_primary_provider_failure(
    site: str, expected: type[ApplyGuardError], close_error: BaseException
) -> None:
    """The failure that caused the discard is the one the caller needs."""
    session = _FakeSession()
    session.close_failure = close_error
    hold = _inject(session, site, psycopg.OperationalError("primary-failure"))

    with pytest.raises(expected) as caught:
        hold.run()

    assert "close-canary" not in _reachable_text(caught.value)
    assert session.close_calls == 1


@pytest.mark.parametrize("close_error", _CLEANUP_FAILURES)
def test_retiring_a_guard_never_raises_whatever_the_session_does(close_error: BaseException) -> None:
    session = _FakeSession()
    session.close_failure = close_error
    guard = ApplyGuard(connection=session, key=_ALPHA_KEY, backend_pid=_BACKEND_PID, secrets=())

    guard.retire()

    assert guard.retired is True
    assert session.close_calls == 1


def test_a_close_failure_after_a_confirmed_unlock_leaves_the_guard_permanently_retired() -> None:
    """A caught release failure must not let the hold exit as a success.

    The unlock was confirmed, but the dedicated session was never closed, so the
    hold is not a clean release and a later confirmation must refuse.
    """
    session = _FakeSession()
    session.close_failure = psycopg.OperationalError("close failed")
    hold = _Hold(session)

    def release_and_swallow(guard: ApplyGuard) -> None:
        with pytest.raises(ApplyGuardReleaseError):
            guard.release()

    with pytest.raises(ApplyGuardReleaseError):
        hold.run(release_and_swallow)

    assert hold.guard is not None
    assert hold.guard.retired is True
    assert session.close_calls == 1
