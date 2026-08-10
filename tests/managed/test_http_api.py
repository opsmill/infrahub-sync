from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.managed.app import create_app
from infrahub_sync.managed.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.managed.models import PlanResource
from infrahub_sync.managed.orchestration import Observation, Submission
from infrahub_sync.managed.service import PLAN_ARTIFACT_ID, ManagedRunService
from infrahub_sync.product_store import ProductProjection, local_product_projection

OWNER_TOKEN = "owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "other-token-canary-0002"  # noqa: S105 - deliberate non-secret boundary canary.
ADMIN_TOKEN = "admin-token-canary-0003"  # noqa: S105 - deliberate non-secret boundary canary.
RAW_KEY = "client-idempotency-key-canary"
AUTH = {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": RAW_KEY}


class _FakeOrchestration:
    def __init__(self) -> None:
        self.submissions: list[tuple[dict[str, object], str]] = []
        self.by_key: dict[str, Submission] = {}
        self.observations: dict[str, Observation] = {}
        self.fail_after_accept_once = False
        self.cancelled: list[str] = []

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        self.submissions.append((parameters, idempotency_key))
        submission = self.by_key.get(idempotency_key)
        if submission is None:
            flow_run_id = str(uuid5(NAMESPACE_URL, idempotency_key))
            submission = Submission(flow_run_id=flow_run_id, state="pending")
            self.by_key[idempotency_key] = submission
            self.observations[flow_run_id] = Observation(available=True, state="running")
            if self.fail_after_accept_once:
                self.fail_after_accept_once = False
                msg = "response lost after Prefect accepted token-canary-should-redact"
                raise TimeoutError(msg)
        return submission

    async def observe(self, flow_run_id: str) -> Observation:
        return self.observations.get(
            flow_run_id,
            Observation(available=False, state=None, reason="prefect-execution-unavailable"),
        )

    async def cancel(self, flow_run_id: str) -> Observation:
        self.cancelled.append(flow_run_id)
        observed = Observation(available=True, state="cancelling")
        self.observations[flow_run_id] = observed
        return observed


@pytest.fixture
def managed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TestClient, ProductProjection, _FakeOrchestration]:
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "owner": {"token": OWNER_TOKEN},
                "other": {"token": OTHER_TOKEN},
                "admin": {"token": ADMIN_TOKEN, "administrator": True},
            }
        ),
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    orchestration = _FakeOrchestration()
    service = ManagedRunService(projection, orchestration, secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), projection, orchestration


def _create(client: TestClient, *, key: str = RAW_KEY, reason: str = "review inventory changes"):
    return client.post(
        "/runs",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": key},
        json={
            "sync_name": "inventory",
            "operation": "plan",
            "configuration_reference": "sha256:configuration",
            "reason": reason,
        },
    )


def _publish_plan(projection: ProductProjection, run_id: str, *, checksum: str = "a" * 64) -> PlanResource:
    plan = PlanResource(
        run_id=run_id,
        checksum=checksum,
        checksum_ok=True,
        verification_notes=(),
        summary={"by_action": {"create": 1}, "by_kind": {"Device": 1}, "total": 1},
        operations=(
            {
                "operation_id": "op-001",
                "action": "create",
                "kind": "Device",
                "identity": {"name": "edge-01"},
            },
        ),
    )
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )
    return plan


def test_authentication_idempotency_and_secret_boundaries(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration], tmp_path: Path
) -> None:
    client, projection, orchestration = managed

    missing = client.post("/runs", json={})
    invalid = client.post("/runs", headers={"Authorization": "Bearer invalid-token-value"}, json={})
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json()["error"]["code"] == "unauthenticated"

    first = _create(client, reason=f"requested because {OWNER_TOKEN}")
    replay = _create(client, reason=f"requested because {OWNER_TOKEN}")
    conflict = _create(client, reason="different reason")

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency-conflict"
    assert len(orchestration.submissions) == 1
    run_id = first.json()["run"]["run_id"]
    assert projection.lookup_run(run_id).available

    parameters, opaque_key = orchestration.submissions[0]
    assert set(parameters) == {
        "run_id",
        "sync_name",
        "stage",
        "configuration_reference",
        "branch",
        "expected_checksum",
        "confirm_writes",
    }
    assert RAW_KEY not in opaque_key
    boundary = repr((first.json(), parameters, projection.audit_events()))
    assert OWNER_TOKEN not in boundary
    assert RAW_KEY not in boundary
    database_bytes = (tmp_path / "product-records.sqlite3").read_bytes()
    assert OWNER_TOKEN.encode() not in database_bytes
    assert RAW_KEY.encode() not in database_bytes
    assert any(event.outcome == "refused-authentication" for event in projection.audit_events())

    secret_parameter = client.post(
        "/runs",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": "secret-parameter-key"},
        json={
            "sync_name": "inventory",
            "operation": "plan",
            "configuration_reference": OWNER_TOKEN,
            "reason": "reject credential-bearing parameter",
        },
    )
    assert secret_parameter.status_code == 422
    assert secret_parameter.json()["error"]["code"] == "secret-parameter-refused"
    assert len(orchestration.submissions) == 1


def test_lost_submission_response_reuses_one_opaque_prefect_key_and_flow_run(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    orchestration.fail_after_accept_once = True

    uncertain = _create(client, key="timeout-retry-key")
    recovered = _create(client, key="timeout-retry-key")

    assert uncertain.status_code == 503
    assert uncertain.json()["error"]["mutation_id"].startswith("m-")
    assert recovered.status_code == 202
    assert len(orchestration.submissions) == 2
    assert orchestration.submissions[0][1] == orchestration.submissions[1][1]
    run = projection.lookup_run(recovered.json()["run"]["run_id"]).value
    assert run is not None
    assert len(run.prefect_executions) == 1
    assert run.prefect_executions[0].flow_run_id == next(iter(orchestration.by_key.values())).flow_run_id


def test_retained_routes_survive_missing_prefect_detail(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    projection.publish_artifact(
        run_id,
        artifact_id="report",
        kind="result-report",
        media_type="text/plain",
        data=b"retained result",
    )
    projection.finish_run(
        run_id,
        phase="planned",
        outcome="planned",
        summary={"sync_name": "inventory", "total": 1},
        results={"status": "planned"},
    )
    flow_run_id = created.json()["orchestration"][0]["flow_run_id"]
    orchestration.observations[flow_run_id] = Observation(
        available=False,
        state=None,
        reason="prefect-execution-unavailable",
    )

    run_response = client.get(f"/runs/{run_id}", headers={"Authorization": f"Bearer {OTHER_TOKEN}"})
    plan_response = client.get(f"/runs/{run_id}/plan", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    results = client.get(f"/runs/{run_id}/results", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    artifacts = client.get(f"/runs/{run_id}/artifacts", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    artifact = client.get(
        f"/runs/{run_id}/artifacts/report",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )

    assert run_response.status_code == 200
    assert run_response.json()["orchestration"][0] == {
        "flow_run_id": flow_run_id,
        "purpose": "plan",
        "attempt": 1,
        "state": "pending",
        "detail_available": False,
        "unavailable_reason": "prefect-execution-unavailable",
    }
    assert plan_response.json() == plan.model_dump(mode="json")
    assert results.json() == {"run_id": run_id, "results": {"status": "planned"}}
    assert {item["artifact_id"] for item in artifacts.json()["artifacts"]} == {PLAN_ARTIFACT_ID, "report"}
    assert artifact.content == b"retained result"
    assert artifact.headers["digest"].startswith("sha-256=")


def test_owner_admin_authorization_apply_verify_and_cancel(  # noqa: PLR0914 - one end-to-end matrix.
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    other_headers = {"Authorization": f"Bearer {OTHER_TOKEN}", "Idempotency-Key": "other-key"}
    admin_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Idempotency-Key": "admin-key"}

    refused = client.post(f"/runs/{run_id}/verify", headers=other_headers, json={"reason": "not my run"})
    verified = client.post(f"/runs/{run_id}/verify", headers=admin_headers, json={"reason": "admin review"})
    verified_replay = client.post(f"/runs/{run_id}/verify", headers=admin_headers, json={"reason": "admin review"})
    unconfirmed = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "apply-unconfirmed"},
        json={"expected_checksum": plan.checksum, "reason": "approved", "confirm_writes": False},
    )
    stale = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "apply-stale"},
        json={"expected_checksum": "b" * 64, "reason": "approved", "confirm_writes": True},
    )
    applied = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "apply-ok"},
        json={"expected_checksum": plan.checksum, "reason": "approved", "confirm_writes": True},
    )
    applied_replay = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "apply-ok"},
        json={"expected_checksum": plan.checksum, "reason": "approved", "confirm_writes": True},
    )
    cancelled = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-ok"},
        json={"reason": "operator requested stop"},
    )
    cancelled_replay = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-ok"},
        json={"reason": "operator requested stop"},
    )
    applied_flow_run_id = applied.json()["orchestration"][-1]["flow_run_id"]
    orchestration.observations[applied_flow_run_id] = Observation(available=True, state="completed")
    terminal_cancel = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-terminal"},
        json={"reason": "too late to stop"},
    )

    assert refused.status_code == 403
    assert verified.status_code == 202
    assert verified_replay.json() == verified.json()
    assert unconfirmed.status_code == 409
    assert stale.status_code == 409
    assert applied.status_code == 202
    assert applied_replay.json() == applied.json()
    assert cancelled.status_code == 202
    assert cancelled_replay.json() == cancelled.json()
    assert terminal_cancel.status_code == 409
    assert terminal_cancel.json()["error"]["code"] == "execution-terminal"
    assert len(orchestration.submissions) == 3
    assert [parameters["stage"] for parameters, _ in orchestration.submissions] == ["plan", "verify", "apply"]
    assert orchestration.cancelled == [applied.json()["orchestration"][-1]["flow_run_id"]]
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert [(link.purpose, link.attempt) for link in run.prefect_executions] == [
        ("plan", 1),
        ("verify", 1),
        ("apply", 1),
    ]
    outcomes = {event.outcome for event in projection.audit_events(run_id)}
    assert {"refused-authorization", "refused-confirmation", "refused-checksum", "accepted"} <= outcomes
    assert "refused-terminal" in outcomes
    assert set(run.audit_links) == {event.event_id for event in projection.audit_events(run_id)}


@pytest.mark.parametrize(
    ("actor", "token", "expected_status"),
    [
        ("owner", OWNER_TOKEN, 202),
        ("admin", ADMIN_TOKEN, 202),
        ("other", OTHER_TOKEN, 403),
    ],
)
@pytest.mark.parametrize("operation", ["verify", "apply", "cancel"])
def test_owner_and_administrator_mutation_matrix(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
    actor: str,
    token: str,
    expected_status: int,
    operation: str,
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": f"{actor}-{operation}"}
    bodies = {
        "verify": {"reason": f"{actor} verification"},
        "apply": {
            "expected_checksum": plan.checksum,
            "reason": f"{actor} apply",
            "confirm_writes": True,
        },
        "cancel": {"reason": f"{actor} cancellation"},
    }

    response = client.post(f"/runs/{run_id}/{operation}", headers=headers, json=bodies[operation])

    assert response.status_code == expected_status
    events = projection.audit_events(run_id)
    allowed = expected_status == 202
    expected_outcome = "accepted" if allowed else "refused-authorization"
    assert any(
        event.actor == actor and event.operation == operation and event.outcome == expected_outcome for event in events
    )
    if allowed and operation in {"verify", "apply"}:
        assert len(orchestration.submissions) == 2
        assert orchestration.submissions[-1][0]["stage"] == operation
    elif allowed:
        assert len(orchestration.cancelled) == 1
    else:
        assert len(orchestration.submissions) == 1
        assert not orchestration.cancelled


def test_stable_not_found_expired_unavailable_and_unhandled_errors(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projection, orchestration = managed
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    missing = client.get("/runs/missing", headers=headers)

    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    _publish_plan(projection, run_id)
    projection.publish_artifact(
        run_id,
        artifact_id="unavailable",
        kind="result-report",
        media_type="text/plain",
        data=b"result",
    )
    run = projection.lookup_run(run_id).value
    assert run is not None
    unavailable = next(item for item in run.artifact_refs if item.artifact_id == "unavailable")
    (tmp_path / "artifacts" / unavailable.object_key).unlink()
    unavailable_response = client.get(f"/runs/{run_id}/artifacts/unavailable", headers=headers)

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with sqlite3.connect(tmp_path / "product-records.sqlite3") as connection:
        connection.execute(
            "UPDATE artifact_refs SET expires_at = ? WHERE run_id = ? AND artifact_id = ?",
            (expired_at.isoformat(), run_id, PLAN_ARTIFACT_ID),
        )
    expired = client.get(f"/runs/{run_id}/plan", headers=headers)

    async def broken_observation(_flow_run_id: str) -> Observation:  # noqa: RUF029
        msg = "internal-token-canary"
        raise RuntimeError(msg)

    monkeypatch.setattr(orchestration, "observe", broken_observation)
    with TestClient(client.app, raise_server_exceptions=False) as error_client:
        unhandled = error_client.get(f"/runs/{run_id}", headers=headers)

    assert missing.status_code == 404
    assert unavailable_response.status_code == 503
    assert expired.status_code == 410
    assert unhandled.status_code == 503
    for response in (missing, unavailable_response, expired, unhandled):
        assert set(response.json()) == {"error"}
        assert "token-canary" not in response.text


def test_confirmation_schema_errors_and_openapi_contract(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, _projection, orchestration = managed
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": "sync-key"}
    unconfirmed = client.post(
        "/runs",
        headers=headers,
        json={
            "sync_name": "inventory",
            "operation": "sync",
            "configuration_reference": "sha256:configuration",
            "reason": "run now",
        },
    )
    missing_key = client.post(
        "/runs",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        json={
            "sync_name": "inventory",
            "configuration_reference": "sha256:configuration",
            "reason": "plan now",
        },
    )
    invalid = client.post("/runs", headers=headers, json={"sync_name": "inventory"})
    invalid_operation = client.post(
        "/runs",
        headers=headers,
        json={
            "sync_name": "inventory",
            "operation": "delete",
            "configuration_reference": "sha256:configuration",
            "reason": "unsupported operation",
        },
    )

    assert unconfirmed.status_code == 409
    assert missing_key.status_code == 422
    assert invalid.status_code == 422
    assert invalid_operation.status_code == 422
    assert all(set(response.json()) == {"error"} for response in (unconfirmed, missing_key, invalid, invalid_operation))
    assert not orchestration.submissions

    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/plan",
        "/runs/{run_id}/results",
        "/runs/{run_id}/artifacts",
        "/runs/{run_id}/artifacts/{artifact_id}",
        "/runs/{run_id}/verify",
        "/runs/{run_id}/apply",
        "/runs/{run_id}/cancel",
    }
    for route in paths.values():
        for operation in route.values():
            assert "401" in operation["responses"]
            assert "422" in operation["responses"]
