"""The decision on resuming an interrupted write without a person (spec 030, T009 and T010)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

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
from infrahub_sync.product_store.models import RunApproval
from infrahub_sync.service.events import RUN_FAILED
from infrahub_sync.service.liveness import (
    AUTOMATIC_RESUME_ACTOR,
    AUTOMATIC_RESUME_LIMIT,
    LivenessPolicy,
    RunLivenessReconciler,
    decide_automatic_resume,
)
from infrahub_sync.service.orchestration import Observation, PoolStatus, PoolWorker
from infrahub_sync.service.settings import AUTOMATIC_RESUME_ENV, REQUIRE_APPROVAL_ENV
from tests.service.test_liveness_policy import _Orchestration

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
