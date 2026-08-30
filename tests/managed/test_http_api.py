from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.managed.app import create_app
from infrahub_sync.managed.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.managed.models import CreateRunRequest, PlanResource
from infrahub_sync.managed.orchestration import Observation, Submission
from infrahub_sync.managed.service import PLAN_ARTIFACT_ID, ManagedAPIError, ManagedRunService
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductProjection, ProductRun, local_product_projection

OWNER_TOKEN = "owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "other-token-canary-0002"  # noqa: S105 - deliberate non-secret boundary canary.
ADMIN_TOKEN = "admin-token-canary-0003"  # noqa: S105 - deliberate non-secret boundary canary.
RAW_KEY = "client-idempotency-key-canary"
AUTH = {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": RAW_KEY}


def _registered_package() -> ConfigurationPackage:
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


class _FakeOrchestration:
    def __init__(self) -> None:
        self.submissions: list[tuple[dict[str, object], str]] = []
        self.by_key: dict[str, Submission] = {}
        self.observations: dict[str, Observation] = {}
        self.fail_after_accept_once = False
        self.cancel_failure = False
        self.cancel_exception = False
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
        if self.cancel_exception:
            msg = "Prefect cancellation race exposed token-canary"
            raise RuntimeError(msg)
        if self.cancel_failure:
            return Observation(available=False, state="running", reason="prefect-cancellation-unavailable")
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
    version = projection.create_configuration(_registered_package())
    service = ManagedRunService(projection, orchestration, secrets=resolver.secret_values)
    client = TestClient(create_app(service, resolver))
    client.app.state.run_binding = version
    return client, projection, orchestration


def _create(
    client: TestClient,
    *,
    key: str = RAW_KEY,
    reason: str = "review inventory changes",
    authorization: str = f"Bearer {OWNER_TOKEN}",
):
    version = client.app.state.run_binding
    return client.post(
        "/runs",
        headers={"Authorization": authorization, "Idempotency-Key": key},
        json={
            "operation": "plan",
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "reason": reason,
        },
    )


def test_admission_reads_registered_binding_before_allocating_run(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    """Admission takes package identity and display name from the immutable registry row."""
    _client, projection, orchestration = managed
    version = projection.create_configuration(_registered_package())
    request = CreateRunRequest(
        operation="plan",
        config_id=version.config_id,
        registry_version=version.registry_version,
        reason="plan registered package",
    )
    service = ManagedRunService(projection, orchestration)
    principal = EnvironmentPrincipalResolver.from_environment().resolve(OWNER_TOKEN)
    assert principal is not None

    _status, body = asyncio.run(service.create_run(request, principal, "registered-key"))
    run = projection.lookup_run(body["run"]["run_id"]).value
    assert run is not None
    assert run.configuration_binding == (version.config_id, version.registry_version, version.package_checksum)
    assert run.summary["sync_name"] == "registered-inventory"
    assert set(orchestration.submissions[0][0]) == {
        "run_id",
        "stage",
        "config_id",
        "registry_version",
        "package_checksum",
        "branch",
        "expected_checksum",
        "confirm_writes",
    }

    missing = CreateRunRequest(
        operation="plan", config_id="missing-config", registry_version=1, reason="refuse before allocation"
    )
    with pytest.raises(ManagedAPIError, match="requested configuration version does not exist"):
        asyncio.run(service.create_run(missing, principal, "missing-key"))
    assert len(orchestration.submissions) == 1


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


def test_worker_published_plan_is_retrievable_through_an_independent_api_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Worker publication crosses the shared backend and the API artifact boundary."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    api_projection = local_product_projection(tmp_path.resolve())
    worker_projection = local_product_projection(tmp_path.resolve())
    version = api_projection.create_configuration(_registered_package())
    run_id = "run-worker-api-artifact"
    api_projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
            phase="accepted",
        )
    )
    saved = SavedPlan(
        manifest=PlanManifest(
            format_version=2,
            run_id=run_id,
            created_at="2026-08-29T12:00:00+00:00",
            config_version=f"{version.config_id}@{version.registry_version}",
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )

    managed_flow._publish_plan(worker_projection, run_id, saved, ())

    expected_plan = managed_flow._review_document(run_id, saved)
    expected_bytes = expected_plan.model_dump_json().encode()
    service = ManagedRunService(api_projection, _FakeOrchestration(), secrets=resolver.secret_values)
    client = TestClient(create_app(service, resolver))
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    plan_response = client.get(f"/runs/{run_id}/plan", headers=headers)
    artifact_response = client.get(f"/runs/{run_id}/artifacts/{PLAN_ARTIFACT_ID}", headers=headers)

    assert plan_response.status_code == 200
    assert plan_response.json() == expected_plan.model_dump(mode="json")
    assert artifact_response.status_code == 200
    assert artifact_response.content == expected_bytes
    assert artifact_response.headers["digest"] == f"sha-256={sha256(expected_bytes).hexdigest()}"


def test_authentication_idempotency_and_secret_boundaries(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration], tmp_path: Path
) -> None:
    client, projection, orchestration = managed

    missing = client.post("/runs", json={})
    malformed = client.post("/runs", headers={"Authorization": "Basic not-a-bearer-token"}, json={})
    invalid = client.post("/runs", headers={"Authorization": "Bearer invalid-token-value"}, json={})
    for response in (
        missing,
        malformed,
        client.post("/runs", headers={"Authorization": "Bearer    "}, json={}),
        invalid,
    ):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"
        assert response.headers["WWW-Authenticate"] == "Bearer"

    first = _create(client, reason=f"requested because {OWNER_TOKEN}", authorization=f"Bearer    {OWNER_TOKEN}")
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
        "stage",
        "config_id",
        "registry_version",
        "package_checksum",
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
            "operation": "plan",
            "config_id": OWNER_TOKEN,
            "registry_version": 1,
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


def test_reserved_apply_retry_replays_after_the_plan_artifact_expires(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
    tmp_path: Path,
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    headers = {**AUTH, "Idempotency-Key": "apply-expired-plan-retry"}
    body = {
        "expected_checksum": plan.checksum,
        "reason": "approved before retention elapsed",
        "confirm_writes": True,
    }
    orchestration.fail_after_accept_once = True

    uncertain = client.post(f"/runs/{run_id}/apply", headers=headers, json=body)
    with sqlite3.connect(tmp_path / "product-records.sqlite3") as connection:
        connection.execute(
            "UPDATE artifact_refs SET expires_at = ? WHERE run_id = ? AND artifact_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), run_id, PLAN_ARTIFACT_ID),
        )
    recovered = client.post(f"/runs/{run_id}/apply", headers=headers, json=body)

    assert uncertain.status_code == 503
    assert recovered.status_code == 202
    assert len(orchestration.submissions) == 3
    assert orchestration.submissions[-2][1] == orchestration.submissions[-1][1]


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


def test_cancellation_transport_failure_remains_a_typed_mutation_error(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, _projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    orchestration.cancel_failure = True

    response = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-transport-failure"},
        json={"reason": "stop after transport failure"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "orchestration-unavailable"
    assert response.json()["error"]["mutation_id"].startswith("m-")


def test_cancellation_exception_remains_typed_secret_safe_and_audited(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    orchestration.cancel_exception = True

    response = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-exception"},
        json={"reason": "stop after cancellation race"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "orchestration-unavailable"
    assert response.json()["error"]["mutation_id"].startswith("m-")
    assert "token-canary" not in response.text
    assert any(event.operation == "cancel" and event.outcome == "unavailable" for event in projection.audit_events())


@pytest.mark.parametrize(
    "newest_observation",
    [
        Observation(available=True, state="completed"),
        Observation(available=False, state=None, reason="prefect-execution-unavailable"),
    ],
)
def test_cancel_scans_past_newer_non_active_links(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
    newest_observation: Observation,
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    applied = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "cancel-active-apply"},
        json={"expected_checksum": plan.checksum, "reason": "approved", "confirm_writes": True},
    )
    verified = client.post(
        f"/runs/{run_id}/verify",
        headers={**AUTH, "Idempotency-Key": "newer-finished-verify"},
        json={"reason": "later read-only check"},
    )
    apply_flow_run_id = applied.json()["orchestration"][-1]["flow_run_id"]
    verify_flow_run_id = verified.json()["orchestration"][-1]["flow_run_id"]
    orchestration.observations[apply_flow_run_id] = Observation(available=True, state="running")
    orchestration.observations[verify_flow_run_id] = newest_observation

    cancelled = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": f"scan-cancel-{newest_observation.reason or newest_observation.state}"},
        json={"reason": "stop the active write"},
    )

    assert cancelled.status_code == 202
    assert orchestration.cancelled == [apply_flow_run_id]


def test_cancel_treats_expired_and_terminal_links_as_non_cancellable(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    applied = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "apply-before-expiry"},
        json={"expected_checksum": plan.checksum, "reason": "approved", "confirm_writes": True},
    )
    plan_flow_run_id = created.json()["orchestration"][-1]["flow_run_id"]
    apply_flow_run_id = applied.json()["orchestration"][-1]["flow_run_id"]
    orchestration.observations.pop(plan_flow_run_id)
    orchestration.observations[apply_flow_run_id] = Observation(available=True, state="completed")

    cancelled = client.post(
        f"/runs/{run_id}/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-expired-and-terminal"},
        json={"reason": "stop if still active"},
    )

    assert cancelled.status_code == 409
    assert cancelled.json()["error"]["code"] == "execution-terminal"
    assert orchestration.cancelled == []


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
    for summary in applied.json()["orchestration"]:
        orchestration.observations[summary["flow_run_id"]] = Observation(available=True, state="completed")
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


def test_concurrent_and_post_completion_distinct_apply_keys_are_refused(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    created = _create(client)
    run_id = created.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    ready = Barrier(2)

    def apply(position: int):
        ready.wait()
        return client.post(
            f"/runs/{run_id}/apply",
            headers={**AUTH, "Idempotency-Key": f"concurrent-apply-{position}"},
            json={
                "expected_checksum": plan.checksum,
                "confirm_writes": True,
                "reason": f"concurrent approval {position}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(apply, range(2)))

    accepted = next(response for response in responses if response.status_code == 202)
    refused = next(response for response in responses if response.status_code == 409)
    accepted_position = next(position for position, response in enumerate(responses) if response.status_code == 202)
    assert refused.json()["error"]["code"] == "apply-already-admitted"
    assert len(orchestration.submissions) == 2

    projection.finish_run(
        run_id,
        phase="applied",
        outcome="applied",
        summary={"sync_name": "inventory", "create": 1, "update": 0, "delete": 0},
        results={"outcome": "applied"},
    )
    replay = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": f"concurrent-apply-{accepted_position}"},
        json={
            "expected_checksum": plan.checksum,
            "confirm_writes": True,
            "reason": f"concurrent approval {accepted_position}",
        },
    )
    post_completion = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "post-completion-apply"},
        json={"expected_checksum": plan.checksum, "confirm_writes": True, "reason": "apply again"},
    )

    assert replay.status_code == 202
    assert replay.json() == accepted.json()
    assert post_completion.status_code == 409
    assert post_completion.json()["error"]["code"] == "apply-already-admitted"
    assert len(orchestration.submissions) == 2
    refusals = [event for event in projection.audit_events(run_id) if event.outcome == "refused-apply-admission"]
    assert len(refusals) == 2


def test_confirmed_sync_reserves_its_write_admission_and_replays(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, projection, orchestration = managed
    version = client.app.state.run_binding
    body = {
        "operation": "sync",
        "config_id": version.config_id,
        "registry_version": version.registry_version,
        "confirm_writes": True,
        "reason": "approved composed sync",
    }
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": "confirmed-sync"}

    accepted = client.post("/runs", headers=headers, json=body)
    replay = client.post("/runs", headers=headers, json=body)
    run_id = accepted.json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    later_apply = client.post(
        f"/runs/{run_id}/apply",
        headers={**AUTH, "Idempotency-Key": "apply-after-sync"},
        json={"expected_checksum": plan.checksum, "confirm_writes": True, "reason": "duplicate write"},
    )

    assert accepted.status_code == 202
    assert replay.json() == accepted.json()
    assert later_apply.status_code == 409
    assert later_apply.json()["error"]["code"] == "apply-already-admitted"
    assert [parameters["stage"] for parameters, _ in orchestration.submissions] == ["sync"]


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


def test_stable_not_found_expired_unavailable_and_degraded_prefect_reads(
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
    missing_artifact = client.get(f"/runs/{run_id}/artifacts/missing", headers=headers)

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with sqlite3.connect(tmp_path / "product-records.sqlite3") as connection:
        connection.execute(
            "UPDATE artifact_refs SET expires_at = ? WHERE run_id = ? AND artifact_id = ?",
            (expired_at.isoformat(), run_id, PLAN_ARTIFACT_ID),
        )
    expired = client.get(f"/runs/{run_id}/plan", headers=headers)

    async def unavailable_observation(_flow_run_id: str) -> Observation:  # noqa: RUF029
        return Observation(available=False, state=None, reason="prefect-read-unavailable")

    monkeypatch.setattr(orchestration, "observe", unavailable_observation)
    degraded = client.get(f"/runs/{run_id}", headers=headers)

    assert missing.status_code == 404
    assert unavailable_response.status_code == 503
    assert missing_artifact.status_code == 404
    assert missing_artifact.json()["error"]["code"] == "artifact-not-found"
    assert expired.status_code == 410
    assert degraded.status_code == 200
    assert degraded.json()["orchestration"][0]["detail_available"] is False
    assert degraded.json()["orchestration"][0]["unavailable_reason"] == "prefect-read-unavailable"
    for response in (missing, missing_artifact, unavailable_response, expired):
        assert set(response.json()) == {"error"}
        assert "token-canary" not in response.text


def test_generic_service_failure_is_contained_before_server_logging(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, projection, _orchestration = managed
    canary = "unexpected-storage-token-canary"

    def fail_lookup(_run_id: str):
        raise RuntimeError(canary)

    monkeypatch.setattr(projection, "lookup_run", fail_lookup)
    with caplog.at_level(logging.ERROR, logger="infrahub_sync.managed.app"):
        response = client.get("/runs/failing-run", headers={"Authorization": f"bearer {OWNER_TOKEN}"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service-unavailable"
    assert canary not in response.text
    assert canary not in caplog.text
    assert "RuntimeError" in caplog.text


def test_confirmation_schema_errors_and_openapi_contract(
    managed: tuple[TestClient, ProductProjection, _FakeOrchestration],
) -> None:
    client, _projection, orchestration = managed
    version = client.app.state.run_binding
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": "sync-key"}
    unconfirmed = client.post(
        "/runs",
        headers=headers,
        json={
            "operation": "sync",
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "reason": "run now",
        },
    )
    missing_key = client.post(
        "/runs",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        json={
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "reason": "plan now",
        },
    )
    invalid = client.post("/runs", headers=headers, json={"config_id": version.config_id})
    invalid_operation = client.post(
        "/runs",
        headers=headers,
        json={
            "operation": "delete",
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "reason": "unsupported operation",
        },
    )

    assert unconfirmed.status_code == 409
    assert missing_key.status_code == 422
    assert invalid.status_code == 422
    assert invalid_operation.status_code == 422
    assert all(set(response.json()) == {"error"} for response in (unconfirmed, missing_key, invalid, invalid_operation))
    assert not orchestration.submissions

    openapi = client.get("/openapi.json").json()
    assert openapi["components"]["securitySchemes"] == {"BearerAuth": {"scheme": "bearer", "type": "http"}}

    paths = openapi["paths"]
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
            assert operation["security"] == [{"BearerAuth": []}]
