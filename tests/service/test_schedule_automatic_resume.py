"""How an automatic resume counts toward a schedule's failed runs in a row (spec 030, user story 3, FR-018).

Each test starts a scheduled `sync` run on the default branch, after 2 failed runs in a row,
lets the worker save its plan and a progress marker, and then stops the worker's heartbeats so
that the liveness reconciler ends the `sync` under rule 8. The reconciler then steps the
automatic resume record with the real `RunService` as its submitter, and each test ends the
submitted executions as a worker does.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import (
    ApplyProgress,
    ProductRun,
    ScheduledStartRun,
    ScheduleRecord,
    local_product_projection,
)
from infrahub_sync.product_store.models import ExecutionFinishWriteback
from infrahub_sync.service.app import create_app
from infrahub_sync.service.liveness import automatic_resume_key
from infrahub_sync.service.service import RunService
from infrahub_sync.service.settings import AUTOMATIC_RESUME_ENV, REQUIRE_APPROVAL_ENV
from tests.configuration.validation_packages import DESTINATION_CREDENTIAL, SOURCE_CREDENTIAL
from tests.helpers.stored_credentials import create_stored_credential_names
from tests.service.accounts import seed_accounts
from tests.service.test_automatic_resume import (
    _STEP_MARKER,
    _STEP_TOTAL,
    _STEP_WORKER_ID,
    _finish,
    _LiveOrchestration,
    _passed_check,
    _record,
    _resumed_by_sync,
    _rule_8,
    _step,
    _StepAPI,
    _verification,
)
from tests.service.test_http_api import ADMIN_TOKEN, OWNER_TOKEN, _registered_package
from tests.service.test_resume_run import _headers, _publish_plan

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

_FAILED_RUNS_BEFORE = 2


@pytest.fixture(autouse=True)
def _automatic_resume_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTOMATIC_RESUME_ENV, "true")
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)


@pytest.fixture
def api(tmp_path: Path) -> _StepAPI:
    """A Sync API whose orchestration reports a live worker unless a test stops its heartbeats."""
    projection = local_product_projection(tmp_path.resolve())
    resolver = seed_accounts(
        projection,
        {"owner": {"token": OWNER_TOKEN}, "admin": {"token": ADMIN_TOKEN, "administrator": True}},
        environment_token=True,
    ).resolver
    orchestration = _LiveOrchestration()
    create_stored_credential_names(projection, SOURCE_CREDENTIAL, DESTINATION_CREDENTIAL)
    version = projection.create_configuration(_registered_package())
    service = RunService(projection, orchestration, secrets=resolver.secret_values)
    client = TestClient(create_app(service, resolver))
    client.app.state.run_binding = version
    return client, projection, orchestration, service


def _scheduled_sync(api: _StepAPI, *, branch_review: bool = False) -> tuple[str, str]:
    """Admit one start of a schedule with 2 failed runs in a row; its `sync` saves a plan and a marker.

    Returns the schedule and the run.
    """
    client, projection, _orchestration, _service = api
    version = client.app.state.run_binding
    owner = projection.lookup_account_by_name("owner")
    assert owner is not None
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    schedule_id = f"s-{uuid4().hex}"
    projection.create_schedule(
        ScheduleRecord.model_validate(
            {
                "schedule_id": schedule_id,
                "name": f"hourly-{schedule_id[2:10]}",
                "config_id": version.config_id,
                "registry_version": version.registry_version,
                "cron": "0 * * * *",
                "timezone": "UTC",
                "write_target": "branch-review" if branch_review else "default-branch",
                "state": "active",
                "owner": owner.account_id,
                "consecutive_failures": _FAILED_RUNS_BEFORE,
                "created_at": start - timedelta(days=1),
                "updated_at": start - timedelta(days=1),
            }
        )
    )
    run = ProductRun.model_validate(
        {
            "run_id": f"run-{uuid4().hex[:12]}",
            "operation": "sync",
            "configuration_reference": f"{version.config_id}@{version.registry_version}",
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "package_checksum": version.package_checksum,
            "actor": owner.account_id,
            "started_at": now,
            "phase": "accepted",
            "trigger_type": "scheduled",
            "schedule_id": schedule_id,
            "scheduled_start_at": start,
            **({"destination_branch": f"sync-{schedule_id[2:10]}"} if branch_review else {}),
        }
    )
    flow_run_id = str(uuid4())
    result = projection.admit_scheduled_start(
        schedule_id,
        start,
        flow_run_id,
        _STEP_WORKER_ID,
        now,
        ScheduledStartRun(
            run=run,
            branch_review=branch_review,
            events=(),
            kept_run_ids=(),
            last_start_at=None,
            waiting_start_at=None,
        ),
    )
    assert result.run_id == run.run_id
    _publish_plan(projection, run.run_id, total=_STEP_TOTAL)
    marker = ApplyProgress(
        position=_STEP_MARKER, operation_id=f"op_{_STEP_MARKER:016x}", total=_STEP_TOTAL, saved_at=now
    )
    assert projection.record_apply_progress(run.run_id, flow_run_id, marker) == "saved"
    return schedule_id, run.run_id


def _failures(projection: ProductProjection, schedule_id: str) -> int:
    stored = projection.lookup_schedule(schedule_id).value
    assert stored is not None
    return stored.consecutive_failures


def _end(projection: ProductProjection, run_id: str, flow_run_id: str, *, applied: bool) -> None:
    """End the claimed automatic resume as its worker does: applied, or with a failure it reports."""
    at = datetime.now(timezone.utc)
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_STEP_WORKER_ID,
        terminal_at=at,
        terminal_state="completed" if applied else "failed",
        terminal_outcome="succeeded" if applied else "failed",
        writeback=ExecutionFinishWriteback(
            phase="applied" if applied else "failed",
            outcome="applied" if applied else "failed",
            finished_at=at,
            summary={},
            results={},
        ),
    )


def _rows(api: _StepAPI, run_id: str) -> list[tuple[str, str | None, str | None]]:
    return [(row.state, row.decision, row.reason) for row in api[1].automatic_resumes_of(run_id)]


def test_an_automatic_resume_that_ends_applied_sets_the_count_to_zero(api: _StepAPI) -> None:
    """Scenario 1: the interrupted run counts once, as succeeded, when its automatic resume applies."""
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)
    while_waiting = _failures(projection, schedule_id)

    resume = _resumed_by_sync(api, run_id, progress=True)
    while_resuming = _failures(projection, schedule_id)
    _end(projection, run_id, resume, applied=True)
    _step(api)

    assert (while_waiting, while_resuming, _failures(projection, schedule_id)) == (2, 2, 0)
    assert _rows(api, run_id) == [("ended", "resume", None)]


def _check_found_differs(api: _StepAPI, run_id: str) -> None:
    _step(api)
    _finish(api[1], run_id, _record(api, run_id).check_flow_run_id, results=_verification("differs", "matches"))
    _step(api)


def _person_took_over(api: _StepAPI, run_id: str) -> None:
    _passed_check(api, run_id)
    response = api[0].post(f"/runs/{run_id}/verify", headers=_headers("person-verify"), json={})
    assert response.status_code == 202, response.json()


def _resume_not_picked_up(api: _StepAPI, run_id: str) -> None:
    """No worker picks up the automatic resume: its abandoned execution and the record's end count once."""
    _passed_check(api, run_id)
    _step(api)
    resume = _record(api, run_id).resume_flow_run_id
    assert resume is not None
    assert api[1].abandon_execution(run_id, resume)
    _step(api)


@pytest.mark.parametrize(
    ("ends", "reason"),
    [
        pytest.param(_check_found_differs, "check-found-differs", id="check-found-differs"),
        pytest.param(_person_took_over, "person-took-over", id="person-took-over"),
        pytest.param(_resume_not_picked_up, "not-picked-up", id="resume-not-picked-up"),
    ],
)
def test_a_record_that_ends_without_a_resume_adds_one_failed_run(
    api: _StepAPI, ends: Callable[[_StepAPI, str], None], reason: str
) -> None:
    """Scenario 4: a run left interrupted after an automatic resume did not start counts as one failed run."""
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)

    ends(api, run_id)

    assert _rows(api, run_id) == [("ended", "no-resume", reason)]
    assert _failures(projection, schedule_id) == _FAILED_RUNS_BEFORE + 1


def test_an_automatic_resume_that_fails_adds_one_failed_run(api: _StepAPI) -> None:
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)
    resume = _resumed_by_sync(api, run_id, progress=True)

    _end(projection, run_id, resume, applied=False)
    _step(api)

    assert _rows(api, run_id) == [("ended", "resume", None)]
    assert _failures(projection, schedule_id) == _FAILED_RUNS_BEFORE + 1


def test_an_interrupted_automatic_resume_counts_once_when_the_next_one_ends(api: _StepAPI) -> None:
    """The second record waits: the count does not change until the second automatic resume applies."""
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)
    _resumed_by_sync(api, run_id, progress=True)

    _rule_8(api)
    _step(api)
    after_second_interruption = _failures(projection, schedule_id)
    second = _resumed_by_sync(api, run_id, progress=True)
    _end(projection, run_id, second, applied=True)
    _step(api)

    assert after_second_interruption == _FAILED_RUNS_BEFORE
    assert _rows(api, run_id) == [("ended", "resume", None), ("ended", "resume", None)]
    assert _failures(projection, schedule_id) == 0


def test_a_branch_review_run_gets_not_resumable_and_counts_as_failed(api: _StepAPI) -> None:
    """Scenario 3: spec 021 makes a run with branch review not resumable, so no automatic resume starts."""
    _client, projection, orchestration, _service = api
    schedule_id, run_id = _scheduled_sync(api, branch_review=True)

    _rule_8(api)
    _step(api)

    assert _rows(api, run_id) == [("ended", "no-resume", "not-resumable")]
    assert orchestration.stage_submissions("verify") == []
    assert _failures(projection, schedule_id) == _FAILED_RUNS_BEFORE + 1


def _resume_submitted_before_the_move(api: _StepAPI, run_id: str) -> str:
    """The reconciler submitted the automatic resume, and the Sync API stopped before the move to `resuming`.

    A worker claims the resume. Returns its flow run; the record still waits for the resume.
    """
    projection, service = api[1], api[3]
    _passed_check(api, run_id)
    submitted = asyncio.run(service.submit_resume(run_id, automatic_resume_key(_record(api, run_id))))
    assert submitted.flow_run_id is not None
    assert _record(api, run_id).state == "waiting-resume"
    assert projection.claim_execution(run_id, submitted.flow_run_id, worker_id=_STEP_WORKER_ID)
    return submitted.flow_run_id


@pytest.mark.parametrize(("applied", "failures"), [(True, 0), (False, _FAILED_RUNS_BEFORE + 1)])
def test_an_automatic_resume_that_ends_before_the_move_counts_once(
    api: _StepAPI,
    applied: bool,  # noqa: FBT001 - one parametrized dimension.
    failures: int,
) -> None:
    """The resume ends before the replayed submission records it: the move counts the run once (research R11)."""
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)
    resume = _resume_submitted_before_the_move(api, run_id)
    _end(projection, run_id, resume, applied=applied)
    before_the_move = _failures(projection, schedule_id)

    _step(api)
    _step(api)

    assert before_the_move == _FAILED_RUNS_BEFORE
    assert _rows(api, run_id) == [("ended", "resume", None)]
    assert _failures(projection, schedule_id) == failures


def test_an_automatic_resume_interrupted_before_the_move_counts_once_when_the_next_record_ends(
    api: _StepAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume is interrupted before the move and the next record ends at once: the move counts one failed run."""
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)
    _resume_submitted_before_the_move(api, run_id)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    _rule_8(api)
    before_the_move = _failures(projection, schedule_id)

    _step(api)
    _step(api)

    assert before_the_move == _FAILED_RUNS_BEFORE
    assert _rows(api, run_id) == [("ended", "resume", None), ("ended", "no-resume", "approval-missing")]
    assert _failures(projection, schedule_id) == _FAILED_RUNS_BEFORE + 1


def test_an_automatic_resume_interrupted_before_the_move_waits_for_the_next_record(api: _StepAPI) -> None:
    """The next record waits for its check: the move counts nothing, and the next automatic resume counts the run."""
    projection = api[1]
    schedule_id, run_id = _scheduled_sync(api)
    _rule_8(api)
    first = _resume_submitted_before_the_move(api, run_id)
    position = _STEP_MARKER + 5
    marker = ApplyProgress(
        position=position,
        operation_id=f"op_{position:016x}",
        total=_STEP_TOTAL,
        start_after=_STEP_MARKER,
        saved_at=datetime.now(timezone.utc),
    )
    assert projection.record_apply_progress(run_id, first, marker) == "saved"
    _rule_8(api)

    _step(api)
    after_the_move = _failures(projection, schedule_id)
    _step(api)
    second = _resumed_by_sync(api, run_id, progress=True)
    _end(projection, run_id, second, applied=True)
    _step(api)

    assert after_the_move == _FAILED_RUNS_BEFORE
    assert _rows(api, run_id) == [("ended", "resume", None), ("ended", "resume", None)]
    assert _failures(projection, schedule_id) == 0
