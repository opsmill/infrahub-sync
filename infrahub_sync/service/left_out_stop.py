"""The `sync` stop after a plan that leaves out records, and the counts an apply records.

A `sync` run plans and applies in one stage. When its plan leaves out records, the stage
stops after publishing the plan: the run ends with phase `planned`, outcome
`left-out-review-required` and a stored failure of type `left_out_review_required`, and
gives back its write admission, so the team applies the plan with
`POST /runs/{run_id}/apply` and the confirmed left-out count (spec 009 FR-012,
contracts/sync-api.md). No request field skips the stop.

The failure is built from fixed text, the plan's count, the run ID and the plan checksum only.
It holds no text of an exception (ADR 0021). Its next action names the commands that review
this run's plan and apply this run with the confirmation: the stop is not a failure to retry. It is stored under `results["failure"]`, the one place
`GET /runs/{run_id}` reads a run's cause from, as a failed stage's cause is.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from infrahub_sync.execution import ACTION_KEYS
from infrahub_sync.failure_cause import RunFailureCause
from infrahub_sync.product_store import ExecutionFinishWriteback

if TYPE_CHECKING:
    from infrahub_sync.execution import RunResult
    from infrahub_sync.plan.models import ApplyRecord, PlanManifest
    from infrahub_sync.plan.review import SavedPlan

LEFT_OUT_REVIEW_OUTCOME = "left-out-review-required"
LEFT_OUT_REVIEW_FAILURE_TYPE = "left_out_review_required"


def left_out_count(manifest: PlanManifest) -> int:
    """The number of records the plan leaves out; 0 for a plan saved before format 4."""
    return 0 if manifest.report is None else manifest.report.left_out


def applied_summary(
    sync_name: str, applied: RunResult, manifest: PlanManifest, record: ApplyRecord | None
) -> dict[str, Any]:
    """The run summary of a completed apply: the counts by action, written and left out."""
    return {
        "sync_name": sync_name,
        **{key: applied.summary[key] for key in ACTION_KEYS},
        "operations_written": 0 if record is None else len(record.applied_operations),
        "left_out": left_out_count(manifest),
    }


def left_out_next_action(run_id: str, checksum: str) -> str:
    """The next action of a stopped `sync`: review this run's plan, then apply this run (review #10)."""
    return (
        f"Review the plan with `infrahub-sync runs plan {run_id}` and the records it leaves out with "
        f"`infrahub-sync runs left-out {run_id}`, then apply this run with "
        f"`infrahub-sync apply {run_id} --expected-checksum {checksum} --confirm-left-out`."
    )


def left_out_stop(
    run_id: str, saved: SavedPlan, sync_name: str
) -> tuple[dict[str, Any], ExecutionFinishWriteback] | None:
    """The result and writeback that stop a `sync` after its plan, or `None` to go on."""
    left_out = left_out_count(saved.manifest)
    if left_out == 0:
        return None
    summary = saved.summary().model_dump(mode="json")
    failure = RunFailureCause(
        stage="sync",
        failure_type="left_out_review_required",
        summary=f"The plan leaves out {left_out} records, so the sync stopped before writing",
        next_action=left_out_next_action(run_id, saved.manifest.plan_checksum),
    ).model_dump(mode="json")
    result = {
        "run_id": run_id,
        "stage": "sync",
        "operation": "sync",
        "outcome": LEFT_OUT_REVIEW_OUTCOME,
        "summary": summary,
        "failure": failure,
    }
    writeback = ExecutionFinishWriteback(
        phase="planned",
        outcome=LEFT_OUT_REVIEW_OUTCOME,
        finished_at=datetime.now(timezone.utc),
        summary={"sync_name": sync_name, **summary},
        results=result,
        release_write_admission=True,
    )
    return result, writeback
