"""Pure timing policy for service execution liveness."""

from __future__ import annotations

import os
from collections.abc import Callable  # noqa: TC003 - constructor default is evaluated at runtime.
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from infrahub_sync.product_store import (
    WRITE_PURPOSES,
    AuditEvent,
    AutomaticResume,
    AutomaticResumeReason,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    run_apply_progress,
)
from infrahub_sync.product_store.models import AutomaticResumeCause  # noqa: TC001 - runtime annotation.

from .events import RUN_CANCELLED, RUN_FAILED, run_event
from .orchestration import (
    Observation,
    PoolStatus,
    PoolWorker,
    ServiceOrchestration,
    normalized_pool_status,
    worker_is_live,
)
from .resume_view import run_has_saved_plan
from .settings import approval_required, automatic_resume_enabled

RUN_ADMISSION_TTL_ENV = "INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS"
# The actor of the check and the resume that Sync submits by itself; never an account (research R5).
AUTOMATIC_RESUME_ACTOR = "sync:automatic-resume"
# How many automatic resumes one run receives at most (FR-010).
AUTOMATIC_RESUME_LIMIT = 3
_POLICY_ERROR = "service liveness settings are invalid"
_TERMINAL_STATES = frozenset({"completed", "failed", "crashed", "cancelled"})
# The one Prefect state under rule 6 that means the worker disappeared (research R2).
_WORKER_LOST_STATE = "crashed"


class CancellationSelectionUnavailableError(RuntimeError):
    """Signal that Prefect could not classify a candidate without carrying provider detail."""


async def select_cancellable_execution(
    run: ProductRun,
    receipt_id: str | None,
    orchestration: ServiceOrchestration,
) -> PrefectExecutionLink | None:
    """Select an eligible link without writing product or receipt state."""
    for candidate in reversed(run.prefect_executions):
        if candidate.terminal_at is not None:
            continue
        if candidate.cancellation_requested_at is not None:
            if receipt_id is not None and candidate.cancellation_receipt_id == receipt_id:
                return candidate
            return None
        observed = await orchestration.observe(candidate.flow_run_id)
        if not observed.available:
            if observed.reason == "prefect-execution-unavailable":
                continue
            raise CancellationSelectionUnavailableError
        if observed.state not in _TERMINAL_STATES:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class LivenessPolicy:
    """Validated code-owned timing values for one service instance."""

    admission_ttl_seconds: int
    stall_threshold_seconds: float
    cadence_seconds: float

    @classmethod
    def from_environment(cls, *, worker_query_seconds: str = "10") -> LivenessPolicy:
        """Build the policy from the admission TTL and Prefect worker-query settings."""
        try:
            ttl_value = int(os.environ.get(RUN_ADMISSION_TTL_ENV, "300"))
        except ValueError:
            raise ValueError(_POLICY_ERROR) from None
        if not 1 <= ttl_value <= 86400:
            raise ValueError(_POLICY_ERROR)
        try:
            query = Decimal(worker_query_seconds)
        except InvalidOperation:
            raise ValueError(_POLICY_ERROR) from None
        if not query.is_finite() or not Decimal(0) < query <= Decimal(3600):
            raise ValueError(_POLICY_ERROR)
        threshold = max(float(query * 3), 30.0)
        return cls(ttl_value, threshold, max(0.25, min(5.0, threshold / 2)))


def age(now: datetime, anchor: datetime) -> float:
    """Return non-negative elapsed seconds; clock reversal cannot accelerate a verdict."""
    return max(0.0, (now - anchor).total_seconds())


def live_workers(pool: PoolStatus, now: datetime) -> tuple[PoolWorker, ...]:
    """Return the pool's live workers: online, with a heartbeat within ``max(3 * interval, 30)`` seconds.

    A snapshot without worker detail has no live worker (research R8, critique E3).
    """
    if not pool.detail_available:
        return ()
    return tuple(worker for worker in pool.workers if worker_is_live(worker, now))


def decide_automatic_resume(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    run: ProductRun,
    link: PrefectExecutionLink,
    cause: AutomaticResumeCause,
    worker_name: str | None,
    previous_rows: tuple[AutomaticResume, ...],
    now: datetime,
    *,
    branch_review: bool = False,
) -> AutomaticResume | None:
    """Decide whether Sync resumes the interrupted write execution ``link`` by itself (spec 030).

    Returns ``None``, so that no record and no event is written, when automatic resume is off
    (FR-004) or ``link`` is not a write (``apply``, ``sync`` or ``resume``). Otherwise returns
    the record to insert with the interruption: ``waiting-check``, or ``ended`` with decision
    ``no-resume`` and the first reason that applies, in the order of ``_no_resume_reason``.

    ``previous_rows`` are the run's earlier records. ``branch_review`` is whether the run is a
    scheduled run with branch review, which the caller reads from the store (research R11).
    """
    if not automatic_resume_enabled() or link.purpose not in WRITE_PURPOSES:
        return None
    reason = _no_resume_reason(run, link, previous_rows, branch_review=branch_review)
    return AutomaticResume(
        run_id=run.run_id,
        interrupted_flow_run_id=link.flow_run_id,
        cause=cause,
        worker_name=worker_name or None,
        state="waiting-check" if reason is None else "ended",
        decision=None if reason is None else "no-resume",
        reason=reason,
        decided_at=now,
        updated_at=now,
    )


def _no_resume_reason(
    run: ProductRun,
    link: PrefectExecutionLink,
    previous_rows: tuple[AutomaticResume, ...],
    *,
    branch_review: bool,
) -> AutomaticResumeReason | None:
    """The first reason not to resume the run automatically, or ``None`` (research R10, R11, R12).

    The order is: not resumable, approval missing, no progress, limit reached.
    """
    # The checks of `runs resume` that do not depend on the interruption being recorded yet:
    # a saved plan, not cancelled, and not a scheduled run with branch review.
    if not run_has_saved_plan(run) or run.phase == "cancelled" or branch_review:
        return "not-resumable"
    # An approval is always recorded for the saved plan's checksum (`RunService.approve_run`),
    # and a run's plan never changes, so a recorded approval matches the plan.
    if approval_required() and run.approval is None:
        return "approval-missing"
    if _automatic_resume_made_no_progress(run, link, previous_rows):
        return "no-progress"
    if sum(row.decision == "resume" for row in previous_rows) >= AUTOMATIC_RESUME_LIMIT:
        return "limit-reached"
    return None


def _automatic_resume_made_no_progress(
    run: ProductRun, link: PrefectExecutionLink, previous_rows: tuple[AutomaticResume, ...]
) -> bool:
    """Whether ``link`` is an automatic resume that ended without passing the marker it started after (FR-009).

    A resume that started after no marker made progress once any marker is saved, position 0
    included. A write that is not an automatic resume is never judged here.
    """
    previous = next(
        (row for row in previous_rows if row.decision == "resume" and row.resume_flow_run_id == link.flow_run_id),
        None,
    )
    if previous is None:
        return False
    marker = run_apply_progress(run)
    if marker is None:
        return True
    return previous.start_after is not None and marker.position <= previous.start_after


class RunLivenessReconciler:
    """Conservatively terminalize durable executions; never submit or replay work.

    ``reconcile_execution`` evaluates one link against the rules below in order.
    The first matching rule decides the link; later rules do not run for it.

    ===  ==============================================================  =========================================
    #    Condition                                                       Durable transition
    ===  ==============================================================  =========================================
    0    ``terminal_at`` is set                                          none; a terminal verdict is final
    1    intent + acknowledged + observed ``cancelled``                   ``cancelled`` / ``cancelled``
    2    intent + ``now >= cancellation_recovery_deadline_at``            claimed: ``interrupted`` / ``ambiguous``;
                                                                         unclaimed: ``abandoned`` / ``abandoned``
    3    intent, deadline not reached                                    none; await acknowledgement or deadline
    4    unclaimed + ``age(submitted_at) >= admission_ttl_seconds``       ``abandoned`` / ``abandoned``
    5    unclaimed + ``age(submitted_at) >= stall_threshold_seconds``     ``stalled_at`` marker; stays claimable
         + pool detail available
    6    claimed + Prefect observes a terminal state                     ``interrupted`` / ``ambiguous``
    7    claimed + ``age(claimed_at) < stall_threshold_seconds``          none; owner grace period
    8    claimed + pool detail available + owner not fresh               ``interrupted`` / ``ambiguous``
    ===  ==============================================================  =========================================

    "intent" is a non-null ``cancellation_requested_at``. Rules 1-3 outrank 4-8,
    so within reconciliation intent is the only authority over a link that
    carries it: rules 4-8 never act on an intent-owned link. Rules 5 and 8 need
    available pool detail; an unavailable snapshot makes no transition. An owner
    is fresh when the pool reports the exact claiming worker UUID ``online``
    with a heartbeat inside ``max(3 * interval, 30)`` seconds.

    There are five legal ``(terminal_state, terminal_outcome)`` verdicts.
    ``(completed, succeeded)`` and ``(failed, failed)`` are written only by the
    claiming worker. ``(cancelled, cancelled)`` is written only by
    reconciliation rule 1. ``(abandoned, abandoned)`` and
    ``(interrupted, ambiguous)`` may be written either by reconciliation or by
    request-time cancellation recovery in ``RunService.cancel_run``, at
    the same inclusive deadline as rule 2. Those competing reconciliation and
    request-time paths use the durable compare-and-set, so only one of them
    commits a verdict. No writer submits or resubmits work: an execution is
    never replayed after any verdict.
    """

    def __init__(
        self,
        projection: ProductProjection,
        orchestration: ServiceOrchestration,
        policy: LivenessPolicy,
        work_pool_name: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._projection = projection
        self._orchestration = orchestration
        self._policy = policy
        self._work_pool_name = work_pool_name
        self._clock = clock

    @property
    def cadence_seconds(self) -> float:
        """Return the bounded background-loop cadence."""
        return self._policy.cadence_seconds

    async def reconcile_once(self) -> None:
        """Apply one bounded snapshot to every non-terminal execution."""
        now = self._clock()
        pool = await self._orchestration.pool_status(self._work_pool_name, now)
        for run_id, link in self._projection.pending_executions():
            await self.reconcile_execution(run_id, link, pool, now)

    async def reconcile_run(self, run_id: str) -> dict[str, Observation]:
        """Refresh every pending link of one requested run before it is rendered.

        Returns the observation taken for each refreshed link, keyed by flow-run ID, so
        the caller that renders the run does not have to observe it a second time.
        """
        run = self._projection.lookup_run(run_id).value
        if run is None:
            return {}
        now = self._clock()
        pool = await self._orchestration.pool_status(self._work_pool_name, now)
        observations: dict[str, Observation] = {}
        for link in run.prefect_executions:
            if link.terminal_at is None:
                observations[link.flow_run_id] = await self.reconcile_execution(run_id, link, pool, now)
        return observations

    async def reconcile_execution(  # noqa: PLR0911  # pylint: disable=too-many-return-statements
        self, run_id: str, link: PrefectExecutionLink, pool: PoolStatus | None = None, now: datetime | None = None
    ) -> Observation:
        """Reconcile one link, suitable for request-time freshness before rendering.

        Returns the single remote observation this call took, on every path, so a caller
        rendering the same link can reuse it rather than observe the link again.
        """
        now = now or self._clock()
        pool = normalized_pool_status(pool or await self._orchestration.pool_status(self._work_pool_name, now))
        observed = await self._orchestration.observe(link.flow_run_id)
        if observed.available:
            self._projection.observe_prefect_execution(run_id, link.flow_run_id, state=observed.state)
        refreshed_run = self._projection.lookup_run(run_id).value
        if refreshed_run is None:
            return observed
        refreshed = next(
            (candidate for candidate in refreshed_run.prefect_executions if candidate.flow_run_id == link.flow_run_id),
            None,
        )
        if refreshed is None or refreshed.terminal_at is not None:
            return observed
        link = refreshed
        if link.cancellation_requested_at is not None:
            if (
                link.cancellation_acknowledged_at is not None
                and link.last_observed_state == "cancelled"
                and self._projection.cancel_execution(
                    run_id,
                    link.flow_run_id,
                    terminal_at=now,
                    event=self._cancelled_event(refreshed_run, link.flow_run_id, now),
                )
            ):
                return observed
            assert link.cancellation_recovery_deadline_at is not None
            if now >= link.cancellation_recovery_deadline_at:
                self._projection.expire_execution_cancellation(run_id, link.flow_run_id, terminal_at=now)
            return observed
        if link.claimed_at is None:
            if link.submitted_at is not None and age(now, link.submitted_at) >= self._policy.admission_ttl_seconds:
                self._projection.abandon_execution(run_id, link.flow_run_id, terminal_at=now)
                return observed
            if (
                link.submitted_at is not None
                and age(now, link.submitted_at) >= self._policy.stall_threshold_seconds
                and pool.detail_available
            ):
                self._projection.mark_execution_stalled(run_id, link.flow_run_id, stalled_at=now)
            return observed
        if observed.available and observed.state in _TERMINAL_STATES:
            # Only a crashed flow run means the worker disappeared; a failure the worker raised,
            # a cancellation in Prefect and a completion get no automatic resume (research R2).
            self._interrupt(refreshed_run, link, now, cause="rule-6" if observed.state == _WORKER_LOST_STATE else None)
            return observed
        if link.claimed_at is None or age(now, link.claimed_at) < self._policy.stall_threshold_seconds:
            return observed
        if pool.detail_available and not _owner_is_fresh(link.claiming_worker_id, pool, now):
            self._interrupt(
                refreshed_run, link, now, cause="rule-8", worker_name=_worker_name(link.claiming_worker_id, pool)
            )
        return observed

    def _interrupt(
        self,
        run: ProductRun,
        link: PrefectExecutionLink,
        now: datetime,
        *,
        cause: AutomaticResumeCause | None,
        worker_name: str | None = None,
    ) -> None:
        """End ``link`` as ``interrupted``, with the automatic resume decision when ``cause`` is given.

        The decision record is written in the same transaction as the interruption (research R1).
        """
        automatic_resume = None
        if cause is not None and link.purpose in WRITE_PURPOSES and automatic_resume_enabled():
            branch_review = (
                run.trigger_type == "scheduled" and self._projection.lookup_schedule_branch(run.run_id).available
            )
            automatic_resume = decide_automatic_resume(
                run,
                link,
                cause,
                worker_name,
                self._projection.automatic_resumes_of(run.run_id),
                now,
                branch_review=branch_review,
            )
        self._projection.interrupt_execution(
            run.run_id,
            link.flow_run_id,
            terminal_at=now,
            event=self._interrupted_event(run, link, now),
            automatic_resume=automatic_resume,
        )

    def _interrupted_event(self, run: ProductRun, link: PrefectExecutionLink, now: datetime) -> AuditEvent:
        """Build the `run.failed` event of an interrupted stage, outcome `ambiguous` (data-model.md).

        The actor and reason are those of the request whose receipt holds this execution, else
        the run's actor, as for the worker's own stage result.
        """
        request = self._projection.lookup_mutation_for_execution(run.run_id, link.flow_run_id, link.purpose).value
        actor = request.actor if request is not None else run.actor
        return run_event(
            run.run_id,
            run,
            event_type=RUN_FAILED,
            actor=actor or "unknown",
            operation=link.purpose,
            reason=None if request is None else request.reason,
            outcome="ambiguous",
            created_at=now,
        )

    def _cancelled_event(self, run: ProductRun, flow_run_id: str, now: datetime) -> AuditEvent:
        """Build the `run.cancelled` event: the actor and reason of the cancel request, else the run's actor."""
        request = self._projection.lookup_mutation_for_execution(run.run_id, flow_run_id, "cancel").value
        actor = request.actor if request is not None else run.actor
        return run_event(
            run.run_id,
            run,
            event_type=RUN_CANCELLED,
            actor=actor or "unknown",
            operation="cancel",
            reason=None if request is None else request.reason,
            outcome="cancelled",
            created_at=now,
        )


def _owner_is_fresh(worker_id: str | None, pool: PoolStatus, now: datetime) -> bool:
    """Only the exact claiming UUID can keep a claimed execution alive."""
    if worker_id is None:
        return False
    for worker in pool.workers:
        if worker.worker_id != worker_id or worker.status != "online":
            continue
        return worker_is_live(worker, now)
    return False


def _worker_name(worker_id: str | None, pool: PoolStatus) -> str | None:
    """The name the pool snapshot gives the claiming worker, or ``None`` when it has none."""
    if worker_id is None:
        return None
    return next((worker.name for worker in pool.workers if worker.worker_id == worker_id), None)
