"""`GET /runs/{run_id}/logs`: a run's Sync log lines, read through the Sync API (spec 017, FR-004 to FR-013)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.orchestration import (
    CancellationResult,
    FlowRunLog,
    Observation,
    OrchestrationUnavailableError,
    PoolStatus,
    RunLogRead,
    Submission,
)
from infrahub_sync.service.service import RunService

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from httpx import Response

OWNER_TOKEN = "logs-owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
READ = {"Authorization": f"Bearer {OWNER_TOKEN}"}
T0 = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
PLAN_RUN = "11111111-1111-4111-8111-111111111111"
VERIFY_RUN = "22222222-2222-4222-8222-222222222222"
RUN_ID = "run-logs"


def _log(log_id: str, flow_run_id: str, seconds: int, message: str, level: int = 20) -> FlowRunLog:
    return FlowRunLog(
        log_id=log_id, flow_run_id=flow_run_id, timestamp=T0 + timedelta(seconds=seconds), level=level, message=message
    )


class _FakeOrchestration:
    """Hold fixed Prefect log lines; ``mode`` makes the log expired or unreachable."""

    def __init__(self) -> None:
        self.logs: list[FlowRunLog] = [
            _log("p0", PLAN_RUN, 0, "Beginning flow run 'calm-otter' for flow 'infrahub-sync-service'"),
            _log("p1", PLAN_RUN, 1, "infrahub_sync.service.flow | Plan started"),
            _log("p2", PLAN_RUN, 2, "infrahub_sync.potenda | Loaded 3 nodes"),
            _log("p3", PLAN_RUN, 3, "Finished in state Completed()"),
            _log("v0", VERIFY_RUN, 4, "Engine execution of flow run 'brave-fox' exited"),
            _log("v1", VERIFY_RUN, 5, "infrahub_sync.service.flow | Verify failed", level=40),
        ]
        self.mode = "read"
        self.requested: list[tuple[str, ...]] = []

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        del parameters, idempotency_key
        raise AssertionError

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=False, state=None, reason="prefect-read-unavailable")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=True)

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:
        self.requested.append(tuple(flow_run_ids))
        if self.mode == "unavailable":
            msg = "the orchestrator cannot be reached"
            raise OrchestrationUnavailableError(msg, error_class="ConnectError")
        if self.mode == "expired":
            return RunLogRead(expired=True)
        return RunLogRead(expired=False, logs=tuple(log for log in self.logs if log.flow_run_id in flow_run_ids))


def _run(run_id: str, *links: PrefectExecutionLink) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference="logs-inventory",
        actor="owner",
        started_at=T0,
        phase="verify-failed",
        prefect_executions=links,
    )


@pytest.fixture
def logs_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, _FakeOrchestration]:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    projection.create_run(
        _run(
            RUN_ID,
            PrefectExecutionLink(flow_run_id=PLAN_RUN, purpose="plan", attempt=1, submitted_at=T0),
            PrefectExecutionLink(flow_run_id=VERIFY_RUN, purpose="verify", attempt=1, submitted_at=T0),
        )
    )
    projection.create_run(_run("run-empty"))
    orchestration = _FakeOrchestration()
    service = RunService(projection, orchestration, secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), orchestration


def _error(response: Response) -> dict[str, object]:
    error = response.json()["error"]
    assert isinstance(error, dict)
    return error


def test_returns_only_sync_lines_of_every_flow_run_in_order_with_their_stage(
    logs_api: tuple[TestClient, _FakeOrchestration],
) -> None:
    client, orchestration = logs_api

    response = client.get(f"/runs/{RUN_ID}/logs", headers=READ)

    assert response.status_code == 200
    body = response.json()
    assert orchestration.requested == [(PLAN_RUN, VERIFY_RUN)]
    assert [(item["stage"], item["attempt"], item["source"], item["message"]) for item in body["items"]] == [
        ("plan", 1, "infrahub_sync.service.flow", "Plan started"),
        ("plan", 1, "infrahub_sync.potenda", "Loaded 3 nodes"),
        ("verify", 1, "infrahub_sync.service.flow", "Verify failed"),
    ]
    assert body["items"][2]["level"] == "ERROR"
    assert body["items"][0]["timestamp"].startswith("2026-11-06T10:00:01")
    assert (body["run_id"], body["limit"], body["offset"], body["has_more"]) == (RUN_ID, 200, 0, False)
    assert body["run_phase"] == "verify-failed"


def test_stage_filters_the_flow_runs_read(logs_api: tuple[TestClient, _FakeOrchestration]) -> None:
    client, orchestration = logs_api

    response = client.get(f"/runs/{RUN_ID}/logs", params={"stage": "verify"}, headers=READ)

    assert response.status_code == 200
    assert orchestration.requested == [(VERIFY_RUN,)]
    assert [item["message"] for item in response.json()["items"]] == ["Verify failed"]


def test_limit_and_offset_page_the_sync_lines(logs_api: tuple[TestClient, _FakeOrchestration]) -> None:
    client, orchestration = logs_api

    first = client.get(f"/runs/{RUN_ID}/logs", params={"limit": "2"}, headers=READ).json()
    orchestration.logs.append(_log("v2", VERIFY_RUN, 6, "infrahub_sync.service.flow | Next step: plan again"))
    newer = client.get(f"/runs/{RUN_ID}/logs", params={"offset": "3"}, headers=READ).json()

    assert [item["message"] for item in first["items"]] == ["Plan started", "Loaded 3 nodes"]
    assert (first["limit"], first["has_more"]) == (2, True)
    assert [item["message"] for item in newer["items"]] == ["Next step: plan again"]
    assert (newer["offset"], newer["has_more"]) == (3, False)


@pytest.mark.parametrize(
    "params",
    [{"stage": "deploy"}, {"limit": "0"}, {"limit": "1001"}, {"limit": "1.0"}, {"offset": "-1"}, {"offset": "x"}],
)
def test_a_value_outside_the_contract_is_refused(
    logs_api: tuple[TestClient, _FakeOrchestration], params: dict[str, str]
) -> None:
    client, _orchestration = logs_api

    response = client.get(f"/runs/{RUN_ID}/logs", params=params, headers=READ)

    assert response.status_code == 422
    assert _error(response)["code"] == "request-invalid"


def test_a_limit_of_1000_is_accepted(logs_api: tuple[TestClient, _FakeOrchestration]) -> None:
    client, _orchestration = logs_api

    response = client.get(f"/runs/{RUN_ID}/logs", params={"limit": "1000"}, headers=READ)

    assert response.status_code == 200
    assert response.json()["limit"] == 1000


def test_an_unknown_run_is_not_found(logs_api: tuple[TestClient, _FakeOrchestration]) -> None:
    client, orchestration = logs_api

    response = client.get("/runs/run-missing/logs", headers=READ)

    assert response.status_code == 404
    assert _error(response)["code"] == "run-not-found"
    assert orchestration.requested == []


def test_a_run_without_a_flow_run_returns_no_lines(logs_api: tuple[TestClient, _FakeOrchestration]) -> None:
    client, orchestration = logs_api

    response = client.get("/runs/run-empty/logs", headers=READ)

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["has_more"] is False
    assert orchestration.requested == []


def test_an_expired_log_answers_410(logs_api: tuple[TestClient, _FakeOrchestration]) -> None:
    client, orchestration = logs_api
    orchestration.mode = "expired"

    response = client.get(f"/runs/{RUN_ID}/logs", headers=READ)

    assert response.status_code == 410
    error = _error(response)
    assert error["code"] == "log-expired"
    assert error["message"] == "the log of this run has expired"


def test_an_unreachable_orchestrator_answers_503_while_record_and_timeline_stay_readable(
    logs_api: tuple[TestClient, _FakeOrchestration], caplog: pytest.LogCaptureFixture
) -> None:
    client, orchestration = logs_api
    orchestration.mode = "unavailable"

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.service.service"):
        logs = client.get(f"/runs/{RUN_ID}/logs", headers=READ)
    # The server log names the error class, so an operator can tell a timeout from a refusal.
    assert [record.getMessage() for record in caplog.records if record.name == "infrahub_sync.service.service"] == [
        f"the log of run {RUN_ID} could not be read from the orchestrator (ConnectError)"
    ]
    run = client.get(f"/runs/{RUN_ID}", headers=READ)
    events = client.get("/events", params={"run_id": RUN_ID}, headers=READ)

    assert logs.status_code == 503
    error = _error(logs)
    assert error["code"] == "log-unavailable"
    assert error["message"] == "the log of this run cannot be read now; retry later"
    assert (run.status_code, events.status_code) == (200, 200)


@pytest.mark.parametrize("mode", ["expired", "unavailable"])
def test_no_error_message_names_the_orchestrator(logs_api: tuple[TestClient, _FakeOrchestration], mode: str) -> None:
    client, orchestration = logs_api
    orchestration.mode = mode

    response = client.get(f"/runs/{RUN_ID}/logs", headers=READ)

    assert "prefect" not in response.text.lower()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-token"}])
def test_reading_a_log_needs_a_valid_token(
    logs_api: tuple[TestClient, _FakeOrchestration], headers: dict[str, str]
) -> None:
    client, orchestration = logs_api

    response = client.get(f"/runs/{RUN_ID}/logs", headers=headers)

    assert response.status_code == 401
    assert orchestration.requested == []
