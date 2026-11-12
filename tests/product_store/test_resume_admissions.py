"""A `verify` is admitted on a resumable run, and every other stage keeps its refusal (spec 022).

Research R5 and critique E1: a run that admitted its one write refuses every later stage, so
the `verify` that a resume requires would be refused on an interrupted run. The store admits
one exception: a `verify` on a resumable run, which is a run whose write was interrupted
(`reconciliation_required`), that is not cancelled, and that has no unfinished execution
(data-model.md "State rules").

The same cases run against a real PostgreSQL server when `PRODUCT_STORE_TEST_POSTGRESQL_DSN`
names one; see `tests/product_store/postgresql_isolation`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from infrahub_sync.product_store import (
    ExecutionFinishWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
)
from infrahub_sync.product_store.models import RunApproval
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_SESSION = uuid4().hex[:10]
_CONFLICT_CODE = "run-execution-conflict"
_WORKER_ID = "3d6c1f2a-8b4e-4c7d-9e0f-1a2b3c4d5e6f"
_STARTED_AT = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)

_postgresql_schema = postgresql_schema_fixture("the resume admissions' PostgreSQL parameter")


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    schema = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    return f"{name}-{_SESSION}"


def _receipt(name: str, *, run_id: str, operation: str) -> MutationReceipt:
    return MutationReceipt(
        receipt_id=f"{name}.{run_id}",
        actor="operator@example.com",
        key_digest=sha256(f"{name}:{run_id}".encode()).hexdigest(),
        operation=operation,
        target_run_id=run_id,
        request_fingerprint=sha256(f"{operation}:{name}:{run_id}".encode()).hexdigest(),
        reason="resume admission",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{name}:{run_id}".encode()).hexdigest(),
        created_at=_STARTED_AT,
        updated_at=_STARTED_AT,
    )


def _link(flow_run_id: str, *, purpose: str) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=flow_run_id, purpose=purpose, attempt=1, submitted_at=datetime.now(timezone.utc)
    )


def _refusal_code(receipt: MutationReceipt) -> str | None:
    body = receipt.response_body
    if body is None:
        return None
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _reserve(projection: ProductProjection, run_id: str, name: str, *, operation: str) -> MutationReceipt:
    reserved, _created = projection.reserve_mutation(
        _receipt(name, run_id=run_id, operation=operation), admit_write=operation in {"apply", "sync"}
    )
    return reserved


def _submit(projection: ProductProjection, run_id: str, receipt: MutationReceipt, *, purpose: str) -> str:
    """Append and claim the receipt's execution, as the service and the worker do."""
    flow_run_id = f"flow-{receipt.receipt_id}"
    projection.add_prefect_execution(run_id, _link(flow_run_id, purpose=purpose), receipt_id=receipt.receipt_id)
    projection.complete_mutation(
        receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return flow_run_id


def _interrupted_run(projection: ProductProjection, name: str) -> str:
    """A run whose claimed apply was interrupted: it requires reconciliation and is resumable."""
    run_id = _rid(name)
    projection.create_run(
        ProductRun(
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
    )
    apply_receipt = _reserve(projection, run_id, "m-apply", operation="apply")
    assert apply_receipt.state == "reserved"
    flow_run_id = _submit(projection, run_id, apply_receipt, purpose="apply")
    assert projection.interrupt_execution(run_id, flow_run_id) is True
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.reconciliation_required is True
    return run_id


def _assert_refused(receipt: MutationReceipt) -> None:
    assert receipt.state == "accepted"
    assert receipt.response_status == 409
    assert _refusal_code(receipt) == _CONFLICT_CODE


def test_an_interrupted_run_with_no_unfinished_execution_is_resumable(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "resumable")

    assert projection.run_is_resumable(run_id) is True


def test_verify_is_admitted_on_a_resumable_run_and_can_submit_its_execution(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "verify-admitted")

    verify = _reserve(projection, run_id, "m-verify", operation="verify")

    assert verify.state == "reserved"
    assert verify.response_status is None
    flow_run_id = _submit(projection, run_id, verify, purpose="verify")
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert [link.purpose for link in stored.prefect_executions] == ["apply", "verify"]
    assert stored.prefect_executions[-1].flow_run_id == flow_run_id


def test_a_second_verify_is_refused_while_the_first_is_reserved(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "verify-reserved-twice")
    assert _reserve(projection, run_id, "m-verify-1", operation="verify").state == "reserved"

    _assert_refused(_reserve(projection, run_id, "m-verify-2", operation="verify"))


def test_a_second_verify_is_refused_while_the_first_execution_is_unfinished(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "verify-running")
    _submit(projection, run_id, _reserve(projection, run_id, "m-verify-1", operation="verify"), purpose="verify")

    assert projection.run_is_resumable(run_id) is False
    _assert_refused(_reserve(projection, run_id, "m-verify-2", operation="verify"))


@pytest.mark.parametrize("operation", ["plan", "apply", "sync"])
def test_every_other_stage_is_still_refused_on_a_resumable_run(projection: ProductProjection, operation: str) -> None:
    run_id = _interrupted_run(projection, f"other-stage-{operation}")

    _assert_refused(_reserve(projection, run_id, f"m-later-{operation}", operation=operation))


def test_approve_is_still_refused_on_a_resumable_run(projection: ProductProjection) -> None:
    run_id = _interrupted_run(projection, "approve")
    approve, _created = projection.reserve_mutation(_receipt("m-approve", run_id=run_id, operation="approve"))
    approval = RunApproval.model_validate(
        {
            "approved_by": "reviewer@example.com",
            "approved_at": datetime(2026, 11, 12, 10, tzinfo=timezone(timedelta(hours=1))),
            "plan_checksum": "b" * 64,
            "reason": "CHG-42 reviewed",
        }
    )

    stored = projection.record_approval(run_id, approval, approve, {"run": {"run_id": run_id}})

    assert stored is False
    answered = projection.lookup_mutation(approve.actor, approve.key_digest).value
    assert answered is not None
    assert answered.response_status is not None
    assert answered.response_status >= 400
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert run.approval is None


def test_verify_is_still_refused_on_an_applied_run(projection: ProductProjection) -> None:
    run_id = _rid("applied")
    projection.create_run(
        ProductRun(
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
    )
    flow_run_id = _submit(
        projection, run_id, _reserve(projection, run_id, "m-apply", operation="apply"), purpose="apply"
    )
    finished_at = datetime.now(timezone.utc)
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=finished_at,
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionFinishWriteback(
            phase="applied", outcome="succeeded", finished_at=finished_at, summary={}, results={}
        ),
    )

    assert projection.run_is_resumable(run_id) is False
    _assert_refused(_reserve(projection, run_id, "m-verify", operation="verify"))
