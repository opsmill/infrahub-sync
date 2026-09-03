"""Opt-in proof of the configuration write guard against a real PostgreSQL server.

The unit suite drives the guard against a scripted fake. These tests prove the
provider facts that fake assumes: that a session-level `lock_timeout` bounds
`pg_advisory_lock`, that `pg_locks` reports the holding backend, that
`pg_advisory_unlock` confirms release, and that two independent OS processes
serialize on one configuration while different configurations do not contend.

WARNING: point the DSN only at a disposable, single-purpose database. These tests
terminate backends on it.

Opt in with ``-m integration`` and a reachable ``APPLY_GUARD_TEST_POSTGRESQL_DSN``::

    docker run -d --rm --name ph3-pg16 -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=probe \\
        -p 127.0.0.1:55433:5432 postgres:16-alpine
    APPLY_GUARD_TEST_POSTGRESQL_DSN="host=127.0.0.1 port=55433 user=postgres password=probe dbname=probe" \\
        uv run pytest -m integration tests/integration/test_apply_guard_integration.py
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - the holder process runs a fixed interpreter and inline script.
import sys
import textwrap
import time
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("psycopg")

import psycopg

from infrahub_sync.service.apply_guard import (
    ApplyGuardContentionError,
    ApplyGuardOwnershipError,
    ApplyGuardReleaseError,
    advisory_lock_key,
    connect_guard_session,
    hold_apply_guard,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.integration

_Session = psycopg.Connection[Any]

_DSN_ENVIRONMENT_NAME = "APPLY_GUARD_TEST_POSTGRESQL_DSN"
_ALPHA = "guard-integration-alpha"
_BETA = "guard-integration-beta"
# Pinned independently of the product code: an independent process must derive
# exactly these keys from `infrahub-sync:apply:v1:<configuration-id>`.
_ALPHA_KEY = -2161056473457444919
_BETA_KEY = -2624437274915816768
_CHILD_TIMEOUT_SECONDS = 60
_CONTENTION_DEADLINE_SECONDS = 0.5

_HOLDER_SCRIPT = textwrap.dedent(
    """
    import sys

    from infrahub_sync.service.apply_guard import connect_guard_session, hold_apply_guard

    dsn, configuration_id = sys.argv[1], sys.argv[2]
    with hold_apply_guard(configuration_id, connect=lambda: connect_guard_session(dsn)) as guard:
        print(f"HELD {guard.key}", flush=True)
        sys.stdin.readline()
        guard.require_ownership()
    print("RELEASED", flush=True)
    """
)

_LOCK_TIMEOUT_SETTING = "SELECT setting FROM pg_settings WHERE name = 'lock_timeout'"
_UNLOCK = "SELECT pg_advisory_unlock(%s)"
_ADVISORY_HOLDERS = """
SELECT pid FROM pg_locks
WHERE locktype = 'advisory' AND granted
  AND classid::bigint = %s AND objid::bigint = %s AND objsubid = 1
"""


def _dsn_or_skip() -> str:
    """Return a reachable disposable DSN, or skip before any server is contacted."""
    dsn = os.environ.get(_DSN_ENVIRONMENT_NAME)
    if not dsn:
        pytest.skip(f"the apply-guard integration tests require {_DSN_ENVIRONMENT_NAME}")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as probe:
            probe.execute("SELECT 1")
    except psycopg.Error:
        pytest.skip(f"{_DSN_ENVIRONMENT_NAME} is not reachable")
    return dsn


@pytest.fixture(name="dsn")
def dsn_fixture() -> str:
    return _dsn_or_skip()


@pytest.fixture(name="admin")
def admin_fixture(dsn: str) -> Iterator[_Session]:
    """One independent observer session that never holds the guard's key."""
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


def _connect(dsn: str) -> Callable[[], _Session]:
    return lambda: connect_guard_session(dsn)


def _capturing_connect(dsn: str, captured: list[_Session]) -> Callable[[], _Session]:
    """Return a connect callable that also hands the live session to the test."""

    def connect() -> _Session:
        session = connect_guard_session(dsn)
        captured.append(session)
        return session

    return connect


def _advisory_holders(admin: _Session, key: int) -> list[int]:
    """Return the backend PIDs holding one exact single-bigint advisory key."""
    rows = admin.execute(_ADVISORY_HOLDERS, ((key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF)).fetchall()
    return sorted(row[0] for row in rows)


def test_a_hold_registers_exactly_its_own_key_on_one_live_session(dsn: str, admin: _Session) -> None:
    """The real server must show one holder for the derived key and none for another."""
    with hold_apply_guard(_ALPHA, connect=_connect(dsn)) as guard:
        assert guard.key == advisory_lock_key(_ALPHA) == _ALPHA_KEY
        holders = _advisory_holders(admin, _ALPHA_KEY)
        other = _advisory_holders(admin, _BETA_KEY)
        guard.require_ownership()

    assert len(holders) == 1
    assert other == []
    assert _advisory_holders(admin, _ALPHA_KEY) == []


def test_repeated_ownership_proofs_never_stack_a_second_lock(dsn: str, admin: _Session) -> None:
    """Session advisory locks are counted, so one unlock must fully release the key.

    Counting the hold levels from the guard's own session is what makes this
    discriminating: closing the session releases every level at once, so a proof
    that reacquired would leave no trace once the hold ended.
    """
    captured: list[_Session] = []
    unlocks: list[tuple[Any, ...] | None] = []

    def prove_repeatedly() -> None:
        with hold_apply_guard(_ALPHA, connect=_capturing_connect(dsn, captured)) as guard:
            for _ in range(5):
                guard.require_ownership()
            unlocks.append(captured[0].execute(_UNLOCK, (guard.key,)).fetchone())
            unlocks.append(captured[0].execute(_UNLOCK, (guard.key,)).fetchone())

    with pytest.raises(ApplyGuardReleaseError):
        prove_repeatedly()

    assert unlocks == [(True,), (False,)]
    assert _advisory_holders(admin, _ALPHA_KEY) == []


def test_the_guard_session_holds_no_open_transaction(dsn: str, admin: _Session) -> None:
    """A guard held across a long write must not sit idle in a transaction."""
    with hold_apply_guard(_ALPHA, connect=_connect(dsn)) as guard:
        pid = _advisory_holders(admin, guard.key)[0]
        state = admin.execute("SELECT state FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()

    assert state == ("idle",)


@pytest.mark.parametrize(("deadline_seconds", "expected"), [(0.001, "1"), (1.5, "1500"), (300.0, "300000")])
def test_the_deadline_reaches_the_server_as_bounded_milliseconds(
    dsn: str, deadline_seconds: float, expected: str
) -> None:
    """PostgreSQL reads `lock_timeout = 0` as "wait forever"; the guard never sends it."""
    captured: list[_Session] = []

    with hold_apply_guard(_ALPHA, connect=_capturing_connect(dsn, captured), deadline_seconds=deadline_seconds):
        setting = captured[0].execute(_LOCK_TIMEOUT_SETTING).fetchone()

    assert setting == (expected,)


def test_an_external_unlock_breaks_the_ownership_proof(dsn: str, admin: _Session) -> None:
    """A stray unlock on the guard's own session must not pass as ownership."""
    captured: list[_Session] = []

    def unlock_then_prove() -> None:
        with hold_apply_guard(_ALPHA, connect=_capturing_connect(dsn, captured)) as guard:
            captured[0].execute(_UNLOCK, (guard.key,))
            guard.require_ownership()

    with pytest.raises(ApplyGuardOwnershipError):
        unlock_then_prove()

    assert _advisory_holders(admin, _ALPHA_KEY) == []


def test_backend_session_loss_breaks_the_ownership_proof(dsn: str, admin: _Session) -> None:
    """A terminated backend releases the lock; the guard must refuse, not assume."""

    def terminate_then_prove() -> None:
        with hold_apply_guard(_ALPHA, connect=_connect(dsn)) as guard:
            pid = _advisory_holders(admin, guard.key)[0]
            admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            guard.require_ownership()

    with pytest.raises(ApplyGuardOwnershipError):
        terminate_then_prove()

    assert _advisory_holders(admin, _ALPHA_KEY) == []


def test_a_body_exception_releases_the_real_lock(dsn: str, admin: _Session) -> None:
    """An interrupted hold must leave the configuration immediately writable again."""
    message = "interrupted"

    def interrupted_hold() -> None:
        with hold_apply_guard(_ALPHA, connect=_connect(dsn)):
            assert len(_advisory_holders(admin, _ALPHA_KEY)) == 1
            raise ValueError(message)

    with pytest.raises(ValueError, match=message):
        interrupted_hold()

    assert _advisory_holders(admin, _ALPHA_KEY) == []
    with hold_apply_guard(_ALPHA, connect=_connect(dsn), deadline_seconds=_CONTENTION_DEADLINE_SECONDS):
        assert len(_advisory_holders(admin, _ALPHA_KEY)) == 1


def _start_holder(dsn: str, configuration_id: str) -> tuple[subprocess.Popen[str], int]:
    """Start an independent process holding one configuration's guard, and read its key."""
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter and inline script.
        [sys.executable, "-c", _HOLDER_SCRIPT, dsn, configuration_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    first = child.stdout.readline().strip()
    if not first.startswith("HELD "):
        child.kill()
        pytest.fail(f"holder process did not acquire the guard: {first!r} {child.communicate()!r}")
    return child, int(first.split()[1])


def _finish_holder(child: subprocess.Popen[str]) -> tuple[str, str]:
    assert child.stdin is not None
    child.stdin.write("go\n")
    child.stdin.flush()
    return child.communicate(timeout=_CHILD_TIMEOUT_SECONDS)


def test_two_independent_processes_serialize_one_configuration(dsn: str, admin: _Session) -> None:
    """The mechanism must exclude a second OS process, not only a second object.

    Also pins the cross-process contract: exclusion only works because the child
    process derives exactly the same literal key from the same configuration.
    """

    def contended_hold() -> None:
        with hold_apply_guard(_ALPHA, connect=_connect(dsn), deadline_seconds=_CONTENTION_DEADLINE_SECONDS) as guard:
            pytest.fail(f"a contended configuration must not be entered: {guard!r}")

    child, child_key = _start_holder(dsn, _ALPHA)
    try:
        assert child_key == _ALPHA_KEY
        assert len(_advisory_holders(admin, _ALPHA_KEY)) == 1
        started = time.monotonic()
        with pytest.raises(ApplyGuardContentionError):
            contended_hold()
        waited = time.monotonic() - started
    finally:
        stdout, stderr = _finish_holder(child)

    assert waited >= _CONTENTION_DEADLINE_SECONDS, f"the guard returned before its deadline: {waited}s"
    assert "RELEASED" in stdout, stderr
    assert child.returncode == 0, stderr
    assert _advisory_holders(admin, _ALPHA_KEY) == []
    with hold_apply_guard(_ALPHA, connect=_connect(dsn), deadline_seconds=1.0) as guard:
        guard.require_ownership()


def test_different_configurations_write_concurrently(dsn: str, admin: _Session) -> None:
    """Serialization is per configuration, not global."""
    child, _ = _start_holder(dsn, _ALPHA)
    try:
        with hold_apply_guard(_BETA, connect=_connect(dsn), deadline_seconds=_CONTENTION_DEADLINE_SECONDS) as guard:
            guard.require_ownership()
            assert len(_advisory_holders(admin, _BETA_KEY)) == 1
            assert len(_advisory_holders(admin, _ALPHA_KEY)) == 1
    finally:
        stdout, stderr = _finish_holder(child)

    assert "RELEASED" in stdout, stderr
    assert child.returncode == 0, stderr
