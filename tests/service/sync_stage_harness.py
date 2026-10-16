"""A Sync API and stage boundary for `sync` runs over the T001 fixture plan (spec 009).

The run is admitted through the Sync API and its execution is driven through the production
stage boundary; only the extraction, the engine's write and the live schema read are doubles.
Used by the tests of the `sync` stop after a plan that leaves out records and of the run's
failure cause.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from infrahub_sync.client import SyncClient
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

    import pytest

    from infrahub_sync.product_store import ProductProjection

OWNER_TOKEN = "owner-token-canary-0404"  # noqa: S105 - deliberate non-secret boundary canary.
WORKER_ID = "3c5d8e21-7a46-4f0b-9b12-6e8d0f4a2c97"
SCHEMA_FINGERPRINT = "e" * 64


@dataclass
class SyncStageHarness:
    """The Sync API, its store and the doubles one `sync` stage runs against."""

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

    def get(self, path: str) -> Any:  # noqa: ANN401
        return self.client.get(path, headers={"Authorization": f"Bearer {OWNER_TOKEN}"})

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
        """Admit a `sync` run and drive its stage; a stage failure stays recorded, not raised."""
        admitted = self.admit_sync()
        assert admitted.status_code == 202, admitted.text
        run_id = admitted.json()["run"]["run_id"]
        self.flow_run_id[:] = [admitted.json()["orchestration"][-1]["flow_run_id"]]
        # The run record is the subject, not the raise.
        with suppress(Exception):
            service_sync_run.fn(run_id, "sync", *self.binding, confirm_writes=True)
        return run_id

    def apply_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.engine_calls if call.get("operation") == "apply"]

    def sync_client(self) -> SyncClient:
        """A real HTTP client of this Sync API, as the CLI builds it."""
        return SyncClient(
            "http://testserver",
            OWNER_TOKEN,
            transport=self.client._transport,
        )


def build_sync_stage_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SyncStageHarness:
    """Build the harness and install its doubles on the service flow."""
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "sync-destination-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "sync-source-canary")
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    orchestration = _FakeOrchestration()
    client = TestClient(create_app(RunService(projection, orchestration, secrets=resolver.secret_values), resolver))
    harness = SyncStageHarness(
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
