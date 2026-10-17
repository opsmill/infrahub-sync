"""A `sync` run whose plan leaves out records stops after the plan (spec 009 T059).

The run ends with phase `planned`, outcome `left-out-review-required` and a stored failure
of type `left_out_review_required` at stage `sync`. It wrote nothing and holds no write
admission, so the team applies it with `POST /runs/{run_id}/apply` and the confirmed count.
No request field skips the stop. A `sync` whose plan leaves out nothing applies as before.

The run is admitted through the Sync API and its execution is driven through the production
stage boundary; only the extraction, the engine's write and the live schema read are doubles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from infrahub_sync.service.flow import service_sync_run
from tests.plan.problem_fixtures import Variant
from tests.service.sync_stage_harness import OWNER_TOKEN, SyncStageHarness, build_sync_stage_harness

if TYPE_CHECKING:
    from pathlib import Path

_Harness = SyncStageHarness


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SyncStageHarness:
    return build_sync_stage_harness(monkeypatch, tmp_path)


def _left_out(harness: _Harness, run_id: str) -> int:
    plan = harness.client.get(f"/runs/{run_id}/plan", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    assert plan.status_code == 200, plan.text
    return plan.json()["summary"]["left_out"]


def test_a_sync_whose_plan_leaves_out_records_stops_after_the_plan(harness: _Harness) -> None:
    run_id = harness.run_sync()

    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert (run.phase, run.outcome) == ("planned", "left-out-review-required")
    failure = harness.get(f"/runs/{run_id}").json()["run"]["failure"]
    assert failure["failure_type"] == "left_out_review_required"
    assert failure["stage"] == "sync"
    assert run.reconciliation_required is False
    assert harness.apply_calls() == [], "The stop comes before any write."
    assert _left_out(harness, run_id) > 0


def test_the_stopped_run_is_applied_with_the_left_out_confirmation(harness: _Harness) -> None:
    run_id = harness.run_sync()
    left_out = _left_out(harness, run_id)
    checksum = harness.client.get(f"/runs/{run_id}/plan", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).json()[
        "checksum"
    ]

    refused = harness.post(f"/runs/{run_id}/apply", "apply-unconfirmed", _apply_body(checksum))
    accepted = harness.post(f"/runs/{run_id}/apply", "apply-confirmed", _apply_body(checksum, confirm_left_out=True))

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "left-out-confirmation-required"
    assert accepted.status_code == 202, accepted.text
    harness.flow_run_id[:] = [accepted.json()["orchestration"][-1]["flow_run_id"]]
    service_sync_run.fn(
        run_id,
        "apply",
        *harness.binding,
        expected_checksum=checksum,
        confirm_writes=True,
        confirm_left_out=True,
    )
    (call,) = harness.apply_calls()
    assert call["confirm_left_out"] is True
    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert run.summary["left_out"] == left_out
    assert harness.get(f"/runs/{run_id}").json()["run"]["failure"] is None, "The applied run has no cause."
    assert run.summary["operations_written"] == 0, "The engine double reports no applied record."


def _apply_body(checksum: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"expected_checksum": checksum, "confirm_writes": True, "reason": "apply after review", **extra}


@pytest.mark.parametrize(
    "extra",
    [{"confirm_left_out": True}, {"skip_left_out_review": True}, {"allow_left_out": True}],
    ids=["confirm", "skip", "allow"],
)
def test_no_request_field_skips_the_stop(harness: _Harness, extra: dict[str, Any]) -> None:
    refused = harness.admit_sync(key=f"sync-{next(iter(extra))}", **extra)

    assert refused.status_code == 422
    assert harness.orchestration.submissions == []


def test_a_sync_that_leaves_out_nothing_applies_as_today(harness: _Harness) -> None:
    harness.variant[0] = Variant.NO_PROBLEMS

    run_id = harness.run_sync()

    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert harness.get(f"/runs/{run_id}").json()["run"]["failure"] is None
    assert len(harness.apply_calls()) == 1
    assert run.summary["left_out"] == 0


def test_a_stopped_sync_records_its_finish_time(harness: _Harness) -> None:
    run_id = harness.run_sync()

    run = harness.projection.lookup_run(run_id).value
    assert run is not None
    assert run.finished_at is not None
    assert run.finished_at <= datetime.now(timezone.utc)
