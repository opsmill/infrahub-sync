"""Derive a run's apply progress view and its resume history (spec 016 data-model.md).

Nothing here is stored: the view is derived from the run's executions, their saved progress
markers, the failure record the latest write execution wrote, if any, and the saved plan's
operations, in the plan's stored order. Every function is pure, so the Sync API and the `verify` report
(critique E3) compute the same uncertain window.

The view holds positions, operation counts and kinds only, never a value read from or written
to a system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.client.models import (
    ApplyProgressKindResource,
    ApplyProgressResource,
    AutomaticResumeResource,
    ResumeResource,
)
from infrahub_sync.product_store import PLAN_ARTIFACT_ID, WRITE_PURPOSES, run_apply_progress

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from infrahub_sync.product_store import AutomaticResume, MutationReceipt, PrefectExecutionLink, ProductRun

# The actor of the check and the resume that Sync submits by itself; never an account (research R5).
# The account name middleware shows it as `Sync (automatic resume)` in every answer.
AUTOMATIC_RESUME_ACTOR = "sync:automatic-resume"
# How many commits after the saved position may have reached the destination when the failing
# operation is unknown and the last save succeeded: the worker saves at least every 500 commits
# (R3). Recorded deletes are never dispatched, so they are not commits and are not counted.
UNCERTAIN_AFTER_SUCCESSFUL_SAVE = 500
# The error class the engine raises when the apply stopped because its progress could not be saved.
_PROGRESS_LOST_ERROR = "ApplyProgressLostError"


def uncertain_window(
    position: int | None,
    writes: Sequence[bool],
    *,
    failed_index: int | None,
    last_save_failed: bool,
) -> range:
    """Return the positions of the operations that may or may not have reached the destination.

    `writes` holds one entry per operation of the saved plan, in stored order: true for a create
    or an update, which the apply dispatches, false for a recorded delete, which it never does.

    The window starts after the saved `position` (at the first operation when nothing was
    saved). It ends with the failing operation when it is known. Otherwise, when the last save
    attempt succeeded, it runs through the 500th create or update after the start, because the
    worker saves after 500 commits and a recorded delete is not a commit; the recorded deletes
    between them are inside the window. When the last save attempt is not known to have
    succeeded, the window holds every remaining operation (critique E3, FR-005, research R11).
    """
    total = len(writes)
    start = min(total, 0 if position is None else position + 1)
    if failed_index is not None and failed_index >= start:
        end = min(total, failed_index + 1)
    elif last_save_failed:
        end = total
    else:
        end, commits = start, 0
        while end < total and commits < UNCERTAIN_AFTER_SUCCESSFUL_SAVE:
            if writes[end]:
                commits += 1
            end += 1
    return range(start, end)


def latest_write_execution(run: ProductRun) -> PrefectExecutionLink | None:
    """The run's latest write execution, in the run's order of executions."""
    return next((link for link in reversed(run.prefect_executions) if link.purpose in WRITE_PURPOSES), None)


def verify_passed_after_latest_write(run: ProductRun) -> bool:
    """Whether the run's latest `verify` comes after its latest write execution and succeeded.

    The same rule the store applies when it admits a resume (`verify-required`): executions
    are compared by their position in the run, never by time (FR-012).
    """
    executions = run.prefect_executions
    verify = next(
        (index for index in range(len(executions) - 1, -1, -1) if executions[index].purpose == "verify"), None
    )
    if verify is None:
        return False
    link = executions[verify]
    if (link.terminal_state, link.terminal_outcome) != ("completed", "succeeded"):
        return False
    return all(index < verify for index, other in enumerate(executions) if other.purpose in WRITE_PURPOSES)


def run_has_saved_plan(run: ProductRun) -> bool:
    """Whether the run's plan review document is published, the store's `_plan_published` rule.

    The plan stage and a `sync` publish it before any write, so a run without it wrote nothing.
    """
    return any(reference.artifact_id == PLAN_ARTIFACT_ID for reference in run.artifact_refs)


def run_is_resumable(run: ProductRun) -> bool:
    """The store's resumable-run rule: an interrupted write, not cancelled, nothing unfinished, a saved plan.

    A `sync` whose worker was ended while it was still planning requires reconciliation but
    saved no plan, so it is not resumable (research R14).
    """
    return (
        run.reconciliation_required
        and run.phase != "cancelled"
        and all(link.terminal_at is not None for link in run.prefect_executions)
        and run_has_saved_plan(run)
    )


def _failure_of(run: ProductRun, link: PrefectExecutionLink | None) -> Mapping[str, object] | None:
    """The failure record `link` wrote, or `None` when it wrote none.

    Results are merged across executions, so `<purpose>_failure` can belong to an earlier
    execution of the same purpose. A record counts only when its `flow_run_id` names `link`;
    a record stored before records carried that identifier never does.
    """
    if link is None:
        return None
    failure = run.results.get(f"{link.purpose}_failure")
    if not isinstance(failure, dict) or failure.get("flow_run_id") != link.flow_run_id:
        return None
    return failure


def _last_save_succeeded(latest: PrefectExecutionLink | None, failure: Mapping[str, object] | None) -> bool:
    """Whether the latest write execution's last save attempt is known to have succeeded (research R11).

    It never is when the execution saved no marker of its own. While the execution runs, its
    own marker counts as its last save. Once it ended, it counts only when the worker recorded
    its own failure, did not stop with `ApplyProgressLostError`, and the last operation the
    record lists as applied is the one the marker names, so no commit followed the last save.

    A worker that never recorded its end, because it was killed or liveness ended its execution
    first, may have kept committing for up to 5 minutes while its saves failed. The store holds
    nothing that tells this apart from saves that succeeded: `terminal_at` is the time liveness
    noticed, not the time the worker stopped, and a time cannot bound a number of commits.
    """
    if latest is None or latest.apply_progress is None:
        return False
    if latest.terminal_at is None:
        return True
    if failure is None or failure.get("error_type") == _PROGRESS_LOST_ERROR:
        return False
    applied = failure.get("applied_operations")
    return isinstance(applied, list) and bool(applied) and applied[-1] == latest.apply_progress.operation_id


def uncertain_positions(run: ProductRun, operations: Sequence[tuple[str, str]]) -> range:
    """The uncertain window of an interrupted run, over the saved plan's operations in stored order.

    `operations` are the saved plan's `(operation_id, action)` pairs. The failing operation is
    the one named by the failure record the latest write execution wrote itself.

    Whether the last save succeeded follows research R11 (`_last_save_succeeded`). When it
    is not known to have succeeded, every remaining operation is uncertain.
    """
    marker = run_apply_progress(run)
    position = None if marker is None else marker.position
    latest = latest_write_execution(run)
    failure = _failure_of(run, latest)
    failed_operation = None if failure is None else failure.get("failed_operation")
    failed_index = None
    if isinstance(failed_operation, str):
        failed_index = next(
            (index for index, (operation_id, _action) in enumerate(operations) if operation_id == failed_operation),
            None,
        )
    last_save_failed = not _last_save_succeeded(latest, failure)
    writes = [action != "delete" for _operation_id, action in operations]
    return uncertain_window(position, writes, failed_index=failed_index, last_save_failed=last_save_failed)


def _by_kind(run: ProductRun, operations: Sequence[tuple[str, str, str]]) -> tuple[ApplyProgressKindResource, ...]:
    """Count each kind's committed, uncertain and not reached operations, in the plan's kind order.

    The counts are over the plan's positions, so a recorded delete is counted where it stands:
    committed at or before the marker, uncertain inside the window `uncertain_positions`
    returns, not reached after it. The `verify` report reads the same window and lists only its
    creates and updates, because a recorded delete is never written.
    """
    marker = run_apply_progress(run)
    if marker is not None and (
        marker.position >= len(operations) or operations[marker.position][0] != marker.operation_id
    ):
        # The marker does not point into this plan, so no count derived from it would be true.
        return ()
    counts: dict[str, list[int]] = {}
    for _operation_id, kind, _action in operations:
        counts.setdefault(kind, [0, 0, 0])
    if run.phase == "applied" and not run.reconciliation_required:
        for _operation_id, kind, _action in operations:
            counts[kind][0] += 1
    else:
        committed_through = -1 if marker is None else marker.position
        uncertain = (
            uncertain_positions(run, [(operation_id, action) for operation_id, _kind, action in operations])
            if run.reconciliation_required
            else range(0)
        )
        for index, (_operation_id, kind, _action) in enumerate(operations):
            state = 0 if index <= committed_through else 1 if index in uncertain else 2
            counts[kind][state] += 1
    return tuple(
        ApplyProgressKindResource(kind=kind, committed=values[0], uncertain=values[1], not_reached=values[2])
        for kind, values in counts.items()
    )


def apply_progress_view(
    run: ProductRun, operations: Sequence[tuple[str, str, str]] | None
) -> ApplyProgressResource | None:
    """The run's progress view, or `None` for a run that never started a write.

    `operations` are the saved plan's `(operation_id, kind, action)` triples in stored order, or `None`
    when the plan cannot be read; the per-kind counts are then empty.
    """
    if latest_write_execution(run) is None:
        return None
    marker = run_apply_progress(run)
    return ApplyProgressResource(
        position=None if marker is None else marker.position,
        saved_at=None if marker is None else marker.saved_at,
        resumable=run_is_resumable(run),
        # A run with no saved plan has nothing to verify against or resume.
        verify_required=(
            run.reconciliation_required and run_has_saved_plan(run) and not verify_passed_after_latest_write(run)
        ),
        by_kind=() if operations is None else _by_kind(run, operations),
    )


def _resume_written(
    link: PrefectExecutionLink, start_after: int | None, operations: Sequence[tuple[str, str, str]] | None
) -> int | None:
    """The creates and updates one resume committed: those after `start_after`, through its own marker.

    A resume that succeeded wrote every create and update after `start_after`, whatever its
    marker says: a save can fail while the write goes on (FR-003). For any other resume the count
    ends at its own last marker, so it is a lower bound when the resume was stopped after writing
    past it; a resume that saved no marker counts 0. `None` when the plan cannot be read, or the
    marker does not point into it.
    """
    if operations is None:
        return None
    first = 0 if start_after is None else start_after + 1
    marker = link.apply_progress
    if link.terminal_outcome == "succeeded":
        end = len(operations) - 1
    elif marker is None:
        return 0
    elif marker.position >= len(operations) or operations[marker.position][0] != marker.operation_id:
        return None
    else:
        end = marker.position
    return sum(action != "delete" for _operation_id, _kind, action in operations[first : end + 1])


def run_resumes(
    run: ProductRun,
    receipt_of: Callable[[PrefectExecutionLink], MutationReceipt | None],
    operations: Sequence[tuple[str, str, str]] | None = None,
) -> tuple[ResumeResource, ...]:
    """One entry per resume execution of the run, in order (FR-014).

    The actor, time and reason come from the request that started the resume. `start_after` is
    the highest position saved by the run's write executions before the resume, the position
    the worker started after; it is `None` when none was saved. `operations` are the saved
    plan's `(operation_id, kind, action)` triples in stored order, or `None` when the plan cannot
    be read; each entry's `operations_written` is then `None` (see `_resume_written`).
    """
    resumes: list[ResumeResource] = []
    highest: int | None = None
    for link in run.prefect_executions:
        if link.purpose == "resume":
            receipt = receipt_of(link)
            requested_at = receipt.created_at if receipt is not None else link.submitted_at
            if requested_at is not None:
                resumes.append(
                    ResumeResource(
                        actor=receipt.actor if receipt is not None else run.actor or "unknown",
                        requested_at=requested_at,
                        reason=None if receipt is None else receipt.reason,
                        start_after=highest,
                        outcome=link.terminal_outcome,
                        operations_written=_resume_written(link, highest, operations),
                    )
                )
        if link.purpose in WRITE_PURPOSES and link.apply_progress is not None:
            highest = max(link.apply_progress.position, -1 if highest is None else highest)
    return tuple(resumes)


# The fixed sentences of the reasons not to resume a run automatically (spec 030 contracts/http-api.md).
# The reasons whose sentence depends on the record are built in `automatic_resume_reason_text`.
_AUTOMATIC_RESUME_REASON_TEXTS = {
    "not-resumable": (
        "`runs resume` would refuse the run: it has no saved plan, it was cancelled, "
        "or it is a scheduled run with branch review"
    ),
    "approval-missing": "the site requires approval and the run's plan has no recorded approval",
    "no-progress": "the previous automatic resume did not move the progress marker forward",
    "person-took-over": "a person ran `verify` or `runs resume` while the automatic resume waited for a live worker",
}


def automatic_resume_count(rows: Sequence[AutomaticResume]) -> int:
    """Return how many automatic resumes a run received: its records whose resume Sync submitted.

    A resume that no worker picked up counts too: its record ends ``not-picked-up`` (FR-010).
    """
    return sum(row.resume_submitted for row in rows)


def automatic_resume_cause_text(row: AutomaticResume) -> str:
    """Return the sentence that states why the liveness reconciler ended the write ``row`` names."""
    if row.cause == "rule-6":
        return "Prefect reported the flow run as crashed"
    if row.worker_name is not None:
        return f"the worker {row.worker_name} stopped sending heartbeats"
    return "the claiming worker stopped sending heartbeats"


def _differs_text(row: AutomaticResume) -> str:
    differs = (row.reason_detail or {}).get("differs", 0)
    nodes = "1 node that differs" if differs == 1 else f"{differs} nodes that differ"
    return f"the check found {nodes} from the plan"


def _check_failed_text(row: AutomaticResume) -> str:
    cause = (row.reason_detail or {}).get("cause")
    return "the automatic check failed" if cause is None else f"the automatic check failed: {cause}"


def _not_picked_up_text(row: AutomaticResume) -> str:
    # A resume that no worker picked up was submitted, so it has its flow run.
    step = "check" if row.resume_flow_run_id is None else "resume"
    return f"no worker picked up the automatic {step} in time"


def _refused_text(row: AutomaticResume) -> str:
    # The resume is submitted only after a check ran, so a refusal after a check is the resume's.
    step = "check" if row.check_flow_run_id is None else "resume"
    code = (row.reason_detail or {}).get("code")
    refused = f"the Sync API refused the automatic {step}"
    return refused if code is None else f"{refused} with the code {code}"


# The reasons whose sentence names a code or a count of the record.
_AUTOMATIC_RESUME_REASON_BUILDERS: dict[str, Callable[[AutomaticResume], str]] = {
    "check-found-differs": _differs_text,
    "check-failed": _check_failed_text,
    "not-picked-up": _not_picked_up_text,
    "refused": _refused_text,
}


def automatic_resume_reason_text(row: AutomaticResume, earlier_resumes: int = 0) -> str | None:
    """Return the sentence that states why Sync did not resume the run, or ``None`` when it did or may still.

    ``earlier_resumes`` is the number of automatic resumes the run received before ``row``, which
    the sentence of ``limit-reached`` names. The sentences hold codes and counts only.
    """
    if row.reason is None:
        return None
    if row.reason == "limit-reached":
        return f"the run already received {earlier_resumes} automatic resumes, the limit"
    build = _AUTOMATIC_RESUME_REASON_BUILDERS.get(row.reason)
    if build is not None:
        return build(row)
    return _AUTOMATIC_RESUME_REASON_TEXTS.get(row.reason, row.reason)


def automatic_resume_view(rows: Sequence[AutomaticResume]) -> tuple[AutomaticResumeResource, ...]:
    """Return the run detail's view of a run's automatic resume records, oldest first (FR-016)."""
    view = []
    earlier_resumes = 0
    for row in rows:
        view.append(
            AutomaticResumeResource(
                interrupted_flow_run_id=row.interrupted_flow_run_id,
                actor=AUTOMATIC_RESUME_ACTOR,
                cause=row.cause,
                cause_text=automatic_resume_cause_text(row),
                state=row.state,
                decision=row.decision,
                reason=row.reason,
                reason_text=automatic_resume_reason_text(row, earlier_resumes),
                operations_not_checked=row.operations_not_checked,
                start_after=row.start_after,
                check_flow_run_id=row.check_flow_run_id,
                resume_flow_run_id=row.resume_flow_run_id,
                decided_at=row.decided_at,
                updated_at=row.updated_at,
            )
        )
        earlier_resumes += row.resume_submitted
    return tuple(view)
