"""Typed records for Sync's durable product projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from infrahub_sync.execution import Operation  # noqa: TC001 - Pydantic resolves this annotation at runtime.

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_INVALID_SERVICE_WORKER_ID = "service worker identity is invalid"
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
    # The run's one authoritative write-safety fact, never carried in ``results``: a write
    # execution ended without proving what reached the destination, so an operator must
    # inspect it and plan again. Nothing sets it back to false.
    reconciliation_required: bool = False
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


class ExecutionFinishWriteback(BaseModel):
    """Complete business writeback committed with one claimed execution verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["finish"] = "finish"
    phase: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    finished_at: datetime
    summary: dict[str, Any]
    results: dict[str, Any]

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
    reason: str = Field(min_length=1)
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
        # flow-run ID to carry; only an accepted submission does.
        if (
            self.state == "accepted"
            and self.resource_kind == "run"
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

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "configuration timestamps must include a timezone"
            raise ValueError(msg)
        return value


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


class AuditEvent(BaseModel):
    """Secret-safe durable evidence for one Sync API decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    actor: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_audit_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            msg = "audit-event timestamps must include a timezone"
            raise ValueError(msg)
        return value


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
