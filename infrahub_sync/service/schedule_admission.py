"""Decide whether one start of a schedule runs, and save the decision (spec 021, research R2, R7, R10, R11).

The worker calls :func:`admit_scheduled_start` when Prefect starts a flow run of a schedule.
The decision is made here from the stored schedule and the times that the caller passes; the
product store writes it in one transaction, with the run when the start runs. Nothing here
reads the clock: the current time is an argument (research R14).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Protocol

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.configuration.runtime import effective_destination_branch
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_REASON
from infrahub_sync.product_store import (
    ConfigurationNotFoundError,
    EventDetails,
    ProductRun,
    ScheduleChangedError,
    ScheduledStartAlreadyRecordedError,
    ScheduledStartNotRun,
    ScheduledStartRun,
)

from . import schedule_cadence
from .events import (
    SCHEDULE_PROPOSED_CHANGE_KEPT,
    SCHEDULE_START,
    SCHEDULE_START_FAILED,
    SCHEDULE_START_MISSED,
    SCHEDULE_START_SKIPPED,
    run_decision_type,
    run_event,
    schedule_event,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from infrahub_sync.product_store import (
        AuditEvent,
        ProductProjection,
        ScheduleBranch,
        ScheduledStartDecision,
        ScheduleRecord,
    )

# How late the most recent start may begin and still run. User story 3 replaces it with the
# schedule's own ``start_late_by_at_most_seconds``.
MOST_RECENT_START_LATE_LIMIT_SECONDS = 300
# The newest missed starts are recorded one event each; the older ones share one event.
MISSED_STARTS_RECORDED_ONE_BY_ONE = 500
# How many times a decision is made again when the schedule changed while it was being made.
ADMISSION_ATTEMPTS = 5

# Refusal codes of a configuration version that a start cannot run, in ``details.cause``.
_VERSION_NOT_FOUND = "configuration-version-not-found"
_VERSION_INVALID = "configuration-version-invalid"
_BRANCH_DECLARED = "schedule-branch-declared"
# The states of an earlier proposed change in which a person merged or closed it, and the
# outcome of the event that records it as kept. ``merging`` counts as merged and ``canceled``
# as closed (research R15).
_KEPT_OUTCOMES = {"merged": "kept-merged", "merging": "kept-merged", "closed": "kept-closed", "canceled": "kept-closed"}


class EarlierProposedChangesUnreadableError(Exception):
    """The states of the schedule's earlier proposed changes could not be read from Infrahub.

    ``cause`` is the failure class of the read, which the event of the failed start holds.
    """

    def __init__(self, cause: str) -> None:
        super().__init__(f"the earlier proposed changes of the schedule could not be read ({cause})")
        self.cause = cause


class EarlierProposedChangesReader(Protocol):
    """Reads the state of each earlier proposed change of a schedule from Infrahub (research R11)."""

    def __call__(self, schedule: ScheduleRecord, branches: tuple[ScheduleBranch, ...], /) -> Mapping[str, str]:
        """Return, by the run of each branch row, its state in Infrahub.

        The state is ``open``, ``approved``, ``merging``, ``merged``, ``closed`` or ``canceled``.

        Raises:
            EarlierProposedChangesUnreadableError: Infrahub did not answer.
        """
        ...


@dataclass(frozen=True)
class StartAdmission:
    """What the admission of one start decided.

    ``run`` is the run that was created, or ``None`` when the start created no run; ``cause``
    then says why. Besides the causes of the store, the cause is ``schedule-not-found``,
    ``start-not-in-cadence`` (the cadence of the stored schedule no longer holds the start
    time) or ``start-already-recorded`` (a missed start that a later start already recorded).
    """

    run: ProductRun | None
    cause: str | None = None
    branch_review: bool = False
    earlier_branches: tuple[ScheduleBranch, ...] = ()


def branch_review_branch(schedule_name: str, schedule_id: str, start: datetime) -> str:
    """Return the branch of the run of one start with branch review (research R11).

    ``sync/<schedule name>-<YYYYMMDD of the start>-<6 hexadecimal characters>``: the characters
    begin ``sha256(schedule_id + ":" + start)``, with the start in UTC as ISO 8601 text, so two
    attempts of one start derive the same name.
    """
    start_utc = start.astimezone(timezone.utc)
    digest = sha256(f"{schedule_id}:{start_utc.isoformat()}".encode()).hexdigest()[:6]
    return f"sync/{schedule_name}-{start_utc:%Y%m%d}-{digest}"


def admit_scheduled_start(  # pylint: disable=too-many-arguments
    projection: ProductProjection,
    *,
    schedule_id: str,
    scheduled_start_at: datetime,
    flow_run_id: str,
    worker_id: str,
    now: datetime,
    receipt_age_limit: timedelta,
    read_proposed_changes: EarlierProposedChangesReader | None = None,
) -> StartAdmission:
    """Decide on one start of a schedule and save the decision, with the run when it runs.

    A schedule that does not exist or is not active, and a start time that the stored cadence
    does not hold, write nothing. When the schedule changes while the decision is made, the
    schedule is read again and the decision made again, at most ``ADMISSION_ATTEMPTS`` times.
    ``read_proposed_changes`` reads the earlier proposed changes of a schedule with branch
    review: see `decide_start`.

    Raises:
        DuplicateRunError: The start already has a run.
        ScheduleChangedError: The schedule changed during every attempt.
    """
    for _ in range(ADMISSION_ATTEMPTS):
        schedule = projection.lookup_schedule(schedule_id).value
        if schedule is None:
            return StartAdmission(run=None, cause="schedule-not-found")
        if schedule.state != "active":
            return StartAdmission(run=None, cause="schedule-not-active")
        if not _in_cadence(schedule, scheduled_start_at):
            return StartAdmission(run=None, cause="start-not-in-cadence")
        decision = decide_start(
            projection,
            schedule,
            scheduled_start_at,
            now,
            receipt_age_limit,
            read_proposed_changes=read_proposed_changes,
        )
        try:
            result = projection.admit_scheduled_start(
                schedule_id, scheduled_start_at, flow_run_id, worker_id, now, decision
            )
        except (ScheduleChangedError, ConfigurationNotFoundError):
            # Another start, a person or a removed version changed what the decision was made from.
            continue
        except ScheduledStartAlreadyRecordedError:
            return StartAdmission(run=None, cause="start-already-recorded")
        if not result.started:
            return StartAdmission(run=None, cause=result.cause)
        assert isinstance(decision, ScheduledStartRun)
        return StartAdmission(
            run=decision.run, branch_review=decision.branch_review, earlier_branches=result.earlier_branches
        )
    msg = f"schedule {schedule_id!r} changed during each of {ADMISSION_ATTEMPTS} admissions of one start"
    raise ScheduleChangedError(msg)


def decide_start(  # pylint: disable=too-many-arguments,too-many-locals
    projection: ProductProjection,
    schedule: ScheduleRecord,
    start: datetime,
    now: datetime,
    receipt_age_limit: timedelta,
    *,
    read_proposed_changes: EarlierProposedChangesReader | None = None,
) -> ScheduledStartDecision:
    """Decide whether the start of an active schedule runs, with the events of the decision.

    In order: the starts of the cadence that passed with no flow run are recorded as missed; a
    start later than ``MOST_RECENT_START_LATE_LIMIT_SECONDS`` is missed; a configuration version
    that cannot be run fails the start; a run of the configuration in progress skips it (the
    ``reject`` policy). For a schedule with branch review, ``read_proposed_changes`` then reads
    the earlier proposed changes of its ``pending`` and ``open`` branch rows (research R11,
    FR-031): one that cannot be read fails the start; one that is approved and open skips it;
    one that a person merged or closed moves to the state ``kept``. Otherwise the start runs as
    a new ``sync`` run of the owner.
    """
    events = list(_missed_start_events(schedule, start, now))
    kept_run_ids: list[str] = []

    def not_run(cause: str, event: AuditEvent) -> ScheduledStartNotRun:
        return ScheduledStartNotRun.model_validate(
            {
                "cause": cause,
                "last_start_at": schedule.last_start_at,
                "events": (*events, event),
                "kept_run_ids": tuple(kept_run_ids),
            }
        )

    if now - start > timedelta(seconds=MOST_RECENT_START_LATE_LIMIT_SECONDS):
        return not_run("missed", _start_event(schedule, SCHEDULE_START_MISSED, "missed-skipped", start, now))
    version = _runnable_version(projection, schedule)
    if isinstance(version, str):
        event = _start_event(schedule, SCHEDULE_START_FAILED, "failed", start, now, cause=version)
        return not_run("failed", event)
    package_checksum, sync_name, settings = version
    in_progress = projection.run_in_progress(schedule.config_id, now, receipt_age_limit)
    if in_progress is not None:
        event = _start_event(
            schedule, SCHEDULE_START_SKIPPED, "skipped-run-in-progress", start, now, run_id=in_progress
        )
        return not_run("skipped-run-in-progress", event)
    branch_review = schedule.write_target == "branch-review"
    states: Mapping[str, str] = {}
    earlier: tuple[ScheduleBranch, ...] = ()
    if branch_review and read_proposed_changes is not None:
        earlier = tuple(
            row
            for row in projection.schedule_branches(schedule.schedule_id, states=("pending", "open"))
            if row.proposed_change_id is not None
        )
        if earlier:
            try:
                states = read_proposed_changes(schedule, earlier)
            except EarlierProposedChangesUnreadableError as unreadable:
                event = _start_event(schedule, SCHEDULE_START_FAILED, "failed", start, now, cause=unreadable.cause)
                return not_run("failed", event)
    kept = [row for row in earlier if states.get(row.run_id) in _KEPT_OUTCOMES]
    kept_run_ids.extend(row.run_id for row in kept)
    approved = next((row for row in earlier if states.get(row.run_id) == "approved"), None)
    if approved is not None:
        events.extend(_kept_event(schedule, row, _KEPT_OUTCOMES[states[row.run_id]], None, now) for row in kept)
        event = _start_event(
            schedule, SCHEDULE_START_SKIPPED, "skipped-approved-change-open", start, now, run_id=approved.run_id
        )
        return not_run("skipped-approved-change-open", event)
    run = ProductRun(
        run_id=generate_run_id(),
        operation="sync",
        configuration_reference=f"{schedule.config_id}@{schedule.registry_version}",
        config_id=schedule.config_id,
        registry_version=schedule.registry_version,
        package_checksum=package_checksum,
        actor=schedule.owner,
        started_at=now,
        phase="accepted",
        summary={"sync_name": sync_name},
        destination_branch=(
            branch_review_branch(schedule.name, schedule.schedule_id, start)
            if branch_review
            else effective_destination_branch(settings, None)
        ),
        trigger_type="scheduled",
        schedule_id=schedule.schedule_id,
        scheduled_start_at=start,
    )
    accepted = run_event(
        run.run_id,
        run,
        event_type=run_decision_type("accepted"),
        actor=schedule.owner,
        operation="sync",
        reason=None,
        outcome="accepted",
        created_at=now,
    )
    kept_events = tuple(_kept_event(schedule, row, _KEPT_OUTCOMES[states[row.run_id]], run.run_id, now) for row in kept)
    return ScheduledStartRun(
        run=run,
        branch_review=branch_review,
        last_start_at=schedule.last_start_at,
        events=(*events, *kept_events, accepted),
        kept_run_ids=tuple(kept_run_ids),
    )


def _kept_event(
    schedule: ScheduleRecord, branch: ScheduleBranch, outcome: str, run_id: str | None, now: datetime
) -> AuditEvent:
    """Build the event of an earlier proposed change that a person merged or closed, found by a start."""
    return schedule_event(
        event_type=SCHEDULE_PROPOSED_CHANGE_KEPT,
        actor=schedule.owner,
        operation="sync",
        reason=None,
        outcome=outcome,
        schedule_id=schedule.schedule_id,
        config_id=schedule.config_id,
        registry_version=schedule.registry_version,
        run_id=run_id,
        details=EventDetails(branch=branch.branch, proposed_change_id=branch.proposed_change_id),
        created_at=now,
    )


def _in_cadence(schedule: ScheduleRecord, start: datetime) -> bool:
    """Return whether the stored cadence of the schedule has a start at ``start``."""
    found = schedule_cadence.starts_between(
        schedule.cron, schedule.timezone, start - timedelta(microseconds=1), start, 1
    )
    return found == (start,)


def _runnable_version(
    projection: ProductProjection, schedule: ScheduleRecord
) -> tuple[str, str, Mapping[str, Any] | None] | str:
    """Return the checksum, the name and the destination settings of the schedule's version, or a refusal code."""
    stored = projection.lookup_configuration_version(schedule.config_id, schedule.registry_version).value
    if stored is None:
        return _VERSION_NOT_FOUND
    try:
        package = parse_configuration_package(stored.declared_content)
    except ConfigurationPackageParseError:
        return _VERSION_INVALID
    if package.checksum() != stored.package_checksum:
        return _VERSION_INVALID
    if package.configuration.store is not None:
        return UNSUPPORTED_STORE_REASON
    settings = package.configuration.destination.settings
    declared = (settings or {}).get("branch")
    if schedule.write_target == "branch-review" and isinstance(declared, str) and declared:
        # A run with branch review would write to the declared branch with no review (R11).
        return _BRANCH_DECLARED
    return stored.package_checksum, package.configuration.name, settings


def missed_starts(
    schedule: ScheduleRecord, start: datetime
) -> tuple[int, datetime | None, datetime | None, list[datetime]]:
    """Return the starts of the cadence that passed with no flow run before ``start``.

    The starts are counted after the later of the last admitted start and the last change of
    the schedule (``updated_at``): a create, a change, a pause, a resume or a stop. So a paused
    or stopped schedule records no missed start, and a changed cadence does not record the
    starts of the earlier one. Returns the count and the first and last of the older starts
    that share one event, then the newest ``MISSED_STARTS_RECORDED_ONE_BY_ONE`` starts.
    """
    after = schedule.updated_at if schedule.last_start_at is None else max(schedule.last_start_at, schedule.updated_at)
    recent: deque[datetime] = deque(maxlen=MISSED_STARTS_RECORDED_ONE_BY_ONE)
    older, first_older, last_older = 0, None, None
    while after < start:
        page = schedule_cadence.starts_between(
            schedule.cron, schedule.timezone, after, start, schedule_cadence.MAX_STARTS
        )
        for missed in page:
            if missed >= start:
                continue
            if len(recent) == recent.maxlen:
                last_older = recent[0]
                first_older = first_older or last_older
                older += 1
            recent.append(missed)
        if len(page) < schedule_cadence.MAX_STARTS:
            break
        after = page[-1]
    return older, first_older, last_older, list(recent)


def _missed_start_events(schedule: ScheduleRecord, start: datetime, now: datetime) -> Iterator[AuditEvent]:
    """Yield the events of the missed starts before ``start``, the older ones first in one event."""
    older, first_older, last_older, recent = missed_starts(schedule, start)
    if older:
        yield schedule_event(
            event_type=SCHEDULE_START_MISSED,
            actor=schedule.owner,
            operation=SCHEDULE_START,
            reason=None,
            outcome="missed-skipped",
            schedule_id=schedule.schedule_id,
            config_id=schedule.config_id,
            registry_version=schedule.registry_version,
            details=EventDetails(starts=older, first_start_at=first_older, last_start_at=last_older),
            created_at=now,
        )
    for missed in recent:
        yield _start_event(schedule, SCHEDULE_START_MISSED, "missed-skipped", missed, now)


def _start_event(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    schedule: ScheduleRecord,
    event_type: str,
    outcome: str,
    start: datetime,
    now: datetime,
    *,
    cause: str | None = None,
    run_id: str | None = None,
) -> AuditEvent:
    """Build the event of one start of the schedule; the owner is its actor."""
    return schedule_event(
        event_type=event_type,
        actor=schedule.owner,
        operation=SCHEDULE_START,
        reason=None,
        outcome=outcome,
        schedule_id=schedule.schedule_id,
        config_id=schedule.config_id,
        registry_version=schedule.registry_version,
        run_id=run_id,
        details=EventDetails(start_at=start, cause=cause),
        created_at=now,
    )
