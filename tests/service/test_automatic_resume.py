"""The decision on resuming an interrupted write without a person, and its steps (spec 030, T009 to T016)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("prefect")

from infrahub_sync.product_store import (
    PLAN_ARTIFACT_ID,
    ApplyProgress,
    ArtifactReference,
    AutomaticResume,
    PrefectExecutionLink,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.product_store.models import ExecutionMergeWriteback, RunApproval
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import Principal, Resolution
from infrahub_sync.service.events import RUN_FAILED
from infrahub_sync.service.liveness import (
    AUTOMATIC_RESUME_ACTOR,
    AUTOMATIC_RESUME_LIMIT,
    LivenessPolicy,
    RunLivenessReconciler,
    automatic_check_key,
    decide_automatic_resume,
)
from infrahub_sync.service.models import ApplyRunRequest, ResumeRunRequest, VerifyRunRequest
from infrahub_sync.service.orchestration import Observation, PoolStatus, PoolWorker
from infrahub_sync.service.service import RunService, ServiceAPIError
from infrahub_sync.service.settings import AUTOMATIC_RESUME_ENV, REQUIRE_APPROVAL_ENV
from tests.service.accounts import seed_accounts
from tests.service.test_http_api import (
    ADMIN_TOKEN,
    OWNER_TOKEN,
    _create,
    _FakeOrchestration,
    _registered_package,
    _settle_open_executions,
)
from tests.service.test_liveness_policy import _Orchestration
from tests.service.test_resume_run import _CHECKSUM as _PLAN_CHECKSUM
from tests.service.test_resume_run import _headers, _latest_flow_run_id, _publish_plan

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
RUN_ID = "run-automatic-resume"
FLOW_RUN_ID = "flow-interrupted"
_CHECKSUM = "a" * 64
_DIGEST = "b" * 64


@pytest.fixture(autouse=True)
def _automatic_resume_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTOMATIC_RESUME_ENV, "true")
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)


def _marker(position: int) -> ApplyProgress:
    return ApplyProgress(position=position, operation_id=f"op_{position:016x}", total=100, saved_at=NOW)


def _link(purpose: str = "apply", *, marker: int | None = None) -> PrefectExecutionLink:
    return PrefectExecutionLink(
        flow_run_id=FLOW_RUN_ID,
        purpose=purpose,
        attempt=1,
        submitted_at=NOW - timedelta(minutes=10),
        claimed_at=NOW - timedelta(minutes=9),
        claiming_worker_id=str(uuid4()),
        apply_progress=None if marker is None else _marker(marker),
    )


def _plan_reference() -> ArtifactReference:
    return ArtifactReference(
        artifact_id=PLAN_ARTIFACT_ID,
        run_id=RUN_ID,
        kind="saved-plan-review",
        media_type="application/json",
        digest=_DIGEST,
        size=1,
        object_key=f"artifacts/{_DIGEST}/plan.json",
        manifest_key=f"manifests/{_DIGEST}/manifest.json",
        created_at=NOW,
    )


def _run(
    link: PrefectExecutionLink,
    *,
    plan: bool = True,
    phase: str = "applying",
    approval: RunApproval | None = None,
) -> ProductRun:
    return ProductRun(
        run_id=RUN_ID,
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        started_at=NOW - timedelta(hours=1),
        phase=phase,
        approval=approval,
        artifact_refs=(_plan_reference(),) if plan else (),
        prefect_executions=(link,),
    )


def _resumed(resume_flow_run_id: str, start_after: int | None = None) -> AutomaticResume:
    """An earlier record whose automatic resume was submitted as `resume_flow_run_id`."""
    return AutomaticResume(
        run_id=RUN_ID,
        interrupted_flow_run_id=f"flow-before-{resume_flow_run_id}",
        cause="rule-8",
        state="resuming",
        decision="resume",
        start_after=start_after,
        resume_flow_run_id=resume_flow_run_id,
        decided_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
    )


def _not_resumed(interrupted_flow_run_id: str) -> AutomaticResume:
    return AutomaticResume(
        run_id=RUN_ID,
        interrupted_flow_run_id=interrupted_flow_run_id,
        cause="rule-6",
        state="ended",
        decision="no-resume",
        reason="check-found-differs",
        decided_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
    )


def _decide(
    run: ProductRun, previous_rows: tuple[AutomaticResume, ...] = (), *, branch_review: bool = False
) -> AutomaticResume | None:
    return decide_automatic_resume(
        run, run.prefect_executions[-1], "rule-8", "worker-a", previous_rows, NOW, branch_review=branch_review
    )


def _reason(row: AutomaticResume | None) -> str | None:
    assert row is not None
    if row.reason is None:
        assert (row.state, row.decision) == ("waiting-check", None)
    else:
        assert (row.state, row.decision) == ("ended", "no-resume")
    return row.reason


def test_the_constants_name_the_automatic_actor_and_the_limit() -> None:
    assert AUTOMATIC_RESUME_ACTOR == "sync:automatic-resume"
    assert AUTOMATIC_RESUME_LIMIT == 3


@pytest.mark.parametrize("purpose", ["apply", "sync", "resume"])
def test_an_interrupted_write_waits_for_the_check(purpose: str) -> None:
    row = _decide(_run(_link(purpose)))

    assert row == AutomaticResume(
        run_id=RUN_ID,
        interrupted_flow_run_id=FLOW_RUN_ID,
        cause="rule-8",
        worker_name="worker-a",
        state="waiting-check",
        decided_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize("purpose", ["plan", "verify"])
def test_an_execution_that_is_not_a_write_gets_no_record(purpose: str) -> None:
    assert _decide(_run(_link(purpose))) is None


def test_the_setting_off_gives_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """While the setting is off, nothing is decided (FR-004)."""
    monkeypatch.delenv(AUTOMATIC_RESUME_ENV)

    assert _decide(_run(_link())) is None


@pytest.mark.parametrize(
    ("run", "branch_review"),
    [
        (_run(_link(), plan=False), False),
        (_run(_link(), phase="cancelled"), False),
        (_run(_link()), True),
    ],
    ids=("no-saved-plan", "cancelled", "scheduled-branch-review"),
)
def test_a_run_that_runs_resume_refuses_is_not_resumable(run: ProductRun, branch_review: bool) -> None:  # noqa: FBT001 - one parametrized dimension of the run.
    assert _reason(_decide(run, branch_review=branch_review)) == "not-resumable"


def test_not_resumable_comes_before_approval_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    assert _reason(_decide(_run(_link(), plan=False))) == "not-resumable"


def test_a_required_approval_that_is_missing_ends_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    assert _reason(_decide(_run(_link()))) == "approval-missing"


def test_a_recorded_approval_lets_the_record_wait_for_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    approval = RunApproval(approved_by="acct_reviewer", approved_at=NOW, plan_checksum=_CHECKSUM)

    assert _reason(_decide(_run(_link(), approval=approval))) is None


def test_approval_missing_comes_before_no_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    run = _run(_link("resume", marker=5))

    assert _reason(_decide(run, (_resumed(FLOW_RUN_ID, start_after=5),))) == "approval-missing"


@pytest.mark.parametrize(
    ("start_after", "marker", "reason"),
    [
        (5, 5, "no-progress"),
        (5, 6, None),
        (None, None, "no-progress"),
        (None, 0, None),
    ],
    ids=("marker-unchanged", "marker-passed", "no-marker-saved", "first-marker-saved"),
)
def test_an_automatic_resume_that_saved_no_further_marker_made_no_progress(
    start_after: int | None, marker: int | None, reason: str | None
) -> None:
    """The run's highest marker must pass the marker the automatic resume started after (FR-009)."""
    run = _run(_link("resume", marker=marker))

    assert _reason(_decide(run, (_resumed(FLOW_RUN_ID, start_after=start_after),))) == reason


def test_a_write_that_is_not_the_automatic_resume_is_not_judged_for_progress() -> None:
    """A manual resume after an automatic one is compared with nothing (FR-011)."""
    run = _run(_link("resume", marker=5))

    assert _reason(_decide(run, (_resumed("flow-earlier-automatic-resume", start_after=5),))) is None


def test_no_progress_comes_before_limit_reached() -> None:
    run = _run(_link("resume", marker=5))
    previous = (_resumed("flow-1"), _resumed("flow-2"), _resumed(FLOW_RUN_ID, start_after=5))

    assert _reason(_decide(run, previous)) == "no-progress"


def test_three_automatic_resumes_reach_the_limit() -> None:
    previous = (_resumed("flow-1"), _resumed("flow-2"), _resumed("flow-3"))

    assert _reason(_decide(_run(_link()), previous)) == "limit-reached"


def test_records_without_an_automatic_resume_do_not_count_toward_the_limit() -> None:
    previous = (_resumed("flow-1"), _resumed("flow-2"), _not_resumed("flow-3"), _not_resumed("flow-4"))

    assert _reason(_decide(_run(_link()), previous)) is None


# --- the liveness reconciler (T010) ------------------------------------------------------


def _claimed_write(tmp_path, purpose: str = "apply") -> tuple[ProductProjection, str]:
    """A run whose claimed `purpose` execution has a saved plan; returns the store and the owner."""
    owner = str(uuid4())
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference="sha256:configuration",
            actor="operator@example.com",
            started_at=NOW - timedelta(hours=1),
            phase="applying",
            prefect_executions=(
                PrefectExecutionLink(
                    flow_run_id=FLOW_RUN_ID,
                    purpose=purpose,
                    attempt=1,
                    submitted_at=NOW - timedelta(minutes=10),
                    claimed_at=NOW - timedelta(seconds=30),
                    claiming_worker_id=owner,
                ),
            ),
        )
    )
    projection.publish_artifact(
        RUN_ID, artifact_id=PLAN_ARTIFACT_ID, kind="saved-plan-review", media_type="application/json", data=b"{}"
    )
    return projection, owner


def _reconcile(projection: ProductProjection, pool: PoolStatus, observation: Observation | None = None) -> None:
    reconciler = RunLivenessReconciler(
        projection, _Orchestration(pool, observation), LivenessPolicy(300, 30, 5), "pool", clock=lambda: NOW
    )
    asyncio.run(reconciler.reconcile_once())


def _offline_pool(owner: str) -> PoolStatus:
    return PoolStatus(
        detail_available=True,
        queue_depth=0,
        observed_at=NOW,
        workers=(PoolWorker(owner, "offline", NOW - timedelta(minutes=5), 10.0, name="infrahub-sync-worker-a"),),
    )


def _fresh_pool(owner: str) -> PoolStatus:
    return PoolStatus(
        detail_available=True, queue_depth=0, observed_at=NOW, workers=(PoolWorker(owner, "online", NOW, 10.0),)
    )


def _interrupted(projection: ProductProjection) -> bool:
    run = projection.lookup_run(RUN_ID).value
    assert run is not None
    return run.prefect_executions[0].terminal_state == "interrupted"


@pytest.mark.parametrize(
    ("state", "decided"),
    [("crashed", True), ("failed", False), ("cancelled", False), ("completed", False)],
)
def test_rule_6_decides_only_for_a_crashed_flow_run(tmp_path, state: str, decided: bool) -> None:  # noqa: FBT001 - one parametrized dimension of the verdict.
    """A failure the worker raised, a cancellation in Prefect and a completion get no record (research R2)."""
    projection, owner = _claimed_write(tmp_path)

    _reconcile(projection, _fresh_pool(owner), Observation(available=True, state=state))

    assert _interrupted(projection)
    rows = projection.automatic_resumes_of(RUN_ID)
    if decided:
        assert [(row.cause, row.state, row.worker_name) for row in rows] == [("rule-6", "waiting-check", None)]
    else:
        assert rows == ()


def test_rule_8_decides_with_the_claiming_worker_name(tmp_path) -> None:
    projection, owner = _claimed_write(tmp_path)

    _reconcile(projection, _offline_pool(owner))

    assert _interrupted(projection)
    (row,) = projection.automatic_resumes_of(RUN_ID)
    assert (row.interrupted_flow_run_id, row.cause, row.state, row.worker_name) == (
        FLOW_RUN_ID,
        "rule-8",
        "waiting-check",
        "infrahub-sync-worker-a",
    )
    assert row.decided_at == NOW


def test_rule_8_writes_an_ended_record_for_a_run_without_a_required_approval(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    projection, owner = _claimed_write(tmp_path)

    _reconcile(projection, _offline_pool(owner))

    (row,) = projection.automatic_resumes_of(RUN_ID)
    assert (row.state, row.decision, row.reason) == ("ended", "no-resume", "approval-missing")


@pytest.mark.parametrize("purpose", ["plan", "verify"])
def test_an_interrupted_execution_that_is_not_a_write_gets_no_record(tmp_path, purpose: str) -> None:
    projection, owner = _claimed_write(tmp_path, purpose)

    _reconcile(projection, _offline_pool(owner))

    assert _interrupted(projection)
    assert projection.automatic_resumes_of(RUN_ID) == ()


def test_the_setting_off_writes_no_record_and_no_event(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A site that did not turn automatic resume on sees the interruption as before (FR-004)."""
    monkeypatch.delenv(AUTOMATIC_RESUME_ENV)
    projection, owner = _claimed_write(tmp_path)
    before = [event.event_type for event in projection.audit_events(RUN_ID)]

    _reconcile(projection, _offline_pool(owner))

    assert _interrupted(projection)
    assert projection.automatic_resumes_of(RUN_ID) == ()
    after = [event.event_type for event in projection.audit_events(RUN_ID)]
    assert after == [*before, RUN_FAILED]


# --- the reconciler step and the submitter (T012 to T016) ---------------------------------

_STEP_TOTAL = 100
_STEP_MARKER = 59
_STEP_WORKER_ID = "1d2c3b4a-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


class _LiveOrchestration(_FakeOrchestration):
    """The fake Prefect of the HTTP tests, with a scripted work pool snapshot."""

    def __init__(self) -> None:
        super().__init__()
        self.pool = "live"

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:
        del work_pool_name
        if self.pool == "detail-unavailable":
            return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)
        status = "online" if self.pool == "live" else "offline"
        workers = () if self.pool == "no-worker" else (PoolWorker(_STEP_WORKER_ID, status, now, 10.0, name="w"),)
        return PoolStatus(detail_available=True, queue_depth=0, observed_at=now, workers=workers)

    def stage_submissions(self, stage: str) -> list[dict[str, object]]:
        return [parameters for parameters, _key in self.submissions if parameters["stage"] == stage]


_StepAPI = tuple["TestClient", "ProductProjection", _LiveOrchestration, RunService]


@pytest.fixture
def step_api(tmp_path) -> _StepAPI:
    projection = local_product_projection(tmp_path.resolve())
    resolver = seed_accounts(
        projection,
        {"owner": {"token": OWNER_TOKEN}, "admin": {"token": ADMIN_TOKEN, "administrator": True}},
        environment_token=True,
    ).resolver
    orchestration = _LiveOrchestration()
    version = projection.create_configuration(_registered_package())
    service = RunService(projection, orchestration, secrets=resolver.secret_values)
    client = TestClient(create_app(service, resolver))
    client.app.state.run_binding = version
    return client, projection, orchestration, service


def _reconciler(api: _StepAPI) -> RunLivenessReconciler:
    _client, projection, orchestration, service = api
    return RunLivenessReconciler(projection, orchestration, LivenessPolicy(300, 30, 5), "pool", submitter=service)


def _step(api: _StepAPI) -> None:
    asyncio.run(_reconciler(api).reconcile_once())


def _planned_run(api: _StepAPI, *, left_out: int = 0) -> str:
    client, projection, _orchestration, _service = api
    run_id = _create(client).json()["run"]["run_id"]
    _publish_plan(projection, run_id, total=_STEP_TOTAL, left_out=left_out)
    _settle_open_executions(projection, run_id)
    return run_id


def _interrupted_with_record(api: _StepAPI, *, left_out: int = 0) -> str:
    """A run whose apply saved a marker and was interrupted with a record waiting for the check."""
    client, projection, _orchestration, _service = api
    run_id = _planned_run(api, left_out=left_out)
    applied = client.post(
        f"/runs/{run_id}/apply",
        headers=_headers("apply-key"),
        json={"expected_checksum": _PLAN_CHECKSUM, "confirm_writes": True, "confirm_left_out": bool(left_out)},
    )
    assert applied.status_code == 202, applied.json()
    flow_run_id = _latest_flow_run_id(projection, run_id)
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_STEP_WORKER_ID)
    marker = ApplyProgress(
        position=_STEP_MARKER, operation_id=f"op_{_STEP_MARKER:016x}", total=_STEP_TOTAL, saved_at=NOW
    )
    assert projection.record_apply_progress(run_id, flow_run_id, marker) == "saved"
    now = datetime.now(timezone.utc)
    record = AutomaticResume(
        run_id=run_id,
        interrupted_flow_run_id=flow_run_id,
        cause="rule-8",
        state="waiting-check",
        decided_at=now,
        updated_at=now,
    )
    assert projection.interrupt_execution(run_id, flow_run_id, automatic_resume=record)
    return run_id


def _record(api: _StepAPI, run_id: str) -> AutomaticResume:
    return api[1].automatic_resumes_of(run_id)[-1]


def _finish(
    projection: ProductProjection,
    run_id: str,
    flow_run_id: str | None,
    *,
    failed: bool = False,
    results: dict[str, object] | None = None,
) -> None:
    """End a submitted execution as its worker does: completed, or failed, with merged results."""
    assert flow_run_id is not None
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_STEP_WORKER_ID)
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_STEP_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state="failed" if failed else "completed",
        terminal_outcome="failed" if failed else "succeeded",
        writeback=ExecutionMergeWriteback(results=results or {}),
    )


def _verification(*states: str, truncated: int = 0) -> dict[str, object]:
    operations = [
        {"operation_id": f"op_{index:016x}", "kind": "BuiltinTag", "action": "create", "state": state}
        for index, state in enumerate(states)
    ]
    report: dict[str, object] = {"outcome": "verified", "checks": [], "operations": operations}
    if truncated:
        report["operations_truncated"] = truncated
    return {"verification": report}


def _passed_check(api: _StepAPI, run_id: str) -> AutomaticResume:
    """Step the record through a check that passes; returns the record waiting for the resume."""
    _step(api)
    checking = _record(api, run_id)
    _finish(api[1], run_id, checking.check_flow_run_id, results=_verification("matches", "missing"))
    _step(api)
    return _record(api, run_id)


def test_a_record_moves_from_the_check_to_an_ended_resume(step_api: _StepAPI) -> None:
    """Each move: the check, its verdict, the resume and its verdict; one submission of each (FR-005, FR-014)."""
    _client, projection, orchestration, _service = step_api
    run_id = _interrupted_with_record(step_api)

    _step(step_api)
    checking = _record(step_api, run_id)
    assert checking.state == "checking"
    [check] = orchestration.stage_submissions("verify")
    assert check["run_id"] == run_id
    check_flow_run_id = _latest_flow_run_id(projection, run_id)
    assert checking.check_flow_run_id == check_flow_run_id
    receipt = projection.lookup_mutation_for_execution(run_id, check_flow_run_id, "verify").value
    assert receipt is not None
    assert receipt.actor == AUTOMATIC_RESUME_ACTOR

    _step(step_api)
    assert _record(step_api, run_id).state == "checking"  # no verdict yet

    _finish(projection, run_id, checking.check_flow_run_id, results=_verification("matches", "missing"))
    _step(step_api)
    waiting = _record(step_api, run_id)
    assert (waiting.state, waiting.decision, waiting.operations_not_checked) == ("waiting-resume", None, 0)

    _step(step_api)
    resuming = _record(step_api, run_id)
    assert (resuming.state, resuming.decision, resuming.start_after) == ("resuming", "resume", _STEP_MARKER)
    [resume] = orchestration.stage_submissions("resume")
    assert resume["expected_checksum"] == _PLAN_CHECKSUM
    assert resume["confirm_writes"] is True
    assert "confirm_left_out" not in resume
    assert resuming.resume_flow_run_id == _latest_flow_run_id(projection, run_id)

    _finish(projection, run_id, resuming.resume_flow_run_id)
    _step(step_api)
    ended = _record(step_api, run_id)
    assert (ended.state, ended.decision, ended.reason) == ("ended", "resume", None)
    assert len(orchestration.stage_submissions("verify")) == 1
    assert len(orchestration.stage_submissions("resume")) == 1


@pytest.mark.parametrize("pool", ["no-worker", "offline", "detail-unavailable"])
def test_no_submission_without_a_live_worker(step_api: _StepAPI, pool: str) -> None:
    """A record waits, and nothing is submitted, until the pool snapshot has a live worker (FR-012)."""
    orchestration = step_api[2]
    run_id = _interrupted_with_record(step_api)
    orchestration.pool = pool

    _step(step_api)

    assert _record(step_api, run_id).state == "waiting-check"
    assert orchestration.stage_submissions("verify") == []

    orchestration.pool = "live"
    _step(step_api)
    assert _record(step_api, run_id).state == "checking"


def test_a_resume_waits_for_a_live_worker_too(step_api: _StepAPI) -> None:
    orchestration = step_api[2]
    run_id = _interrupted_with_record(step_api)
    assert _passed_check(step_api, run_id).state == "waiting-resume"
    orchestration.pool = "no-worker"

    _step(step_api)

    assert _record(step_api, run_id).state == "waiting-resume"
    assert orchestration.stage_submissions("resume") == []


def test_a_check_that_reports_differs_ends_the_record(step_api: _StepAPI) -> None:
    """Two `differs` end the record with their count; a `missing` alone would not (FR-006, FR-007)."""
    projection = step_api[1]
    run_id = _interrupted_with_record(step_api)
    _step(step_api)
    checking = _record(step_api, run_id)

    _finish(
        projection,
        run_id,
        checking.check_flow_run_id,
        results=_verification("differs", "missing", "differs", truncated=7),
    )
    _step(step_api)

    ended = _record(step_api, run_id)
    assert (ended.state, ended.decision, ended.reason) == ("ended", "no-resume", "check-found-differs")
    assert ended.reason_detail == {"differs": 2}
    assert ended.operations_not_checked == 7
    assert step_api[2].stage_submissions("resume") == []


def test_a_check_that_left_operations_out_still_lets_the_resume_start(step_api: _StepAPI) -> None:
    """The operations the 2,000-operation limit left out are stored; the resume still starts (FR-007)."""
    projection = step_api[1]
    run_id = _interrupted_with_record(step_api)
    _step(step_api)
    checking = _record(step_api, run_id)

    _finish(projection, run_id, checking.check_flow_run_id, results=_verification("matches", truncated=5))
    _step(step_api)

    waiting = _record(step_api, run_id)
    assert (waiting.state, waiting.operations_not_checked) == ("waiting-resume", 5)


def test_a_failed_check_ends_the_record_with_its_failure_class(step_api: _StepAPI) -> None:
    projection = step_api[1]
    run_id = _interrupted_with_record(step_api)
    _step(step_api)
    checking = _record(step_api, run_id)

    _finish(
        projection,
        run_id,
        checking.check_flow_run_id,
        failed=True,
        results={
            "verify_failure": {
                "failure_type": "destination_unreachable",
                "flow_run_id": checking.check_flow_run_id,
            }
        },
    )
    _step(step_api)

    ended = _record(step_api, run_id)
    assert (ended.state, ended.decision, ended.reason) == ("ended", "no-resume", "check-failed")
    assert ended.reason_detail == {"cause": "destination_unreachable"}


def test_an_abandoned_check_ends_the_record_as_not_picked_up(step_api: _StepAPI) -> None:
    projection = step_api[1]
    run_id = _interrupted_with_record(step_api)
    _step(step_api)
    checking = _record(step_api, run_id)
    assert checking.check_flow_run_id is not None

    assert projection.abandon_execution(run_id, checking.check_flow_run_id)
    _step(step_api)

    ended = _record(step_api, run_id)
    assert (ended.state, ended.decision, ended.reason) == ("ended", "no-resume", "not-picked-up")


def test_an_abandoned_resume_ends_the_record_as_not_picked_up(step_api: _StepAPI) -> None:
    projection = step_api[1]
    run_id = _interrupted_with_record(step_api)
    _passed_check(step_api, run_id)
    _step(step_api)
    resuming = _record(step_api, run_id)
    assert resuming.resume_flow_run_id is not None

    assert projection.abandon_execution(run_id, resuming.resume_flow_run_id)
    _step(step_api)

    ended = _record(step_api, run_id)
    assert (ended.state, ended.decision, ended.reason) == ("ended", "no-resume", "not-picked-up")


def test_a_refused_resume_ends_the_record_with_the_refusal_code(
    step_api: _StepAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The site requires approval after the record was written: `resume_run` refuses (research R8)."""
    run_id = _interrupted_with_record(step_api)
    _passed_check(step_api, run_id)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    _step(step_api)

    ended = _record(step_api, run_id)
    assert (ended.state, ended.decision, ended.reason) == ("ended", "no-resume", "refused")
    assert ended.reason_detail == {"code": "approval-required"}
    assert step_api[2].stage_submissions("resume") == []


def test_the_automatic_resume_confirms_the_left_out_records_the_apply_confirmed(step_api: _StepAPI) -> None:
    """The interrupted apply was admitted with `confirm_left_out`, so the resume repeats it (research R6)."""
    run_id = _interrupted_with_record(step_api, left_out=3)
    _passed_check(step_api, run_id)

    _step(step_api)

    assert _record(step_api, run_id).state == "resuming"
    [resume] = step_api[2].stage_submissions("resume")
    assert resume["confirm_left_out"] is True


def test_a_restart_between_the_submission_and_the_move_submits_once(step_api: _StepAPI) -> None:
    """The check was submitted, then the Sync API stopped before the move: the same key replays (SC-005)."""
    _client, projection, orchestration, service = step_api
    run_id = _interrupted_with_record(step_api)
    submitted = asyncio.run(service.submit_check(run_id, automatic_check_key(_record(step_api, run_id))))
    assert submitted.flow_run_id is not None
    assert _record(step_api, run_id).state == "waiting-check"

    _step(step_api)

    checking = _record(step_api, run_id)
    assert (checking.state, checking.check_flow_run_id) == ("checking", submitted.flow_run_id)
    assert len(orchestration.stage_submissions("verify")) == 1
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert [link.purpose for link in run.prefect_executions].count("verify") == 1


def test_an_unconfirmed_submission_is_tried_again_with_the_same_key(step_api: _StepAPI) -> None:
    """Prefect accepted the check but the answer was lost: the record waits, and the next loop replays."""
    _client, projection, orchestration, _service = step_api
    run_id = _interrupted_with_record(step_api)
    orchestration.fail_after_accept_once = True

    _step(step_api)
    assert _record(step_api, run_id).state == "waiting-check"

    _step(step_api)
    checking = _record(step_api, run_id)
    assert checking.state == "checking"
    assert len({key for parameters, key in orchestration.submissions if parameters["stage"] == "verify"}) == 1
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert [link.purpose for link in run.prefect_executions].count("verify") == 1


def test_two_reconcilers_stepping_the_same_record_submit_once(
    step_api: _StepAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second Sync API process read the record before the first moved it: it submits nothing (FR-014)."""
    projection, orchestration = step_api[1], step_api[2]
    run_id = _interrupted_with_record(step_api)
    stale = projection.open_automatic_resumes()
    first, second = _reconciler(step_api), _reconciler(step_api)

    asyncio.run(first.reconcile_once())
    monkeypatch.setattr(projection, "open_automatic_resumes", lambda: stale)
    asyncio.run(second.reconcile_once())

    checking = _record(step_api, run_id)
    assert checking.state == "checking"
    assert len(orchestration.stage_submissions("verify")) == 1


def test_reconcile_run_never_submits(step_api: _StepAPI) -> None:
    """Rendering a run refreshes its executions but never steps a record (critique E4)."""
    orchestration = step_api[2]
    run_id = _interrupted_with_record(step_api)

    asyncio.run(_reconciler(step_api).reconcile_run(run_id))

    assert _record(step_api, run_id).state == "waiting-check"
    assert orchestration.stage_submissions("verify") == []


def test_a_reconciler_without_a_submitter_submits_nothing(step_api: _StepAPI) -> None:
    _client, projection, orchestration, _service = step_api
    run_id = _interrupted_with_record(step_api)

    asyncio.run(RunLivenessReconciler(projection, orchestration, LivenessPolicy(300, 30, 5), "pool").reconcile_once())

    assert _record(step_api, run_id).state == "waiting-check"
    assert orchestration.stage_submissions("verify") == []


def test_the_reserved_actor_is_refused_for_a_run_with_no_waiting_record(step_api: _StepAPI) -> None:
    """Research R5: the reserved actor acts only on a run whose record waits for that step."""
    service = step_api[3]
    run_id = _planned_run(step_api)
    principal = Principal(actor=AUTOMATIC_RESUME_ACTOR)

    with pytest.raises(ServiceAPIError) as refused:
        asyncio.run(service.verify_run(run_id, VerifyRunRequest(), principal, "automatic-check:no-record"))

    assert (refused.value.status, refused.value.code) == (403, "forbidden")
    assert step_api[2].stage_submissions("verify") == []


@pytest.mark.parametrize("operation", ["apply", "resume"])
def test_the_reserved_actor_is_refused_for_a_step_its_record_does_not_wait_for(
    step_api: _StepAPI, operation: str
) -> None:
    """A record waiting for the check lets the reserved actor run `verify` only."""
    service = step_api[3]
    run_id = _interrupted_with_record(step_api)
    principal = Principal(actor=AUTOMATIC_RESUME_ACTOR)

    if operation == "apply":
        call = service.apply_run(
            run_id, ApplyRunRequest(expected_checksum=_PLAN_CHECKSUM, confirm_writes=True), principal, "apply-key"
        )
    else:
        call = service.resume_run(
            run_id, ResumeRunRequest(expected_checksum=_PLAN_CHECKSUM, confirm_writes=True), principal, "resume-key"
        )

    with pytest.raises(ServiceAPIError) as refused:
        asyncio.run(call)

    assert (refused.value.status, refused.value.code) == (403, "forbidden")


class _ReservedActorResolver:
    """A resolver that answers every value with the reserved actor, as a faulty provider might."""

    secret_values: tuple[str, ...] = ()

    def resolve(self, token: str) -> Resolution:  # noqa: PLR6301
        del token
        return Resolution.accepted(Principal(actor=AUTOMATIC_RESUME_ACTOR))

    def principal_of(self, actor: str) -> Principal | None:  # noqa: PLR6301
        del actor
        return None


def test_an_http_request_cannot_act_as_the_reserved_actor(step_api: _StepAPI) -> None:
    """Research R5: no request credential acts as `sync:automatic-resume`, even while a record waits."""
    _client, projection, orchestration, service = step_api
    run_id = _interrupted_with_record(step_api)
    client = TestClient(create_app(service, _ReservedActorResolver()))

    response = client.post(
        f"/runs/{run_id}/verify", headers={"Authorization": "Bearer any", "Idempotency-Key": "k"}, json={}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert orchestration.stage_submissions("verify") == []
    assert any(event.reason == "reserved-actor" for event in projection.audit_events())
