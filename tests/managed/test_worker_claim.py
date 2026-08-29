"""Managed worker claim ordering and Prefect attribution conformance."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("prefect")

from prefect import __version__ as prefect_version
from prefect.workers.process import ProcessJobConfiguration

from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection

FLOW_ID = "ed4778cb-f2cf-4b1f-a87b-68be37659e93"
WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"


def _projection(tmp_path: Path):
    projection = local_product_projection(tmp_path)
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    projection.create_run(
        ProductRun(
            run_id="run-worker-claim",
            operation="plan",
            configuration_reference="legacy",
            started_at=now,
            phase="accepted",
        )
    )
    projection.add_prefect_execution(
        "run-worker-claim",
        PrefectExecutionLink(flow_run_id=FLOW_ID, purpose="plan", attempt=1, submitted_at=now),
    )
    return projection


def test_prefect_381_process_configuration_preserves_worker_attribution_environment() -> None:
    """Pin the documented server UUID channel used by the process worker."""
    assert prefect_version == "3.8.1"
    source = inspect.getsource(ProcessJobConfiguration)
    assert "env" in source
    assert "PREFECT__WORKER_ID" in inspect.getsource(__import__("prefect.workers.base", fromlist=["BaseWorker"]))


def test_worker_claims_canonical_prefect_execution_before_runtime_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = _projection(tmp_path)
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(managed_flow, "_prefect_flow_run_id", lambda: FLOW_ID)

    managed_flow._claim_current_execution(projection, "run-worker-claim")

    link = projection.lookup_run("run-worker-claim").value.prefect_executions[0]  # type: ignore[union-attr]
    assert link.claiming_worker_id == WORKER_ID


@pytest.mark.parametrize("state", ["missing", "claimed", "abandoned", "interrupted"])
def test_claim_refusal_prevents_registry_and_adapter_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    projection = _projection(tmp_path)
    flow_id = FLOW_ID
    if state == "missing":
        flow_id = "d08f703b-ce73-4269-a7aa-1bfb00f8cc63"
    elif state == "claimed":
        assert projection.claim_execution("run-worker-claim", FLOW_ID, worker_id=WORKER_ID)
    elif state == "abandoned":
        assert projection.abandon_execution("run-worker-claim", FLOW_ID)
    else:
        assert projection.claim_execution("run-worker-claim", FLOW_ID, worker_id=WORKER_ID)
        assert projection.interrupt_execution("run-worker-claim", FLOW_ID)
    constructed: list[object] = []
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(managed_flow, "_prefect_flow_run_id", lambda: flow_id)
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: constructed.append(True))

    with pytest.raises(RuntimeError, match="managed worker execution claim was refused"):
        managed_flow.managed_sync_run.fn("run-worker-claim", "plan")
    assert constructed == []


@pytest.mark.parametrize("value", [None, "not-a-uuid", FLOW_ID.upper()])
def test_worker_identity_rejects_noncanonical_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None
) -> None:
    projection = _projection(tmp_path)
    if value is None:
        monkeypatch.delenv("PREFECT__WORKER_ID", raising=False)
    else:
        monkeypatch.setenv("PREFECT__WORKER_ID", value)
    monkeypatch.setattr(managed_flow, "_prefect_flow_run_id", lambda: FLOW_ID)

    with pytest.raises(RuntimeError, match="managed worker execution identity is invalid"):
        managed_flow._claim_current_execution(projection, "run-worker-claim")
    assert projection.lookup_run("run-worker-claim").value.prefect_executions[0].claimed_at is None  # type: ignore[union-attr]
