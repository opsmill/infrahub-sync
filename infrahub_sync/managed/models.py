"""Stable request and response records for the managed HTTP surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from infrahub_sync.product_store import ArtifactReference, ProductRun  # noqa: TC001 - Pydantic resolves at runtime.

ManagedStage = Literal["plan", "verify", "apply", "sync"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateRunRequest(_StrictModel):
    """Create one managed plan or confirmed composed sync."""

    sync_name: str = Field(min_length=1)
    operation: Literal["plan", "sync"] = "plan"
    configuration_reference: str = Field(min_length=1)
    branch: str | None = None
    confirm_writes: bool = False
    reason: str = Field(min_length=1)


class VerifyRunRequest(_StrictModel):
    """Request a read-only verification stage."""

    reason: str = Field(min_length=1)


class ApplyRunRequest(_StrictModel):
    """Request confirmed apply of one reviewed checksum."""

    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_writes: bool = False
    branch: str | None = None
    reason: str = Field(min_length=1)


class CancelRunRequest(_StrictModel):
    """Request cancellation of the current managed Prefect execution."""

    reason: str = Field(min_length=1)


class OrchestrationSummary(_StrictModel):
    """Current Prefect detail layered over one durable execution link."""

    flow_run_id: str
    purpose: str
    attempt: int
    state: str | None
    detail_available: bool
    unavailable_reason: str | None = None


class RunResource(_StrictModel):
    """Stable Sync record plus non-authoritative live orchestration detail."""

    run: ProductRun
    orchestration: tuple[OrchestrationSummary, ...]


class PlanResource(_StrictModel):
    """Retained saved-plan review data."""

    run_id: str
    checksum: str
    checksum_ok: bool
    verification_notes: tuple[str, ...]
    summary: dict[str, Any]
    operations: tuple[dict[str, Any], ...]


class ResultsResource(_StrictModel):
    """Retained product results independent of Prefect result storage."""

    run_id: str
    results: dict[str, Any]


class ArtifactListResource(_StrictModel):
    """Immutable artifact references owned by one Sync run."""

    run_id: str
    artifacts: tuple[ArtifactReference, ...]


class ErrorDetail(_StrictModel):
    """Typed secret-safe HTTP error detail."""

    code: str
    message: str
    status: int
    run_id: str | None = None
    mutation_id: str | None = None


class ErrorEnvelope(_StrictModel):
    """Stable envelope used by every managed API error."""

    error: ErrorDetail


class ConfigMutationRequest(_StrictModel):
    """JSON package submitted for a configuration mutation."""

    package: dict[str, Any]
    reason: str = Field(min_length=1, max_length=512)


class ConfigErrorDetail(_StrictModel):
    """Secret-safe configuration service refusal."""

    code: str
    message: str
    status: int
    family: str
    reason: str | None = None


class ConfigErrorEnvelope(_StrictModel):
    error: ConfigErrorDetail
