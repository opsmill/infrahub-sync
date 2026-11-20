"""Build the typed event each Sync API decision and each stage result records (spec 012, FR-012).

The event type follows the table in the spec's data model: a request's outcome names a run or
configuration decision, and a stage's outcome names the stage result. A request refused for a
missing or invalid token gets no type, because the event list never returns it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from infrahub_sync.product_store import (
    SCHEDULE_START_OPERATION,
    SCHEDULE_STOPPED_EVENT_TYPE,
    AuditEvent,
    EventDetails,
    ProductRun,
    RelatedResource,
)

_RUN_PREFIX = "infrahub_sync.run."
_CONFIG_PREFIX = "infrahub_sync.config."
_SCHEDULE_PREFIX = "infrahub_sync.schedule."
_ACCOUNT_PREFIX = "infrahub_sync.account."
_CREDENTIAL_PREFIX = "infrahub_sync.credential."
# The event a successful stage records, by stage.
_STAGE_SUCCESS_TYPES = {
    "plan": f"{_RUN_PREFIX}planned",
    "verify": f"{_RUN_PREFIX}verified",
    "apply": f"{_RUN_PREFIX}applied",
    "sync": f"{_RUN_PREFIX}synced",
    # A resume of an interrupted apply (spec 016): the rest of the saved plan was applied.
    "resume": f"{_RUN_PREFIX}resumed",
}
RUN_CANCELLED = f"{_RUN_PREFIX}cancelled"
RUN_FAILED = f"{_RUN_PREFIX}failed"
CONFIG_REGISTERED = f"{_CONFIG_PREFIX}registered"
CONFIG_VERSION_REGISTERED = f"{_CONFIG_PREFIX}version_registered"
# A version request whose content matches an existing version returns that version.
CONFIG_VERSION_RETRIEVED = f"{_CONFIG_PREFIX}version_retrieved"
# The events of a schedule request (spec 021, contracts/events.md).
SCHEDULE_CREATED = f"{_SCHEDULE_PREFIX}created"
SCHEDULE_CHANGED = f"{_SCHEDULE_PREFIX}changed"
SCHEDULE_PAUSED = f"{_SCHEDULE_PREFIX}paused"
SCHEDULE_RESUMED = f"{_SCHEDULE_PREFIX}resumed"
SCHEDULE_DELETED = f"{_SCHEDULE_PREFIX}deleted"
SCHEDULE_TAKEN_OVER = f"{_SCHEDULE_PREFIX}taken_over"
SCHEDULE_REFUSED = f"{_SCHEDULE_PREFIX}refused"
SCHEDULE_REPLAYED = f"{_SCHEDULE_PREFIX}replayed"
# The events of a start, written when the worker admits a scheduled flow run. Their
# ``operation`` is ``SCHEDULE_START``.
SCHEDULE_START = SCHEDULE_START_OPERATION
SCHEDULE_START_SKIPPED = f"{_SCHEDULE_PREFIX}start_skipped"
SCHEDULE_START_QUEUED = f"{_SCHEDULE_PREFIX}start_queued"
SCHEDULE_START_MISSED = f"{_SCHEDULE_PREFIX}start_missed"
SCHEDULE_START_FAILED = f"{_SCHEDULE_PREFIX}start_failed"
# The product store writes this event itself when the fifth failed run in a row ends, so it
# owns the name.
SCHEDULE_STOPPED = SCHEDULE_STOPPED_EVENT_TYPE
# The events of a proposed change, written by the worker after the write is committed.
SCHEDULE_PROPOSED_CHANGE_OPENED = f"{_SCHEDULE_PREFIX}proposed_change_opened"
SCHEDULE_PROPOSED_CHANGE_REPLACED = f"{_SCHEDULE_PREFIX}proposed_change_replaced"
SCHEDULE_PROPOSED_CHANGE_KEPT = f"{_SCHEDULE_PREFIX}proposed_change_kept"
SCHEDULE_PROPOSED_CHANGE_FAILED = f"{_SCHEDULE_PREFIX}proposed_change_failed"
# The event an accepted schedule request records, by the operation of the request.
_SCHEDULE_ACCEPTED_TYPES = {
    "schedule-create": SCHEDULE_CREATED,
    "schedule-change": SCHEDULE_CHANGED,
    "schedule-pause": SCHEDULE_PAUSED,
    "schedule-resume": SCHEDULE_RESUMED,
    "schedule-delete": SCHEDULE_DELETED,
    "schedule-take-over": SCHEDULE_TAKEN_OVER,
}
# The events of the account actions. An action is written with underscores, as every event type is.
ACCOUNT_SIGNED_IN = f"{_ACCOUNT_PREFIX}signed_in"
ACCOUNT_SIGNED_OUT = f"{_ACCOUNT_PREFIX}signed_out"
ACCOUNT_CREATED = f"{_ACCOUNT_PREFIX}created"
ACCOUNT_DISABLED = f"{_ACCOUNT_PREFIX}disabled"
ACCOUNT_ENABLED = f"{_ACCOUNT_PREFIX}enabled"
# An administrator set the password of an account.
ACCOUNT_PASSWORD_SET = f"{_ACCOUNT_PREFIX}password_set"
# An account changed its own password.
ACCOUNT_PASSWORD_CHANGED = f"{_ACCOUNT_PREFIX}password_changed"
# An administrator ended the refusal that follows repeated failed sign-ins.
ACCOUNT_UNLOCKED = f"{_ACCOUNT_PREFIX}unlocked"
ACCOUNT_TOKEN_CREATED = f"{_ACCOUNT_PREFIX}token_created"
ACCOUNT_TOKEN_DELETED = f"{_ACCOUNT_PREFIX}token_deleted"
# An administrator changed the level of an account.
ACCOUNT_LEVEL_SET = f"{_ACCOUNT_PREFIX}level_set"
# An administrator deleted an account. The store writes this event in the transaction of the deletion.
ACCOUNT_DELETED = f"{_ACCOUNT_PREFIX}deleted"
# A request for an account action that was refused: its outcome says why.
ACCOUNT_REFUSED = f"{_ACCOUNT_PREFIX}refused"


def _decision_action(outcome: str) -> str | None:
    """Name the action a request decision records, or nothing for a refused authentication."""
    if outcome == "refused-authentication":
        return None
    if outcome.startswith("refused-"):
        return "refused"
    if outcome in {"accepted", "replayed", "unavailable"}:
        return outcome
    # A cancel that withdraws a finished plan is an accepted request (spec 014).
    if outcome == "accepted-withdrawn":
        return "accepted"
    msg = f"no event type for the request outcome {outcome!r}"
    raise ValueError(msg)


def run_decision_type(outcome: str) -> str | None:
    """Return the run event type for a request's outcome, such as ``infrahub_sync.run.refused``."""
    action = _decision_action(outcome)
    return None if action is None else f"{_RUN_PREFIX}{action}"


def config_decision_type(outcome: str) -> str | None:
    """Return the configuration event type for a refused, replayed or unavailable request."""
    action = _decision_action(outcome)
    return None if action is None else f"{_CONFIG_PREFIX}{action}"


def schedule_decision_type(operation: str, outcome: str) -> str | None:
    """Return the schedule event type for the outcome of a schedule request.

    An accepted request records the event of its operation, such as
    ``infrahub_sync.schedule.paused`` for ``schedule-pause``. A refused request records
    ``infrahub_sync.schedule.refused`` and a repeated idempotency key
    ``infrahub_sync.schedule.replayed``. A request refused for a missing or invalid token has
    no type.

    Raises:
        ValueError: The outcome, or the operation of an accepted request, has no schedule event.
    """
    if outcome == "refused-authentication":
        return None
    if outcome.startswith("refused-"):
        return SCHEDULE_REFUSED
    if outcome == "replayed":
        return SCHEDULE_REPLAYED
    if outcome == "accepted":
        try:
            return _SCHEDULE_ACCEPTED_TYPES[operation]
        except KeyError:
            msg = f"no schedule event type for the operation {operation!r}"
            raise ValueError(msg) from None
    msg = f"no schedule event type for the request outcome {outcome!r}"
    raise ValueError(msg)


# The events of the stored credentials (spec 027, contracts/http-api.md). Each names the
# credential by its name and its revision, never a value.
CREDENTIAL_CREATED = f"{_CREDENTIAL_PREFIX}created"
CREDENTIAL_VALUE_CHANGED = f"{_CREDENTIAL_PREFIX}value_changed"
CREDENTIAL_UPDATED = f"{_CREDENTIAL_PREFIX}updated"
CREDENTIAL_REFUSED = f"{_CREDENTIAL_PREFIX}refused"


def credential_event(  # pylint: disable=too-many-arguments
    event_type: str,
    *,
    actor: str,
    operation: str,
    name: str | None,
    revision: int | None = None,
    outcome: str = "accepted",
    cause: str | None = None,
    created_at: datetime | None = None,
) -> AuditEvent:
    """Build the event of one credential action, which names the credential and its revision.

    ``actor`` is an account identifier. ``name`` is ``None`` for a refusal that came before the
    request named a credential that can be recorded. The event holds no value.
    """
    related = [RelatedResource(kind="account", id=actor)]
    if name is not None:
        related.append(RelatedResource(kind="credential", id=name))
    details = None if revision is None and cause is None else EventDetails(revision=revision, cause=cause)
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=None,
        actor=actor,
        operation=operation,
        reason=None,
        outcome=outcome,
        created_at=created_at or datetime.now(timezone.utc),
        event_type=event_type,
        related=tuple(related),
        details=details,
    )


def stage_result_type(stage: str, outcome: str) -> str:
    """Return the event type of a stage result: the stage's success type, else ``run.failed``."""
    if outcome == "succeeded":
        try:
            return _STAGE_SUCCESS_TYPES[stage]
        except KeyError:
            msg = f"no event type for the stage {stage!r}"
            raise ValueError(msg) from None
    return RUN_FAILED


def related_resources(
    actor: str,
    *,
    config_id: str | None = None,
    registry_version: int | None = None,
    branch: str | None = None,
    schedule_id: str | None = None,
) -> tuple[RelatedResource, ...]:
    """Return the account, configuration, version, schedule and branch an event names, where known."""
    related = [RelatedResource(kind="account", id=actor)]
    if config_id is not None:
        related.append(RelatedResource(kind="config", id=config_id))
        if registry_version is not None:
            related.append(RelatedResource(kind="config_version", id=f"{config_id}@{registry_version}"))
    if schedule_id is not None:
        related.append(RelatedResource(kind="schedule", id=schedule_id))
    if branch is not None:
        related.append(RelatedResource(kind="branch", id=branch))
    return tuple(related)


def run_event(  # pylint: disable=too-many-arguments
    run_id: str | None,
    run: ProductRun | None,
    *,
    event_type: str | None,
    actor: str,
    operation: str,
    reason: str | None,
    outcome: str,
    config_id: str | None = None,
    registry_version: int | None = None,
    branch: str | None = None,
    created_at: datetime | None = None,
    details: EventDetails | None = None,
) -> AuditEvent:
    """Build one run event; the configuration, version and branch come from ``run`` when it is given.

    An event of a run that a schedule started also names the schedule as a related resource, so
    the events of a schedule include the events of its runs (spec 021, research R9). ``details``
    holds the facts of the event, such as the left-out count of a scheduled run's result.
    ``created_at`` defaults to the current UTC time; a caller with an injected clock passes its value.
    """
    schedule_id = None
    if run is not None:
        config_id, registry_version, branch = run.config_id, run.registry_version, run.destination_branch
        schedule_id = run.schedule_id
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=run_id,
        actor=actor,
        operation=operation,
        reason=reason,
        outcome=outcome,
        created_at=created_at or datetime.now(timezone.utc),
        event_type=event_type,
        config_id=config_id,
        registry_version=registry_version,
        branch=branch,
        related=related_resources(
            actor, config_id=config_id, registry_version=registry_version, branch=branch, schedule_id=schedule_id
        ),
        details=details,
    )


def config_event(
    *,
    event_type: str | None,
    actor: str,
    operation: str,
    reason: str | None,
    outcome: str,
    config_id: str | None,
    registry_version: int | None = None,
    created_at: datetime | None = None,
) -> AuditEvent:
    """Build one configuration event, whose primary resource is the configuration when it is known.

    ``created_at`` defaults to the current UTC time; a caller with an injected clock passes its value.
    """
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=None,
        actor=actor,
        operation=operation,
        reason=reason,
        outcome=outcome,
        created_at=created_at or datetime.now(timezone.utc),
        event_type=event_type,
        config_id=config_id,
        registry_version=registry_version if config_id is not None else None,
        related=related_resources(actor, config_id=config_id, registry_version=registry_version),
    )


def schedule_event(  # pylint: disable=too-many-arguments
    *,
    event_type: str | None,
    actor: str,
    operation: str,
    reason: str | None,
    outcome: str,
    schedule_id: str | None,
    config_id: str | None = None,
    registry_version: int | None = None,
    run_id: str | None = None,
    details: EventDetails | None = None,
    created_at: datetime | None = None,
) -> AuditEvent:
    """Build one schedule event, which names the schedule, its configuration and the version.

    ``schedule_id`` is ``None`` only for a request that is refused before a schedule exists.
    ``run_id`` is the run that the event concerns: the run that a start created, or the run in
    progress that caused a skipped start. ``reason`` holds only the text that a person gave
    with a request; the facts of the event, such as the start time, go in ``details``.
    ``created_at`` defaults to the current UTC time; a caller with an injected clock passes its value.
    """
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=run_id,
        actor=actor,
        operation=operation,
        reason=reason,
        outcome=outcome,
        created_at=created_at or datetime.now(timezone.utc),
        event_type=event_type,
        config_id=config_id,
        registry_version=registry_version if config_id is not None else None,
        related=related_resources(
            actor, config_id=config_id, registry_version=registry_version, schedule_id=schedule_id
        ),
        details=details,
    )


def account_event(
    event_type: str,
    *,
    actor: str,
    account_id: str,
    operation: str,
    reason: str | None = None,
    outcome: str = "accepted",
    created_at: datetime | None = None,
) -> AuditEvent:
    """Build the event of one account action, which names the account acted on.

    ``actor`` and ``account_id`` are account identifiers, never names: ``actor`` made the request
    and ``account_id`` is the account that the action changed. They are the same for an action
    of a person on their own account. The event has no primary resource.

    ``created_at`` defaults to the current UTC time; a caller with an injected clock passes its value.
    """
    related = {RelatedResource(kind="account", id=identifier) for identifier in (actor, account_id)}
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=None,
        actor=actor,
        operation=operation,
        reason=reason,
        outcome=outcome,
        created_at=created_at or datetime.now(timezone.utc),
        event_type=event_type,
        related=tuple(related),
    )
