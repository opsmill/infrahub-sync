"""Typed records for Sync's durable product projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - pydantic resolves this annotation at runtime.
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from infrahub_sync.execution import Operation  # noqa: TC001 - Pydantic resolves this annotation at runtime.

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


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

    @field_validator("last_observed_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            msg = "Prefect execution timestamps must include a timezone"
            raise ValueError(msg)
        return value


class ProductRun(BaseModel):
    """One compact Sync-owned product record.

    A reviewed apply advances the planning record's ``phase`` and ``outcome``;
    it does not replace ``run_id`` or allocate another record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: Operation
    configuration_reference: str = Field(min_length=1)
    actor: str | None = None
    audit_links: tuple[str, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    phase: str = Field(min_length=1)
    outcome: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
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


class MutationReceipt(BaseModel):
    """Durable actor/key reservation for one managed HTTP mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    actor: str = Field(min_length=1)
    key_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: str = Field(min_length=1)
    target_run_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1)
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    prefect_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["reserved", "accepted"] = "reserved"
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
        if self.state == "accepted" and any(value is None for value in accepted_values):
            msg = "an accepted mutation receipt requires its status, response, and Prefect flow-run ID"
            raise ValueError(msg)
        if self.state == "reserved" and any(value is not None for value in accepted_values):
            msg = "a reserved mutation receipt cannot carry an accepted response"
            raise ValueError(msg)
        return self


class AuditEvent(BaseModel):
    """Secret-safe durable evidence for one managed API decision."""

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
