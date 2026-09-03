"""Opt-in proof that the managed write path serializes real workers on real PostgreSQL.

The unit suites drive the managed composition against a scripted session. What they
cannot show is the fact the whole design rests on: that two *independent worker
processes*, each opening its own direct session from the service database setting,
cannot both hold one configuration's write key — and that a worker whose backend dies
mid-apply cannot dispatch its next operation.

WARNING: point the DSN only at a disposable, single-purpose database. These tests
terminate backends on it.

Opt in with ``-m integration`` and a reachable ``APPLY_GUARD_TEST_POSTGRESQL_DSN``::

    docker run -d --rm --name ph3-pg16 -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=guardprobe \\
        -p 127.0.0.1:55433:5432 postgres:16-alpine
    APPLY_GUARD_TEST_POSTGRESQL_DSN="host=127.0.0.1 port=55433 user=postgres password=probe dbname=guardprobe" \\
        uv run pytest -m integration tests/integration/test_managed_write_guard_integration.py
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - the worker processes run a fixed interpreter and inline script.
import sys
import textwrap
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

psycopg: Any = pytest.importorskip("psycopg")
pytest.importorskip("prefect")

# Imported after the skip: these modules import psycopg and prefect themselves, so a
# static import here would raise instead of skipping where an extra is absent.
from infrahub_sync.plan.errors import OperationApplyFailedError  # noqa: E402
from infrahub_sync.potenda import Potenda  # noqa: E402
from infrahub_sync.service.apply_guard import (  # noqa: E402
    ApplyGuardOwnershipError,
    advisory_lock_key,
    connect_guard_session,
    hold_apply_guard,
)
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

pytestmark = pytest.mark.integration

_Session = psycopg.Connection[Any]

_DSN_ENVIRONMENT_NAME = "APPLY_GUARD_TEST_POSTGRESQL_DSN"
_ALPHA = "managed-write-alpha"
_BETA = "managed-write-beta"
_RUN_ID = "20260903T1100-7ac31d04"
_CHILD_TIMEOUT_SECONDS = 60

# The managed composition itself, not a re-derivation of it: the child resolves the
# service database setting and opens the guard exactly as a worker stage does.
_WORKER_SCRIPT = textwrap.dedent(
    """
    import sys

    from infrahub_sync.plan.ownership import ProvenWriteOwnership, WriteDispatchTracker
    from infrahub_sync.service.flow import _configuration_write_guard

    configuration_id = sys.argv[1]
    with _configuration_write_guard(configuration_id) as guard:
        ownership = ProvenWriteOwnership(prove=guard.require_ownership, tracker=WriteDispatchTracker())
        print("HELD", flush=True)
        sys.stdin.readline()
        ownership.after_final_operation()
    print("RELEASED", flush=True)
    """
)

_ADVISORY_HOLDERS = """
SELECT pid FROM pg_locks
WHERE locktype = 'advisory' AND granted
  AND classid::bigint = %s AND objid::bigint = %s AND objsubid = 1
"""
_TERMINATE = "SELECT pg_terminate_backend(%s)"


def _dsn_or_skip() -> str:
    """Return a reachable disposable DSN, or skip before any server is contacted."""
    dsn = os.environ.get(_DSN_ENVIRONMENT_NAME)
    if not dsn:
        pytest.skip(f"the managed write-guard integration tests require {_DSN_ENVIRONMENT_NAME}")
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
    """One independent observer session that never holds a managed write key."""
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


def _advisory_holders(admin: _Session, key: int) -> list[int]:
    rows = admin.execute(_ADVISORY_HOLDERS, ((key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF)).fetchall()
    return sorted(row[0] for row in rows)


def _worker(dsn: str, configuration_id: str) -> subprocess.Popen[str]:
    """Start one independent worker process holding the managed write guard."""
    environment = {**os.environ, "INFRAHUB_SYNC_DATABASE_URL": dsn}
    return subprocess.Popen(  # noqa: S603 - fixed interpreter, fixed inline script.
        [sys.executable, "-c", _WORKER_SCRIPT, configuration_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )


def _await_held(worker: subprocess.Popen[str]) -> None:
    assert worker.stdout is not None
    line = worker.stdout.readline().strip()
    if line != "HELD":
        stderr = "" if worker.stderr is None else worker.stderr.read()
        pytest.fail(f"the managed worker never reported its hold: {line!r}; {stderr}")


def _release(worker: subprocess.Popen[str]) -> str:
    assert worker.stdin is not None
    assert worker.stdout is not None
    worker.stdin.write("\n")
    worker.stdin.flush()
    worker.wait(timeout=_CHILD_TIMEOUT_SECONDS)
    return worker.stdout.read().strip()


def _terminate(worker: subprocess.Popen[str]) -> None:
    worker.kill()
    worker.wait(timeout=_CHILD_TIMEOUT_SECONDS)


@pytest.mark.parametrize("stage_configuration", [_ALPHA, _BETA])
def test_two_managed_workers_for_one_configuration_serialize(
    dsn: str, admin: _Session, stage_configuration: str
) -> None:
    """The second worker cannot enter the write path while the first one owns the key."""
    key = advisory_lock_key(stage_configuration)
    first = _worker(dsn, stage_configuration)
    try:
        _await_held(first)
        assert len(_advisory_holders(admin, key)) == 1

        second = _worker(dsn, stage_configuration)
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                assert second.poll() is None or second.returncode != 0, "the blocked worker must not enter"
                if len(_advisory_holders(admin, key)) != 1:
                    pytest.fail("two independent managed workers held one configuration's write key")
                time.sleep(0.1)

            assert _release(first) == "RELEASED"
            _await_held(second)
            assert _release(second) == "RELEASED"
        finally:
            if second.poll() is None:
                _terminate(second)
    finally:
        if first.poll() is None:
            _terminate(first)

    assert _advisory_holders(admin, key) == []


def test_two_configurations_write_concurrently(dsn: str, admin: _Session) -> None:
    """Serialization is per configuration; unrelated configurations must not block."""
    alpha = _worker(dsn, _ALPHA)
    beta = _worker(dsn, _BETA)
    try:
        _await_held(alpha)
        _await_held(beta)

        assert len(_advisory_holders(admin, advisory_lock_key(_ALPHA))) == 1
        assert len(_advisory_holders(admin, advisory_lock_key(_BETA))) == 1

        assert _release(alpha) == "RELEASED"
        assert _release(beta) == "RELEASED"
    finally:
        for worker in (alpha, beta):
            if worker.poll() is None:
                _terminate(worker)


class _RecordingDestination:
    """A destination implementing the planned-write surface that records each dispatch."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.dispatched.append(operation.operation_id)
        return "node"


def _two_operation_plan(tmp_path: Path) -> tuple[Path, list[str]]:
    directory = tmp_path / _RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        operation_record(identity={"name": "first"}),
        operation_record(identity={"name": "second"}),
    ]
    write_artifact(directory, records, run_id=_RUN_ID, source_snapshot=[])
    return directory, [str(record["operation_id"]) for record in records]


def test_a_lost_backend_after_the_first_operation_prevents_the_second(
    dsn: str, admin: _Session, tmp_path: Path
) -> None:
    """A guard whose backend dies mid-apply cannot let the engine keep writing."""
    from infrahub_sync.plan.ownership import (  # pylint: disable=import-outside-toplevel
        ProvenWriteOwnership,
        WriteDispatchTracker,
    )

    directory, operations = _two_operation_plan(tmp_path)
    destination = _RecordingDestination()
    key = advisory_lock_key(_ALPHA)

    def apply_under_a_dying_session() -> None:
        with hold_apply_guard(_ALPHA, connect=lambda: connect_guard_session(dsn)) as guard:
            holders = _advisory_holders(admin, key)
            assert len(holders) == 1

            class _KillAfterFirst:
                def __init__(self) -> None:
                    self.calls = 0

                def __call__(self) -> None:
                    self.calls += 1
                    if self.calls == 2:
                        admin.execute(_TERMINATE, (holders[0],))
                    guard.require_ownership()

            Potenda(
                source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
                destination=destination,  # ty: ignore[invalid-argument-type]
                config=None,  # ty: ignore[invalid-argument-type]
                top_level=["BuiltinTag"],
                run_dir=directory,
                run_id=_RUN_ID,
            ).apply_plan(
                config_version=CONFIG_VERSION,
                ownership=ProvenWriteOwnership(prove=_KillAfterFirst(), tracker=WriteDispatchTracker()),
            )

    with pytest.raises((ApplyGuardOwnershipError, OperationApplyFailedError)):
        apply_under_a_dying_session()

    assert destination.dispatched == [operations[0]]
    assert _advisory_holders(admin, key) == []
