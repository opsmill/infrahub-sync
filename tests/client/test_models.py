"""Neutral HTTP contract and server projection tests."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from infrahub_sync.client.models import OrchestrationSummary, PublicRunResource
from infrahub_sync.managed.models import public_run_resource
from infrahub_sync.product_store.models import PrefectExecutionLink, ProductRun


def test_client_package_imports_no_product_or_service_module() -> None:
    imports: set[str] = set()
    for path in Path("infrahub_sync/client").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")

    assert not {name for name in imports if name.startswith("infrahub_sync.product_store")}
    assert not {name for name in imports if name.startswith("infrahub_sync.managed")}
    assert not {name for name in imports if name.startswith("infrahub_sync.adapters")}
    assert not {name for name in imports if name.startswith("infrahub_sync.execution")}
    assert not {
        name for name in imports if name.partition(".")[0] in {"boto3", "fastapi", "prefect", "psycopg", "uvicorn"}
    }


@pytest.mark.parametrize(
    "field",
    [
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
    ],
)
def test_orchestration_timestamps_require_a_timezone(field: str) -> None:
    payload: dict[str, object] = {
        "flow_run_id": "flow-1",
        "purpose": "plan",
        "attempt": 1,
        "state": "pending",
        "detail_available": True,
        "unavailable_reason": None,
        "submitted_at": None,
        "claimed_at": None,
        "stalled_at": None,
        "cancellation_requested_at": None,
        "cancellation_recovery_deadline_at": None,
        "cancellation_acknowledged_at": None,
        "terminal_at": None,
        "terminal_state": None,
        "terminal_outcome": None,
    }
    payload[field] = "2026-08-30T12:00:00"
    if field == "terminal_at":
        payload["terminal_state"] = "completed"
        payload["terminal_outcome"] = "succeeded"

    with pytest.raises(ValidationError, match="execution timestamps must include a timezone"):
        OrchestrationSummary.model_validate(payload)


def test_server_projects_store_run_into_standalone_resource() -> None:
    now = datetime.now(timezone.utc)
    stored = ProductRun(
        run_id="run-1",
        operation="plan",
        configuration_reference="cfg@1",
        config_id="cfg",
        registry_version=1,
        package_checksum="a" * 64,
        started_at=now,
        phase="accepted",
        prefect_executions=(
            PrefectExecutionLink(
                flow_run_id="flow-1",
                purpose="plan",
                attempt=1,
                submitted_at=now,
                claiming_worker_id=None,
            ),
        ),
    )

    projected = public_run_resource(stored)

    assert isinstance(projected, PublicRunResource)
    assert not issubclass(PublicRunResource, ProductRun)
    assert projected.run_id == stored.run_id
    assert projected.prefect_executions[0].model_dump() == {
        "flow_run_id": "flow-1",
        "deployment_id": None,
        "purpose": "plan",
        "attempt": 1,
        "last_observed_state": None,
        "last_observed_at": None,
    }
