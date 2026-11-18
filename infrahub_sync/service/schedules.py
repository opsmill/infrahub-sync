"""Schedules of the Sync API: create, change, pause, resume and delete, and the match with Prefect.

A schedule is stored in the product store. Prefect holds its cadence as one schedule of the
service deployment, with the identifier of the Sync schedule as its slug (spec 021, research R1).
The store is written first and Prefect second. One step, `match_prefect_schedules`, makes the
schedules of the deployment match the stored schedules. It runs after each schedule request, when
the API process starts, and at the end of the bootstrap job (research R13).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import uuid4

import structlog
from pydantic import ValidationError

from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.execution import collect_secret_values, redact
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    AuditEvent,
    MutationReceipt,
    ProductProjection,
    ProductStoreProviderError,
    RunFilters,
    ScheduleChangedError,
    ScheduleDeletedError,
    ScheduleExistsForConfigurationError,
    ScheduleFilters,
    ScheduleNameInUseError,
    ScheduleNotFoundError,
    ScheduleRecord,
)
from infrahub_sync.product_store.models import DEFAULT_START_LATE_SECONDS, SCHEDULE_ID_PATTERN

from . import schedule_cadence
from .events import SCHEDULE_START, SCHEDULE_STOPPED, schedule_decision_type, schedule_event
from .models import EmittedSchedulePage, EmittedScheduleResource
from .orchestration import (
    DeploymentSchedule,
    ObservedDeploymentSchedule,
    OrchestrationUnavailableError,
    ServiceOrchestration,
)
from .paging import FilterValue, decode_cursor, timestamp_cursor, timestamp_key
from .service import ServiceAPIError, page_size_or_refuse
from .settings import REQUIRE_APPROVAL_ENV, approval_required

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from .auth import Principal, PrincipalResolver
    from .models import ChangeScheduleRequest, CreateScheduleRequest, ScheduleActionRequest

__all__ = ["ScheduleService", "match_prefect_schedules"]

logger = structlog.get_logger(__name__)

CREATE: Final = "schedule-create"
CHANGE: Final = "schedule-change"
PAUSE: Final = "schedule-pause"
RESUME: Final = "schedule-resume"
DELETE: Final = "schedule-delete"

_SCHEDULE_ID = re.compile(SCHEDULE_ID_PATTERN)
_SCHEDULE_STATES: Final = frozenset({"active", "paused", "stopped", "deleted"})
# The fields of the schedule resource that the API returns from user story 3 (contracts/http-api.md).
_FIELDS_NOT_DELIVERED: Final = frozenset({"newer_version", "start_late_by_at_most_seconds", "waiting_start_at"})
# How many times a request reads the schedule again when another writer changed it in between.
_SAVE_ATTEMPTS: Final = 3
_MATCH_PAGE_SIZE: Final = 200
_UNAVAILABLE_MESSAGE: Final = (
    "the schedule is stored, and the task manager could not be reached; the schedule is handed to the "
    "task manager by a later schedule request or by a restart of the Sync API"
)
# What a stopped schedule needs before a resume is accepted, by the reason it stopped for.
_RESUME_ACTIONS: Final = {
    "owner-not-found": (
        "the owner's actor does not exist on this Sync API; add the actor to the Sync API again and restart it"
    ),
    "approval-required": (
        f"this Sync API requires approval ({REQUIRE_APPROVAL_ENV}) and the schedule writes to the default "
        "branch; change the write target of the schedule to branch-review"
    ),
}


def _all_schedules(projection: ProductProjection, filters: ScheduleFilters) -> tuple[ScheduleRecord, ...]:
    """Return every stored schedule that matches ``filters``, read page after page."""
    schedules: list[ScheduleRecord] = []
    after: tuple[datetime, str] | None = None
    while True:
        page = projection.list_schedules(filters, after=after, limit=_MATCH_PAGE_SIZE)
        schedules.extend(page.items)
        if not page.has_more or not page.items:
            return tuple(schedules)
        last = page.items[-1]
        after = (last.created_at, last.schedule_id)


def _desired(schedule: ScheduleRecord) -> DeploymentSchedule:
    """Return the schedule of the deployment that a stored schedule needs; it is active only in the state active."""
    return DeploymentSchedule(
        slug=schedule.schedule_id, cron=schedule.cron, timezone=schedule.timezone, active=schedule.state == "active"
    )


async def match_prefect_schedules(projection: ProductProjection, orchestration: ServiceOrchestration) -> None:
    """Make the Prefect schedules of the service deployment match the stored schedules, by slug.

    Each stored schedule that is not deleted gets one Prefect schedule with its cadence, which is
    active only when the stored schedule is active. A Prefect schedule that differs is put back,
    and one whose slug is the identifier of no such stored schedule is deleted. A Prefect schedule
    whose slug is not a schedule identifier of Infrahub Sync is left as it is. The step reads what
    Prefect holds each time, so it also creates again a schedule that Prefect lost.

    Each schedule that matches is recorded with ``prefect_synced`` true.

    Raises:
        OrchestrationUnavailableError: Prefect could not be reached. The schedule that was being
            written is recorded with ``prefect_synced`` false.
    """
    stored = _all_schedules(projection, ScheduleFilters())
    current: ScheduleRecord | None = None
    try:
        held, surplus = _held_by_slug(await orchestration.read_schedules())
        for current in stored:
            desired = _desired(current)
            existing = held.pop(current.schedule_id, None)
            if existing is None:
                orchestrator_id = await orchestration.create_schedule(desired)
            else:
                orchestrator_id = existing.orchestrator_id
                if existing.schedule is None or not existing.schedule.matches(desired):
                    await orchestration.update_schedule(orchestrator_id, desired)
            if not current.prefect_synced or current.prefect_schedule_id != orchestrator_id:
                projection.record_schedule_prefect_sync(
                    current.schedule_id, prefect_schedule_id=orchestrator_id, synced=True
                )
        current = None
        for slug, leftover in held.items():
            # A schedule that was stored after the list above was read keeps its Prefect schedule.
            latest = projection.lookup_schedule(slug).value
            if latest is not None and latest.state != "deleted":
                continue
            await orchestration.delete_schedule(leftover.orchestrator_id)
            # The schedule is deleted, or it was never stored: Prefect holds nothing for it now.
            projection.record_schedule_prefect_sync(slug, prefect_schedule_id=None, synced=True)
        for duplicate in surplus:
            await orchestration.delete_schedule(duplicate.orchestrator_id)
    except OrchestrationUnavailableError:
        if current is not None and current.prefect_synced:
            projection.record_schedule_prefect_sync(
                current.schedule_id, prefect_schedule_id=current.prefect_schedule_id, synced=False
            )
        raise


def _held_by_slug(
    observed: Sequence[ObservedDeploymentSchedule],
) -> tuple[dict[str, ObservedDeploymentSchedule], tuple[ObservedDeploymentSchedule, ...]]:
    """Return the Prefect schedules of Infrahub Sync by slug, and the further ones that repeat a slug.

    A Prefect schedule whose slug is not a schedule identifier of Infrahub Sync is in neither.
    """
    held: dict[str, ObservedDeploymentSchedule] = {}
    surplus: list[ObservedDeploymentSchedule] = []
    for item in observed:
        if item.slug is None or _SCHEDULE_ID.fullmatch(item.slug) is None:
            continue
        if item.slug in held:
            surplus.append(item)
        else:
            held[item.slug] = item
    return held, tuple(surplus)


@dataclass(frozen=True, slots=True)
class _Request:
    """What every event and refusal of one schedule request names."""

    actor: str
    operation: str
    reason: str | None
    schedule_id: str | None = None
    config_id: str | None = None
    registry_version: int | None = None
    administrator: bool = False

    def of(self, schedule: ScheduleRecord) -> _Request:
        """Return the request with the schedule, its configuration and its version named."""
        return replace(
            self,
            schedule_id=schedule.schedule_id,
            config_id=schedule.config_id,
            registry_version=schedule.registry_version,
        )


class ScheduleService:  # pylint: disable=too-many-instance-attributes
    """Application service of the schedule routes.

    ``delivered_write_targets`` is the set of write targets that this build can run. A request
    for another write target is refused with ``schedule-setting-not-available``.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        projection: ProductProjection,
        orchestration: ServiceOrchestration,
        resolver: PrincipalResolver,
        *,
        delivered_write_targets: Collection[str],
        secrets: tuple[str, ...] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._projection = projection
        self._orchestration = orchestration
        self._resolver = resolver
        self._delivered_write_targets = frozenset(delivered_write_targets)
        self._secrets = tuple(dict.fromkeys((*collect_secret_values(), *secrets)))
        self._clock = clock

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        """Return the resource of one schedule, also of a deleted schedule.

        Raises:
            ServiceAPIError: ``404 schedule-not-found``.
        """
        schedule = self._projection.lookup_schedule(schedule_id).value
        if schedule is None:
            raise self._error(404, "schedule-not-found", "the requested schedule does not exist")
        return self._resource(schedule)

    def list_schedules(
        self,
        *,
        config_id: str | None = None,
        state: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return one page of schedules, newest first; without ``state``, deleted schedules are left out.

        Raises:
            ServiceAPIError: ``422 request-invalid`` for an unknown state, a configuration
                identifier that is not one, or a limit outside 1-200.
            InvalidCursorError: ``cursor`` does not decode or was issued for other filters.
        """
        if state is not None and state not in _SCHEDULE_STATES:
            raise self._error(422, "request-invalid", "the request does not match the API schema")
        page_size = page_size_or_refuse(limit, secrets=self._secrets)
        try:
            store_filters = ScheduleFilters.model_validate({"config_id": config_id, "state": state})
        except ValidationError:
            raise self._error(422, "request-invalid", "the request does not match the API schema") from None
        filters: dict[str, FilterValue] = {"list": "schedules", "config_id": config_id, "state": state}
        after = None if cursor is None else timestamp_key(decode_cursor(cursor, filters))
        page = self._projection.list_schedules(store_filters, after=after, limit=page_size)
        last = page.items[-1] if page.has_more else None
        next_cursor = None if last is None else timestamp_cursor(last.created_at, last.schedule_id, filters)
        emitted = EmittedSchedulePage.model_validate(
            {
                "items": tuple(self._emitted(schedule).model_dump() for schedule in page.items),
                "next_cursor": next_cursor,
            }
        )
        return emitted.model_dump(mode="json", exclude={"items": {"__all__": set(_FIELDS_NOT_DELIVERED)}})

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    async def create(
        self, request: CreateScheduleRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Create a schedule that the caller owns, and hand it to Prefect.

        Raises:
            ServiceAPIError: one of the refusals of ``POST /schedules`` (contracts/http-api.md),
                or ``503 orchestration-unavailable`` when the schedule is stored and Prefect
                could not be reached.
        """
        now = self._clock()
        # Checked first, with a context that names no configuration: an identifier that holds a
        # credential must not reach an event as the configuration it names.
        self._refuse_secret_values(
            _Request(principal.actor, CREATE, request.reason),
            name=request.name,
            config_id=request.config_id,
            cron=request.cron,
            timezone=request.timezone,
        )
        context = _Request(principal.actor, CREATE, request.reason, None, request.config_id, request.registry_version)
        receipt = self._receipt(
            context, idempotency_key, None, f"s-{uuid4().hex}", request.model_dump(mode="json"), now
        )
        replayed = await self._replayed(receipt, context)
        if replayed is not None:
            return replayed
        declared_branch = self._declared_branch(context, request.config_id, request.registry_version)
        self._require_cadence(context, request.cron, request.timezone)
        self._require_write_settings(
            context,
            write_target=request.write_target,
            write_when_left_out=request.write_when_left_out,
            declared_branch=declared_branch,
        )
        self._require_delivered(
            context,
            write_target=request.write_target,
            overlap_policy=request.overlap_policy,
            start_late_by_at_most_seconds=request.start_late_by_at_most_seconds,
        )
        reserved = self._reserve(receipt, context)
        schedule = ScheduleRecord(
            schedule_id=reserved.resource_id,
            name=request.name,
            config_id=request.config_id,
            registry_version=request.registry_version,
            cron=request.cron,
            timezone=request.timezone,
            write_target=request.write_target,
            write_when_left_out=request.write_when_left_out,
            left_out_set_by=principal.actor if request.write_when_left_out else None,
            left_out_set_at=now if request.write_when_left_out else None,
            overlap_policy=request.overlap_policy,
            start_late_by_at_most_seconds=DEFAULT_START_LATE_SECONDS,
            state="active",
            owner=principal.actor,
            created_at=now,
            updated_at=now,
        )
        try:
            self._projection.create_schedule(schedule, secrets=self._secrets)
        except ScheduleNameInUseError:
            self._projection.release_mutation(reserved.receipt_id, secrets=self._secrets)
            raise self._refusal(
                context,
                409,
                "schedule-name-in-use",
                f"a schedule that is not deleted already has the name {request.name!r}",
            ) from None
        except ScheduleExistsForConfigurationError as error:
            self._projection.release_mutation(reserved.receipt_id, secrets=self._secrets)
            raise self._refusal(
                context,
                409,
                "schedule-exists-for-configuration",
                f"the configuration {request.config_id!r} already has the schedule {error.name!r} "
                f"({error.schedule_id}); a configuration has one schedule",
            ) from None
        return await self._answer(reserved, 201, context.of(schedule))

    async def change(
        self, schedule_id: str, request: ChangeScheduleRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Change one or more settings of a schedule; a setting that the request leaves out keeps its value.

        A change of the write target to ``branch-review`` makes a schedule active again that was
        stopped because the Sync API requires approval.

        Raises:
            ServiceAPIError: one of the refusals of ``POST /schedules/{schedule_id}/change``.
        """
        context = _Request(principal.actor, CHANGE, request.reason, administrator=principal.administrator)

        def changed(schedule: ScheduleRecord, now: datetime) -> ScheduleRecord:
            target = context.of(schedule)
            values = {
                name: value
                for name, value in request.model_dump(exclude={"reason"}).items()
                if value is not None and name != "start_late_by_at_most_seconds"
            }
            merged = {**schedule.model_dump(), **values}
            self._refuse_secret_values(target, cron=request.cron, timezone=request.timezone)
            declared_branch = self._declared_branch(target, schedule.config_id, merged["registry_version"])
            self._require_cadence(target, merged["cron"], merged["timezone"])
            self._require_write_settings(
                target,
                write_target=merged["write_target"],
                write_when_left_out=merged["write_when_left_out"],
                declared_branch=declared_branch,
            )
            self._require_delivered(
                target,
                write_target=request.write_target,
                overlap_policy=request.overlap_policy,
                start_late_by_at_most_seconds=request.start_late_by_at_most_seconds,
            )
            if merged["write_when_left_out"] and not schedule.write_when_left_out:
                merged.update(left_out_set_by=principal.actor, left_out_set_at=now)
            elif not merged["write_when_left_out"]:
                merged.update(left_out_set_by=None, left_out_set_at=None)
            if schedule.state_reason == "approval-required" and merged["write_target"] == "branch-review":
                merged.update(state="active", state_reason=None)
            return ScheduleRecord.model_validate(merged)

        return await self._act(schedule_id, context, request.model_dump(mode="json"), idempotency_key, changed)

    async def pause(
        self, schedule_id: str, request: ScheduleActionRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Pause an active schedule. A pause of a schedule that is paused or stopped changes nothing.

        Raises:
            ServiceAPIError: ``403 forbidden``, ``404 schedule-not-found`` or ``409 schedule-deleted``.
        """
        context = _Request(principal.actor, PAUSE, request.reason, administrator=principal.administrator)

        def paused(schedule: ScheduleRecord, now: datetime) -> ScheduleRecord:
            del now
            if schedule.state != "active":
                return schedule
            return schedule.model_copy(update={"state": "paused"})

        return await self._act(schedule_id, context, request.model_dump(mode="json"), idempotency_key, paused)

    async def resume(
        self, schedule_id: str, request: ScheduleActionRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Make a paused schedule active, or a stopped schedule whose reason no longer holds.

        A schedule that stopped for ``repeated-failures`` always becomes active, and the store
        sets its count of failed runs to zero. A resume of an active schedule changes nothing.

        Raises:
            ServiceAPIError: ``403 forbidden``, ``404 schedule-not-found``, ``409 schedule-deleted``,
                or ``409 schedule-stopped`` when the reason of a stopped schedule still holds.
        """
        context = _Request(principal.actor, RESUME, request.reason, administrator=principal.administrator)

        def resumed(schedule: ScheduleRecord, now: datetime) -> ScheduleRecord:
            del now
            if schedule.state == "active":
                return schedule
            reason = schedule.state_reason
            if reason is not None and reason != "repeated-failures" and self._reason_holds(schedule, reason):
                raise self._refusal(
                    context.of(schedule),
                    409,
                    "schedule-stopped",
                    f"the schedule is stopped with the reason {reason}: {_RESUME_ACTIONS[reason]}",
                )
            return schedule.model_copy(update={"state": "active", "state_reason": None})

        return await self._act(schedule_id, context, request.model_dump(mode="json"), idempotency_key, resumed)

    async def delete(
        self, schedule_id: str, request: ScheduleActionRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Delete a schedule; its record is kept in the state ``deleted``, and a second deletion changes nothing.

        A run in progress is not cancelled. The open proposed change and its branch stay in
        Infrahub, and ``open_branch`` of the answer names them.

        Raises:
            ServiceAPIError: ``403 forbidden`` or ``404 schedule-not-found``.
        """
        context = _Request(principal.actor, DELETE, request.reason, administrator=principal.administrator)

        def deleted(schedule: ScheduleRecord, now: datetime) -> ScheduleRecord:
            del now
            return schedule.model_copy(update={"state": "deleted", "state_reason": None})

        return await self._act(schedule_id, context, request.model_dump(mode="json"), idempotency_key, deleted)

    # ------------------------------------------------------------------
    # The match with Prefect, and the start of the API process
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Run the step of the start of the API process; it never stops the process from starting.

        The step stops each active schedule whose owner's actor no longer exists on the Sync
        API, or that writes to the default branch while the Sync API requires approval, and
        writes the event of each stop. It then makes the Prefect schedules match the stored
        schedules. When Prefect or the product store fails, the failure is logged and the
        schedules that Prefect does not hold keep ``prefect_synced`` false.
        """
        try:
            self._stop_schedules_that_cannot_run()
        except ProductStoreProviderError:
            logger.warning("schedules.start_failed", step="start", error_class="ProductStoreProviderError")
            return
        await self._matched(step="start")

    def _reason_holds(self, schedule: ScheduleRecord, reason: Literal["owner-not-found", "approval-required"]) -> bool:
        """Return whether one reason to stop the schedule holds now."""
        if reason == "owner-not-found":
            return self._resolver.principal_of(schedule.owner) is None
        return schedule.write_target == "default-branch" and approval_required()

    def _stop_reason(self, schedule: ScheduleRecord) -> Literal["owner-not-found", "approval-required"] | None:
        """Return the reason for which the Sync API stops the schedule when it starts, or ``None``."""
        if self._reason_holds(schedule, "owner-not-found"):
            return "owner-not-found"
        if self._reason_holds(schedule, "approval-required"):
            return "approval-required"
        return None

    def _stop_schedules_that_cannot_run(self) -> None:
        for schedule in _all_schedules(self._projection, ScheduleFilters(state="active")):
            reason = self._stop_reason(schedule)
            if reason is None:
                continue
            now = self._clock()
            stopped = ScheduleRecord.model_validate(
                {
                    **schedule.model_dump(),
                    "state": "stopped",
                    "state_reason": reason,
                    "prefect_synced": False,
                    "updated_at": now,
                }
            )
            event = schedule_event(
                event_type=SCHEDULE_STOPPED,
                actor=schedule.owner,
                operation=SCHEDULE_START,
                reason=None,
                outcome=reason,
                schedule_id=schedule.schedule_id,
                config_id=schedule.config_id,
                registry_version=schedule.registry_version,
                created_at=now,
            )
            try:
                self._projection.update_schedule(
                    stopped, expected_updated_at=schedule.updated_at, events=(event,), secrets=self._secrets
                )
            except (ScheduleChangedError, ScheduleDeletedError, ScheduleNotFoundError):
                # Another writer changed the schedule after it was read; the next start checks it again.
                logger.warning("schedules.stop_skipped", schedule_id=schedule.schedule_id, state_reason=reason)
                continue
            logger.warning("schedules.stopped", schedule_id=schedule.schedule_id, state_reason=reason)

    async def _matched(self, *, step: str = "request") -> bool:
        """Make Prefect match the store, and return whether it did.

        Every failure of the task manager counts as "could not be reached", whatever its class,
        as for the submission of a run: the schedule is stored, so the request must still save
        its answer and its event, and the API process must still start. Only the class name of
        the failure is logged, because the text of a provider error can hold an address.
        """
        try:
            await match_prefect_schedules(self._projection, self._orchestration)
        except OrchestrationUnavailableError as error:
            logger.warning("schedules.match_failed", step=step, error_class=error.error_class)
            return False
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # the remote boundary
            logger.warning("schedules.match_failed", step=step, error_class=type(error).__name__)
            return False
        return True

    # ------------------------------------------------------------------
    # One request
    # ------------------------------------------------------------------

    async def _act(
        self,
        schedule_id: str,
        context: _Request,
        body: dict[str, Any],
        idempotency_key: str,
        decide: Callable[[ScheduleRecord, datetime], ScheduleRecord],
    ) -> tuple[int, dict[str, Any]]:
        """Run one request on an existing schedule: ``decide`` returns the schedule to store.

        ``decide`` returns the schedule that it received when the request changes nothing.
        """
        schedule = self._owned(schedule_id, context)
        context = context.of(schedule)
        now = self._clock()
        receipt = self._receipt(context, idempotency_key, schedule_id, schedule_id, body, now)
        replayed = await self._replayed(receipt, context)
        if replayed is not None:
            return replayed
        reserved: MutationReceipt | None = None
        for _attempt in range(_SAVE_ATTEMPTS):
            if schedule.state == "deleted" and context.operation != DELETE:
                raise self._released(
                    reserved,
                    self._refusal(context, 409, "schedule-deleted", "the schedule is deleted and accepts no action"),
                )
            try:
                wanted = schedule if schedule.state == "deleted" else decide(schedule, now)
            except ServiceAPIError as error:
                raise self._released(reserved, error) from None
            if reserved is None:
                reserved = self._reserve(receipt, context)
            if wanted is schedule:
                return await self._answer(reserved, 200, context)
            wanted = wanted.model_copy(update={"prefect_synced": False, "updated_at": now})
            try:
                self._projection.update_schedule(wanted, expected_updated_at=schedule.updated_at, secrets=self._secrets)
            except (ScheduleChangedError, ScheduleDeletedError):
                # Another writer, such as the worker that stops a schedule, saved a change in between.
                reread = self._projection.lookup_schedule(schedule_id).value
                assert reread is not None
                schedule, now = reread, self._clock()
                continue
            return await self._answer(reserved, 200, context.of(wanted))
        raise self._released(
            reserved,
            self._refusal(
                context,
                409,
                "schedule-changed",
                "the schedule changed while the request was processed; send the request again",
            ),
        )

    def _released(self, receipt: MutationReceipt | None, error: ServiceAPIError) -> ServiceAPIError:
        """Make the receipt of a refused request available for a later attempt, and return the refusal."""
        if receipt is not None:
            self._projection.release_mutation(receipt.receipt_id, secrets=self._secrets)
        return error

    def _owned(self, schedule_id: str, context: _Request) -> ScheduleRecord:
        """Return the schedule when the caller is its owner or an administrator.

        Raises:
            ServiceAPIError: ``404 schedule-not-found`` or ``403 forbidden``.
        """
        schedule = self._projection.lookup_schedule(schedule_id).value
        if schedule is None:
            raise self._refusal(context, 404, "schedule-not-found", "the requested schedule does not exist")
        if not context.administrator and schedule.owner != context.actor:
            raise self._refusal(
                context.of(schedule),
                403,
                "forbidden",
                "only the owner of the schedule or an administrator may change, pause, resume or delete it",
                outcome="refused-authorization",
            )
        return schedule

    def _receipt(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        context: _Request,
        idempotency_key: str,
        target: str | None,
        resource_id: str,
        body: dict[str, Any],
        now: datetime,
    ) -> MutationReceipt:
        """Build the receipt of a schedule request; ``target`` is the schedule that the request acts on.

        The fingerprint leaves out the identifier that a create request generates, so a repeated
        create request has the fingerprint of the first.
        """
        fingerprint = sha256(
            canonical_json_bytes({"operation": context.operation, "schedule_id": target, "body": body})
        ).hexdigest()
        return MutationReceipt(
            receipt_id=f"m-{uuid4().hex}",
            actor=context.actor,
            key_digest=sha256(idempotency_key.encode()).hexdigest(),
            operation=context.operation,
            request_fingerprint=fingerprint,
            reason=context.reason,
            resource_kind="schedule",
            resource_id=resource_id,
            created_at=now,
            updated_at=now,
        )

    def _require_matching(self, stored: MutationReceipt, requested: MutationReceipt, context: _Request) -> None:
        if (stored.resource_kind, stored.operation, stored.request_fingerprint) != (
            requested.resource_kind,
            requested.operation,
            requested.request_fingerprint,
        ):
            raise self._refusal(
                context,
                409,
                "idempotency-conflict",
                "Idempotency-Key was already used by this actor for different content",
                outcome="refused-idempotency",
                mutation_id=stored.receipt_id,
            )

    async def _replayed(self, receipt: MutationReceipt, context: _Request) -> tuple[int, dict[str, Any]] | None:
        """Return the stored answer of a request that repeats an idempotency key, or ``None`` for a new request.

        A replay also runs the match with Prefect, so a request that was answered with
        ``orchestration-unavailable`` hands the schedule to Prefect when it is sent again.
        """
        existing = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        if existing is None:
            return None
        self._require_matching(existing, receipt, context)
        if existing.state != "accepted":
            return None
        assert existing.response_status is not None
        assert existing.response_body is not None
        schedule = self._projection.lookup_schedule(existing.resource_id).value
        self._audit(context if schedule is None else context.of(schedule), "replayed")
        if not await self._matched():
            raise self._error(503, "orchestration-unavailable", _UNAVAILABLE_MESSAGE, mutation_id=existing.receipt_id)
        return existing.response_status, existing.response_body

    def _reserve(self, receipt: MutationReceipt, context: _Request) -> MutationReceipt:
        """Reserve the receipt and make this request its one caller in progress.

        Raises:
            ServiceAPIError: ``409 idempotency-conflict`` or ``409 idempotency-in-progress``.
        """
        stored, _created = self._projection.reserve_mutation(receipt, secrets=self._secrets)
        self._require_matching(stored, receipt, context)
        if stored.state == "accepted" or not self._projection.claim_mutation(stored.receipt_id, secrets=self._secrets):
            raise self._refusal(
                context,
                409,
                "idempotency-in-progress",
                "the matching request is still being processed",
                mutation_id=stored.receipt_id,
            )
        return stored

    async def _answer(self, receipt: MutationReceipt, status: int, context: _Request) -> tuple[int, dict[str, Any]]:
        """Match Prefect to the store, then save the answer of an accepted request with its event.

        Raises:
            ServiceAPIError: ``503 orchestration-unavailable`` when Prefect could not be reached.
                The schedule and the answer are stored, and a repeated request returns the answer.
        """
        assert context.schedule_id is not None
        synced = await self._matched()
        schedule = self._projection.lookup_schedule(context.schedule_id).value
        assert schedule is not None
        if synced and schedule.state == "deleted" and not schedule.prefect_synced:
            # Prefect holds no schedule for a deleted schedule, which is what the record asks for.
            self._projection.record_schedule_prefect_sync(schedule.schedule_id, prefect_schedule_id=None, synced=True)
            schedule = schedule.model_copy(update={"prefect_schedule_id": None, "prefect_synced": True})
        completed = self._projection.complete_mutation(
            receipt.receipt_id,
            response_status=status,
            response_body=self._resource(schedule),
            flow_run_id=None,
            events=(self._event(context, "accepted"),),
            secrets=self._secrets,
        )
        if not synced:
            raise self._error(503, "orchestration-unavailable", _UNAVAILABLE_MESSAGE, mutation_id=receipt.receipt_id)
        assert completed.response_status is not None
        assert completed.response_body is not None
        return completed.response_status, completed.response_body

    # ------------------------------------------------------------------
    # The checks of a create and of a change
    # ------------------------------------------------------------------

    def _refuse_secret_values(self, context: _Request, **values: str | None) -> None:
        for name, value in values.items():
            if value is not None and redact(value, self._secrets) != value:
                raise self._refusal(
                    context,
                    422,
                    "secret-parameter-refused",
                    f"{name} must not hold a credential",
                    outcome="refused-secret-parameter",
                )

    def _declared_branch(self, context: _Request, config_id: str, registry_version: int) -> str | None:
        """Return the destination branch that the configuration version declares, or ``None``.

        Raises:
            ServiceAPIError: ``404 configuration-version-not-found``, or ``503
                configuration-version-invalid`` for a registered version that cannot be read.
        """
        stored = self._projection.lookup_configuration_version(config_id, registry_version).value
        if stored is None:
            raise self._refusal(
                context,
                404,
                "configuration-version-not-found",
                "the requested configuration version does not exist",
            )
        try:
            package = parse_configuration_package(stored.declared_content)
        except ConfigurationPackageParseError:
            raise self._refusal(
                context, 503, "configuration-version-invalid", "the registered configuration version is invalid"
            ) from None
        declared = (package.configuration.destination.settings or {}).get("branch")
        return declared if isinstance(declared, str) and declared else None

    def _require_cadence(self, context: _Request, cron: str, timezone_name: str) -> None:
        try:
            schedule_cadence.validate(cron, timezone_name)
        except schedule_cadence.InvalidCadenceError as error:
            code = "schedule-cron-invalid" if error.field == "cron" else "schedule-timezone-invalid"
            raise self._refusal(context, 422, code, error.reason) from None

    def _require_write_settings(
        self, context: _Request, *, write_target: str, write_when_left_out: bool, declared_branch: str | None
    ) -> None:
        """Refuse a write target that the approval setting or the configuration version does not allow."""
        if write_target == "default-branch" and approval_required():
            raise self._refusal(
                context,
                409,
                "approval-required-for-schedule",
                f"this Sync API requires an approved plan before any write ({REQUIRE_APPROVAL_ENV}), so a "
                "schedule cannot write to the default branch; use the write target branch-review",
            )
        if write_target == "branch-review" and declared_branch is not None:
            raise self._refusal(
                context,
                409,
                "schedule-branch-declared",
                "the configuration version declares its own destination branch, so a run with branch review "
                "would write to that branch with no review; use the write target default-branch",
            )
        if write_target == "branch-review" and write_when_left_out:
            # TODO: user story 2 (T040) gives this pair its own refusal, schedule-left-out-not-applicable.
            raise self._refusal(
                context,
                422,
                "schedule-setting-not-available",
                "write_when_left_out is not available with the write target branch-review",
            )

    def _require_delivered(
        self,
        context: _Request,
        *,
        write_target: str | None,
        overlap_policy: str | None,
        start_late_by_at_most_seconds: int | None,
    ) -> None:
        """Refuse a setting that this build does not run yet; ``None`` is a setting that a change leaves out."""
        if write_target is not None and write_target not in self._delivered_write_targets:
            message = f"the write target {write_target} is not available on this Sync API"
        elif overlap_policy == "queue":
            message = "the overlap policy queue is not available on this Sync API"
        elif start_late_by_at_most_seconds is not None:
            message = "start_late_by_at_most_seconds is not available on this Sync API"
        else:
            return
        raise self._refusal(context, 422, "schedule-setting-not-available", message)

    # ------------------------------------------------------------------
    # Resources, events and errors
    # ------------------------------------------------------------------

    def _emitted(self, schedule: ScheduleRecord) -> Any:
        """Build the bounded resource of a schedule, with the values that are computed and not stored."""
        next_start_at: datetime | None = None
        if schedule.state == "active":
            try:
                next_start_at = schedule_cadence.next_start(schedule.cron, schedule.timezone, self._clock())
            except schedule_cadence.InvalidCadenceError:
                next_start_at = None
        runs = self._projection.list_runs(RunFilters(schedule_id=schedule.schedule_id), limit=1).items
        open_branches = self._projection.schedule_branches(schedule.schedule_id, states=("open",))
        open_branch = None
        if open_branches:
            latest = open_branches[-1]
            open_branch = {
                "run_id": latest.run_id,
                "branch": latest.branch,
                "proposed_change_id": latest.proposed_change_id,
            }
        return EmittedScheduleResource.model_validate(
            {
                **schedule.model_dump(
                    include={
                        "schedule_id",
                        "name",
                        "config_id",
                        "registry_version",
                        "cron",
                        "timezone",
                        "write_target",
                        "write_when_left_out",
                        "left_out_set_by",
                        "left_out_set_at",
                        "overlap_policy",
                        "state",
                        "state_reason",
                        "owner",
                        "prefect_synced",
                        "consecutive_failures",
                        "created_at",
                        "updated_at",
                    }
                ),
                "next_start_at": next_start_at,
                "last_run_id": runs[0].run_id if runs else None,
                "open_branch": open_branch,
            }
        )

    def _resource(self, schedule: ScheduleRecord) -> dict[str, Any]:
        """Return the schedule resource as the API answers it, without the fields of user story 3."""
        return self._emitted(schedule).model_dump(mode="json", exclude=set(_FIELDS_NOT_DELIVERED))

    def _event(self, context: _Request, outcome: str) -> AuditEvent:
        return schedule_event(
            event_type=schedule_decision_type(context.operation, outcome),
            actor=context.actor,
            operation=context.operation,
            reason=context.reason,
            outcome=outcome,
            schedule_id=context.schedule_id,
            config_id=context.config_id,
            registry_version=context.registry_version,
            created_at=self._clock(),
        )

    def _audit(self, context: _Request, outcome: str) -> None:
        """Record the event of a decision that saves nothing else: a refusal or a replay."""
        self._projection.record_audit(self._event(context, outcome), secrets=self._secrets)

    def _refusal(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        context: _Request,
        status: int,
        code: str,
        message: str,
        *,
        outcome: str | None = None,
        mutation_id: str | None = None,
    ) -> ServiceAPIError:
        """Record the event of a refusal, then return the error for the caller to raise."""
        self._audit(context, outcome or f"refused-{code}")
        return self._error(status, code, message, mutation_id=mutation_id)

    def _error(self, status: int, code: str, message: str, *, mutation_id: str | None = None) -> ServiceAPIError:
        return ServiceAPIError(status, code, message, mutation_id=mutation_id, secrets=self._secrets)
