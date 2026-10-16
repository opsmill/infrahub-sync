"""A `sync` run whose plan leaves out records stops after the plan (spec 009 T059).

The run ends with phase `planned`, outcome `left-out-review-required` and a stored failure
of type `left_out_review_required` at stage `sync`. It wrote nothing and holds no write
admission, so the team applies it with `POST /runs/{run_id}/apply` and the confirmed count.
No request field skips the stop. A `sync` whose plan leaves out nothing applies as before.

The run is admitted through the Sync API and its execution is driven through the production
stage boundary; only the extraction, the engine's write and the live schema read are doubles.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.execution import RunResult
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.service import RunService
from tests.configuration.validation_packages import package
from tests.plan.problem_fixtures import Variant
from tests.plan.problem_plan import plan_fixture
from tests.service.execution_fixtures import bind_granting_guard, stage_root, write_applied_sidecar
from tests.service.test_http_api import _FakeOrchestration

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

OWNER_TOKEN = "owner-token-canary-0404"  # noqa: S105 - deliberate non-secret boundary canary.
WORKER_ID = "3c5d8e21-7a46-4f0b-9b12-6e8d0f4a2c97"
SCHEMA_FINGERPRINT = "e" * 64


@dataclass
class _Harness:
    projection: ProductProjection
    client: TestClient
    orchestration: _FakeOrchestration
    binding: tuple[str, int, str]
    variant: list[Variant]
    engine_calls: list[dict[str, Any]] = field(default_factory=list)
    flow_run_id: list[str] = field(default_factory=list)

    def post(self, path: str, key: str, body: dict[str, Any]) -> Any:  # noqa: ANN401
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": key}
        return self.client.post(path, headers=headers, json=body)

    def admit_sync(self, key: str = "sync-key", **extra: Any) -> Any:  # noqa: ANN401
        return self.post(
            "/runs",
            key,
            {
                "operation": "sync",
                "config_id": self.binding[0],
                "registry_version": self.binding[1],
                "confirm_writes": True,
                "reason": "synchronize the inventory",
                **extra,
            },
        )

    def run_sync(self) -> str:
        admitted = self.admit_sync()
        assert admitted.status_code == 202, admitted.text
        run_id = admitted.json()["run"]["run_id"]
        self.flow_run_id[:] = [admitted.json()["orchestration"][-1]["flow_run_id"]]
        service_sync_run.fn(run_id, "sync", *self.binding, confirm_writes=True)
        return run_id

    def apply_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.engine_calls if call.get("operation") == "apply"]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Harness:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "sync-destination-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "sync-source-canary")
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    orchestration = _FakeOrchestration()
    client = TestClient(create_app(RunService(projection, orchestration, secrets=resolver.secret_values), resolver))
    harness = _Harness(
        projection=projection,
        client=client,
        orchestration=orchestration,
        binding=(registered.config_id, registered.registry_version, registered.package_checksum),
        variant=[Variant.ALL],
    )

    def plan(instance: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        """Plan the T001 fixture into this stage's scratch, as the real extraction would."""
        directory = stage_root(kwargs) / instance.name / kwargs["run_id"]
        planned = plan_fixture(directory, harness.variant[0])
        loaded = load_plan_artifact(planned.run_dir)
        return SavedPlan(
            manifest=loaded.manifest,
            operations=loaded.operations,
            checksum_ok=True,
            verification_notes=[],
            report=loaded.report,
        )

    def execute_run(instance: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        harness.engine_calls.append(kwargs)
        base = stage_root(kwargs)
        if kwargs.get("operation") == "verify":
            loaded = load_plan_artifact(base / instance.name / kwargs["run_id"])
            return SavedPlan(
                manifest=loaded.manifest, operations=loaded.operations, checksum_ok=True, verification_notes=[]
            )
        ownership = kwargs["ownership"]
        ownership.before_operation()
        ownership.after_final_operation()
        write_applied_sidecar(base / instance.name / kwargs["run_id"], mode="sync")
        return RunResult(
            sync_name=instance.name,
            operation="apply",
            run_id=kwargs["run_id"],
            status="applied",
            changed=True,
            summary={"create": 1, "update": 0, "delete": 0},
            artifact_path=str(base / instance.name / kwargs["run_id"]),
        )

    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: harness.flow_run_id[0])
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(service_flow, "_plan", plan)
    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    # The fixture plan carries no registered binding: the binding check is another suite's subject.
    monkeypatch.setattr(service_flow, "_verify_registered_apply", lambda **kwargs: _verified_manifest(kwargs))
    monkeypatch.setattr(service_flow, "_require_planned_schema", lambda **_kwargs: None)
    monkeypatch.setattr(
        service_flow,
        "build_runtime_model_plan",
        lambda **_kwargs: RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
        ),
    )
    return harness


def _verified_manifest(kwargs: dict[str, Any]) -> Any:  # noqa: ANN401
    instance = kwargs["instance"]
    return load_plan_artifact(kwargs["base_directory"] / instance.name / kwargs["run_id"]).manifest


def _left_out(harness: _Harness, run_id: str) -> int:
    plan = harness.client.get(f"/runs/{run_id}/plan", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    assert plan.status_code == 200, plan.text
    return plan.json()["summary"]["left_out"]


def test_a_sync_whose_plan_leaves_out_records_stops_after_the_plan(harness: _Harness) -> None:
    run_id = harness.run_sync()

    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert (run.phase, run.outcome) == ("planned", "left-out-review-required")
    failure = run.results["failure"]
    assert failure["failure_type"] == "left_out_review_required"
    assert failure["stage"] == "sync"
    assert run.reconciliation_required is False
    assert harness.apply_calls() == [], "The stop comes before any write."
    assert _left_out(harness, run_id) > 0


def test_the_stopped_run_is_applied_with_the_right_count(harness: _Harness) -> None:
    run_id = harness.run_sync()
    left_out = _left_out(harness, run_id)
    checksum = harness.client.get(f"/runs/{run_id}/plan", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).json()[
        "checksum"
    ]

    refused = harness.post(f"/runs/{run_id}/apply", "apply-no-count", _apply_body(checksum))
    accepted = harness.post(
        f"/runs/{run_id}/apply", "apply-count", _apply_body(checksum, confirmed_left_out_count=left_out)
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "left-out-confirmation-required"
    assert accepted.status_code == 202, accepted.text
    harness.flow_run_id[:] = [accepted.json()["orchestration"][-1]["flow_run_id"]]
    service_sync_run.fn(
        run_id,
        "apply",
        *harness.binding,
        expected_checksum=checksum,
        confirm_writes=True,
        confirmed_left_out_count=left_out,
    )
    (call,) = harness.apply_calls()
    assert call["confirmed_left_out_count"] == left_out
    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert run.summary["left_out"] == left_out
    assert run.summary["operations_written"] == 0, "The engine double reports no applied record."


def _apply_body(checksum: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"expected_checksum": checksum, "confirm_writes": True, "reason": "apply after review", **extra}


@pytest.mark.parametrize(
    "extra",
    [{"confirmed_left_out_count": 12}, {"skip_left_out_review": True}, {"allow_left_out": True}],
    ids=["count", "skip", "allow"],
)
def test_no_request_field_skips_the_stop(harness: _Harness, extra: dict[str, Any]) -> None:
    refused = harness.admit_sync(key=f"sync-{next(iter(extra))}", **extra)

    assert refused.status_code == 422
    assert harness.orchestration.submissions == []


def test_a_sync_that_leaves_out_nothing_applies_as_today(harness: _Harness) -> None:
    harness.variant[0] = Variant.NO_PROBLEMS

    run_id = harness.run_sync()

    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert "failure" not in run.results
    assert len(harness.apply_calls()) == 1
    assert run.summary["left_out"] == 0


def test_a_stopped_sync_records_its_finish_time(harness: _Harness) -> None:
    run_id = harness.run_sync()

    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert run.finished_at is not None
    assert run.finished_at <= datetime.now(timezone.utc)
