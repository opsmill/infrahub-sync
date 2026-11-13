"""Typed records for Sync's durable product projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from infrahub_sync.configuration.worker_credentials import is_credential_name
from infrahub_sync.execution import Operation  # noqa: TC001 - Pydantic resolves this annotation at runtime.

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
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
# The status of a ``cancel`` receipt that withdrew a finished plan (spec 019, User Story 4).
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
    # Each side's stored read document (spec 018 data-model.md); `None` for an execution
    # recorded before that feature, a stage that reads no side, or a `plan` or `sync`
    # execution that has not reached its planning step.
    source_read: dict[str, Any] | None = None
    destination_read: dict[str, Any] | None = None

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
    return f"the run cannot be approved in phase {phase!r}; only a run in phase 'planned' can be approved"


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
    """Return the ``already-approved`` refusal, carrying the approval the run already has."""
    return approval_error_body(
        409,
        "already-approved",
        f"the run's plan was already approved by {approval.approved_by!r}",
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
    # Approval is not a phase: an approved run stays in phase ``planned``.
    approval: RunApproval | None = None
    artifact_refs: tuple[ArtifactReference, ...] = ()
    prefect_executions: tuple[PrefectExecutionLink, ...] = ()

    @field_validator("started_at", "finished_at")
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
    """The credential variable names one worker held at its last poll (spec 020 FR-035).

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
    # records (spec 020 FR-012). The store gives back the run's `sync` write admission in
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
    resource_kind: Literal["run", "configuration", "configuration-registry"] = "run"
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
        if self.resource_kind in {"configuration", "configuration-registry"} and any(
            value is not None for value in (self.run_id, self.prefect_key, self.flow_run_id, self.target_run_id)
        ):
            msg = "a configuration mutation receipt cannot carry run or Prefect identifiers"
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

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "configuration-version timestamps must include a timezone"
            raise ValueError(msg)
        return value


class RunFilters(BaseModel):
    """The configuration binding a run list is restricted to; no filter lists every run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    registry_version: int | None = Field(default=None, ge=1, le=MAX_REGISTRY_VERSION)

    @model_validator(mode="after")
    def _require_configuration_for_version(self) -> RunFilters:
        if self.registry_version is not None and self.config_id is None:
            msg = "registry_version requires config_id"
            raise ValueError(msg)
        return self


# One page of runs, newest first, without artifact references or execution links.
RunPage: TypeAlias = StorePage[ProductRun]


# An event's dotted type, ``infrahub_sync.<resource>.<action>`` (spec 017, FR-012).
EVENT_TYPE_PATTERN = r"^infrahub_sync\.(run|config)\.[a-z_]+$"
# The kinds of resource an event names besides its primary resource, in the order an event lists them.
RelatedResourceKind: TypeAlias = Literal["account", "config", "config_version", "branch"]
_RELATED_KIND_ORDER: dict[str, int] = {"account": 0, "config": 1, "config_version": 2, "branch": 3}


class RelatedResource(BaseModel):
    """One resource an event names besides its primary resource: the actor's account, a configuration, a version or a branch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RelatedResourceKind
    id: str = Field(min_length=1)


class PrimaryResource(BaseModel):
    """The resource an event is about: its run, or else its configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["run", "config"]
    id: str = Field(min_length=1)


class AuditEvent(BaseModel):
    """Secret-safe durable evidence for one Sync API decision or one stage result.

    ``event_type`` is NULL only for a request refused for a missing or invalid token, which
    the event list never returns. ``related`` is kept in one order (account, configuration,
    version, branch, then ID), so an event reads back equal to the event that was recorded.
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
        """Return the run the event is about, else its configuration, else nothing."""
        if self.run_id is not None:
            return PrimaryResource(kind="run", id=self.run_id)
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
