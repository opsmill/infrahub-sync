"""One run read observes each execution at most once, and never a terminal one.

A terminal execution is immutable, so its retained summary needs no live observation.
The counts here are pinned with ``==`` on purpose: the durable observation write inside
reconciliation is what rule 1 later reads, so a dropped write has to fail a test rather
than merely shrink a budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import (
    LookupResult,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import Principal
from infrahub_sync.service.liveness import LivenessPolicy, RunLivenessReconciler
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, Submission
from infrahub_sync.service.service import RunService

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

NOW = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
AUTH = {"Authorization": "Bearer status-budget-token"}
ADMISSION_TTL_SECONDS = 300
STALL_THRESHOLD_SECONDS = 30


class _CountingProjection(ProductProjection):
    """The real local projection with counters for one request's durable I/O."""

    def __init__(self, projection: ProductProjection) -> None:
        super().__init__(projection._records, projection._artifacts)  # noqa: SLF001  # pylint: disable=protected-access
        self.lookup_runs = 0
        self.observation_writes = 0

    def reset_counts(self) -> None:
        """Drop the setup's I/O so a count describes exactly one request."""
        self.lookup_runs = 0
        self.observation_writes = 0

    def lookup_run(self, run_id: str) -> LookupResult[ProductRun]:
        """Count every durable run read."""
        self.lookup_runs += 1
        return super().lookup_run(run_id)

    def observe_prefect_execution(
        self, run_id: str, flow_run_id: str, *, state: str | None, secrets: Sequence[str] = ()
    ) -> None:
        """Count every durable observation write."""
        self.observation_writes += 1
        super().observe_prefect_execution(run_id, flow_run_id, state=state, secrets=secrets)


class _CountingOrchestration:
    """A scripted Prefect double that records every remote observation it is asked for."""

    def __init__(self, observations: dict[str, Observation] | None = None) -> None:
        self.observations = observations or {}
        self.default = Observation(available=True, state="running")
        self.observed: list[str] = []

    async def observe(self, flow_run_id: str) -> Observation:
        """Record the remote read and answer with this flow run's scripted state."""
        self.observed.append(flow_run_id)
        return self.observations.get(flow_run_id, self.default)

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        """Answer without pool detail; no assertion here depends on worker freshness."""
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        """Never used: every run here is created durably rather than submitted."""
        del parameters, idempotency_key
        return Submission(flow_run_id="unused", state="pending")

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        """Never used: cancellation intent is seeded durably."""
        del flow_run_id
        return CancellationResult(acknowledged=True)


class _Resolver:
    """Narrow authentication double; authorization is not this module's subject."""

    secret_values: tuple[str, ...] = ()

    @staticmethod
    def resolve(token: str) -> Principal:
        """Accept any bearer token as the same operator."""
        del token
        return Principal(actor="operator")


def _pending(flow_run_id: str, attempt: int, **fields: object) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=flow_run_id,
        purpose="plan",
        attempt=attempt,
        submitted_at=NOW,
        last_observed_state="pending",
        **fields,  # ty: ignore[invalid-argument-type]
    )


def _terminal(
    flow_run_id: str,
    attempt: int,
    *,
    terminal_state: str = "completed",
    terminal_outcome: str = "succeeded",
    last_observed_state: str | None = "running",
) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=flow_run_id,
        purpose="plan",
        attempt=attempt,
        submitted_at=NOW - timedelta(seconds=120),
        claimed_at=NOW - timedelta(seconds=110),
        claiming_worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        last_observed_state=last_observed_state,
        last_observed_at=NOW - timedelta(seconds=60),
        terminal_at=NOW - timedelta(seconds=30),
        terminal_state=terminal_state,  # ty: ignore[invalid-argument-type]
        terminal_outcome=terminal_outcome,  # ty: ignore[invalid-argument-type]
    )


def _run(run_id: str, links: tuple[PrefectExecutionLink, ...]) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        audit_links=("ticket:42",),
        started_at=NOW - timedelta(seconds=600),
        phase="planning",
        prefect_executions=links,
    )


def _client(
    tmp_path: Path,
    run: ProductRun,
    orchestration: _CountingOrchestration,
    *,
    reconciler: bool = True,
) -> tuple[TestClient, _CountingProjection]:
    projection = _CountingProjection(local_product_projection(tmp_path))
    projection.create_run(run)
    service = RunService(projection, orchestration, clock=lambda: NOW)  # ty: ignore[invalid-argument-type]
    liveness = (
        RunLivenessReconciler(
            projection,
            orchestration,  # ty: ignore[invalid-argument-type]
            LivenessPolicy(ADMISSION_TTL_SECONDS, STALL_THRESHOLD_SECONDS, 5),
            "pool",
            clock=lambda: NOW,
        )
        if reconciler
        else None
    )
    app = create_app(service, _Resolver(), reconciler=liveness)  # type: ignore[arg-type]
    projection.reset_counts()
    return TestClient(app), projection


@pytest.mark.parametrize(
    ("pending", "terminal", "max_lookups"),
    [(1, 0, 3), (0, 20, 2), (1, 20, 3), (21, 0, 23)],
    ids=("one-pending", "twenty-terminal", "one-pending-and-twenty-terminal", "twenty-one-pending"),
)
def test_one_run_read_observes_every_pending_execution_once_and_no_terminal_one(
    tmp_path: Path, pending: int, terminal: int, max_lookups: int
) -> None:
    """Remote observations and durable writes are exactly the run's pending links."""
    links = tuple(_terminal(f"flow-terminal-{index}", index + 1) for index in range(terminal)) + tuple(
        _pending(f"flow-pending-{index}", terminal + index + 1) for index in range(pending)
    )
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-budget", links), orchestration)

    response = client.get("/runs/run-budget", headers=AUTH)

    assert response.status_code == 200
    assert len(response.json()["orchestration"]) == pending + terminal
    assert orchestration.observed == [f"flow-pending-{index}" for index in range(pending)]
    assert len(orchestration.observed) == len(set(orchestration.observed))
    assert projection.observation_writes == pending
    assert projection.lookup_runs <= max_lookups


def test_terminal_executions_render_their_own_distinguished_verdicts_unobserved(tmp_path: Path) -> None:
    """Retained verdicts stay distinct without any live provider detail."""
    verdicts = (
        ("completed", "succeeded"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
        ("abandoned", "abandoned"),
        ("interrupted", "ambiguous"),
    )
    # Attempts are deliberately unsorted: summaries follow the record's own link order.
    attempts = (5, 3, 1, 4, 2)
    links = tuple(
        _terminal(f"flow-{state}", attempt, terminal_state=state, terminal_outcome=outcome)
        for attempt, (state, outcome) in zip(attempts, verdicts, strict=True)
    )
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-verdicts", links), orchestration)

    response = client.get("/runs/run-verdicts", headers=AUTH)

    assert response.status_code == 200
    summaries = response.json()["orchestration"]
    assert [(summary["flow_run_id"], summary["attempt"]) for summary in summaries] == [
        (f"flow-{state}", attempt) for attempt, (state, _outcome) in zip(attempts, verdicts, strict=True)
    ]
    assert [(summary["terminal_state"], summary["terminal_outcome"]) for summary in summaries] == list(verdicts)
    assert all(summary["state"] == "running" for summary in summaries)
    assert all(summary["detail_available"] is False for summary in summaries)
    assert all(summary["unavailable_reason"] == "live-detail-not-requested" for summary in summaries)
    assert orchestration.observed == []
    assert projection.observation_writes == 0


def test_a_terminal_execution_without_an_observed_state_renders_null_without_observing(tmp_path: Path) -> None:
    """A link created with no observed state keeps a null state rather than inventing one."""
    link = _terminal("flow-unobserved", 1, last_observed_state=None)
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-unobserved", (link,)), orchestration)

    response = client.get("/runs/run-unobserved", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert (summary["state"], summary["detail_available"], summary["unavailable_reason"]) == (
        None,
        False,
        "live-detail-not-requested",
    )
    assert orchestration.observed == []
    assert projection.observation_writes == 0


def test_an_unavailable_pending_observation_is_still_taken_exactly_once(tmp_path: Path) -> None:
    """Unavailable detail is an answer, not a missing observation to retry in the same request."""
    orchestration = _CountingOrchestration(
        {"flow-unavailable": Observation(available=False, state=None, reason="prefect-execution-unavailable")}
    )
    client, projection = _client(tmp_path, _run("run-unavailable", (_pending("flow-unavailable", 1),)), orchestration)

    response = client.get("/runs/run-unavailable", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert (summary["state"], summary["detail_available"], summary["unavailable_reason"]) == (
        "pending",
        False,
        "prefect-execution-unavailable",
    )
    assert orchestration.observed == ["flow-unavailable"]
    assert projection.observation_writes == 0


def test_an_execution_that_terminalizes_during_the_request_renders_its_new_verdict(tmp_path: Path) -> None:
    """The response is the post-reconciliation snapshot, not the state the request opened with."""
    link = _pending("flow-abandoning", 1).model_copy(
        update={"submitted_at": NOW - timedelta(seconds=ADMISSION_TTL_SECONDS + 1)}
    )
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-abandoning", (link,)), orchestration)

    response = client.get("/runs/run-abandoning", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert (summary["terminal_state"], summary["terminal_outcome"]) == ("abandoned", "abandoned")
    assert summary["terminal_at"] is not None
    assert orchestration.observed == ["flow-abandoning"]
    assert projection.observation_writes == 1


def test_the_reconciliation_write_still_carries_an_acknowledged_cancellation_to_its_verdict(tmp_path: Path) -> None:
    """Rule 1 reads the durable cancelled state that this same request wrote moments earlier."""
    link = _pending(
        "flow-cancelling",
        1,
        claimed_at=NOW - timedelta(seconds=60),
        claiming_worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        cancellation_requested_at=NOW - timedelta(seconds=10),
        cancellation_recovery_deadline_at=NOW + timedelta(seconds=20),
        cancellation_receipt_id="m-cancel-1",
        cancellation_acknowledged_at=NOW - timedelta(seconds=5),
    )
    orchestration = _CountingOrchestration({"flow-cancelling": Observation(available=True, state="cancelled")})
    client, projection = _client(tmp_path, _run("run-cancelling", (link,)), orchestration)

    response = client.get("/runs/run-cancelling", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert (summary["terminal_state"], summary["terminal_outcome"]) == ("cancelled", "cancelled")
    assert (summary["state"], summary["detail_available"]) == ("cancelled", True)
    assert orchestration.observed == ["flow-cancelling"]
    assert projection.observation_writes == 1
    stored = projection.lookup_run("run-cancelling").value
    assert stored is not None
    assert stored.prefect_executions[0].last_observed_state == "cancelled"


@pytest.mark.parametrize("reconciler", [True, False], ids=("with-reconciler", "without-reconciler"))
def test_a_missing_run_is_still_a_not_found_on_both_route_paths(tmp_path: Path, reconciler: bool) -> None:  # noqa: FBT001 - one parametrized dimension of the route.
    """Reconciliation of an absent run does not change the refusal the route owes."""
    orchestration = _CountingOrchestration()
    client, _projection = _client(
        tmp_path, _run("run-present", (_pending("flow-present", 1),)), orchestration, reconciler=reconciler
    )

    response = client.get("/runs/run-absent", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run-not-found"
    assert orchestration.observed == []


def test_without_a_reconciler_the_route_still_observes_only_pending_executions(tmp_path: Path) -> None:
    """The reconciler-free path keeps pending freshness and stops observing terminal links."""
    links = (_terminal("flow-done", 1), _pending("flow-live", 2))
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-no-reconciler", links), orchestration, reconciler=False)

    response = client.get("/runs/run-no-reconciler", headers=AUTH)

    assert response.status_code == 200
    assert orchestration.observed == ["flow-live"]
    assert projection.observation_writes == 1
    summaries = response.json()["orchestration"]
    assert (summaries[0]["detail_available"], summaries[0]["unavailable_reason"]) == (
        False,
        "live-detail-not-requested",
    )
    assert (summaries[1]["state"], summaries[1]["detail_available"]) == ("running", True)
