"""Typed records for Sync's durable product projection."""

# pylint: disable=too-many-lines  # one module owns every record of the product store, the schedule records included

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from infrahub_sync.configuration.worker_credentials import is_credential_name
from infrahub_sync.execution import Operation  # noqa: TC001 - Pydantic resolves this annotation at runtime.
from infrahub_sync.plan.identity import OPERATION_ID_PATTERN

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
# A schedule's generated identifier: ``s-`` and 32 hexadecimal characters.
SCHEDULE_ID_PATTERN = r"^s-[0-9a-f]{32}$"
# What started a run: a request to ``POST /runs``, or a schedule.
TriggerType: TypeAlias = Literal["manual", "scheduled"]
# The largest registry version the store can allocate: the signed 64-bit integer limit.
MAX_REGISTRY_VERSION = 2**63 - 1
# Page size of the store's configuration, version and run lists when a caller gives none.
# Equal to the Sync API's ``DEFAULT_LIMIT``; the store does not import the client package.
DEFAULT_LIMIT = 50
_INVALID_SERVICE_WORKER_ID = "service worker identity is invalid"
# Run mutations the service answers in the request itself, without submitting a flow run:
# their accepted receipt stores a success response with no flow-run ID. The receipt's
# ``operation`` column is plain text, so this set is the only place an operation is named.
SERVICE_ANSWERED_OPERATIONS = frozenset({"approve"})
# The status of a ``cancel`` receipt that withdrew a finished plan (spec 014, User Story 4).
WITHDRAWN_CANCEL_STATUS = 200
_LEGAL_EXECUTION_VERDICTS = {
    ("completed", "succeeded"),
    ("failed", "failed"),
    ("cancelled", "cancelled"),
    ("abandoned", "abandoned"),
    ("interrupted", "ambiguous"),
}


class ArtifactReference(BaseModel):
    """Immutable reference to one published, run-owned artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
    # An internal artifact is the product's own stage-handoff state. It is never
    # enumerated or served to a client, and the public wire resource does not declare
    # this field, so the distinction cannot leak by being projected.
    visibility: Literal["public", "internal"] = "public"

    @field_validator("created_at", "expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "artifact-reference timestamps must include a timezone"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_immutable_keys(self) -> ArtifactReference:
        marker = f"/{self.digest}/"
        if marker not in f"/{self.object_key}" or marker not in f"/{self.manifest_key}":
            msg = "artifact object and manifest keys must include the content digest"
            raise ValueError(msg)
        return self


class ApplyProgress(BaseModel):
    """How far one write execution got through its saved plan (spec 016 data-model.md).

    `position` is the index, in the saved plan's stored order, of the last operation this
    execution knows was committed at the destination; the worker never saves a position ahead
    of a commit. The document holds positions, an operation identifier and a time only, never
    a value read from or written to a system.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    position: int = Field(ge=0)
    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)
    total: int = Field(ge=1)
    start_after: int | None = Field(default=None, ge=0)
    saved_at: datetime

    @field_validator("saved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "the apply progress time must include a timezone"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_positions_in_plan(self) -> ApplyProgress:
        if self.position >= self.total:
            msg = "the apply progress position is not in the plan"
            raise ValueError(msg)
        if self.start_after is not None and self.start_after >= self.position:
            msg = "the apply progress position must come after the position the resume started after"
            raise ValueError(msg)
        return self


# What the store answers when asked to save a progress marker (spec 016, research R2): `saved`
# when the execution's row changed; `ended` when the execution exists but is terminal, which is
# final, because a terminal execution never takes a marker again; `unknown` when the run has no
# such execution.
ApplyProgressSave: TypeAlias = Literal["saved", "ended", "unknown"]


class PrefectExecutionLink(BaseModel):
    """Historical correlation to one purpose-labelled Prefect flow execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flow_run_id: str = Field(min_length=1)
    deployment_id: str | None = None
    purpose: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    last_observed_state: str | None = None
    last_observed_at: datetime | None = None
    submitted_at: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))
    claimed_at: datetime | None = None
    claiming_worker_id: str | None = None
    stalled_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    cancellation_recovery_deadline_at: datetime | None = None
    cancellation_receipt_id: str | None = None
    cancellation_acknowledged_at: datetime | None = None
    terminal_at: datetime | None = None
    terminal_state: Literal["completed", "failed", "cancelled", "abandoned", "interrupted"] | None = None
    terminal_outcome: Literal["succeeded", "failed", "cancelled", "abandoned", "ambiguous"] | None = None
    # Each side's stored read document (spec 013 data-model.md); `None` for an execution
    # recorded before that feature, a stage that reads no side, or a `plan` or `sync`
    # execution that has not reached its planning step.
    source_read: dict[str, Any] | None = None
    destination_read: dict[str, Any] | None = None
    # The last progress marker a write execution saved (spec 016, research R2); `None` for a
    # read stage, an execution recorded before that feature, or a write that committed nothing.
    apply_progress: ApplyProgress | None = None
    # The plan step document of a `plan` or `sync` execution (spec 019 data-model.md): the
    # parts after the reads, with their times, and the part that runs. `None` for an execution
    # recorded before that feature, or for a stage that has no plan step.
    plan_step: dict[str, Any] | None = None

    @field_validator(
        "last_observed_at",
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
        mode="before",
    )
    @classmethod
    def _require_timezone(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                persisted = f"{value[:-1]}+00:00" if value.endswith("Z") else value
                parsed = datetime.fromisoformat(persisted)
            except ValueError:
                msg = "Prefect execution timestamps must be datetimes or persisted ISO strings"
                raise ValueError(msg) from None
        else:
            msg = "Prefect execution timestamps must be datetimes or persisted ISO strings"
            raise ValueError(msg)  # noqa: TRY004 - Pydantic reports ValueError, not TypeError.
        if parsed.utcoffset() is None:
            msg = "Prefect execution timestamps must include a timezone"
            raise ValueError(msg)
        return parsed

    @field_validator("claiming_worker_id", mode="before")
    @classmethod
    def _require_canonical_worker_id(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(_INVALID_SERVICE_WORKER_ID)  # noqa: TRY004 - Pydantic reports ValueError.
        try:
            canonical = str(UUID(value))
        except ValueError:
            raise ValueError(_INVALID_SERVICE_WORKER_ID) from None
        if canonical != value:
            raise ValueError(_INVALID_SERVICE_WORKER_ID)
        return value

    @model_validator(mode="after")
    def _require_liveness_boundaries(self) -> PrefectExecutionLink:
        if (self.claimed_at is None) is not (self.claiming_worker_id is None):
            msg = "execution claim time and worker ID must be all absent or all present"
            raise ValueError(msg)
        cancellation = (
            self.cancellation_requested_at,
            self.cancellation_recovery_deadline_at,
            self.cancellation_receipt_id,
        )
        if any(value is None for value in cancellation) and any(value is not None for value in cancellation):
            msg = "execution cancellation request fields must be all absent or all present"
            raise ValueError(msg)
        if self.cancellation_acknowledged_at is not None and self.cancellation_requested_at is None:
            msg = "execution cancellation acknowledgement requires a request"
            raise ValueError(msg)
        terminal = (self.terminal_at, self.terminal_state, self.terminal_outcome)
        if any(value is None for value in terminal) and any(value is not None for value in terminal):
            msg = "execution terminal fields must be all absent or all present"
            raise ValueError(msg)
        if (
            self.terminal_state is not None
            and (self.terminal_state, self.terminal_outcome) not in _LEGAL_EXECUTION_VERDICTS
        ):
            msg = "execution terminal verdict is invalid"
            raise ValueError(msg)
        return self


class RunApproval(BaseModel):
    """The one recorded approval of a run's published plan.

    Stored at most once per run and never changed or removed. ``approved_by``,
    ``approved_at`` and ``plan_checksum`` are required together, so a partial approval
    cannot be built; ``reason`` is the optional audit reason of the approve request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approved_by: str = Field(min_length=1)
    approved_at: datetime
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = None

    @field_validator("approved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "approval timestamps must include a timezone"
            raise ValueError(msg)
        return value

    @classmethod
    def from_columns(
        cls,
        approved_by: str | None,
        approved_at: str | datetime | None,
        approved_checksum: str | None,
        approval_reason: str | None,
    ) -> RunApproval | None:
        """Build the approval from its four stored columns, or ``None`` when none is stored.

        ``approved_at`` may be the stored ISO 8601 text; Pydantic parses it.

        Raises:
            ValueError: Some but not all of the required columns are present, or a reason
                is stored without an approval.
        """
        required = (approved_by, approved_at, approved_checksum)
        if all(value is None for value in required):
            if approval_reason is not None:
                msg = "an approval reason requires a stored approval"
                raise ValueError(msg)
            return None
        if any(value is None for value in required):
            msg = "approval columns must be all absent or all present"
            raise ValueError(msg)
        return cls.model_validate(
            {
                "approved_by": approved_by,
                "approved_at": approved_at,
                "plan_checksum": approved_checksum,
                "reason": approval_reason,
            }
        )


RUN_CANCELLED_MESSAGE = (
    "the run was cancelled, so its plan cannot be approved or applied; create a new plan run to try again"
)
RUN_ALREADY_CANCELLED_MESSAGE = "the run is already cancelled"


def not_approvable_message(phase: str, active_stage: str | None = None) -> str:
    """Say why a run cannot be approved: the stage still running, or the run's phase."""
    if active_stage is not None:
        return f"the run cannot be approved while a {active_stage} execution is in progress"
    return (
        f"the run cannot be approved in phase {phase!r}; only a run in phase 'planned', or a run whose "
        "write stopped part-way and can be resumed, can be approved"
    )


def approval_error_body(
    status: int,
    code: str,
    message: str,
    *,
    run_id: str,
    mutation_id: str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the error envelope of a refused approval, as the Sync API sends and a receipt stores it."""
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "status": status,
        "run_id": run_id,
        "mutation_id": mutation_id,
    }
    if details is not None:
        error["details"] = details
    return {"error": error}


def already_approved_body(approval: RunApproval, *, run_id: str, mutation_id: str | None) -> dict[str, Any]:
    """Return the ``already-approved`` refusal, carrying the approval the run already has.

    The message names no account: ``approved_by`` holds an account identifier, which the API
    shows as a name only in the approval of the details.
    """
    return approval_error_body(
        409,
        "already-approved",
        "the run's plan was already approved",
        run_id=run_id,
        mutation_id=mutation_id,
        details={"approval": approval.model_dump(mode="json")},
    )


class ProductRun(BaseModel):
    """One compact Sync-owned product record.

    A reviewed apply advances the planning record's ``phase`` and ``outcome``;
    it does not replace ``run_id`` or allocate another record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: Operation
    configuration_reference: str = Field(min_length=1)
    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    registry_version: int | None = Field(default=None, ge=1, le=MAX_REGISTRY_VERSION)
    package_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    actor: str | None = None
    audit_links: tuple[str, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    phase: str = Field(min_length=1)
    outcome: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    # The run's one authoritative write-safety fact, never carried in ``results``: a write
    # execution ended without proving what reached the destination, so an operator must
    # inspect it and plan again. Nothing sets it back to false.
    reconciliation_required: bool = False
    # The effective Infrahub branch the run writes to, recorded when the run is created.
    # ``None`` for runs created before the store recorded it.
    destination_branch: str | None = None
    # The run's one approval of its published plan, or ``None`` when it was never approved.
    # Approval is not a phase: an approved run keeps its phase, ``planned`` or the phase of a
    # run whose write stopped part-way and that is approved before its resume (spec 016).
    approval: RunApproval | None = None
    # What started the run. A run of ``POST /runs`` holds ``manual``.
    trigger_type: TriggerType = "manual"
    # The schedule that started the run, and the start time of its cadence. Both are set for
    # a scheduled run and ``None`` for a manual run. One schedule holds one run for each start.
    schedule_id: str | None = Field(default=None, pattern=SCHEDULE_ID_PATTERN)
    scheduled_start_at: datetime | None = None
    artifact_refs: tuple[ArtifactReference, ...] = ()
    prefect_executions: tuple[PrefectExecutionLink, ...] = ()

    @field_validator("started_at", "finished_at", "scheduled_start_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "product-record timestamps must include a timezone"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_consistent_children(self) -> ProductRun:
        binding = (self.config_id, self.registry_version, self.package_checksum)
        if any(value is None for value in binding) and any(value is not None for value in binding):
            msg = "configuration binding must be all absent or all present"
            raise ValueError(msg)
        start = (self.schedule_id, self.scheduled_start_at)
        if self.trigger_type == "scheduled" and any(value is None for value in start):
            msg = "a scheduled run requires its schedule and its scheduled start time"
            raise ValueError(msg)
        if self.trigger_type == "manual" and any(value is not None for value in start):
            msg = "a manual run cannot name a schedule or a scheduled start time"
            raise ValueError(msg)
        if self.finished_at is not None and self.outcome is None:
            msg = "a finished product record requires an outcome"
            raise ValueError(msg)
        artifact_ids = [reference.artifact_id for reference in self.artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            msg = "artifact IDs must be unique within a product record"
            raise ValueError(msg)
        if any(reference.run_id != self.run_id for reference in self.artifact_refs):
            msg = "every artifact reference must belong to the product record's run ID"
            raise ValueError(msg)
        flow_run_ids = [link.flow_run_id for link in self.prefect_executions]
        if len(flow_run_ids) != len(set(flow_run_ids)):
            msg = "Prefect flow-run IDs must be unique within a product record"
            raise ValueError(msg)
        return self

    @property
    def configuration_binding(self) -> tuple[str, int, str] | None:
        """Return the complete registered package identity, never a partial tuple."""
        if self.config_id is None:
            return None
        assert self.registry_version is not None
        assert self.package_checksum is not None
        return self.config_id, self.registry_version, self.package_checksum


class BaselineWriteback(BaseModel):
    """The safety reference one successful write execution contributes.

    Carries what the run observed, not what the next run should conclude: the cadence
    counter is derived by the store, so the rule for advancing it lives in one place.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_row_counts: dict[str, int]
    full_extract: bool


class ConfigurationBaseline(BaseModel):
    """The durable safety reference for one configuration's last successful write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_row_counts: dict[str, int]
    runs_since_full_extract: int = Field(ge=0)
    updated_at: datetime


class WorkerEnvironment(BaseModel):
    """The credential variable names one worker held at its last poll (spec 007 FR-035).

    Names only, never values: each is a set, non-empty variable of the credential namespace.
    `worker_id` is the worker's name, the identity it has before Prefect resolves its record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=256)
    credential_names: tuple[str, ...]
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        """Read a time without a zone as UTC, so it compares with the liveness cutoff, which is in UTC."""
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @field_validator("credential_names")
    @classmethod
    def _names_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse anything that is not a credential variable name, so no value can be stored here."""
        if not all(is_credential_name(name) for name in value):
            msg = "credential_names holds only variable names of the credential namespace"
            raise ValueError(msg)
        return tuple(sorted(set(value)))


class BootstrapRecord(BaseModel):
    """The Sync version and work pool the service bootstrap last set up (spec 018 FR-007b).

    The database holds one such record at most. The Sync API and the worker compare it with
    their own version and work pool when they start, and refuse to start on a difference.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_version: str = Field(min_length=1, max_length=256)
    work_pool: str = Field(min_length=1, max_length=256)
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        """Read a time without a zone as UTC."""
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ExecutionFinishWriteback(BaseModel):
    """Complete business writeback committed with one claimed execution verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["finish"] = "finish"
    phase: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    finished_at: datetime
    summary: dict[str, Any]
    results: dict[str, Any]
    # Present only when this execution completed a write whose source counts are safe to
    # become the next run's reference. Absent on every read stage.
    baseline: BaselineWriteback | None = None
    # Set only by a `sync` stage that stopped after its plan because the plan leaves out
    # records (spec 007 FR-012). The store gives back the run's `sync` write admission in
    # the same transaction, and only when the run record shows no dispatched operation.
    release_write_admission: bool = False

    @field_validator("finished_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: object) -> datetime:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            msg = "execution writeback timestamps must include a timezone"
            raise ValueError(msg)
        return value


class ExecutionMergeWriteback(BaseModel):
    """Business result patch committed with one claimed execution verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["merge"] = "merge"
    results: dict[str, Any]


ExecutionWriteback: TypeAlias = Annotated[
    ExecutionFinishWriteback | ExecutionMergeWriteback,
    Field(discriminator="kind"),
]


class MutationReceipt(BaseModel):
    """Durable actor/key reservation for one Sync API mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    actor: str = Field(min_length=1)
    key_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: str = Field(min_length=1)
    target_run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(min_length=1)
    resource_kind: Literal["run", "configuration", "configuration-registry", "schedule"] = "run"
    resource_id: str = Field(min_length=1)
    run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    prefect_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    state: Literal["reserved", "processing", "accepted"] = "reserved"
    response_status: int | None = Field(default=None, ge=100, le=599)
    response_body: dict[str, Any] | None = None
    flow_run_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_receipt_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "mutation-receipt timestamps must include a timezone"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_accepted_response(self) -> MutationReceipt:
        accepted_values = (self.response_status, self.response_body, self.flow_run_id)
        if self.resource_kind == "run" and (self.run_id is None or self.prefect_key is None):
            msg = "a run mutation receipt requires its run and Prefect identifiers"
            raise ValueError(msg)
        # A receipt of a configuration route or of a schedule route names no run: a schedule
        # request creates none, and a scheduled run's own receipt has the resource kind ``run``.
        if self.resource_kind != "run" and any(
            value is not None for value in (self.run_id, self.prefect_key, self.flow_run_id, self.target_run_id)
        ):
            resource = "schedule" if self.resource_kind == "schedule" else "configuration"
            msg = f"a {resource} mutation receipt cannot carry run or Prefect identifiers"
            raise ValueError(msg)
        if self.state == "accepted" and (self.response_status is None or self.response_body is None):
            msg = "an accepted mutation receipt requires its stored status and response"
            raise ValueError(msg)
        # A run receipt answered with a refusal never reached Prefect, so it has no
        # flow-run ID to carry; only an accepted submission does. An operation the
        # service answers itself, such as ``approve``, never submits a flow run.
        # A ``cancel`` that withdraws a finished plan is answered ``200`` by the service, with no
        # execution to target; a ``cancel`` of an active execution is answered ``202`` and names it.
        withdrawal = self.operation == "cancel" and self.response_status == WITHDRAWN_CANCEL_STATUS
        submits_flow_run = (
            self.resource_kind == "run" and self.operation not in SERVICE_ANSWERED_OPERATIONS and not withdrawal
        )
        if (
            self.state == "accepted"
            and submits_flow_run
            and self.flow_run_id is None
            and self.response_status is not None
            and self.response_status < 400
        ):
            msg = "an accepted run mutation receipt requires its Prefect flow-run ID"
            raise ValueError(msg)
        if self.state in {"reserved", "processing"} and any(value is not None for value in accepted_values):
            msg = "an incomplete mutation receipt cannot carry an accepted response"
            raise ValueError(msg)
        return self


class ConfigurationSummary(BaseModel):
    """Registry identity for one configuration's version lineage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    created_at: datetime
    # ``configuration.name`` of the latest version's package. Not unique; ``None`` when that
    # package declares no name.
    name: str | None = None
    # The highest registered ``registry_version``. ``None`` only for a summary built without
    # reading the versions.
    latest_version: int | None = Field(default=None, ge=1)

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "configuration timestamps must include a timezone"
            raise ValueError(msg)
        return value


PageItemT = TypeVar("PageItemT")


class StorePage(BaseModel, Generic[PageItemT]):
    """One page of a store listing, in the listing's order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[PageItemT, ...]
    # Whether more items match after the last item; the store reads one row past the page.
    has_more: bool


# One page of configurations, newest first.
ConfigurationPage: TypeAlias = StorePage[ConfigurationSummary]


class RunReference(BaseModel):
    """The identity and state of one run, as a configuration version's latest run shows it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    phase: str = Field(min_length=1)
    outcome: str | None = None
    started_at: datetime
    # When the run last finished a stage; for an applied run, when its apply finished.
    finished_at: datetime | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "run timestamps must include a timezone"
            raise ValueError(msg)
        return value


class ConfigurationVersionSummary(BaseModel):
    """One configuration version without its package, with its latest run and most recently applied run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int = Field(ge=1)
    package_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    # The actor and reason of the registration request; ``None`` for versions registered
    # before the store recorded them.
    registered_by: str | None = None
    registration_reason: str | None = None
    # ``True`` when the registration request said the package was not validated (spec 025).
    registered_without_validation: bool = False
    latest_run: RunReference | None = None
    latest_applied_run: RunReference | None = None

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "configuration-version timestamps must include a timezone"
            raise ValueError(msg)
        return value


# One page of a configuration's versions, newest version first.
ConfigurationVersionPage: TypeAlias = StorePage[ConfigurationVersionSummary]


class ConfigurationVersion(BaseModel):
    """One immutable, checksum-identified declared configuration version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
        if value.utcoffset() is None:
            msg = "configuration-version timestamps must include a timezone"
            raise ValueError(msg)
        return value


class RunFilters(BaseModel):
    """What a run list is restricted to; every given filter must match, and none lists every run.

    The filters are the configuration binding, what started the run, the schedule that
    started it, and one start time of that schedule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    registry_version: int | None = Field(default=None, ge=1, le=MAX_REGISTRY_VERSION)
    trigger_type: TriggerType | None = None
    schedule_id: str | None = Field(default=None, pattern=SCHEDULE_ID_PATTERN)
    scheduled_start_at: datetime | None = None

    @field_validator("scheduled_start_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "run-list start times must include a timezone"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_configuration_for_version(self) -> RunFilters:
        if self.registry_version is not None and self.config_id is None:
            msg = "registry_version requires config_id"
            raise ValueError(msg)
        if self.scheduled_start_at is not None and self.schedule_id is None:
            msg = "scheduled_start_at requires schedule_id"
            raise ValueError(msg)
        return self


# One page of runs, newest first, without artifact references or execution links.
RunPage: TypeAlias = StorePage[ProductRun]


# An event's dotted type, ``infrahub_sync.<resource>.<action>`` (spec 012, FR-012).
EVENT_TYPE_PATTERN = r"^infrahub_sync\.(run|config|schedule|account)\.[a-z_]+$"
# The kinds of resource an event names besides its primary resource, in the order an event lists them.
RelatedResourceKind: TypeAlias = Literal["account", "config", "config_version", "schedule", "branch"]
_RELATED_KIND_ORDER: dict[str, int] = {"account": 0, "config": 1, "config_version": 2, "schedule": 3, "branch": 4}


class RelatedResource(BaseModel):
    """One resource an event names besides its primary resource.

    The resource is the actor's account, a configuration, a version, a schedule or a branch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RelatedResourceKind
    id: str = Field(min_length=1)


class PrimaryResource(BaseModel):
    """The resource an event is about: its run, or else its schedule, or else its configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["run", "schedule", "config"]
    id: str = Field(min_length=1)


class EventDetails(BaseModel):
    """The facts of one event, under a closed set of keys. A key the event does not use is ``None``.

    ``reason`` of the event keeps the text of the person who made the request; the start
    time, the counts and the cause of an event are stored here. ``cause`` is a refusal code or
    a failure class, never free text from Infrahub.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The start time of the cadence, on every event of a scheduled start.
    start_at: datetime | None = None
    # The count, the first and the last of the missed starts that one event records together.
    starts: int | None = Field(default=None, ge=0)
    first_start_at: datetime | None = None
    last_start_at: datetime | None = None
    # The plan's count of records left out, on the result event of a scheduled run.
    left_out: int | None = Field(default=None, ge=0)
    # The branch and the proposed change of a run with branch review.
    branch: str | None = Field(default=None, min_length=1)
    proposed_change_id: str | None = Field(default=None, min_length=1)
    # The earlier run whose branch or proposed change was replaced.
    replaced_run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    cause: str | None = Field(default=None, min_length=1)

    @field_validator("start_at", "first_start_at", "last_start_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "event-detail timestamps must include a timezone"
            raise ValueError(msg)
        return value


class AuditEvent(BaseModel):
    """Secret-safe durable evidence for one Sync API decision or one stage result.

    ``event_type`` is NULL only for a request refused for a missing or invalid token, which
    the event list never returns. ``related`` is kept in one order (account, configuration,
    version, schedule, branch, then ID), so an event reads back equal to the event that was
    recorded. ``details`` is ``None`` for an event that records no fact of its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    actor: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    reason: str | None = Field(min_length=1)
    outcome: str = Field(min_length=1)
    created_at: datetime
    event_type: str | None = Field(default=None, pattern=EVENT_TYPE_PATTERN)
    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    registry_version: int | None = Field(default=None, ge=1, le=MAX_REGISTRY_VERSION)
    branch: str | None = Field(default=None, min_length=1)
    related: tuple[RelatedResource, ...] = ()
    details: EventDetails | None = None

    @field_validator("created_at")
    @classmethod
    def _require_audit_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "audit-event timestamps must include a timezone"
            raise ValueError(msg)
        return value

    @field_validator("related")
    @classmethod
    def _order_related(cls, value: tuple[RelatedResource, ...]) -> tuple[RelatedResource, ...]:
        if len(set(value)) != len(value):
            msg = "an event names each related resource once"
            raise ValueError(msg)
        return tuple(sorted(value, key=lambda item: (_RELATED_KIND_ORDER[item.kind], item.id)))

    @property
    def primary_resource(self) -> PrimaryResource | None:
        """Return the run the event is about, else the schedule it names, else its configuration, else nothing."""
        if self.run_id is not None:
            return PrimaryResource(kind="run", id=self.run_id)
        schedule = next((resource for resource in self.related if resource.kind == "schedule"), None)
        if schedule is not None:
            return PrimaryResource(kind="schedule", id=schedule.id)
        if self.config_id is not None:
            return PrimaryResource(kind="config", id=self.config_id)
        return None


class EventFilters(BaseModel):
    """What an event list is restricted to; every given filter must match, and none lists every typed event.

    ``config_id`` matches the event's configuration or a ``config`` related resource.
    ``event_types`` matches any of the types. ``related`` matches an event that names every
    listed resource. ``since`` is inclusive and ``until`` exclusive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    event_types: tuple[Annotated[str, Field(pattern=EVENT_TYPE_PATTERN)], ...] = ()
    related: tuple[RelatedResource, ...] = ()
    since: datetime | None = None
    until: datetime | None = None

    @field_validator("since", "until")
    @classmethod
    def _require_filter_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "event-list time bounds must include a timezone"
            raise ValueError(msg)
        return value


# One page of events, oldest first.
EventPage: TypeAlias = StorePage[AuditEvent]

# The order of an event list: ``asc`` oldest first, ``desc`` newest first; both by ``created_at`` then ``event_id``.
EventOrder: TypeAlias = Literal["asc", "desc"]

ACCOUNT_ID_PATTERN = r"^acct_[0-9a-f]{32}$"
_ACCOUNT_SHORT_ID_PATTERN = r"^[0-9a-f]{8}$"
_ACCOUNT_NAME_MAX_LENGTH = 64


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        msg = "account timestamps must include a timezone"
        raise ValueError(msg)
    return value


class Account(BaseModel):
    """One local account of the Sync API.

    ``account_id`` is the value every other record holds. ``name`` and ``name_key`` are empty
    once the account is deleted; ``short_id`` then identifies it in a display name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    short_id: str = Field(pattern=_ACCOUNT_SHORT_ID_PATTERN)
    name: str | None = None
    # The lower-case name. Unique among the accounts that have one.
    name_key: str | None = None
    # Algorithm, cost parameters, salt and derived key, as the caller built them. Left out of
    # the representation and of every serialized form, so that it reaches no log and no answer.
    password_hash: str | None = Field(default=None, repr=False, exclude=True)
    administrator: bool = False
    disabled: bool = False
    environment_managed: bool = False
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime
    deleted_at: datetime | None = None

    _aware = field_validator("created_at", "updated_at", "password_changed_at", "deleted_at")(_require_aware)


class AccountToken(BaseModel):
    """One API token of an account. The store keeps its digest, which this record leaves out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_id: str = Field(min_length=1)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=_ACCOUNT_NAME_MAX_LENGTH)
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    environment_managed: bool = False

    _aware = field_validator("created_at", "expires_at", "last_used_at")(_require_aware)


class AccountSession(BaseModel):
    """One session of an account. The store keeps its digest, which this record leaves out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    created_at: datetime
    last_used_at: datetime

    _aware = field_validator("created_at", "last_used_at")(_require_aware)


class SignInAttempt(BaseModel):
    """The failed sign-ins of one lower-case name in the current window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name_key: str = Field(min_length=1, max_length=_ACCOUNT_NAME_MAX_LENGTH)
    failed_count: int = Field(ge=0)
    window_started_at: datetime
    refused_until: datetime | None = None

    _aware = field_validator("window_started_at", "refused_until")(_require_aware)


class ResolvedCredential(BaseModel):
    """The API token or session that a digest matches, with its account.

    The store reports what it holds and decides nothing: the caller checks ``account.disabled``,
    ``expires_at`` and the idle period. ``last_used_at`` is the value stored before this
    resolution, so that an idle period is measured from the previous use.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: Account
    kind: Literal["token", "session"]
    # ``token_id`` of a token, ``session_id`` of a session.
    credential_id: str = Field(min_length=1)
    # Always empty for a session.
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    _aware = field_validator("expires_at", "last_used_at")(_require_aware)


ScheduleWriteTarget: TypeAlias = Literal["branch-review", "default-branch"]
ScheduleOverlapPolicy: TypeAlias = Literal["reject", "queue"]
ScheduleState: TypeAlias = Literal["active", "paused", "stopped", "deleted"]
# Why a schedule is in the state ``stopped``.
ScheduleStateReason: TypeAlias = Literal["owner-not-found", "approval-required", "repeated-failures"]
ScheduleBranchState: TypeAlias = Literal["pending", "open", "removed", "replaced", "kept"]
# The fewest and the most seconds a scheduled run may start after its start time.
MIN_START_LATE_SECONDS = 60
MAX_START_LATE_SECONDS = 604800
DEFAULT_START_LATE_SECONDS = 3600
_SCHEDULE_TIME_MESSAGE = "schedule timestamps must include a timezone"


class ScheduleRecord(BaseModel):
    """One stored schedule: the cadence that starts a ``sync`` of one configuration version.

    The record is kept after deletion, in the state ``deleted``. The next start time is
    computed from ``cron`` and ``timezone`` each time the schedule is returned, so the record
    does not hold it. ``owner`` is an actor name; the record holds no token.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule_id: str = Field(pattern=SCHEDULE_ID_PATTERN)
    # Unique among schedules that are not deleted.
    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    # A configuration has at most one schedule that is not deleted.
    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int = Field(ge=1, le=MAX_REGISTRY_VERSION)
    # A five-field cron expression and a time zone name. The store keeps them as text; the
    # Sync API checks them before it saves the schedule.
    cron: str = Field(min_length=1)
    timezone: str = Field(default="UTC", min_length=1)
    write_target: ScheduleWriteTarget = "branch-review"
    write_when_left_out: bool = False
    # The actor who last turned ``write_when_left_out`` on, and when. Both are ``None`` while
    # it is off.
    left_out_set_by: str | None = Field(default=None, min_length=1)
    left_out_set_at: datetime | None = None
    overlap_policy: ScheduleOverlapPolicy = "reject"
    start_late_by_at_most_seconds: int = Field(
        default=DEFAULT_START_LATE_SECONDS, ge=MIN_START_LATE_SECONDS, le=MAX_START_LATE_SECONDS
    )
    state: ScheduleState
    # Set only in the state ``stopped``.
    state_reason: ScheduleStateReason | None = None
    owner: str = Field(min_length=1)
    # The count of failed runs in a row.
    consecutive_failures: int = Field(default=0, ge=0)
    # The most recent start that was admitted or recorded.
    last_start_at: datetime | None = None
    # The Prefect schedule of this schedule, and whether it matches this record.
    prefect_schedule_id: str | None = Field(default=None, min_length=1)
    prefect_synced: bool = False
    # The start that waits under the ``queue`` policy.
    waiting_start_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("left_out_set_at", "last_start_at", "waiting_start_at", "created_at", "updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError(_SCHEDULE_TIME_MESSAGE)
        return value

    @model_validator(mode="after")
    def _require_consistent_settings(self) -> ScheduleRecord:
        if (self.state == "stopped") is (self.state_reason is None):
            msg = "a schedule holds a state reason only in the state stopped, and always there"
            raise ValueError(msg)
        if self.write_when_left_out and self.write_target == "branch-review":
            msg = "a schedule with branch review always writes the remaining records"
            raise ValueError(msg)
        set_by = (self.left_out_set_by, self.left_out_set_at)
        if self.write_when_left_out and any(value is None for value in set_by):
            msg = "write_when_left_out requires the actor who turned it on and the time"
            raise ValueError(msg)
        if not self.write_when_left_out and any(value is not None for value in set_by):
            msg = "left_out_set_by and left_out_set_at are set only while write_when_left_out is on"
            raise ValueError(msg)
        return self


class ScheduleBranch(BaseModel):
    """The branch of one run with branch review, and what became of it.

    ``pending``: the run was created and its branch may or may not exist in Infrahub.
    ``open``: the run finished its write and opened its proposed change. ``removed``: the
    run deleted its own branch because the plan held no operation. ``replaced``: a later run
    of the schedule closed the proposed change, if any, and deleted the branch. ``kept``: a
    person merged or closed the proposed change, and the schedule leaves both alone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule_id: str = Field(pattern=SCHEDULE_ID_PATTERN)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    branch: str = Field(min_length=1)
    # Set when the run opened a proposed change.
    proposed_change_id: str | None = Field(default=None, min_length=1)
    state: ScheduleBranchState
    # The run that replaced this one; set only in the state ``replaced``.
    replaced_by_run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError(_SCHEDULE_TIME_MESSAGE)
        return value

    @model_validator(mode="after")
    def _require_consistent_state(self) -> ScheduleBranch:
        if (self.state == "replaced") is (self.replaced_by_run_id is None):
            msg = "a schedule branch names the run that replaced it only in the state replaced, and always there"
            raise ValueError(msg)
        if self.state == "open" and self.proposed_change_id is None:
            msg = "a schedule branch in the state open names the proposed change that its run opened"
            raise ValueError(msg)
        return self


# Why a later run left an earlier proposed change of its schedule in place: a person approved it
# while the run was writing, merged or closed it, or deleted it (`missing`).
BranchReviewKeptCause: TypeAlias = Literal["approved", "merged", "closed", "missing"]


class BranchReviewKept(BaseModel):
    """An earlier run of the schedule whose proposed change a run left in place, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    cause: BranchReviewKeptCause


class BranchReviewOutcome(BaseModel):
    """The part of a run's ``branch_review`` result that follows its committed write (spec 021, R11).

    ``proposed_change_id`` is the proposed change the run opened, or ``None`` when opening it
    failed. ``replaced`` holds the earlier runs whose proposed change the run closed, if any, and
    whose branch it deleted. ``kept`` holds the earlier runs whose proposed change it left in
    place. ``failure`` is the failure class of the first step that failed; the write verdict of
    the run does not change.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    proposed_change_id: str | None = Field(default=None, min_length=1)
    replaced: tuple[Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)], ...] = ()
    kept: tuple[BranchReviewKept, ...] = ()
    failure: str | None = Field(default=None, min_length=1)

    def results_entry(self) -> dict[str, Any]:
        """The fields this part adds to ``branch_review`` in the run's results."""
        return {
            "proposed_change_id": self.proposed_change_id,
            "replaced": list(self.replaced),
            "kept": [kept.model_dump(mode="json") for kept in self.kept],
            "failure": self.failure,
        }


class ScheduleFilters(BaseModel):
    """What a schedule list is restricted to.

    Without ``state``, the list leaves out deleted schedules.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    state: ScheduleState | None = None


# One page of schedules, newest first.
SchedulePage: TypeAlias = StorePage[ScheduleRecord]

# The count of failed runs in a row at which a schedule stops (spec 021, FR-032).
MAX_CONSECUTIVE_FAILURES = 5
# The event that records a schedule moving to the state ``stopped``, and the operation of an
# event of a scheduled start. The store writes this event itself when the fifth failed run in a
# row ends, so the two names live here; ``infrahub_sync.service.events`` uses the same values.
SCHEDULE_STOPPED_EVENT_TYPE = "infrahub_sync.schedule.stopped"
SCHEDULE_START_OPERATION = "schedule-start"

# Why an admitted start of an active schedule creates no run.
ScheduledStartNotRunCause: TypeAlias = Literal[
    "missed",
    "skipped-run-in-progress",
    "skipped-approved-change-open",
    "skipped-start-waiting",
    "queued",
    "failed",
    # The worker's own check of research R6: the schedule writes to the default branch while the
    # Sync API requires approval. The admission also stops the schedule with this reason.
    "approval-required",
    # The owner's account is disabled or deleted, so no run can act as the owner. The admission
    # also stops the schedule with this reason.
    "owner-not-found",
]


class _ScheduledStartFacts(BaseModel):
    """What both decisions on a scheduled start carry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The schedule's ``last_start_at`` that the decision was made from. The store refuses the
    # decision when the stored value differs, so no missed start is recorded twice.
    last_start_at: datetime | None = None
    # The schedule's ``waiting_start_at`` that the decision was made from; the store refuses
    # the decision when the stored value differs, as for ``last_start_at``.
    waiting_start_at: datetime | None = None
    # The schedule's ``updated_at`` that the decision was made from. The store refuses the
    # decision when the stored value differs, so a change of the cadence, the left-out setting
    # or the overlap policy saved after the read is not admitted under the earlier settings.
    # ``None`` skips the check.
    schedule_updated_at: datetime | None = None
    # The events of the start, in the order they are written: one for each missed start, and
    # the event of this start.
    events: tuple[AuditEvent, ...] = ()
    # The runs of the schedule whose proposed change a person merged or closed: their
    # ``schedule_branches`` rows move to the state ``kept``.
    kept_run_ids: tuple[Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)], ...] = ()

    @field_validator("last_start_at", "waiting_start_at", "schedule_updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError(_SCHEDULE_TIME_MESSAGE)
        return value


class ScheduledStartRun(_ScheduledStartFacts):
    """The decision that a scheduled start runs: the run to create, and whether it has branch review.

    The run is a ``sync`` run with the trigger type ``scheduled``, not finished, with no artifact
    reference and no execution: the store adds its one execution, already claimed. With
    ``branch_review`` the run's destination branch is the derived branch name, and the store
    writes a ``schedule_branches`` row in the state ``pending`` for it.
    """

    kind: Literal["started"] = "started"
    run: ProductRun
    branch_review: bool = False

    @model_validator(mode="after")
    def _require_a_new_scheduled_sync(self) -> ScheduledStartRun:
        run = self.run
        if run.trigger_type != "scheduled" or run.operation != "sync":
            msg = "a scheduled start creates a sync run with the trigger type scheduled"
            raise ValueError(msg)
        if run.finished_at is not None or run.outcome is not None or run.artifact_refs or run.prefect_executions:
            msg = "the run of a scheduled start must be unfinished, with no artifact reference and no execution"
            raise ValueError(msg)
        if run.configuration_binding is None or run.actor is None:
            msg = "the run of a scheduled start requires its configuration version and its actor"
            raise ValueError(msg)
        if self.branch_review and run.destination_branch is None:
            msg = "a run with branch review requires its branch"
            raise ValueError(msg)
        return self


class ScheduledStartNotRun(_ScheduledStartFacts):
    """The decision that a scheduled start creates no run, with the cause."""

    kind: Literal["not-started"] = "not-started"
    cause: ScheduledStartNotRunCause


ScheduledStartDecision: TypeAlias = Annotated[
    ScheduledStartRun | ScheduledStartNotRun,
    Field(discriminator="kind"),
]


class ScheduledStartResult(BaseModel):
    """What the admission of a scheduled start wrote.

    ``cause`` is set when no run was created: ``schedule-not-active`` when the schedule was not
    active, in which case nothing was written, else the cause of the decision. ``schedule`` is
    the stored schedule after the admission. ``earlier_branches`` holds the schedule's rows in
    the states ``pending`` and ``open`` other than the row of the run that was created.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    started: bool
    cause: ScheduledStartNotRunCause | Literal["schedule-not-active"] | None = None
    run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    schedule: ScheduleRecord
    earlier_branches: tuple[ScheduleBranch, ...] = ()

    @model_validator(mode="after")
    def _require_one_outcome(self) -> ScheduledStartResult:
        if self.started is (self.run_id is None) or self.started is (self.cause is not None):
            msg = "an admission result holds a run identifier when the start ran, else a cause"
            raise ValueError(msg)
        return self


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LookupResult(Generic[T]):
    """An available value or an explicit, non-exceptional unavailable verdict."""

    value: T | None
    reason: str | None = None

    @property
    def available(self) -> bool:
        """Return whether the requested value is available."""
        return self.value is not None

    def __post_init__(self) -> None:
        if (self.value is None) is (self.reason is None):
            msg = "a lookup result requires exactly one of value or reason"
            raise ValueError(msg)
