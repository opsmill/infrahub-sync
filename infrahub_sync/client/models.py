# pylint: disable=too-many-lines  # one module holds every request and resource model of the Sync HTTP API
"""Standalone request and resource models for the Sync HTTP API."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves resource annotations at runtime.
from math import isfinite
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, TypeVar, cast, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

Operation = Literal["plan", "verify", "apply", "sync"]
TerminalState = Literal["completed", "failed", "cancelled", "abandoned", "interrupted"]
TerminalOutcome = Literal["succeeded", "failed", "cancelled", "abandoned", "ambiguous"]

# The longest `cause` and `next_action` text a failed stage records in a run's results, and the
# longest the CLI prints of each.
MAX_FAILURE_CAUSE_TEXT = 1000

# The fixed next actions the service records and the CLI prints as `hint:`. Defined here because
# the CLI and the service both import this module, and the CLI uses them for a record an older
# Sync API wrote without a `next_action`. Fixed text, so a record never quotes the manifest
# values a damaged or hand-edited artifact may hold.
SCHEMA_CHANGED_NEXT_ACTION = "create and review a new plan before applying again"

# What started a run: a request to `POST /runs`, or a schedule.
TriggerType = Literal["manual", "scheduled"]

# Where the runs of a schedule write: a branch with a proposed change for review, or the default branch.
ScheduleWriteTarget = Literal["branch-review", "default-branch"]

# What a schedule does with a start while a run of its configuration is in progress.
ScheduleOverlapPolicy = Literal["reject", "queue"]

# The range of `start_late_by_at_most_seconds`: from one minute to seven days.
MIN_START_LATE_SECONDS = 60
MAX_START_LATE_SECONDS = 604800

# The count of start times that `POST /schedules/next-starts` returns by default, and its largest count.
DEFAULT_NEXT_STARTS = 5
MAX_NEXT_STARTS = 20

# The recovery signal a refused registered plan carries.
RecoveryAction = Literal["rebuild", "compatible_version"]

# The next action for each recovery signal.
RECOVERY_NEXT_ACTIONS: dict[RecoveryAction, str] = {
    "rebuild": "re-run plan for this sync, review the new plan, then apply that run",
    "compatible_version": (
        "re-run plan with this version and review the new plan, "
        "or apply the reviewed plan with the version that wrote it"
    ),
}

# Page sizes of the run, configuration and version lists: the default when a request gives
# none, and the accepted range.
DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 200

# Page sizes of a run's log (spec 012, contracts/logs-route.md), and the stages it filters by.
DEFAULT_LOG_LIMIT = 200
MAX_LOG_LIMIT = 1000
LOG_STAGES: tuple[str, ...] = ("plan", "verify", "apply", "sync", "resume")

# The largest page of a saved plan's problem, left-out or kept list, and the default (spec 007,
# contracts/sync-api.md).
MAX_REPORT_LIMIT = 500

_Item_co = TypeVar("_Item_co", covariant=True)


class ListPage(Protocol[_Item_co]):
    """A list response envelope: one page of items and the cursor of the next page."""

    @property
    def items(self) -> tuple[_Item_co, ...]: ...

    @property
    def next_cursor(self) -> str | None: ...


_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_LEGAL_EXECUTION_VERDICTS = {
    ("completed", "succeeded"),
    ("failed", "failed"),
    ("cancelled", "cancelled"),
    ("abandoned", "abandoned"),
    ("interrupted", "ambiguous"),
}
_REGISTRY_VERSION_MESSAGE = "registry_version must be an integer in the registry allocatable range"
_REASON_MESSAGE = "reason must be printable and trimmed"
# The longest reason a request can give for an action.
REASON_MAX_LENGTH = 512
# The rule of a local account name (spec 022, research R8), which the Sync API and the client both
# apply: no space, so a name differs from every placeholder and is safe in a URL path.
ACCOUNT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$"
# The name of a stored credential, as a package names it in ``credential:`` (spec 027).
CREDENTIAL_NAME_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
_JSON_PACKAGE_MESSAGE = "package must be recursively exact JSON-native"
_PACKAGE_CHECK_FIELDS_MESSAGE = "exactly one of package and document is required"
_ARTIFACT_TIME_MESSAGE = "artifact-reference timestamps must include a timezone"
_EXECUTION_TIME_MESSAGE = "execution timestamps must include a timezone"
_ARTIFACT_KEY_MESSAGE = "artifact object and manifest keys must include the content digest"
_BINDING_MESSAGE = "configuration binding must be all absent or all present"
_FINISHED_RUN_MESSAGE = "a finished product record requires an outcome"
_SCHEDULED_RUN_MESSAGE = "a scheduled run requires its schedule and its scheduled start time"
_MANUAL_RUN_MESSAGE = "a manual run cannot name a schedule or a scheduled start time"
_TERMINAL_FIELDS_MESSAGE = "execution terminal fields must be all absent or all present"
_TERMINAL_VERDICT_MESSAGE = "execution terminal verdict is invalid"
_HEALTH_ERROR_MESSAGE = "a component error must be none exactly when the component is up"
_HEALTH_DEPENDENCIES_MESSAGE = "a health report must list at least one component, each once"
_HEALTH_STATUS_MESSAGE = (
    "a health report must be healthy exactly when every component is up and enough workers are live"
)
_HEALTH_TIME_MESSAGE = "health timestamps must include a timezone"
_WORKERS_HEALTH_MESSAGE = "worker details must be present exactly when the worker state is known"
_ARTIFACT_IDS_MESSAGE = "artifact IDs must be unique within a product record"
_ARTIFACT_OWNER_MESSAGE = "every artifact reference must belong to the product record's run ID"
_EXECUTION_IDS_MESSAGE = "execution IDs must be unique within a product record"
_SCHEDULE_TIME_MESSAGE = "schedule timestamps must include a timezone"
_SCHEDULE_CHANGE_MESSAGE = "a schedule change must name at least one setting to change"
_EVENT_TIME_MESSAGE = "event timestamps must include a timezone"


def _timezone(value: datetime | None, message: str) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(message)
    return value


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _ResourceModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, str_strip_whitespace=True)


class CreateRunRequest(_RequestModel):
    """Request creation of a plan or confirmed synchronization run."""

    operation: Literal["plan", "sync"] = "plan"
    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int
    branch: str | None = None
    confirm_writes: bool = False
    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_registry_version(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 2**63 - 1:  # pylint: disable=unidiomatic-typecheck
            raise ValueError(_REGISTRY_VERSION_MESSAGE)
        return value


class VerifyRunRequest(_RequestModel):
    """Request verification of a saved run plan."""

    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)


class ApplyRunRequest(_RequestModel):
    """Request application of a reviewed plan checksum.

    `confirm_left_out` confirms that the person reviewed the records the plan leaves out
    (spec 007 FR-010). It is a strict JSON boolean, and it is serialized only when true, so a
    request that does not confirm keeps today's body and today's idempotency fingerprint.
    """

    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_writes: bool = False
    confirm_left_out: StrictBool = False
    branch: str | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)

    # No return annotation on purpose: with one, Pydantic replaces the model's serialization
    # JSON schema (and so the OpenAPI document) with a bare object.
    @model_serializer(mode="wrap")
    def _omit_unconfirmed_left_out(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if not self.confirm_left_out:
            data.pop("confirm_left_out", None)
        return data


class ApproveRunRequest(_RequestModel):
    """Request approval of a run's saved plan, bound to the reviewed checksum, without applying it."""

    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)


class CancelRunRequest(_RequestModel):
    """Request cancellation of an accepted run."""

    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)


class ResumeRunRequest(_RequestModel):
    """Request a resume of an interrupted apply from the run's saved position (spec 016 data-model.md).

    The checks match `ApplyRunRequest`: `expected_checksum` must equal the saved plan's checksum,
    `confirm_writes` must be true, and `confirm_left_out` is required when the plan left out records.
    """

    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_writes: bool = False
    confirm_left_out: StrictBool = False
    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)


def _json_native_package(value: dict[str, Any]) -> dict[str, Any]:
    """Refuse a package that is not recursively exact JSON-native: what the service hashes."""

    def visit(item: object) -> None:
        item_type = type(item)
        if item_type is dict:
            for key, child in cast("dict[object, object]", item).items():
                if type(key) is not str:  # pylint: disable=unidiomatic-typecheck
                    raise ValueError(_JSON_PACKAGE_MESSAGE)
                visit(child)
            return
        if item_type is list:
            for child in cast("list[object]", item):
                visit(child)
            return
        if item_type is float and not isfinite(cast("float", item)):
            raise ValueError(_JSON_PACKAGE_MESSAGE)
        if item_type not in {str, int, float, bool, type(None)}:
            raise ValueError(_JSON_PACKAGE_MESSAGE)

    visit(value)
    return value


class ConfigMutationRequest(_RequestModel):
    """Submit a configuration package with an optional audit reason.

    The package is ``package``, as JSON, or ``document``, the text of a package file, YAML or
    JSON, which the Sync API parses as `POST /configs/validate` does. The web interface sends
    ``document``, because a browser that parses and serializes the file again can change its
    numbers; the CLI sends ``package``. Exactly one is set. The Sync API checks that rule
    itself, after the administrator check, so this model shared with it does not; the field
    that is not set is omitted.

    ``registered_without_validation`` marks the created version as registered without
    validation. The web interface sets it; the CLI never does. A request without the mark
    keeps today's body, so it is omitted when ``false``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)
    package: dict[str, Any] | None = None
    document: str | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=REASON_MAX_LENGTH)
    registered_without_validation: StrictBool = False

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or not value.isprintable()):
            raise ValueError(_REASON_MESSAGE)
        return value

    @field_validator("package")
    @classmethod
    def _require_json_package(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _json_native_package(value)

    # No return annotation on purpose: with one, Pydantic replaces the model's serialization
    # JSON schema (and so the OpenAPI document) with a bare object.
    @model_serializer(mode="wrap")
    def _omit_unset_fields(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        for field in ("package", "document"):
            if data.get(field) is None:
                data.pop(field, None)
        if not self.registered_without_validation:
            data.pop("registered_without_validation", None)
        return data


class PackageCheckRequest(_RequestModel):
    """Submit a package to check without registering it: as JSON, or as the text of a package file.

    Exactly one of ``package`` and ``document`` is set, and only that field is sent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)
    package: dict[str, Any] | None = None
    document: str | None = None

    @field_validator("package")
    @classmethod
    def _require_json_package(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _json_native_package(value)

    @model_validator(mode="after")
    def _require_exactly_one(self) -> PackageCheckRequest:
        if (self.package is None) == (self.document is None):
            raise ValueError(_PACKAGE_CHECK_FIELDS_MESSAGE)
        return self

    # No return annotation on purpose: with one, Pydantic replaces the model's serialization
    # JSON schema (and so the OpenAPI document) with a bare object.
    @model_serializer(mode="wrap")
    def _omit_the_unset_field(self, handler: SerializerFunctionWrapHandler):
        return {key: value for key, value in handler(self).items() if value is not None}


def _registry_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:  # pylint: disable=unidiomatic-typecheck
        raise ValueError(_REGISTRY_VERSION_MESSAGE)
    return value


class CreateScheduleRequest(_RequestModel):
    """Request a schedule that starts a `sync` run of one configuration version on a cron cadence.

    `cron` and `timezone` are text that this model does not validate: the Sync API validates
    both and answers `schedule-cron-invalid` or `schedule-timezone-invalid`. The Sync API also
    decides which pairs of settings it accepts. `start_late_by_at_most_seconds` is `None` when
    the request does not set it, and the Sync API then uses 3600.
    """

    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int
    cron: str
    timezone: str = "UTC"
    write_target: ScheduleWriteTarget = "branch-review"
    write_when_left_out: StrictBool = False
    overlap_policy: ScheduleOverlapPolicy = "reject"
    start_late_by_at_most_seconds: int | None = Field(
        default=None, ge=MIN_START_LATE_SECONDS, le=MAX_START_LATE_SECONDS
    )
    reason: str | None = Field(default=None, min_length=1)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_registry_version(cls, value: object) -> int:
        return _registry_version(value)


class ChangeScheduleRequest(_RequestModel):
    """Request a change of one or more settings of a schedule.

    A setting that is `None` keeps its value. The request must name at least one setting. The
    name, the configuration and the owner of a schedule cannot be changed, so the model refuses
    them as unknown fields. `cron` and `timezone` are not validated here, as in
    `CreateScheduleRequest`.
    """

    registry_version: int | None = None
    cron: str | None = None
    timezone: str | None = None
    write_target: ScheduleWriteTarget | None = None
    write_when_left_out: StrictBool | None = None
    overlap_policy: ScheduleOverlapPolicy | None = None
    start_late_by_at_most_seconds: int | None = Field(
        default=None, ge=MIN_START_LATE_SECONDS, le=MAX_START_LATE_SECONDS
    )
    reason: str | None = Field(default=None, min_length=1)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_registry_version(cls, value: object) -> int | None:
        return None if value is None else _registry_version(value)

    @model_validator(mode="after")
    def _require_a_change(self) -> ChangeScheduleRequest:
        if all(getattr(self, name) is None for name in type(self).model_fields if name != "reason"):
            raise ValueError(_SCHEDULE_CHANGE_MESSAGE)
        return self


class ScheduleActionRequest(_RequestModel):
    """Request a pause, a resume, a deletion or a take-over of a schedule, with an optional reason."""

    reason: str | None = Field(default=None, min_length=1)


class ArtifactReferenceResource(_ResourceModel):
    """Describe one immutable run artifact."""

    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    object_key: str = Field(min_length=1)
    manifest_key: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime | None = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _ARTIFACT_TIME_MESSAGE)

    @model_validator(mode="after")
    def _require_immutable_keys(self) -> ArtifactReferenceResource:
        marker = f"/{self.digest}/"
        if marker not in f"/{self.object_key}" or marker not in f"/{self.manifest_key}":
            raise ValueError(_ARTIFACT_KEY_MESSAGE)
        return self


class PublicExecutionLink(_ResourceModel):
    """Expose the public fields that link a run to one execution."""

    flow_run_id: str = Field(min_length=1)
    deployment_id: str | None = None
    purpose: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    last_observed_state: str | None = None
    last_observed_at: datetime | None = None

    @field_validator("last_observed_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, "execution timestamps must include a timezone")


class RunApprovalResource(_ResourceModel):
    """The one recorded approval of a run's saved plan: who approved which checksum, and when."""

    approved_by: str = Field(min_length=1)
    approved_at: datetime
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = None

    @field_validator("approved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "approval timestamps must include a timezone")
        assert checked is not None
        return checked


class PublicRunResource(_ResourceModel):
    """Represent the public product record for one Sync run."""

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: Operation
    configuration_reference: str = Field(min_length=1)
    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    registry_version: int | None = Field(default=None, ge=1, le=2**63 - 1)
    package_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    actor: str | None = None
    audit_links: tuple[str, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    phase: str = Field(min_length=1)
    outcome: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    # Exposed directly rather than through ``results``: an operator deciding whether a run
    # needs reconciling must not have to parse failure evidence to find out.
    reconciliation_required: bool = False
    # The effective Infrahub branch the run writes to; ``None`` for runs created before
    # the store recorded it.
    destination_branch: str | None = None
    # The run's one approval of its saved plan; ``None`` when the plan was never approved.
    # Approval is not a phase: an approved run stays in phase ``planned``.
    approval: RunApprovalResource | None = None
    # What started the run. A Sync API that predates schedules returns none of the three
    # fields, and the run then reads as a manual run.
    trigger_type: TriggerType = "manual"
    # The schedule that started the run, and the start time of its cadence, in UTC. Both are
    # ``None`` for a manual run.
    schedule_id: str | None = None
    scheduled_start_at: datetime | None = None
    artifact_refs: tuple[ArtifactReferenceResource, ...] = ()
    prefect_executions: tuple[PublicExecutionLink, ...] = ()
    # How many times Sync resumed the run's interrupted write without a person (spec 030).
    # A Sync API that predates spec 030 returns no value, and the run reads as never resumed.
    automatic_resume_count: int = Field(default=0, ge=0)

    @field_validator("started_at", "finished_at", "scheduled_start_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, "product-record timestamps must include a timezone")

    @model_validator(mode="after")
    def _require_consistent_binding(self) -> PublicRunResource:
        binding = (self.config_id, self.registry_version, self.package_checksum)
        if any(value is None for value in binding) and any(value is not None for value in binding):
            raise ValueError(_BINDING_MESSAGE)
        start = (self.schedule_id, self.scheduled_start_at)
        if self.trigger_type == "scheduled" and any(value is None for value in start):
            raise ValueError(_SCHEDULED_RUN_MESSAGE)
        if self.trigger_type == "manual" and any(value is not None for value in start):
            raise ValueError(_MANUAL_RUN_MESSAGE)
        if self.finished_at is not None and self.outcome is None:
            raise ValueError(_FINISHED_RUN_MESSAGE)
        artifact_ids = [reference.artifact_id for reference in self.artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(_ARTIFACT_IDS_MESSAGE)
        if any(reference.run_id != self.run_id for reference in self.artifact_refs):
            raise ValueError(_ARTIFACT_OWNER_MESSAGE)
        flow_run_ids = [link.flow_run_id for link in self.prefect_executions]
        if len(flow_run_ids) != len(set(flow_run_ids)):
            raise ValueError(_EXECUTION_IDS_MESSAGE)
        return self


# The states a side can have today. `SideRead.state` accepts any string, so a client keeps reading
# a run when a later Sync API returns a state this release does not know.
SideReadState = Literal["waiting", "reading", "succeeded", "failed", "not_started", "interrupted"]


class SideRead(_ResourceModel):
    """The read state of one side of a `plan` or `sync` execution.

    `not_started` and `interrupted` are never stored: the Sync API returns a side stored as
    `waiting` or `reading` with them once the execution ended without completing. No field holds
    an exception message; `error_class` is the class name of the error that ended the read.

    `state` is one of `SideReadState` and `read_mode` is `full` or `incremental` today. Both are
    plain strings, so a state or mode a later Sync API adds does not fail the whole run response.
    """

    state: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    read_mode: str | None = None
    nodes_by_kind: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    error_class: str | None = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _EXECUTION_TIME_MESSAGE)


class StageReads(_ResourceModel):
    """The read state of the source and the destination of one execution."""

    source: SideRead | None = None
    destination: SideRead | None = None


class PlanStepPart(_ResourceModel):
    """The times of one part of the plan step of a `plan` or `sync` execution.

    `ended_at` is set once the part ended. `stopped_at` is set for the part that was running
    when the plan step failed, was cancelled or was interrupted: the time of that verdict. A part
    that runs has `started_at` only. No field holds an error message or a data value.
    """

    started_at: datetime
    ended_at: datetime | None = None
    stopped_at: datetime | None = None

    @field_validator("started_at", "ended_at", "stopped_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _EXECUTION_TIME_MESSAGE)


class PlanStepTimings(_ResourceModel):
    """The timings of the plan step of one `plan` or `sync` execution; subject to change.

    `parts` maps a part name to its times, in the order the plan step runs them: `waiting`,
    `preparing`, `comparing`, `checking` and `saving` today. A part that did not start has no
    entry. `running` names the part that runs, or is `None`. Part names are plain strings, so a
    part a later Sync API adds does not fail the whole run response. The two reads keep their
    times in `OrchestrationSummary.reads`.
    """

    running: str | None = None
    parts: dict[str, PlanStepPart] = Field(default_factory=dict)


class OrchestrationSummary(_ResourceModel):
    """Summarize one orchestration attempt and its terminal verdict."""

    flow_run_id: str
    purpose: str
    attempt: int
    state: str | None
    detail_available: bool
    unavailable_reason: str | None = None
    submitted_at: datetime | None
    claimed_at: datetime | None
    stalled_at: datetime | None
    cancellation_requested_at: datetime | None
    cancellation_recovery_deadline_at: datetime | None
    cancellation_acknowledged_at: datetime | None
    terminal_at: datetime | None
    terminal_state: TerminalState | None
    terminal_outcome: TerminalOutcome | None
    # Absent from a Sync API without a Prefect UI address, or one that predates the link.
    prefect_flow_run_url: str | None = None
    # Each side's read for a `plan` or `sync` execution; `None` for a `verify` or `apply`
    # execution, one recorded before the read state, or a Sync API that predates it.
    reads: StageReads | None = None
    # The timings of the plan step of a `plan` or `sync` execution; `None` for a `verify` or
    # `apply` execution, one recorded before the timings, or a Sync API that predates them.
    plan_step: PlanStepTimings | None = None

    @field_validator(
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
    )
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _EXECUTION_TIME_MESSAGE)

    @model_validator(mode="after")
    def _require_terminal_verdict(self) -> OrchestrationSummary:
        terminal = (self.terminal_at, self.terminal_state, self.terminal_outcome)
        if any(value is None for value in terminal) and any(value is not None for value in terminal):
            raise ValueError(_TERMINAL_FIELDS_MESSAGE)
        verdict = (self.terminal_state, self.terminal_outcome)
        if self.terminal_state is not None and verdict not in _LEGAL_EXECUTION_VERDICTS:
            raise ValueError(_TERMINAL_VERDICT_MESSAGE)
        return self


class VersionResource(_ResourceModel):
    """Declare the server and API versions supported by the service."""

    server_version: str
    api_versions: tuple[str, ...]
    stability: str
    # The installed `infrahub-sdk` version; `None` from a server that predates the field, or
    # when the service cannot read the SDK's package metadata.
    sdk_version: str | None = None


class DependencyHealthResource(_ResourceModel):
    """Report whether one component of the Sync API answered its health check."""

    name: Literal["database", "object_store", "task_manager", "task_manager_db"]
    status: Literal["up", "down"]
    error: Literal[
        "none",
        "timeout",
        "connection_refused",
        "connection_closed",
        "name_resolution_failed",
        "unavailable",
        "unknown_error",
    ]

    @model_validator(mode="after")
    def _require_error_for_down(self) -> DependencyHealthResource:
        if (self.status == "up") != (self.error == "none"):
            raise ValueError(_HEALTH_ERROR_MESSAGE)
        return self


class WorkerHealthResource(_ResourceModel):
    """Report one worker that sent a heartbeat within the last 24 hours."""

    name: str
    state: Literal["live", "stale"]
    last_heartbeat: datetime

    @field_validator("last_heartbeat")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, _HEALTH_TIME_MESSAGE)
        assert checked is not None
        return checked


class WorkersHealthResource(_ResourceModel):
    """Report the workers of the work pool, or that the work pool could not be read."""

    state: Literal["known", "unknown"]
    minimum: int = Field(ge=1, strict=True)
    live_workers: int | None = Field(ge=0, strict=True)
    queue_depth: int | None = Field(ge=0, strict=True)
    items: list[WorkerHealthResource]

    @model_validator(mode="after")
    def _require_details_when_known(self) -> WorkersHealthResource:
        if self.state == "unknown":
            if self.live_workers is not None or self.queue_depth is not None or self.items:
                raise ValueError(_WORKERS_HEALTH_MESSAGE)
        elif self.live_workers is None or self.queue_depth is None:
            raise ValueError(_WORKERS_HEALTH_MESSAGE)
        return self


def expected_health_status(
    dependencies: Sequence[DependencyHealthResource], workers: WorkersHealthResource
) -> Literal["healthy", "unhealthy"]:
    """Return the status a health report must carry for these components and workers.

    ``healthy`` exactly when every component is up, the work pool was read, and at least
    ``minimum`` workers are live. The Sync API builds its report with this rule, and the client
    refuses a report that breaks it.
    """
    if any(dependency.status != "up" for dependency in dependencies):
        return "unhealthy"
    if workers.state != "known" or workers.live_workers is None or workers.live_workers < workers.minimum:
        return "unhealthy"
    return "healthy"


class HealthResource(_ResourceModel):
    """Report the health of the Sync API's components and of its workers.

    The model does not fix the number or the order of the components: it requires at least one,
    each named once, and a ``status`` that matches the components and the workers.
    """

    status: Literal["healthy", "unhealthy"]
    dependencies: list[DependencyHealthResource]
    workers: WorkersHealthResource
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, _HEALTH_TIME_MESSAGE)
        assert checked is not None
        return checked

    @model_validator(mode="after")
    def _require_consistent_report(self) -> HealthResource:
        names = [dependency.name for dependency in self.dependencies]
        if not names or len(set(names)) != len(names):
            raise ValueError(_HEALTH_DEPENDENCIES_MESSAGE)
        if self.status != expected_health_status(self.dependencies, self.workers):
            raise ValueError(_HEALTH_STATUS_MESSAGE)
        return self


class PrincipalResource(_ResourceModel):
    """Name the authenticated caller, whether it is an administrator, and whether the site requires approval.

    ``approval_required`` defaults to ``False`` for a Sync API that predates the setting, since such a
    service never requires approval.
    """

    actor: str
    administrator: bool
    approval_required: bool = False


class ApplyProgressKindResource(_ResourceModel):
    """How many of one kind's operations were committed, are uncertain, and were not reached."""

    kind: str
    committed: int = Field(ge=0)
    uncertain: int = Field(ge=0)
    not_reached: int = Field(ge=0)


class ApplyProgressResource(_ResourceModel):
    """How far a run's write got through its saved plan (spec 016 data-model.md "Run progress view").

    `position` is the highest saved position over all the run's write executions, or `None` when
    none was saved. `resumable` is true when the run's write was interrupted and no execution is
    unfinished; `verify_required` is true when no `verify` passed after the latest interruption.
    """

    position: int | None = Field(default=None, ge=0)
    saved_at: datetime | None = None
    resumable: bool = False
    verify_required: bool = False
    by_kind: tuple[ApplyProgressKindResource, ...] = ()

    @field_validator("saved_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _EXECUTION_TIME_MESSAGE)


class ResumeResource(_ResourceModel):
    """One resume request of a run: who asked, when, why, where it started, and how it ended.

    `outcome` is the resume execution's terminal outcome, or `None` while it has not ended.
    `operations_written` is the number of creates and updates this resume committed: every one after
    `start_after` for a resume that succeeded, otherwise those through its own last saved position,
    a lower bound for a resume that was stopped after writing past it. `None` when the saved plan
    cannot be read.
    """

    actor: str
    requested_at: datetime
    reason: str | None = None
    start_after: int | None = Field(default=None, ge=0)
    outcome: str | None = None
    operations_written: int | None = Field(default=None, ge=0)

    @field_validator("requested_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, _EXECUTION_TIME_MESSAGE)
        assert checked is not None
        return checked


class CredentialUsedResource(_ResourceModel):
    """The stored credential that one stage of a run received for one block: its name and revision, never a value.

    `stage` is `plan`, `verify`, `apply`, `sync` or `resume`, and `block` is `source` or
    `destination`. Both are plain strings so the client still reads a value a newer server adds.
    The name and the revision are the ones recorded when the stage received the values, so they
    stay after the credential is changed or deleted.
    """

    stage: str
    block: str
    name: str
    revision: int = Field(ge=1)


class AutomaticResumeResource(_ResourceModel):
    """Sync's decision on resuming one interrupted write without a person (spec 030 contracts/http-api.md).

    `state` is `waiting-check`, `checking`, `waiting-resume`, `resuming` or `ended`. `decision` is
    `resume` once Sync submitted the resume, and `no-resume` with a `reason` code when it did not.
    `cause_text` and `reason_text` are fixed English sentences built from the codes. `actor` is
    the name the Sync API shows for the reserved actor of the automatic check and resume,
    `Sync (automatic resume)`. The fields are plain strings, so a client still reads a code that
    a later Sync API adds.
    """

    interrupted_flow_run_id: str = Field(min_length=1)
    actor: str
    cause: str
    cause_text: str
    state: str
    decision: str | None = None
    reason: str | None = None
    reason_text: str | None = None
    operations_not_checked: int | None = Field(default=None, ge=0)
    start_after: int | None = Field(default=None, ge=0)
    check_flow_run_id: str | None = Field(default=None, min_length=1)
    resume_flow_run_id: str | None = Field(default=None, min_length=1)
    decided_at: datetime
    updated_at: datetime

    @field_validator("decided_at", "updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, _EXECUTION_TIME_MESSAGE)
        assert checked is not None
        return checked


class RunResource(_ResourceModel):
    """Combine a public run record with its orchestration history.

    `apply_progress` is `None` for a run that never started a write, and for a Sync API that
    predates spec 016; `resumes` is then empty. `automatic_resumes` holds Sync's decisions on
    resuming the run's interrupted writes without a person, oldest first, and
    `automatic_resume_count` the number of them whose resume Sync submitted (spec 030). For a run
    that no decision concerns, and for a Sync API that predates spec 030, `automatic_resumes` is
    empty and `automatic_resume_count` is 0. `credentials_used` holds one entry for each block
    whose stored credential values a stage execution received, oldest first; it is empty for a
    run whose stages received none, and for a Sync API that predates spec 027.
    """

    run: PublicRunResource
    orchestration: tuple[OrchestrationSummary, ...]
    apply_progress: ApplyProgressResource | None = None
    resumes: tuple[ResumeResource, ...] = ()
    credentials_used: tuple[CredentialUsedResource, ...] = ()
    automatic_resumes: tuple[AutomaticResumeResource, ...] = ()
    automatic_resume_count: int = Field(default=0, ge=0)


class ApprovedPlanSummaryResource(_ResourceModel):
    """The counts of the plan an approval covers: creates, updates and recorded deletes."""

    creates: int = Field(ge=0)
    updates: int = Field(ge=0)
    # Deletes the plan records; apply executes none of them.
    deletes_recorded: int = Field(ge=0)


class ApprovedRunResource(RunResource):
    """The approve response: the run with its approval, and the counts of the plan approved."""

    plan_summary: ApprovedPlanSummaryResource


class RunPage(_ResourceModel):
    """One page of the run list, newest first.

    Items carry the public run fields with empty ``artifact_refs`` and
    ``prefect_executions``; ``GET /runs/{run_id}`` returns them. ``next_cursor`` is ``None``
    on the last page.
    """

    items: tuple[PublicRunResource, ...]
    next_cursor: str | None = None


class EventPrimaryResource(_ResourceModel):
    """The resource an event is about: its run, or else its configuration.

    ``kind`` is a plain string so the client still reads a kind a newer server adds.
    """

    kind: str
    id: str


class EventRelatedResource(_ResourceModel):
    """One resource an event names besides its primary resource, such as ``account:alice`` or ``branch:main``."""

    kind: str
    id: str


class ScheduleSettingChangeResource(_ResourceModel):
    """One setting that a change of a schedule altered, with its stored value before and after.

    ``field`` is the name of the setting as the change request names it, such as ``cron`` or
    ``start_late_by_at_most_seconds``; the values are the stored values, such as a version
    number, a cron expression or a number of seconds.
    """

    field: str
    before: StrictBool | StrictInt | str
    after: StrictBool | StrictInt | str


class EventDetailsResource(_ResourceModel):
    """The facts of one event, under a closed set of keys. A key the event does not use is ``None``.

    ``cause`` is a refusal code or a failure class, never free text from Infrahub.
    """

    # The start time of the cadence, on every event of a scheduled start.
    start_at: datetime | None = None
    # The count, the first and the last of the missed starts that one event records together.
    starts: int | None = Field(default=None, ge=0)
    first_start_at: datetime | None = None
    last_start_at: datetime | None = None
    # The plan's count of records left out, on the result event of a scheduled run.
    left_out: int | None = Field(default=None, ge=0)
    # The branch and the proposed change of a run with branch review.
    branch: str | None = None
    proposed_change_id: str | None = None
    # The earlier run whose branch or proposed change was replaced.
    replaced_run_id: str | None = None
    cause: str | None = None
    # The revision of the stored credential an event names.
    revision: int | None = Field(default=None, ge=1)
    # The settings whose stored value a change of a schedule altered; empty when every setting
    # the change sent already had its value. Only `infrahub_sync.schedule.changed` holds it.
    changes: tuple[ScheduleSettingChangeResource, ...] | None = None
    # The events of an automatic resume (spec 030), whose `cause` is `rule-6` or `rule-8`:
    # the reason code of an automatic resume that did not start, with its codes and counts,
    # and the marker, the operations the check left out and the flow run of one that started.
    reason: str | None = None
    reason_detail: dict[str, StrictInt | str] | None = None
    start_after: int | None = Field(default=None, ge=0)
    operations_not_checked: int | None = Field(default=None, ge=0)
    resume_flow_run_id: str | None = Field(default=None, min_length=1)

    @field_validator("start_at", "first_start_at", "last_start_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _EVENT_TIME_MESSAGE)


class EventResource(_ResourceModel):
    """One typed event: a Sync API decision or a stage result, with the resources it names."""

    event_id: str
    event_type: str
    occurred_at: datetime
    actor: str
    operation: str
    outcome: str
    reason: str | None = None
    primary_resource: EventPrimaryResource | None = None
    related: tuple[EventRelatedResource, ...] = ()
    # The facts of the event; ``None`` for an event that records none, and for a Sync API
    # that predates schedules.
    details: EventDetailsResource | None = None

    @field_validator("occurred_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime | None:
        return _timezone(value, _EVENT_TIME_MESSAGE)


class EventPage(_ResourceModel):
    """One page of the event list, oldest first; ``has_more`` says whether events follow the page."""

    items: tuple[EventResource, ...]
    limit: int = Field(ge=MIN_LIMIT, le=MAX_LIMIT)
    offset: int = Field(ge=0)
    has_more: bool


class ScheduleOpenBranchResource(_ResourceModel):
    """The most recent branch of a schedule that is open for review, with the run that wrote it."""

    run_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    # ``None`` when the run opened no proposed change.
    proposed_change_id: str | None = None


class ScheduleResource(_ResourceModel):
    """Represent one schedule: its cadence, its settings, its state and its most recent run.

    ``write_target``, ``overlap_policy``, ``state`` and ``state_reason`` are plain strings, so
    the client still reads a value a newer Sync API adds. ``newer_version``,
    ``start_late_by_at_most_seconds`` and ``waiting_start_at`` are ``None`` for a Sync API that
    does not return them. All times are in UTC.
    """

    schedule_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    config_id: str = Field(min_length=1)
    registry_version: int = Field(ge=1, le=2**63 - 1)
    # The highest registered version of the configuration when it is higher than
    # ``registry_version``.
    newer_version: int | None = Field(default=None, ge=1, le=2**63 - 1)
    cron: str
    timezone: str
    write_target: str = Field(min_length=1)
    write_when_left_out: bool
    # The actor who last turned ``write_when_left_out`` on, and when.
    left_out_set_by: str | None = None
    left_out_set_at: datetime | None = None
    overlap_policy: str = Field(min_length=1)
    start_late_by_at_most_seconds: int | None = None
    state: str = Field(min_length=1)
    # Why a stopped schedule stopped; ``None`` in every other state.
    state_reason: str | None = None
    owner: str = Field(min_length=1)
    # ``None`` when the schedule is not active.
    next_start_at: datetime | None = None
    # The start that waits for the run in progress to end.
    waiting_start_at: datetime | None = None
    # The schedule's most recent run; ``GET /runs/{run_id}`` holds its outcome.
    last_run_id: str | None = None
    # False while the task manager does not hold this schedule as it is stored.
    prefect_synced: bool
    # The count of failed runs in a row.
    consecutive_failures: int = Field(ge=0)
    open_branch: ScheduleOpenBranchResource | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("left_out_set_at", "next_start_at", "waiting_start_at", "created_at", "updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, _SCHEDULE_TIME_MESSAGE)


class SchedulePage(_ResourceModel):
    """One page of the schedule list, newest first. ``next_cursor`` is ``None`` on the last page."""

    items: tuple[ScheduleResource, ...]
    next_cursor: str | None = None


class ScheduleNextStartsRequest(_RequestModel):
    """Request the next start times of a cron expression in a time zone, before a schedule is saved.

    `cron` and `timezone` are not validated here, as in `CreateScheduleRequest`: the Sync API
    answers `schedule-cron-invalid` or `schedule-timezone-invalid`.
    """

    cron: str
    timezone: str = "UTC"
    count: StrictInt = Field(default=DEFAULT_NEXT_STARTS, ge=1, le=MAX_NEXT_STARTS)


class ScheduleNextStarts(_ResourceModel):
    """The next start times of a cadence, in UTC and in increasing order.

    ``starts`` holds fewer times than requested when the cadence has fewer starts before
    ``searched_until``.
    """

    cron: str
    timezone: str
    starts: tuple[datetime, ...]
    searched_until: datetime

    @field_validator("starts", "searched_until")
    @classmethod
    def _require_timezone(cls, value: tuple[datetime, ...] | datetime) -> tuple[datetime, ...] | datetime:
        for item in value if isinstance(value, tuple) else (value,):
            _timezone(item, _SCHEDULE_TIME_MESSAGE)
        return value


class RunLogLine(_ResourceModel):
    """One log line that Sync's own code wrote during a run, tagged with its stage.

    ``stage`` and ``level`` are plain strings so the client still reads a value a newer
    server adds.
    """

    stage: str
    attempt: int = Field(ge=1)
    timestamp: datetime
    level: str
    source: str
    # Kept as written: the resource models strip text, which would drop a table's or a
    # traceback's indentation.
    message: Annotated[str, StringConstraints(strip_whitespace=False)]

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime | None:
        return _timezone(value, "log timestamps must include a timezone")


class RunLogPage(_ResourceModel):
    """One page of a run's log, oldest first, with the run's phase so a poller knows when to stop."""

    run_id: str
    items: tuple[RunLogLine, ...]
    limit: int = Field(ge=MIN_LIMIT, le=MAX_LOG_LIMIT)
    offset: int = Field(ge=0)
    has_more: bool
    run_phase: str


class PlanSummaryResource(_ResourceModel):
    """Validated saved-plan counts and delete-review disclosures."""

    by_action: dict[str, int]
    by_kind: dict[str, int]
    total: int = Field(ge=0)
    delete_operations_computed: bool
    deletes_not_executed: int = Field(ge=0)
    # Spec 007 FR-007 and FR-008: the counts of the plan report. Defaults, so a review
    # published before these fields existed still validates; a format 2 or 3 plan has zeros
    # and `dropdown_values_checked` false.
    left_out: int = Field(default=0, ge=0)
    left_out_with_problem: int = Field(default=0, ge=0)
    left_out_dependent: int = Field(default=0, ge=0)
    left_out_by_kind: dict[str, int] = Field(default_factory=dict)
    problems: int = Field(default=0, ge=0)
    problems_by_type: dict[str, int] = Field(default_factory=dict)
    kept: int = Field(default=0, ge=0)
    # Per kind, the records whose deletes the plan does not compute (research R21).
    deletes_withheld_by_kind: dict[str, int] = Field(default_factory=dict)
    dropdown_values_checked: bool = False


class PlanReportPageResource(_ResourceModel):
    """One page of the problems, left-out records or kept records of a saved plan (spec 007).

    `entries` are the lines of the plan's `report.jsonl` without their `entry` key, in the
    report's stable order. `next_offset` is `None` on the last page.
    """

    run_id: str
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[dict[str, Any], ...]
    offset: int = Field(ge=0)
    limit: int = Field(ge=MIN_LIMIT, le=MAX_REPORT_LIMIT)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class PlanOperationResource(_ResourceModel):
    """One validated operation returned by the saved-plan route."""

    operation_id: str = Field(min_length=1)
    action: Literal["create", "update", "delete"]
    kind: str = Field(min_length=1)
    identity: dict[str, Any]
    tier: int = Field(ge=0)
    payload: dict[str, Any] | None = None
    relationships: tuple[dict[str, Any], ...] | None = None
    # The destination object an update will be keyed by, recorded at plan time. `None` on a
    # create or a delete, and on every operation of a format-2 plan. A reviewer reads it to
    # see which destination object each update names before approving the apply.
    destination_id: str | None = None


class PlanLineageAccountResource(_ResourceModel):
    """The import account a saved plan was computed with."""

    name: str = Field(min_length=1)
    id: str = Field(min_length=1)


class PlanLineageNodeResource(_ResourceModel):
    """A source or owner node, by kind and name, as the saved plan recorded it."""

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)


class PlanLineageResource(_ResourceModel):
    """The lineage a saved plan writes: import account, source, owner and protection.

    `owner` is `None` when the package names no owner.
    """

    import_account: PlanLineageAccountResource
    source: PlanLineageNodeResource
    owner: PlanLineageNodeResource | None = None
    protect: bool


class PlanLastChangeAccountResource(_ResourceModel):
    """The account Infrahub recorded as `updated_by` for a value, by id and display label."""

    id: str = Field(min_length=1)
    display_label: str | None = None


class PlanLastChangeResource(_ResourceModel):
    """Who last changed one field an update proposes to change, and when.

    `updated_by` or `updated_at` is `None` when Infrahub holds no value for it, or when the
    plan did not read metadata; review shows such a value as "not recorded".
    """

    operation_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    field_type: Literal["attribute", "relationship"]
    updated_by: PlanLastChangeAccountResource | None = None
    updated_at: str | None = None


class PlanResource(_ResourceModel):
    """Return a saved plan, checksum, summary, and operations."""

    run_id: str
    checksum: str
    checksum_ok: bool
    verification_notes: tuple[str, ...]
    summary: PlanSummaryResource
    operations: tuple[PlanOperationResource, ...]
    # The consumed destination-schema semantics a registered plan was computed against, so a
    # reviewer reads the binding the apply will compare. `None` for an unregistered plan.
    schema_fingerprint: str | None = None
    destination_branch: str | None = None
    # The lineage the plan records in its manifest. `None` for a plan whose destination
    # records no lineage. Optional, so a service that does not send it still validates.
    lineage: PlanLineageResource | None = None
    # Who last changed each field each update proposes to change, from the plan's
    # `last_changes.jsonl`. `None` for a plan made without the file. Optional, like `lineage`.
    last_changes: tuple[PlanLastChangeResource, ...] | None = None


FailureType = Literal[
    "destination_rejected_write",
    "system_unreachable",
    "system_timeout",
    "token_rejected",
    "permission_missing",
    "tls_certificate_rejected",
    "record_load_failed",
    "transform_failed",
    "credential_missing",
    "credential_unavailable",
    "verification_failed",
    "apply_refused",
    "left_out_review_required",
    "destination_peer_missing",
    "destination_peer_ambiguous",
    "lineage_refused",
    "destination_schema_unavailable",
    "destination_default_branch_not_main",
    "branch_list_failed",
    "branch_create_failed",
    "branch_delete_failed",
    "proposed_change_open_failed",
    "proposed_change_read_failed",
    "proposed_change_close_failed",
    "internal_error",
]
"""The failure type names a stage failure record holds (spec 007 data-model.md, FR-014).

The seven names from `destination_default_branch_not_main` to `proposed_change_close_failed` are
the failures of the branch review operations of a scheduled run (spec 021, research R15).

`credential_unavailable` is a stage that could not get the stored credential values of its run
from the Sync API (spec 027, FR-019): the cause says which of the reasons applies.
"""

FAILURE_TYPES: tuple[FailureType, ...] = get_args(FailureType)

# The longest Infrahub error code, node kind and attribute name a stage failure record holds.
MAX_FAILURE_NAME_TEXT = 128


class FailureSystemResource(_ResourceModel):
    """The system a stage failure came from: its role in the sync and its adapter name."""

    role: Literal["source", "destination"]
    adapter: str = Field(min_length=1)


class FailureRecordReferenceResource(_ResourceModel):
    """Name one source record by destination kind, source identity and source record ID."""

    kind: str = Field(min_length=1)
    identity: dict[str, Any] | None = None
    source_id: str | None = None


class FailedOperationDetailResource(_ResourceModel):
    """The action, kind and identity of the plan operation a stopped apply was writing."""

    action: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    identity: dict[str, Any] | None = None


class StageFailureResource(_ResourceModel):
    """One `<stage>_failure` record of a run's results.

    The keys a record held before spec 007 keep their meaning and stay optional, so a record
    written by an older Sync API validates. The worker of this version sets every spec 007 field
    on every record it writes, `failure_type` always and the others `None` when the failure does
    not name them (ADR 0021). The reader keeps them optional, because a record written by an
    older Sync API has none of them (FR-014), and `failure_type` is a string rather than
    `FailureType`: a later server may add a name. No field holds the text of an exception,
    apart from Infrahub's own message of a rejected write.
    """

    stage: str | None = None
    outcome: str | None = None
    error_type: str | None = None
    cause_type: str | None = None
    cause: str | None = None
    next_action: str | None = None
    infrahub_error: Any = None
    recovery_action: str | None = None
    reason: str | None = None
    message: str | None = None
    failure_type: str | None = None
    system: FailureSystemResource | None = None
    error_code: str | None = Field(default=None, max_length=MAX_FAILURE_NAME_TEXT)
    node_kind: str | None = Field(default=None, max_length=MAX_FAILURE_NAME_TEXT)
    attribute: str | None = Field(default=None, max_length=MAX_FAILURE_NAME_TEXT)
    infrahub_message: str | None = Field(default=None, max_length=MAX_FAILURE_CAUSE_TEXT)
    record: FailureRecordReferenceResource | None = None
    field: str | None = None
    operations_total: int | None = Field(default=None, ge=0)
    # The creates and updates the whole run wrote, across every execution (spec 016 research R6).
    # `None` in a record written before this field existed; `applied_operations` then counts.
    operations_written: int | None = Field(default=None, ge=0)
    failed_operation_detail: FailedOperationDetailResource | None = None


class ResultsResource(_ResourceModel):
    """Return the recorded results for one run."""

    run_id: str
    results: dict[str, Any]

    def stage_failure(self, stage: str) -> StageFailureResource | None:
        """The run's `<stage>_failure` record, or `None` when the run has none or it does not validate."""
        return stage_failure_resource(self.results.get(f"{stage}_failure"))


def stage_failure_resource(value: object) -> StageFailureResource | None:
    """Validate one recorded `<stage>_failure` value, or return `None` for any other value."""
    if not isinstance(value, dict):
        return None
    try:
        return StageFailureResource.model_validate(value)
    except ValidationError:
        return None


class ArtifactListResource(_ResourceModel):
    """List the artifact references owned by one run."""

    run_id: str
    artifacts: tuple[ArtifactReferenceResource, ...]


class ArtifactContent(_ResourceModel):
    """Hold verified artifact bytes and content metadata."""

    data: bytes
    media_type: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConfigurationSummaryResource(_ResourceModel):
    """Identify one registered configuration."""

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    created_at: datetime
    # ``configuration.name`` of the latest version's package; names are not unique.
    name: str | None = None
    # The highest registered version. The server always sends it; ``None`` only when reading
    # a server that predates the field.
    latest_version: int | None = Field(default=None, ge=1)

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "configuration timestamps must include a timezone")
        assert checked is not None
        return checked


class ConfigurationPage(_ResourceModel):
    """One page of the configuration list, newest first; ``next_cursor`` is ``None`` on the last page."""

    items: tuple[ConfigurationSummaryResource, ...]
    next_cursor: str | None = None


class RunReference(_ResourceModel):
    """The identity and state of one run, as a configuration version's latest run shows it."""

    run_id: str
    phase: str
    outcome: str | None = None
    started_at: datetime
    # When the run last finished a stage; for an applied run, when its apply finished.
    finished_at: datetime | None = None

    @field_validator("started_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "run timestamps must include a timezone")
        assert checked is not None
        return checked

    @field_validator("finished_at")
    @classmethod
    def _require_finished_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, "run timestamps must include a timezone")


class ConfigurationVersionSummaryResource(_ResourceModel):
    """One configuration version without its package, with its latest run and most recently applied run.

    ``GET /configs/{config_id}/versions/{registry_version}`` returns the full version.
    """

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int = Field(ge=1)
    package_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    # ``None`` for versions registered before the server recorded who registered them and why.
    registered_by: str | None = None
    registration_reason: str | None = None
    # ``True`` when the registration request said the package was not validated (spec 025).
    registered_without_validation: bool = False
    latest_run: RunReference | None = None
    latest_applied_run: RunReference | None = None

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "configuration-version timestamps must include a timezone")
        assert checked is not None
        return checked


class ConfigurationVersionPage(_ResourceModel):
    """One page of a configuration's versions, newest version first."""

    items: tuple[ConfigurationVersionSummaryResource, ...]
    next_cursor: str | None = None


class ConfigurationVersionResource(_ResourceModel):
    """Represent one immutable registered configuration version."""

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int = Field(ge=1)
    package_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_content: dict[str, Any]
    created_at: datetime
    # ``True`` when the registration request said the package was not validated (spec 025).
    registered_without_validation: bool = False

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "configuration-version timestamps must include a timezone")
        assert checked is not None
        return checked


class ConfigurationVersionReferenceResource(_ResourceModel):
    """Name one registered version by its checksum, without its package (spec 007 FR-038).

    The registration responses return this reference: the stored package is redacted, so
    showing it next to the checksum of the original content would show two things that do not
    match. ``GET /configs/{config_id}/versions/{registry_version}`` returns the content.
    """

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int = Field(ge=1)
    package_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    # ``True`` when the registration request said the package was not validated (spec 025).
    registered_without_validation: bool = False

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "configuration-version timestamps must include a timezone")
        assert checked is not None
        return checked


class RegisteredConfigurationResource(_ResourceModel):
    """Return a newly registered configuration and a reference to its first version."""

    configuration: ConfigurationSummaryResource
    version: ConfigurationVersionReferenceResource


class RegisteredVersionResource(_ResourceModel):
    """Return a reference to a configuration version and whether it was newly created."""

    version: ConfigurationVersionReferenceResource
    created: bool


class ValidationFindingResource(_ResourceModel):
    """Describe one ordered configuration validation finding."""

    code: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=256)
    severity: Literal["error", "warning"]
    location: str = Field(pattern=r"^(/(?:[^~/]|~[01])*)*$", max_length=256)
    message: str = Field(min_length=1, max_length=256)


class ValidationReportResource(_ResourceModel):
    """Return one page of configuration validation findings."""

    config_id: str
    registry_version: int
    package_checksum: str
    destination_schema_fingerprint: str | None = None
    findings: tuple[ValidationFindingResource, ...]
    offset: int
    limit: int
    total_findings: int
    next_offset: int | None


class AccountResource(_ResourceModel):
    """What the Sync API shows of a local account: its name, its level and its state."""

    name: str
    administrator: bool
    disabled: bool
    environment_managed: bool
    created_at: datetime
    sign_in_refused_until: datetime | None = None


class CredentialResource(_ResourceModel):
    """What the Sync API shows of a stored credential: the names of its fields, never their values.

    ``created_by`` and ``updated_by`` are account names, including ``Deleted account <short identifier>``.
    """

    name: str
    description: str
    fields: tuple[str, ...]
    revision: int = Field(ge=1)
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class CredentialPage(_ResourceModel):
    """One page of stored credentials sorted by name; ``next_offset`` is ``None`` on the last page."""

    items: tuple[CredentialResource, ...]
    next_offset: int | None = Field(default=None, ge=0)


class DeletedAccountResource(_ResourceModel):
    """The answer of a deletion: what every record of the deleted account displays from now on."""

    placeholder: str


class SessionResource(_ResourceModel):
    """The answer of a sign-in: the session token, the account and the idle period of a session.

    The Sync API returns the token this once. Its printed form shows the account name only.
    """

    token: str = Field(repr=False)
    account: AccountResource
    idle_seconds: int

    def __repr__(self) -> str:
        """Show the account name only, so that no log line or traceback holds the session token."""
        return f"SessionResource(account={self.account.name!r})"

    __str__ = __repr__


class TokenResource(_ResourceModel):
    """What the Sync API shows of an API token: never its value."""

    token_id: str
    name: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class CreatedTokenResource(TokenResource):
    """The answer of the creation of an API token: the token and its value, returned this once.

    Its printed form shows the token name only.
    """

    token: str = Field(repr=False)

    def __repr__(self) -> str:
        """Show the token name only, so that no log line or traceback holds the value."""
        return f"CreatedTokenResource(name={self.name!r})"

    __str__ = __repr__


class PackageCheckResource(_ResourceModel):
    """Return the check of a package that is not registered, with one page of its findings.

    ``package_checksum`` and ``declared_content`` are what registering the package would store.
    ``destination_read`` is ``not_requested`` without a branch, ``read`` when the destination
    schema was read, ``not_read`` when the destination adapter is unknown or declares no schema
    read, or the reason the read failed: ``timeout``, ``unreachable``,
    ``unauthorized``, ``forbidden``, ``nobranch``, ``rejected``, ``unconfigured`` or
    ``credentials``.
    """

    # Keys and values of the declared content are returned exactly, so registering the
    # returned content from Python gives the returned checksum. A browser that parses and
    # serializes it again can change its numbers (`30.0` becomes `30`, integers above 2**53
    # lose precision), so the web interface registers the file's text as `document` instead.
    model_config = ConfigDict(extra="allow", frozen=True, str_strip_whitespace=False)
    package_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str | None = None
    declared_content: dict[str, Any]
    destination_read: str = Field(pattern=r"^[a-z][a-z_]*$", max_length=64)
    destination_schema_fingerprint: str | None = None
    findings: tuple[ValidationFindingResource, ...]
    offset: int
    limit: int
    total_findings: int
    next_offset: int | None


class ErrorDetail(_ResourceModel):
    """Parse the public fields in a general API error."""

    code: str
    message: str
    status: int
    run_id: str | None = None
    mutation_id: str | None = None
    # Structured context of a refusal, such as the existing approval of ``already-approved``.
    # Absent from most errors.
    details: dict[str, Any] | None = None
    # The count of records the plan leaves out, on a `left-out-confirmation-required` refusal
    # only (spec 007 FR-010). Absent from every other error body.
    left_out: int | None = Field(default=None, ge=0)

    # No return annotation on purpose: with one, Pydantic replaces the model's serialization
    # JSON schema (and so the OpenAPI document) with a bare object.
    @model_serializer(mode="wrap")
    def _omit_absent_left_out(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.left_out is None:
            data.pop("left_out", None)
        return data


class ErrorEnvelope(_ResourceModel):
    """Wrap a general API error response."""

    error: ErrorDetail


class ConfigErrorDetail(_ResourceModel):
    """Parse the public fields in a configuration API error."""

    code: str
    message: str
    status: int
    family: str
    reason: str | None = None
    # Set when a registration is refused for the stored credentials it names: one entry for each
    # block whose credential does not exist or does not hold a field the block needs. Absent
    # from every other error body.
    details: dict[str, Any] | None = None

    # No return annotation on purpose, as on `ErrorDetail`.
    @model_serializer(mode="wrap")
    def _omit_absent_details(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.details is None:
            data.pop("details", None)
        return data


class ConfigErrorEnvelope(_ResourceModel):
    """Wrap a configuration API error response."""

    error: ConfigErrorDetail
