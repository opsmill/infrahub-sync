"""`GET /events`: the typed event list a frontend reads a run's timeline from (spec 017, FR-007 to FR-009)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import (
    AuditEvent,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    ProductProjection,
    RelatedResource,
    local_product_projection,
)
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, RunLogRead, Submission
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from httpx import Response

OWNER_TOKEN = "events-owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
READ = {"Authorization": f"Bearer {OWNER_TOKEN}"}
T0 = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
_WORKER_ID = "0f4b8a8e-5d0c-4f0e-9b6a-3c1d2e4f5a6b"


class _FakeOrchestration:
    """Accept every submission and report each flow run as running."""

    def __init__(self) -> None:
        self.by_key: dict[str, Submission] = {}

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        del parameters
        return self.by_key.setdefault(
            idempotency_key, Submission(flow_run_id=str(uuid5(NAMESPACE_URL, idempotency_key)), state="pending")
        )

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=True)

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:  # noqa: PLR6301
        del flow_run_ids
        return RunLogRead(expired=False)


def _package() -> ConfigurationPackage:
    return ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "events-inventory",
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
            "credentials": {"token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_TOKEN"}},
        }
    )


@pytest.fixture
def events_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, ProductProjection]:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    service = RunService(projection, _FakeOrchestration(), secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), projection


def _event(  # noqa: PLR0913, PLR0917 - one stored event, every field the filters read.
    event_id: str,
    minutes: int,
    event_type: str = "infrahub_sync.run.accepted",
    run_id: str | None = "run-a",
    config_id: str | None = "cfg-a",
    actor: str = "alice",
    branch: str | None = None,
) -> AuditEvent:
    related = [RelatedResource(kind="account", id=actor)]
    if config_id is not None:
        related.extend(
            (RelatedResource(kind="config", id=config_id), RelatedResource(kind="config_version", id=f"{config_id}@1"))
        )
    if branch is not None:
        related.append(RelatedResource(kind="branch", id=branch))
    return AuditEvent(
        event_id=event_id,
        run_id=run_id,
        actor=actor,
        operation="plan",
        reason="review",
        outcome="accepted",
        created_at=T0 + timedelta(minutes=minutes),
        event_type=event_type,
        config_id=config_id,
        registry_version=None if config_id is None else 1,
        branch=branch,
        related=tuple(related),
    )


def _seed(projection: ProductProjection) -> None:
    for event in (
        _event("e-1", 0, branch="main"),
        _event("e-2", 1, "infrahub_sync.run.planned"),
        _event("e-3", 2, run_id="run-b", actor="bob", branch="dev"),
        _event("e-4", 3, "infrahub_sync.config.registered", run_id=None, config_id="cfg-b", actor="bob"),
        _event("e-5", 4, "infrahub_sync.run.applied"),
    ):
        projection.record_audit(event)


def _ids(response: Response) -> list[str]:
    body = response.json()
    return [item["event_id"] for item in body["items"]]


def test_the_event_list_returns_the_contract_shape(events_api: tuple[TestClient, ProductProjection]) -> None:
    client, projection = events_api
    projection.record_audit(_event("e-1", 0, branch="main"))

    response = client.get("/events", headers=READ)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "event_id": "e-1",
                "event_type": "infrahub_sync.run.accepted",
                "occurred_at": "2026-11-06T10:00:00Z",
                "actor": "alice",
                "operation": "plan",
                "outcome": "accepted",
                "reason": "review",
                "primary_resource": {"kind": "run", "id": "run-a"},
                "related": [
                    {"kind": "account", "id": "alice"},
                    {"kind": "config", "id": "cfg-a"},
                    {"kind": "config_version", "id": "cfg-a@1"},
                    {"kind": "branch", "id": "main"},
                ],
            }
        ],
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, ["e-1", "e-2", "e-3", "e-4", "e-5"]),
        ({"run_id": "run-a"}, ["e-1", "e-2", "e-5"]),
        ({"config_id": "cfg-b"}, ["e-4"]),
        ({"event_type": "infrahub_sync.run.planned"}, ["e-2"]),
        ({"event_type": ["infrahub_sync.run.planned", "infrahub_sync.run.applied"]}, ["e-2", "e-5"]),
        ({"related": "account:bob"}, ["e-3", "e-4"]),
        ({"related": ["account:alice", "branch:main"]}, ["e-1"]),
        ({"related": "config_version:cfg-a@1"}, ["e-1", "e-2", "e-3", "e-5"]),
        ({"since": "2026-11-06T10:01:00Z"}, ["e-2", "e-3", "e-4", "e-5"]),
        ({"until": "2026-11-06T10:01:00+00:00"}, ["e-1"]),
        ({"since": "2026-11-06T11:01:00+01:00", "until": "2026-11-06T10:03:00Z"}, ["e-2", "e-3"]),
    ],
)
def test_each_query_parameter_narrows_the_list_in_time_order(
    events_api: tuple[TestClient, ProductProjection], params: dict[str, object], expected: list[str]
) -> None:
    """FR-007: every contract parameter filters; the list is oldest first."""
    client, projection = events_api
    _seed(projection)

    response = client.get("/events", headers=READ, params=params)

    assert response.status_code == 200, response.text
    assert _ids(response) == expected


def test_limit_and_offset_page_through_every_event_once(events_api: tuple[TestClient, ProductProjection]) -> None:
    client, projection = events_api
    _seed(projection)

    pages = [client.get("/events", headers=READ, params={"limit": 2, "offset": offset}).json() for offset in (0, 2, 4)]

    assert [[item["event_id"] for item in page["items"]] for page in pages] == [["e-1", "e-2"], ["e-3", "e-4"], ["e-5"]]
    assert [(page["limit"], page["offset"], page["has_more"]) for page in pages] == [
        (2, 0, True),
        (2, 2, True),
        (2, 4, False),
    ]


def test_events_at_the_same_time_are_ordered_by_event_id(events_api: tuple[TestClient, ProductProjection]) -> None:
    client, projection = events_api
    for event_id in ("e-c", "e-a", "e-b"):
        projection.record_audit(_event(event_id, 0))

    assert _ids(client.get("/events", headers=READ)) == ["e-a", "e-b", "e-c"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": "1.0"},
        {"offset": -1},
        {"offset": "true"},
        {"run_id": "bad id"},
        {"config_id": "-bad"},
        {"event_type": "run.planned"},
        {"related": "account"},
        {"related": "device:edge-01"},
        {"related": "account:"},
        {"since": "2026-11-06T10:00:00"},
        {"until": "yesterday"},
    ],
)
def test_an_invalid_value_is_refused_with_the_existing_error_body(
    events_api: tuple[TestClient, ProductProjection], params: dict[str, object]
) -> None:
    client, _projection = events_api

    refused = client.get("/events", headers=READ, params=params)

    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert (error["code"], error["message"], error["status"]) == (
        "request-invalid",
        "the request does not match the API schema",
        422,
    )


@pytest.mark.parametrize("limit", [1, 200])
def test_the_limit_bounds_match_the_run_list(events_api: tuple[TestClient, ProductProjection], limit: int) -> None:
    client, _projection = events_api

    assert client.get("/events", headers=READ, params={"limit": limit}).json()["limit"] == limit


@pytest.mark.parametrize("authorization", [None, "Bearer not-a-token"])
def test_the_event_list_requires_a_valid_token_and_never_lists_the_refusal(
    events_api: tuple[TestClient, ProductProjection], authorization: str | None
) -> None:
    """FR-008 and FR-009: a refused token gets 401; its stored record never appears in the list."""
    client, projection = events_api
    headers = {} if authorization is None else {"Authorization": authorization}

    refused = client.get("/events", headers=headers)

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "unauthenticated"
    listed = client.get("/events", headers=READ)
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    if authorization is not None:
        # Spec 015 keeps the refusal on record; only the event list leaves it out.
        assert [event.outcome for event in projection.audit_events()] == ["refused-authentication"]


def _plan_document(run_id: str) -> PlanResource:
    return PlanResource(
        run_id=run_id,
        checksum="a" * 64,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001",
                action="create",
                kind="Device",
                identity={"name": "edge-01"},
                tier=0,
                payload={"name": "edge-01"},
                relationships=(),
            ),
        ),
    )


def _finish_open_execution(projection: ProductProjection, run_id: str, stage: str) -> None:
    """Claim the run's open execution and commit its success, as the stage's worker does."""
    run = projection.lookup_run(run_id).value
    assert run is not None
    (link,) = [link for link in run.prefect_executions if link.terminal_at is None]
    assert projection.claim_execution(run_id, link.flow_run_id, worker_id=_WORKER_ID)
    writeback = (
        ExecutionMergeWriteback(results={"plan": {"outcome": "planned"}})
        if stage == "plan"
        else ExecutionFinishWriteback(
            phase="applied", outcome="applied", finished_at=datetime.now(timezone.utc), summary={}, results={}
        )
    )
    service_flow._commit_success(
        projection, run_id, link.flow_run_id, stage=stage, worker_id=_WORKER_ID, writeback=writeback, secrets=()
    )


def test_a_planned_and_applied_run_reads_back_its_timeline(events_api: tuple[TestClient, ProductProjection]) -> None:
    """US1 independent test: plan accepted, planned, apply accepted, applied, each with its resources."""
    client, projection = events_api
    version = projection.create_configuration(_package())
    created = client.post(
        "/runs",
        headers={**READ, "Idempotency-Key": "events-plan"},
        json={
            "operation": "plan",
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "reason": "review inventory",
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run"]["run_id"]
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=_plan_document(run_id).model_dump_json().encode(),
    )
    _finish_open_execution(projection, run_id, "plan")
    applied = client.post(
        f"/runs/{run_id}/apply",
        headers={**READ, "Idempotency-Key": "events-apply"},
        json={"expected_checksum": "a" * 64, "reason": "approved", "confirm_writes": True},
    )
    assert applied.status_code == 202, applied.text
    _finish_open_execution(projection, run_id, "apply")

    response = client.get("/events", headers=READ, params={"run_id": run_id})

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(item["event_type"], item["operation"], item["outcome"]) for item in items] == [
        ("infrahub_sync.run.accepted", "plan", "accepted"),
        ("infrahub_sync.run.planned", "plan", "succeeded"),
        ("infrahub_sync.run.accepted", "apply", "accepted"),
        ("infrahub_sync.run.applied", "apply", "succeeded"),
    ]
    reference = f"{version.config_id}@{version.registry_version}"
    for item in items:
        assert item["actor"] == "owner"
        assert item["primary_resource"] == {"kind": "run", "id": run_id}
        assert item["related"][:3] == [
            {"kind": "account", "id": "owner"},
            {"kind": "config", "id": version.config_id},
            {"kind": "config_version", "id": reference},
        ]
