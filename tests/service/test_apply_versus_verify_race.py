"""One apply and one verify that start together: exactly one of them is admitted.

The rule is the product store's — whichever request wins the run's write reservation is
decided inside one store transaction — but proving it concurrently needs the service path
that reaches that transaction, because the property under test is that the loser is
answered from the store and never reaches Prefect at all. That makes this a service test
by its dependencies, and it lives here rather than beside the sequential admission cases
in `tests/product_store/test_write_admission.py` for that reason: `tests/service` is the
tree the Python 3.10 legs exclude, and the Sync service is Python 3.11+.

Both store profiles still run. SQLite covers the transaction shape; the same case runs
against a real PostgreSQL server when ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names one,
because row-level serialization between two competing reservations is a provider fact.

WARNING: the PostgreSQL parameter's session fixture runs ``DROP SCHEMA public CASCADE``
against whatever database ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` points at, which destroys
every table in that database's ``public`` schema. Point that variable only at a disposable,
single-purpose database — never at a shared or persistent development database.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from threading import Barrier, Lock
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from infrahub_sync.product_store import MutationReceipt, ProductProjection, ProductRun
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from infrahub_sync.service.auth import Principal
from infrahub_sync.service.models import (
    ApplyRunRequest,
    PlanResource,
    PlanSummaryResource,
    VerifyRunRequest,
)
from infrahub_sync.service.orchestration import Observation, Submission
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

_DSN_ENVIRONMENT_NAME = "PRODUCT_STORE_TEST_POSTGRESQL_DSN"
# The PostgreSQL parameter runs against a persistent disposable database, so every run
# ID and receipt identity in this module is namespaced to one test session.
_SESSION = uuid4().hex[:10]
_CONFLICT_CODE = "run-execution-conflict"
_STARTED_AT = datetime(2026, 9, 3, 9, tzinfo=timezone.utc)


def _postgresql_dsn_or_skip() -> str:
    """Return a reachable disposable DSN, or skip before any server is contacted."""
    dsn = os.environ.get(_DSN_ENVIRONMENT_NAME)
    if not dsn:
        pytest.skip(f"the admission race's PostgreSQL parameter requires {_DSN_ENVIRONMENT_NAME}")
    psycopg = pytest.importorskip("psycopg")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as probe:
            probe.execute("SELECT 1")
    except psycopg.Error:
        pytest.skip(f"{_DSN_ENVIRONMENT_NAME} is not reachable")
    return dsn


@pytest.fixture(name="_postgresql_schema", scope="session")
def postgresql_schema_fixture() -> str:
    """Recreate the disposable database's schema once, then return its DSN."""
    dsn = _postgresql_dsn_or_skip()
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    with psycopg.connect(dsn) as admin:
        admin.execute("DROP SCHEMA public CASCADE")
        admin.execute("CREATE SCHEMA public")
        admin.commit()
    return dsn


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    dsn = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    """Namespace one run ID to this test session."""
    return f"{name}-{_SESSION}"


def _run(run_id: str) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference="config-001@1",
        config_id="config-001",
        registry_version=1,
        package_checksum="a" * 64,
        actor="operator@example.com",
        started_at=_STARTED_AT,
        phase="accepted",
    )


def _refusal_code(receipt: MutationReceipt) -> str | None:
    """Return the stored refusal's error code, or None when the receipt is not a refusal."""
    body = receipt.response_body
    if body is None:
        return None
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


class _SubmissionRecorder:
    """A Prefect stand-in that records every submission it is asked to make.

    The point of the barrier below is that the loser never reaches this at all, so what
    matters is the count, not what a submission returns.
    """

    def __init__(self) -> None:
        self.submissions: list[str] = []
        self._lock = Lock()

    async def submit(self, parameters: Mapping[str, Any], *, idempotency_key: str) -> Any:  # noqa: ANN401
        """Record one submission and answer with a stable flow-run identity."""
        _ = parameters
        with self._lock:
            self.submissions.append(idempotency_key)
        return Submission(flow_run_id=str(uuid5(NAMESPACE_URL, idempotency_key)), state="pending")

    async def observe(self, flow_run_id: str) -> Any:  # noqa: ANN401, PLR6301
        """Report the submitted execution as running; nothing here reads it back."""
        _ = flow_run_id
        return Observation(available=True, state="running")


def _publish_reviewed_plan(projection: ProductProjection, run_id: str) -> str:
    """Publish the retained review document both stages read before reserving."""
    checksum = "b" * 64
    document = PlanResource(
        run_id=run_id,
        checksum=checksum,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"update": 1},
            by_kind={},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
        ),
        operations=(),
    )
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=document.model_dump_json().encode(),
    )
    return checksum


_RACE_ROUNDS = 4


def _race_one_round(
    projection: ProductProjection, principal: Principal, run_id: str, round_number: int
) -> tuple[dict[str, int], list[str]]:
    """Start one apply and one verify at a barrier; return their statuses and submissions."""
    checksum = _publish_reviewed_plan(projection, run_id)
    orchestration = _SubmissionRecorder()
    service = RunService(projection, orchestration)  # ty: ignore[invalid-argument-type]
    barrier = Barrier(2)

    def submit(stage: str) -> tuple[str, int]:
        barrier.wait(timeout=30)
        key = f"race-{round_number}-{stage}"
        if stage == "apply":
            request: Any = ApplyRunRequest(
                expected_checksum=checksum, confirm_writes=True, reason="race: apply the reviewed plan"
            )
            status, _body = asyncio.run(service.apply_run(run_id, request, principal, key))
        else:
            request = VerifyRunRequest(reason="race: verify the reviewed plan")
            status, _body = asyncio.run(service.verify_run(run_id, request, principal, key))
        return stage, status

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = dict(pool.map(submit, ("apply", "verify")))
    return outcomes, orchestration.submissions


def test_a_concurrent_apply_and_verify_admit_exactly_one_of_them(projection: ProductProjection) -> None:
    """An apply and a verify that start together decide before either submits anything.

    This is the cross-purpose race, and it is not the apply-versus-apply one: the two
    requests want different stages, so nothing but the run's own arbitration keeps them
    apart. Either may win, and which one wins decides which rule the round exercises — an
    apply that wins is refusing the verify by its admission, a verify that wins is refusing
    the apply by being an unresolved submission. The case asserts the shape of the outcome
    rather than the identity of the winner, and runs several independent rounds so a broken
    rule cannot hide behind one round's scheduling. Each rule is also pinned on its own,
    deterministically, by the sequential cases above.

    What makes it worth running concurrently: the loser is answered from the store, so it
    never reaches Prefect. The recorder counts exactly one submission per round, whichever
    way that round went.
    """
    principal = Principal(actor="operator@example.com", administrator=True)

    for round_number in range(_RACE_ROUNDS):
        run_id = _rid(f"apply-versus-verify-race-{round_number}")
        projection.create_run(_run(run_id))
        outcomes, submissions = _race_one_round(projection, principal, run_id, round_number)

        assert sorted(outcomes.values()) == [202, 409], f"round {round_number}: {outcomes}"
        winner = next(stage for stage, status in outcomes.items() if status == 202)
        loser = next(stage for stage, status in outcomes.items() if status == 409)

        admitted = projection.lookup_mutation(
            principal.actor, sha256(f"race-{round_number}-{winner}".encode()).hexdigest()
        ).value
        assert admitted is not None
        assert submissions == [admitted.prefect_key], (
            f"round {round_number}: {loser} reached Prefect despite losing the reservation: {submissions}"
        )

        refused = projection.lookup_mutation(
            principal.actor, sha256(f"race-{round_number}-{loser}".encode()).hexdigest()
        ).value
        assert refused is not None
        assert refused.response_status == 409
        assert _refusal_code(refused) == _CONFLICT_CODE
        assert refused.flow_run_id is None, "the loser was answered from the store, before any submission"

        stored = projection.lookup_run(run_id).value
        assert stored is not None
        assert len(stored.prefect_executions) == 1, (
            f"round {round_number}: {loser} appended an execution: {stored.prefect_executions}"
        )
        assert stored.prefect_executions[0].purpose == winner
