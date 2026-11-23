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
import secrets
import subprocess  # noqa: S404 - the holder process runs a fixed interpreter and inline script.
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest

psycopg: Any = pytest.importorskip("psycopg")
pytest.importorskip("psycopg.sql")
pytest.importorskip("psycopg.conninfo")

# Imported after the skip: the guard module imports psycopg itself, so a static
# import here would raise instead of skipping where the service extra is absent.
from infrahub_sync.service.apply_guard import (  # noqa: E402
    GUARD_LABEL_PREFIX,
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
    run_id = sys.argv[3] if len(sys.argv) > 3 else None
    with hold_apply_guard(configuration_id, connect=lambda: connect_guard_session(dsn), run_id=run_id) as guard:
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


def _start_holder(dsn: str, configuration_id: str, run_id: str | None = None) -> tuple[subprocess.Popen[str], int]:
    """Start an independent process holding one configuration's guard, and read its key.

    With `run_id`, the holder labels its guard session with that run.
    """
    arguments = [dsn, configuration_id] if run_id is None else [dsn, configuration_id, run_id]
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter and inline script.
        [sys.executable, "-c", _HOLDER_SCRIPT, *arguments],
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


# --- Naming the holder while another run also waits (spec 028, SC-001) ----------------

_RUN_A = "20261123T1012-aaaaaaaa"
_RUN_B = "20261123T1012-bbbbbbbb"
_RUN_C = "20261123T1012-cccccccc"
_WAITING_DEADLINE_SECONDS = 5.0
_TIMING_OUT_DEADLINE_SECONDS = 1.0
_REPETITIONS = 20
# A session that waits for the key, identified by its label: `pg_locks` lists a waiting
# request as a row that is not granted.
_LABELLED_WAITERS = """
SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND NOT l.granted
  AND l.classid::bigint = %s AND l.objid::bigint = %s AND l.objsubid = 1
  AND a.application_name = %s
"""


def _hold_once(dsn: str, run_id: str, deadline_seconds: float) -> bool:
    """Take the alpha guard as `run_id`, prove the hold, and release it."""
    with hold_apply_guard(_ALPHA, connect=_connect(dsn), deadline_seconds=deadline_seconds, run_id=run_id) as guard:
        guard.require_ownership()
    return True


def _wait_until_queued(admin: _Session, key: int, run_id: str) -> None:
    """Return once the session labelled with `run_id` waits for `key`, without a fixed sleep."""
    parameters = ((key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF, f"{GUARD_LABEL_PREFIX}{run_id}")
    limit = time.monotonic() + _WAITING_DEADLINE_SECONDS
    while time.monotonic() < limit:
        if admin.execute(_LABELLED_WAITERS, parameters).fetchone() == (1,):
            return
        time.sleep(0.01)
    pytest.fail(f"run {run_id} never queued for the guard")


@pytest.mark.parametrize("repetition", range(_REPETITIONS))
def test_the_run_whose_wait_ends_names_the_holder_and_never_the_other_waiter(
    dsn: str, admin: _Session, repetition: int
) -> None:
    """Run A holds the guard; B and C wait. C's wait ends first and names A, never B.

    B waits longer than C, so it is still queued, labelled, when C reads the holder. Only a
    granted lock is a holder, so B's waiting request must never be read as one.
    """
    del repetition
    child, _ = _start_holder(dsn, _ALPHA, _RUN_A)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        waiting = pool.submit(_hold_once, dsn, _RUN_B, _WAITING_DEADLINE_SECONDS)
        _wait_until_queued(admin, _ALPHA_KEY, _RUN_B)
        with pytest.raises(ApplyGuardContentionError) as caught:
            _hold_once(dsn, _RUN_C, _TIMING_OUT_DEADLINE_SECONDS)
    finally:
        stdout, stderr = _finish_holder(child)
        pool.shutdown(wait=True)

    assert caught.value.holder_run_id == _RUN_A
    assert caught.value.holder_run_id != _RUN_B
    assert waiting.result() is True
    assert "RELEASED" in stdout, stderr
    assert _advisory_holders(admin, _ALPHA_KEY) == []


# --- The holder cannot be proven (spec 028, User Story 2, SC-002) ----------------------

_SET_APPLICATION_NAME = "SELECT set_config('application_name', %s, false)"
_LOCK = "SELECT pg_advisory_lock(%s)"
_APPLICATION_NAME_BYTES = """
SELECT octet_length(a.application_name)
FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.granted
  AND l.classid::bigint = %s AND l.objid::bigint = %s AND l.objsubid = 1
"""


def _contended_holder_run_id(connect: Callable[[], Any]) -> str | None:
    """Wait as run C until the deadline, and return the holder candidate the contention error carries."""
    with pytest.raises(ApplyGuardContentionError) as caught:  # noqa: SIM117 - the hold is the raising statement.
        with hold_apply_guard(
            _ALPHA, connect=connect, deadline_seconds=_CONTENTION_DEADLINE_SECONDS, run_id=_RUN_C
        ) as guard:
            pytest.fail(f"a contended configuration must not be entered: {guard!r}")
    assert str(caught.value) == "another writer holds this configuration's write guard"
    return caught.value.holder_run_id


def _hold_by_hand(dsn: str, label: str) -> _Session:
    """Open a session that sets `application_name` to `label` itself, then takes the alpha key."""
    session = psycopg.connect(dsn, autocommit=True)
    session.execute(_SET_APPLICATION_NAME, (label,))
    session.execute(_LOCK, (_ALPHA_KEY,))
    return session


def test_a_holder_without_a_label_is_not_known(dsn: str, admin: _Session) -> None:
    """A holder that never labelled its session gives no candidate."""
    child, _ = _start_holder(dsn, _ALPHA)
    try:
        holder_run_id = _contended_holder_run_id(_connect(dsn))
    finally:
        stdout, stderr = _finish_holder(child)

    assert holder_run_id is None
    assert "RELEASED" in stdout, stderr
    assert _advisory_holders(admin, _ALPHA_KEY) == []


def test_a_holder_label_cut_to_63_bytes_is_not_known(dsn: str, admin: _Session) -> None:
    """FR-004: PostgreSQL cuts a longer label to 63 bytes, and a 63-byte label may be a cut-off one.

    The tail matches the run-ID pattern, so only the length rule refuses the label.
    """
    holder = _hold_by_hand(dsn, f"{GUARD_LABEL_PREFIX}{'a' * 80}")
    try:
        stored_bytes = admin.execute(
            _APPLICATION_NAME_BYTES, ((_ALPHA_KEY >> 32) & 0xFFFFFFFF, _ALPHA_KEY & 0xFFFFFFFF)
        )
        assert stored_bytes.fetchall() == [(63,)]
        holder_run_id = _contended_holder_run_id(_connect(dsn))
    finally:
        holder.close()

    assert holder_run_id is None
    assert _advisory_holders(admin, _ALPHA_KEY) == []


class _ReleaseBeforeLookup:
    """A guard session that makes the holder release the key between the lock timeout and the lookup.

    The guard bounds its holder lookup with `statement_timeout` first, so that statement is the
    point after the lock timeout and before the holder query. There, the holder session is closed
    and the test waits until the server no longer lists it as holding the key.
    """

    def __init__(self, dsn: str, holder: _Session, admin: _Session) -> None:
        self._session = connect_guard_session(dsn)
        self._holder = holder
        self._admin = admin
        self.released = False

    def execute(self, query: Any, params: Any = None) -> Any:  # noqa: ANN401 - the driver's own cursor.
        if "'statement_timeout'" in query and not self.released:
            self._holder.close()
            limit = time.monotonic() + _WAITING_DEADLINE_SECONDS
            while _advisory_holders(self._admin, _ALPHA_KEY) and time.monotonic() < limit:
                time.sleep(0.01)
            self.released = _advisory_holders(self._admin, _ALPHA_KEY) == []
        return self._session.execute(query, params)

    def close(self) -> None:
        self._session.close()


def test_a_holder_that_releases_before_the_lookup_is_not_known(dsn: str, admin: _Session) -> None:
    """The lock is free when the lookup runs, so no session holds it and no run is named.

    Run A's labelled session held the key when C's wait ended; naming A after its release would be a
    run that no longer holds the guard, and any other run would be wrong.
    """
    holder = _hold_by_hand(dsn, f"{GUARD_LABEL_PREFIX}{_RUN_A}")
    waiter: list[_ReleaseBeforeLookup] = []

    def connect() -> _ReleaseBeforeLookup:
        session = _ReleaseBeforeLookup(dsn, holder, admin)
        waiter.append(session)
        return session

    try:
        holder_run_id = _contended_holder_run_id(connect)
    finally:
        holder.close()

    assert waiter[0].released is True
    assert holder_run_id is None
    assert _advisory_holders(admin, _ALPHA_KEY) == []


_HOLDER_ROLE = "infrahub_sync_guard_holder_028"
_WAITER_ROLE = "infrahub_sync_guard_waiter_028"


def _drop_role(admin: _Session, role: str, database: str) -> None:
    """Remove one test role and what it was granted, if it exists."""
    exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    if exists is None:
        return
    identifier = psycopg.sql.Identifier(role)
    admin.execute(
        psycopg.sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(psycopg.sql.Identifier(database), identifier)
    )
    admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(identifier))
    admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(identifier))


@pytest.fixture(name="account_dsns")
def account_dsns_fixture(dsn: str, admin: _Session) -> Iterator[tuple[str, str]]:
    """Two login roles without superuser rights, allowed only to connect: the holder's and the waiter's."""
    database = admin.execute("SELECT current_database()").fetchone()[0]
    dsns: list[str] = []
    for role in (_HOLDER_ROLE, _WAITER_ROLE):
        _drop_role(admin, role, database)
        password = secrets.token_hex(16)
        admin.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(
                psycopg.sql.Identifier(role), psycopg.sql.Literal(password)
            )
        )
        admin.execute(
            psycopg.sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                psycopg.sql.Identifier(database), psycopg.sql.Identifier(role)
            )
        )
        dsns.append(psycopg.conninfo.make_conninfo(dsn, user=role, password=password))
    try:
        yield dsns[0], dsns[1]
    finally:
        # The sessions of both roles have ended before the roles are dropped.
        for role in (_HOLDER_ROLE, _WAITER_ROLE):
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
            _drop_role(admin, role, database)


def test_a_holder_of_another_account_is_named_correctly_or_not_known(
    admin: _Session, account_dsns: tuple[str, str]
) -> None:
    """R4, critique E7: a waiter of a third account may not read the holder's label; never a wrong run.

    The result depends on what PostgreSQL shows of another account's session in `pg_stat_activity`,
    which also needs `track_activities`; the test prints both.
    """
    holder_dsn, waiter_dsn = account_dsns
    track_activities = admin.execute("SHOW track_activities").fetchone()[0]
    child, _ = _start_holder(holder_dsn, _ALPHA, _RUN_A)
    try:
        holder_run_id = _contended_holder_run_id(_connect(waiter_dsn))
    finally:
        stdout, stderr = _finish_holder(child)

    # Critique E7: the server setting and the result belong in the test output.
    print(f"track_activities={track_activities} holder_run_id={holder_run_id}")  # noqa: T201
    assert holder_run_id in {None, _RUN_A}
    assert "RELEASED" in stdout, stderr
    assert _advisory_holders(admin, _ALPHA_KEY) == []
