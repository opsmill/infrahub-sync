"""Derive a run's apply progress view and its resume history (spec 022 data-model.md).

Nothing here is stored: the view is derived from the run's executions, their saved progress
markers, the failure record the latest write execution wrote, if any, and the saved plan's
operations, in the plan's stored order. Every function is pure, so the Sync API and the `verify` report
(critique E3) compute the same uncertain window.

The view holds positions, operation counts and kinds only, never a value read from or written
to a system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.client.models import ApplyProgressKindResource, ApplyProgressResource, ResumeResource
from infrahub_sync.product_store import WRITE_PURPOSES, run_apply_progress

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from infrahub_sync.product_store import MutationReceipt, PrefectExecutionLink, ProductRun

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


def run_is_resumable(run: ProductRun) -> bool:
    """The store's resumable-run rule: an interrupted write, not cancelled, nothing unfinished."""
    return (
        run.reconciliation_required
        and run.phase != "cancelled"
        and all(link.terminal_at is not None for link in run.prefect_executions)
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
        verify_required=run.reconciliation_required and not verify_passed_after_latest_write(run),
        by_kind=() if operations is None else _by_kind(run, operations),
    )


def run_resumes(
    run: ProductRun, receipt_of: Callable[[PrefectExecutionLink], MutationReceipt | None]
) -> tuple[ResumeResource, ...]:
    """One entry per resume execution of the run, in order (FR-014).

    The actor, time and reason come from the request that started the resume. `start_after` is
    the highest position saved by the run's write executions before the resume, the position
    the worker started after; it is `None` when none was saved.
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
                    )
                )
        if link.purpose in WRITE_PURPOSES and link.apply_progress is not None:
            highest = max(link.apply_progress.position, -1 if highest is None else highest)
    return tuple(resumes)
