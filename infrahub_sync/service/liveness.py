"""Pure timing policy for service execution liveness."""

from __future__ import annotations

import os
from collections.abc import Callable  # noqa: TC003 - constructor default is evaluated at runtime.
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import structlog

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
from infrahub_sync.product_store.models import (  # noqa: TC001 - runtime annotations.
    AutomaticResumeCause,
    AutomaticResumeState,
)

from .events import RUN_CANCELLED, RUN_FAILED, automatic_resume_event, run_event
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
# Refusal codes of a submission that a later loop tries again with the same idempotency key:
# Prefect did not confirm the submission, and the receipt stays reserved for the next try.
_RETRIED_REFUSALS = frozenset({"orchestration-unavailable"})

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """What one automatic submission gave: the flow run identifier, or the code of the refusal."""

    flow_run_id: str | None = None
    refusal: str | None = None

    def __post_init__(self) -> None:
        """Refuse an outcome that is both, or neither, a submission and a refusal."""
        if (self.flow_run_id is None) == (self.refusal is None):
            msg = "a submission outcome holds a flow run identifier or a refusal code, and only one of them"
            raise ValueError(msg)


class AutomaticResumeSubmitter(Protocol):
    """Submits the automatic check and the automatic resume of a run (research R3, critique E2).

    ``RunService`` implements it. The protocol lives here because ``service.py`` imports this
    module, so the liveness reconciler cannot import ``RunService`` back.
    """

    async def submit_check(self, run_id: str, idempotency_key: str) -> SubmissionOutcome:
        """Submit the automatic check (a `verify`) of the run with this idempotency key."""
        ...

    async def submit_resume(self, run_id: str, idempotency_key: str) -> SubmissionOutcome:
        """Submit the automatic resume of the run with this idempotency key."""
        ...


def automatic_check_key(row: AutomaticResume) -> str:
    """The idempotency key of the automatic check that ``row`` decides (research R4)."""
    return f"automatic-check:{row.run_id}:{row.interrupted_flow_run_id}"


def automatic_resume_key(row: AutomaticResume) -> str:
    """The idempotency key of the automatic resume that ``row`` decides (research R4)."""
    return f"automatic-resume:{row.run_id}:{row.interrupted_flow_run_id}"


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
    """Conservatively terminalize durable executions; replay work only for an automatic resume (ADR 0056).

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
    commits a verdict. No rule submits or resubmits work: an execution is
    never replayed, except an automatic resume after rule 6 (Prefect reports
    the flow run as ``crashed``) or rule 8 when the site turned on
    ``INFRAHUB_SYNC_AUTOMATIC_RESUME`` (ADR 0056, which amends ADR 0027). For
    that case, the interruption records an automatic resume record, and after
    the rules ``reconcile_once`` steps the records through
    ``step_automatic_resumes``. It submits a new `verify` and then a new resume
    through ``submitter``, never the ended execution itself. A rule 6 ending in
    any other Prefect state, and every other rule, writes no automatic resume record.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        projection: ProductProjection,
        orchestration: ServiceOrchestration,
        policy: LivenessPolicy,
        work_pool_name: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        submitter: AutomaticResumeSubmitter | None = None,
    ) -> None:
        self._projection = projection
        self._orchestration = orchestration
        self._policy = policy
        self._work_pool_name = work_pool_name
        self._clock = clock
        # Without a submitter, the automatic resume records wait and nothing is submitted.
        self._submitter = submitter

    @property
    def cadence_seconds(self) -> float:
        """Return the bounded background-loop cadence."""
        return self._policy.cadence_seconds

    async def reconcile_once(self) -> None:
        """Apply one bounded snapshot to every non-terminal execution, then step the automatic resumes."""
        now = self._clock()
        pool = await self._orchestration.pool_status(self._work_pool_name, now)
        for run_id, link in self._projection.pending_executions():
            await self.reconcile_execution(run_id, link, pool, now)
        # Only the background loop steps the records: a request that renders a run
        # (`reconcile_run`) never submits a flow run (critique E4).
        await self.step_automatic_resumes(pool, now)

    async def step_automatic_resumes(self, pool: PoolStatus, now: datetime) -> None:
        """Move every automatic resume record that is not ``ended`` one step, when it can move (research R3).

        A record that waits for the check or for the resume moves only when ``pool`` has a live
        worker (research R8, FR-012). The submission comes first, with the record's
        idempotency key, and the compare-and-set to ``checking`` or ``resuming`` second, with
        the flow run identifier: a restart or another Sync API process between the two repeats
        the submission with the same key, which replays the same receipt (FR-014).
        """
        rows = self._projection.open_automatic_resumes()
        if not rows:
            return
        live = bool(live_workers(normalized_pool_status(pool), now))
        for row in rows:
            if row.state == "waiting-check":
                if live and self._submitter is not None:
                    outcome = await self._submitter.submit_check(row.run_id, automatic_check_key(row))
                    self._after_submission(row, outcome, "checking", now, check_flow_run_id=outcome.flow_run_id)
            elif row.state == "checking":
                self._step_checking(row, now)
            elif row.state == "waiting-resume":
                if live and self._submitter is not None:
                    # Read before the submission, so the marker is the one the resume starts after.
                    start_after = self._start_after(row.run_id)
                    outcome = await self._submitter.submit_resume(row.run_id, automatic_resume_key(row))
                    self._after_submission(
                        row,
                        outcome,
                        "resuming",
                        now,
                        decision="resume",
                        start_after=start_after,
                        resume_flow_run_id=outcome.flow_run_id,
                    )
            elif row.state == "resuming":
                self._step_resuming(row, now)

    def _after_submission(
        self,
        row: AutomaticResume,
        outcome: SubmissionOutcome,
        to_state: AutomaticResumeState,
        now: datetime,
        **fields: object,
    ) -> None:
        """Record a submission: the move to ``to_state``, or the end of the record as ``refused``.

        When the submission was accepted but the move fails because a person took over the
        record in between (FR-019), which only another Sync API process can do (see
        ``RunService._take_over_automatic_resume``), the automatic execution runs anyway: it
        is logged with its flow run, and the run's executions and receipts show it with the
        reserved actor. A record that another reconciler moved first is not logged.
        """
        if outcome.flow_run_id is not None:
            if not self._move(row, to_state, now, **fields):
                self._log_lost_take_over(row, outcome.flow_run_id)
            return
        if outcome.refusal in _RETRIED_REFUSALS:
            logger.info(
                "automatic resume submission not confirmed, next loop tries again",
                run_id=row.run_id,
                state=row.state,
                reason=outcome.refusal,
            )
            return
        self._end(row, "refused", now, reason_detail={"code": str(outcome.refusal)})

    def _log_lost_take_over(self, row: AutomaticResume, flow_run_id: str) -> None:
        """Log an automatic execution submitted after a person ended ``row`` as ``person-took-over``."""
        current = next(
            (
                stored
                for stored in self._projection.automatic_resumes_of(row.run_id)
                if stored.interrupted_flow_run_id == row.interrupted_flow_run_id
            ),
            None,
        )
        if current is None or current.reason != "person-took-over":
            return
        logger.warning(
            "automatic resume submitted after a person took over",
            run_id=row.run_id,
            state=row.state,
            flow_run_id=flow_run_id,
        )

    def _step_checking(self, row: AutomaticResume, now: datetime) -> None:
        """End the check step once the check's execution has a verdict (research R7, FR-006, FR-007)."""
        run, link = self._execution(row.run_id, row.check_flow_run_id)
        if run is None or link is None:
            self._end(row, "check-failed", now, reason_detail={"cause": "execution-not-found"})
            return
        if link.terminal_at is None:
            return
        if link.terminal_state == "abandoned":
            self._end(row, "not-picked-up", now)
            return
        if link.terminal_state != "completed":
            self._end(row, "check-failed", now, reason_detail={"cause": _check_failure_cause(run, link)})
            return
        verification = run.results.get("verification")
        report: dict[str, Any] = verification if isinstance(verification, dict) else {}
        operations = report.get("operations")
        differs = sum(
            1
            for entry in (operations if isinstance(operations, list) else ())
            if isinstance(entry, dict) and entry.get("state") == "differs"
        )
        truncated = report.get("operations_truncated")
        not_checked = truncated if isinstance(truncated, int) and truncated > 0 else 0
        if differs:
            self._end(
                row,
                "check-found-differs",
                now,
                reason_detail={"differs": differs},
                operations_not_checked=not_checked,
            )
            return
        self._move(row, "waiting-resume", now, operations_not_checked=not_checked)

    def _step_resuming(self, row: AutomaticResume, now: datetime) -> None:
        """End the record once the resume's execution has a verdict; an abandoned resume ends ``not-picked-up``."""
        run, link = self._execution(row.run_id, row.resume_flow_run_id)
        if run is not None and link is not None and link.terminal_at is None:
            return
        if link is not None and link.terminal_state == "abandoned":
            self._end(row, "not-picked-up", now)
            return
        # Any other verdict ends the record with its decision `resume`. A resume that ended
        # `interrupted` under rule 6 or rule 8 already has the next record (data-model.md).
        self._move(row, "ended", now)

    def _execution(self, run_id: str, flow_run_id: str | None) -> tuple[ProductRun | None, PrefectExecutionLink | None]:
        """The run and its execution ``flow_run_id``, each ``None`` when the store has none."""
        run = self._projection.lookup_run(run_id).value
        if run is None or flow_run_id is None:
            return run, None
        return run, next((link for link in run.prefect_executions if link.flow_run_id == flow_run_id), None)

    def _start_after(self, run_id: str) -> int | None:
        """The run's highest saved progress marker: where the resume starts after (`resume_start_after`)."""
        run = self._projection.lookup_run(run_id).value
        marker = None if run is None else run_apply_progress(run)
        return None if marker is None else marker.position

    def _move(self, row: AutomaticResume, to_state: AutomaticResumeState, now: datetime, **fields: object) -> bool:
        """Compare-and-set ``row`` from its state to ``to_state``; write one log line when it moved.

        The move to ``resuming`` and every end without a resume write their run event in the
        transaction of the move, so only the process that moved the row writes it (FR-017).
        """
        event = None
        if to_state == "resuming" or (to_state == "ended" and fields.get("decision") == "no-resume"):
            decided = row.model_copy(update={**fields, "state": to_state, "updated_at": now})
            event = automatic_resume_event(
                decided, self._projection.lookup_run(row.run_id).value, actor=AUTOMATIC_RESUME_ACTOR, created_at=now
            )
        moved = self._projection.move_automatic_resume(
            row.run_id,
            row.interrupted_flow_run_id,
            from_state=row.state,
            to_state=to_state,
            event=event,
            updated_at=now,
            **fields,
        )
        if moved:
            logger.info(
                "automatic resume moved",
                run_id=row.run_id,
                state=to_state,
                reason=fields.get("reason"),
            )
        return moved

    def _end(
        self,
        row: AutomaticResume,
        reason: AutomaticResumeReason,
        now: datetime,
        *,
        reason_detail: dict[str, str | int] | None = None,
        **fields: object,
    ) -> bool:
        """End ``row`` with the decision ``no-resume`` and ``reason``."""
        return self._move(row, "ended", now, decision="no-resume", reason=reason, reason_detail=reason_detail, **fields)

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
        # A record that ends at once, without a check, records its event with the interruption (FR-017).
        decision_event = (
            automatic_resume_event(automatic_resume, run, actor=AUTOMATIC_RESUME_ACTOR, created_at=now)
            if automatic_resume is not None and automatic_resume.decision == "no-resume"
            else None
        )
        self._projection.interrupt_execution(
            run.run_id,
            link.flow_run_id,
            terminal_at=now,
            event=self._interrupted_event(run, link, now),
            automatic_resume=automatic_resume,
            automatic_resume_event=decision_event,
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


def _check_failure_cause(run: ProductRun, link: PrefectExecutionLink) -> str:
    """The failure class of a check that did not complete, never its message text.

    The worker records a failed `verify` under ``results["verify_failure"]`` with its flow run
    identifier. An execution that the reconciler or a cancellation ended has none, and its
    verdict names the cause.
    """
    evidence = run.results.get("verify_failure")
    if isinstance(evidence, dict) and evidence.get("flow_run_id") == link.flow_run_id:
        for key in ("failure_type", "error_type"):
            value = evidence.get(key)
            if isinstance(value, str) and value:
                return value
    return f"execution-{link.terminal_state}"


def _worker_name(worker_id: str | None, pool: PoolStatus) -> str | None:
    """The name the pool snapshot gives the claiming worker, or ``None`` when it has none."""
    if worker_id is None:
        return None
    return next((worker.name for worker in pool.workers if worker.worker_id == worker_id), None)
