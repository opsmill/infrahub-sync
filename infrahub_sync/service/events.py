"""Build the typed event each Sync API decision and each stage result records (spec 017, FR-012).

The event type follows the table in the spec's data model: a request's outcome names a run or
configuration decision, and a stage's outcome names the stage result. A request refused for a
missing or invalid token gets no type, because the event list never returns it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from infrahub_sync.product_store import AuditEvent, ProductRun, RelatedResource

_RUN_PREFIX = "infrahub_sync.run."
_CONFIG_PREFIX = "infrahub_sync.config."
# The event a successful stage records, by stage.
_STAGE_SUCCESS_TYPES = {
    "plan": f"{_RUN_PREFIX}planned",
    "verify": f"{_RUN_PREFIX}verified",
    "apply": f"{_RUN_PREFIX}applied",
    "sync": f"{_RUN_PREFIX}synced",
}
RUN_CANCELLED = f"{_RUN_PREFIX}cancelled"
RUN_FAILED = f"{_RUN_PREFIX}failed"
CONFIG_REGISTERED = f"{_CONFIG_PREFIX}registered"
CONFIG_VERSION_REGISTERED = f"{_CONFIG_PREFIX}version_registered"
# A version request whose content matches an existing version returns that version.
CONFIG_VERSION_RETRIEVED = f"{_CONFIG_PREFIX}version_retrieved"


def _decision_action(outcome: str) -> str | None:
    """Name the action a request decision records, or nothing for a refused authentication."""
    if outcome == "refused-authentication":
        return None
    if outcome.startswith("refused-"):
        return "refused"
    if outcome in {"accepted", "replayed", "unavailable"}:
        return outcome
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
) -> tuple[RelatedResource, ...]:
    """Return the account, configuration, version and branch an event names, where known."""
    related = [RelatedResource(kind="account", id=actor)]
    if config_id is not None:
        related.append(RelatedResource(kind="config", id=config_id))
        if registry_version is not None:
            related.append(RelatedResource(kind="config_version", id=f"{config_id}@{registry_version}"))
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
) -> AuditEvent:
    """Build one run event; the configuration, version and branch come from ``run`` when it is given.

    ``created_at`` defaults to the current UTC time; a caller with an injected clock passes its value.
    """
    if run is not None:
        config_id, registry_version, branch = run.config_id, run.registry_version, run.destination_branch
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
        related=related_resources(actor, config_id=config_id, registry_version=registry_version, branch=branch),
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
