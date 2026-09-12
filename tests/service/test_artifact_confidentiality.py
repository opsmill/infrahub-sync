"""Internal artifacts stay inside the product: no enumeration, no fetch, no schema hint."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import ProductRun, local_product_projection
from infrahub_sync.product_store.bundle import (
    FINAL_CHECKPOINT_ARTIFACT_ID,
    PLAN_CHECKPOINT_ARTIFACT_ID,
    write_bundle,
)
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.models import EMITTED_RESOURCES, EmittedArtifactListResource
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection
    from infrahub_sync.service.orchestration import (
        CancellationResult,
        Observation,
        PoolStatus,
        Submission,
    )

OWNER_TOKEN = "owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
RUN_ID = "run-internal-artifact"
_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
_BUNDLE = write_bundle(
    {
        "plan/operations.jsonl": b'{"op": "create"}\n',
        "plan/manifest.json": b'{"checksum": "' + b"c" * 64 + b'"}',
        "A/devices.parquet": b"PAR1payloadPAR1",
    }
)


class _StubOrchestration:
    """Satisfies the orchestration boundary; the read routes under test never call it."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        raise NotImplementedError

    async def observe(self, flow_run_id: str) -> Observation:
        raise NotImplementedError

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:
        raise NotImplementedError

    async def cancel(self, flow_run_id: str) -> CancellationResult:
        raise NotImplementedError


def _package() -> ConfigurationPackage:
    return ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "registered-inventory",
                "source": {
                    "name": "netbox",
                    "settings": {"url": "https://netbox.example", "token": {"$credential": "token"}},
                },
                "destination": {
                    "name": "infrahub",
                    "settings": {"url": "https://infrahub.example", "token": {"$credential": "token"}},
                },
                "order": [],
                "schema_mapping": [],
                "diffsync_flags": [],
                "incremental": None,
            },
            "credentials": {"token": {"provider": "env", "identifier": "TOKEN"}},
        }
    )


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, ProductProjection, RunService]:
    """One run carrying both a public review artifact and both internal checkpoints."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    version = projection.create_configuration(_package())
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
            phase="accepted",
        )
    )
    projection.publish_artifact(
        RUN_ID,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=b'{"run_id": "run-internal-artifact"}',
    )
    for artifact_id in (PLAN_CHECKPOINT_ARTIFACT_ID, FINAL_CHECKPOINT_ARTIFACT_ID):
        projection.publish_artifact(
            RUN_ID,
            artifact_id=artifact_id,
            kind="run-bundle",
            media_type="application/zip",
            data=_BUNDLE,
            visibility="internal",
        )
    service = RunService(projection, _StubOrchestration(), secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), projection, service


def test_the_artifact_list_enumerates_only_public_artifacts(
    published: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, _projection, _service = published

    response = client.get(f"/runs/{RUN_ID}/artifacts", headers=_HEADERS)

    assert response.status_code == 200, response.text
    assert [item["artifact_id"] for item in response.json()["artifacts"]] == [PLAN_ARTIFACT_ID]


def test_the_run_resource_does_not_carry_internal_references(
    published: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, _projection, _service = published

    response = client.get(f"/runs/{RUN_ID}", headers=_HEADERS)

    assert response.status_code == 200, response.text
    assert [item["artifact_id"] for item in response.json()["run"]["artifact_refs"]] == [PLAN_ARTIFACT_ID]


@pytest.mark.parametrize(
    "artifact_id",
    [PLAN_CHECKPOINT_ARTIFACT_ID, FINAL_CHECKPOINT_ARTIFACT_ID],
)
def test_a_guessed_internal_identifier_is_refused_exactly_like_an_absent_one(
    published: tuple[TestClient, ProductProjection, RunService], artifact_id: str
) -> None:
    """A distinguishable refusal would confirm the identifier the caller guessed."""
    client, _projection, _service = published

    guessed = client.get(f"/runs/{RUN_ID}/artifacts/{artifact_id}", headers=_HEADERS)
    absent = client.get(f"/runs/{RUN_ID}/artifacts/never-published", headers=_HEADERS)

    assert guessed.status_code == 404
    assert guessed.json() == absent.json()


def test_internal_bytes_never_reach_a_public_response(
    published: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, _projection, _service = published
    marker = b"PAR1payloadPAR1"

    for path in (f"/runs/{RUN_ID}", f"/runs/{RUN_ID}/artifacts"):
        response = client.get(path, headers=_HEADERS)
        assert marker not in response.content


def test_no_public_response_exposes_a_visibility_field(
    published: tuple[TestClient, ProductProjection, RunService],
) -> None:
    """Publishing the distinction would tell a caller exactly what to go looking for."""
    client, _projection, _service = published

    for path in (f"/runs/{RUN_ID}", f"/runs/{RUN_ID}/artifacts"):
        response = client.get(path, headers=_HEADERS)
        assert response.status_code == 200, response.text
        assert "visibility" not in response.text


def test_the_openapi_schema_declares_no_visibility_field(
    published: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, _projection, _service = published

    schema = client.get("/openapi.json").json()

    assert "visibility" not in json.dumps(schema)


def test_the_emitted_artifact_list_is_bounded() -> None:
    """The list is built from a store record, so it owns everything it can emit."""
    assert EmittedArtifactListResource.model_config.get("extra") in {"ignore", "forbid"}


def test_the_emitted_artifact_list_is_part_of_the_walked_resource_set() -> None:
    """Otherwise the boundedness walk would keep passing while this surface drifts."""
    assert EmittedArtifactListResource in EMITTED_RESOURCES


def test_the_service_builds_the_artifact_list_through_the_bounded_model(
    published: tuple[TestClient, ProductProjection, RunService],
) -> None:
    """Declaring a bounded twin is not using it; this is the surface that emits."""
    _client, projection, service = published

    assert isinstance(service.list_artifacts(RUN_ID), EmittedArtifactListResource)
    assert projection.lookup_internal_artifact(RUN_ID, PLAN_CHECKPOINT_ARTIFACT_ID).value == _BUNDLE
