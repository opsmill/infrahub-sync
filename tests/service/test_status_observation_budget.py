"""One run read observes each execution at most once, and never a terminal one.

A terminal execution is immutable, so its retained summary needs no live observation.
The counts here are pinned with ``==`` on purpose: the durable observation write inside
reconciliation is what rule 1 later reads, so a dropped write has to fail a test rather
than merely shrink a budget.

The same request is also the redaction boundary for retained provider text: the durable
row stays raw so reconciliation's predicates keep comparing what they wrote, and only the
rendered copy is redacted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

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

# `PrefectExecutionLink` spells its verdict domains inline, so they are named here.
TerminalState = Literal["completed", "failed", "cancelled", "abandoned", "interrupted"]
TerminalOutcome = Literal["succeeded", "failed", "cancelled", "abandoned", "ambiguous"]
NOW = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
AUTH = {"Authorization": "Bearer status-budget-token"}
CANARY = "canary-secret-0001"
ADMISSION_TTL_SECONDS = 300
STALL_THRESHOLD_SECONDS = 30


class _CountingProjection(ProductProjection):
    """The real local projection with counters for one request's durable I/O."""

    def __init__(self, projection: ProductProjection) -> None:
        super().__init__(projection._records, projection._artifacts)  # pylint: disable=protected-access
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


def _pending(flow_run_id: str, attempt: int) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=flow_run_id,
        purpose="plan",
        attempt=attempt,
        submitted_at=NOW,
        last_observed_state="pending",
    )


def _terminal(
    flow_run_id: str,
    attempt: int,
    *,
    terminal_state: TerminalState = "completed",
    terminal_outcome: TerminalOutcome = "succeeded",
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
        terminal_state=terminal_state,
        terminal_outcome=terminal_outcome,
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
    secrets: tuple[str, ...] = (),
) -> tuple[TestClient, _CountingProjection]:
    projection = _CountingProjection(local_product_projection(tmp_path))
    projection.create_run(run)
    service = RunService(projection, orchestration, secrets=secrets, clock=lambda: NOW)
    liveness = (
        RunLivenessReconciler(
            projection,
            orchestration,
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
    verdicts: tuple[tuple[TerminalState, TerminalOutcome], ...] = (
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
    # FR-019: the retained observation of every link is `running`; a finished execution reports its verdict.
    assert [summary["state"] for summary in summaries] == [state for state, _outcome in verdicts]
    assert all(summary["detail_available"] is False for summary in summaries)
    assert all(summary["unavailable_reason"] == "live-detail-not-requested" for summary in summaries)
    assert orchestration.observed == []
    assert projection.observation_writes == 0


def test_a_terminal_execution_without_an_observed_state_renders_its_verdict_without_observing(
    tmp_path: Path,
) -> None:
    """FR-019: a link that was never observed reports its verdict as its state, and nothing is observed."""
    link = _terminal("flow-unobserved", 1, last_observed_state=None)
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-unobserved", (link,)), orchestration)

    response = client.get("/runs/run-unobserved", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert (summary["state"], summary["detail_available"], summary["unavailable_reason"]) == (
        "completed",
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
    link = PrefectExecutionLink(
        flow_run_id="flow-cancelling",
        purpose="plan",
        attempt=1,
        submitted_at=NOW,
        last_observed_state="pending",
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


def test_a_supplied_secret_is_redacted_out_of_a_pending_links_retained_state(tmp_path: Path) -> None:
    """Retained observation text is provider-supplied, so the rendered copy is redacted."""
    orchestration = _CountingOrchestration({"flow-supplied": Observation(available=True, state=f"running-{CANARY}")})
    client, projection = _client(
        tmp_path, _run("run-supplied", (_pending("flow-supplied", 1),)), orchestration, secrets=(CANARY,)
    )

    response = client.get("/runs/run-supplied", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["prefect_executions"][0]["last_observed_state"] == "running-***"
    assert CANARY not in json.dumps(body["run"])
    stored = projection.lookup_run("run-supplied").value
    assert stored is not None
    # Raw at rest by design: reconciliation's predicates compare the value they wrote.
    assert stored.prefect_executions[0].last_observed_state == f"running-{CANARY}"


def test_a_secret_collected_from_the_environment_is_redacted_without_being_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credential-shaped variable is a secret of the process, not only of the caller."""
    monkeypatch.setenv("PROBE_PASSWORD", CANARY)
    orchestration = _CountingOrchestration({"flow-env": Observation(available=True, state=f"running-{CANARY}")})
    client, _projection = _client(tmp_path, _run("run-env", (_pending("flow-env", 1),)), orchestration)

    response = client.get("/runs/run-env", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["prefect_executions"][0]["last_observed_state"] == "running-***"
    assert CANARY not in json.dumps(body["run"])


def test_a_terminal_links_retained_state_is_redacted_in_both_places_it_is_rendered(tmp_path: Path) -> None:
    """The durable observation is redacted where it is returned; FR-019 makes the summary's state the verdict."""
    link = _terminal("flow-terminal-secret", 1, last_observed_state=f"running-{CANARY}")
    orchestration = _CountingOrchestration()
    client, projection = _client(tmp_path, _run("run-terminal-secret", (link,)), orchestration, secrets=(CANARY,))

    response = client.get("/runs/run-terminal-secret", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["prefect_executions"][0]["last_observed_state"] == "running-***"
    assert body["orchestration"][0]["state"] == "completed"
    assert CANARY not in json.dumps(body)
    assert orchestration.observed == []
    assert projection.observation_writes == 0


def test_a_secret_that_spells_a_terminal_state_still_reaches_its_cancellation_verdict(tmp_path: Path) -> None:
    """Redacting before the predicate would lose the verdict, so the durable row stays raw."""
    link = PrefectExecutionLink(
        flow_run_id="flow-overlap",
        purpose="plan",
        attempt=1,
        submitted_at=NOW,
        last_observed_state="pending",
        claimed_at=NOW - timedelta(seconds=60),
        claiming_worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        cancellation_requested_at=NOW - timedelta(seconds=10),
        cancellation_recovery_deadline_at=NOW + timedelta(seconds=20),
        cancellation_receipt_id="m-overlap-1",
        cancellation_acknowledged_at=NOW - timedelta(seconds=5),
    )
    orchestration = _CountingOrchestration({"flow-overlap": Observation(available=True, state="cancelled")})
    client, projection = _client(tmp_path, _run("run-overlap", (link,)), orchestration, secrets=("cancelled",))

    response = client.get("/runs/run-overlap", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert (summary["terminal_state"], summary["terminal_outcome"]) == ("cancelled", "cancelled")
    # The verdict is a fixed enum the response already carries raw as terminal_state, so the
    # summary's state repeats it unredacted; the provider-supplied observation stays redacted.
    assert (summary["state"], summary["detail_available"]) == ("cancelled", True)
    assert response.json()["run"]["prefect_executions"][0]["last_observed_state"] == "***"
    stored = projection.lookup_run("run-overlap").value
    assert stored is not None
    assert stored.prefect_executions[0].last_observed_state == "cancelled"


def test_redacting_the_rendered_links_leaves_the_record_it_was_given_untouched(tmp_path: Path) -> None:
    """The redacted copy is for rendering only, so it cannot become the value anything reads."""
    link = _pending("flow-untouched", 1).model_copy(update={"last_observed_state": f"running-{CANARY}"})
    run = _run("run-untouched", (link,))
    service = RunService(
        _CountingProjection(local_product_projection(tmp_path)),
        _CountingOrchestration(),
        secrets=(CANARY,),
        clock=lambda: NOW,
    )

    redacted = service._redacted_links(run)  # pylint: disable=protected-access

    assert redacted is not run
    assert redacted.prefect_executions[0].last_observed_state == "running-***"
    assert run.prefect_executions[0] is link
    assert link.last_observed_state == f"running-{CANARY}"


def test_a_reconciler_supplied_live_observation_is_redacted_in_the_response(tmp_path: Path) -> None:
    """Reconciliation hands its observations to the route, so the response is where they are redacted."""
    orchestration = _CountingOrchestration({"flow-live": Observation(available=True, state=f"running-{CANARY}")})
    client, _projection = _client(
        tmp_path, _run("run-live", (_pending("flow-live", 1),)), orchestration, secrets=(CANARY,)
    )

    response = client.get("/runs/run-live", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["orchestration"][0]["state"] == "running-***"
    assert CANARY not in response.text


def test_a_live_observation_is_redacted_by_a_secret_collected_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process's own credential-shaped variables redact the same response fields."""
    monkeypatch.setenv("PROBE_TOKEN", CANARY)
    orchestration = _CountingOrchestration({"flow-env-live": Observation(available=True, state=f"running-{CANARY}")})
    client, _projection = _client(tmp_path, _run("run-env-live", (_pending("flow-env-live", 1),)), orchestration)

    response = client.get("/runs/run-env-live", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["orchestration"][0]["state"] == "running-***"
    assert CANARY not in response.text


def test_an_unavailable_observations_reason_is_redacted_in_the_response(tmp_path: Path) -> None:
    """Provider text reaches the summary through the reason as well as the state."""
    orchestration = _CountingOrchestration(
        {"flow-reason": Observation(available=False, state=None, reason=f"prefect-execution-unavailable-{CANARY}")}
    )
    client, _projection = _client(
        tmp_path, _run("run-reason", (_pending("flow-reason", 1),)), orchestration, secrets=(CANARY,)
    )

    response = client.get("/runs/run-reason", headers=AUTH)

    assert response.status_code == 200
    summary = response.json()["orchestration"][0]
    assert summary["unavailable_reason"] == "prefect-execution-unavailable-***"
    assert (summary["state"], summary["detail_available"]) == ("pending", False)
    assert CANARY not in response.text


def test_the_stores_redaction_pass_is_a_no_op_on_an_already_redacted_response_body(tmp_path: Path) -> None:
    """A replayed body must equal the one first returned, so the second pass has to change nothing."""

    from infrahub_sync.product_store.store import _normalize_mapping, _redact_value  # noqa: PLC2701

    link = _pending("flow-replay", 1)
    service = RunService(
        _CountingProjection(local_product_projection(tmp_path)),
        _CountingOrchestration(),
        secrets=(CANARY,),
        clock=lambda: NOW,
    )
    observations = {"flow-replay": Observation(available=True, state=f"running-{CANARY}", reason=None)}

    resource = service._resource_with_observations(_run("run-replay", (link,)), observations)  # pylint: disable=protected-access
    body = resource.model_dump(mode="json")

    assert CANARY not in json.dumps(body)
    assert _redact_value(_normalize_mapping(body), (CANARY,)) == body


def _summary_state(tmp_path: Path, link: PrefectExecutionLink, observation: Observation) -> str | None:
    """Build one run's summaries from a scripted live observation, without a route or a reconciler."""
    service = RunService(
        _CountingProjection(local_product_projection(tmp_path)),
        _CountingOrchestration(),
        clock=lambda: NOW,
    )
    observations = {link.flow_run_id: observation}

    resource = service._resource_with_observations(_run("run-state-rule", (link,)), observations)  # pylint: disable=protected-access

    return resource.orchestration[0].state


def test_a_terminal_execution_reports_its_verdict_over_an_available_live_observation(tmp_path: Path) -> None:
    """FR-019: a recorded terminal state is the summary's state, even when Prefect still reports `Running`."""
    link = _terminal("flow-finished", 1, terminal_state="completed")

    state = _summary_state(tmp_path, link, Observation(available=True, state="Running"))

    assert state == "completed"


@pytest.mark.parametrize("observed", [True, False], ids=("observed", "unobserved"))
def test_a_secret_inside_a_terminal_state_name_leaves_state_equal_to_terminal_state(
    tmp_path: Path,
    observed: bool,  # noqa: FBT001 - one parametrized dimension of the summary loop.
) -> None:
    """The verdict is a fixed enum returned raw as terminal_state, so state repeats it raw."""
    link = _terminal("flow-failed", 1, terminal_state="failed", terminal_outcome="failed")
    service = RunService(
        _CountingProjection(local_product_projection(tmp_path)),
        _CountingOrchestration(),
        secrets=("led",),
        clock=lambda: NOW,
    )
    observations = {link.flow_run_id: Observation(available=True, state="Running")} if observed else {}

    resource = service._resource_with_observations(_run("run-led", (link,)), observations)  # pylint: disable=protected-access

    summary = resource.orchestration[0]
    assert summary.state == summary.terminal_state == "failed"


def test_an_execution_without_a_terminal_state_reports_its_available_live_observation(tmp_path: Path) -> None:
    """FR-019 keeps today's rule for an execution with no verdict: the live observation is its state."""
    link = _pending("flow-unfinished", 1)

    state = _summary_state(tmp_path, link, Observation(available=True, state="Running"))

    assert state == "Running"


@pytest.mark.parametrize("observed", [True, False], ids=("observed", "unobserved"))
def test_every_summary_links_its_flow_run_in_the_prefect_ui_when_one_is_set(
    tmp_path: Path,
    observed: bool,  # noqa: FBT001 - one parametrized dimension of the summary loop.
) -> None:
    """US3: an operator follows a run from its summary into the Prefect UI, whichever branch built it."""
    links = (_terminal("flow-first", 1), _pending("flow-second", 2))
    service = RunService(
        _CountingProjection(local_product_projection(tmp_path)),
        _CountingOrchestration(),
        clock=lambda: NOW,
        prefect_ui_url="http://127.0.0.1:4230",
    )
    observations = (
        {link.flow_run_id: Observation(available=True, state="Running") for link in links} if observed else {}
    )

    resource = service._resource_with_observations(_run("run-ui-link", links), observations)  # pylint: disable=protected-access

    assert [summary.prefect_flow_run_url for summary in resource.orchestration] == [
        "http://127.0.0.1:4230/runs/flow-run/flow-first",
        "http://127.0.0.1:4230/runs/flow-run/flow-second",
    ]


@pytest.mark.parametrize("observed", [True, False], ids=("observed", "unobserved"))
def test_no_summary_carries_a_prefect_ui_link_when_none_is_set(
    tmp_path: Path,
    observed: bool,  # noqa: FBT001 - one parametrized dimension of the summary loop.
) -> None:
    """Without a Prefect UI address the link is `None`, not a guessed address."""
    link = _pending("flow-unlinked", 1)
    service = RunService(
        _CountingProjection(local_product_projection(tmp_path)),
        _CountingOrchestration(),
        clock=lambda: NOW,
    )
    observations = {link.flow_run_id: Observation(available=True, state="Running")} if observed else {}

    resource = service._resource_with_observations(_run("run-no-ui-link", (link,)), observations)  # pylint: disable=protected-access

    assert resource.orchestration[0].prefect_flow_run_url is None
