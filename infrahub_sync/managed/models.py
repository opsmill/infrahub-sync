"""Stable request and response records for the managed HTTP surface."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves response annotations at runtime.
from math import isfinite
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from infrahub_sync.product_store import ArtifactReference, ProductRun  # noqa: TC001 - Pydantic resolves at runtime.

ManagedStage = Literal["plan", "verify", "apply", "sync"]
_REASON_GRAMMAR_MESSAGE = "reason must be printable and trimmed"
_JSON_NATIVE_PACKAGE_MESSAGE = "package must be recursively exact JSON-native"
_REGISTRY_VERSION_TYPE_MESSAGE = "registry_version must be int"
_REGISTRY_VERSION_RANGE_MESSAGE = "registry_version must be in the registry allocatable range"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateRunRequest(_StrictModel):
    """Create one managed plan or confirmed composed sync."""

    operation: Literal["plan", "sync"] = "plan"
    config_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    registry_version: int
    branch: str | None = None
    confirm_writes: bool = False
    reason: str = Field(min_length=1)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_exact_registry_version(cls, value: object) -> int:
        if type(value) is not int:  # pylint: disable=unidiomatic-typecheck  # bool is not a registry version.
            raise ValueError(_REGISTRY_VERSION_TYPE_MESSAGE)
        if not 1 <= value <= 2**63 - 1:
            raise ValueError(_REGISTRY_VERSION_RANGE_MESSAGE)
        return value


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


class VersionResource(_StrictModel):
    """Unauthenticated managed API compatibility discovery."""

    server_version: str
    api_versions: tuple[Literal["v3-unstable"], ...]
    stability: Literal["unstable"]


class WorkerStatusResource(_StrictModel):
    """Publicly safe summary of managed worker availability."""

    state: Literal["ready", "busy", "no-live-worker", "unavailable"]
    detail_available: bool
    live_workers: int | None
    queue_depth: int | None
    observed_at: datetime | None


class ServiceStatusResource(_StrictModel):
    """Unauthenticated lifecycle status without provider identifiers."""

    service: Literal["ready"]
    worker: WorkerStatusResource


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

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    package: dict[str, Any]
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def _require_printable_trimmed_reason(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError(_REASON_GRAMMAR_MESSAGE)
        return value

    @field_validator("package")
    @classmethod
    def _require_exact_json_native_package(cls, value: dict[str, Any]) -> dict[str, Any]:
        def visit(item: object) -> None:
            item_type = type(item)
            if item_type is dict:
                for key, child in cast("dict[object, object]", item).items():
                    if type(key) is not str:  # pylint: disable=unidiomatic-typecheck  # Exact JSON key type.
                        raise ValueError(_JSON_NATIVE_PACKAGE_MESSAGE)
                    visit(child)
                return
            if item_type is list:
                for child in cast("list[object]", item):
                    visit(child)
                return
            if item_type is float and not isfinite(cast("float", item)):
                raise ValueError(_JSON_NATIVE_PACKAGE_MESSAGE)
            if item_type not in {str, int, float, bool, type(None)}:
                raise ValueError(_JSON_NATIVE_PACKAGE_MESSAGE)

        visit(value)
        return value


class ConfigErrorDetail(_StrictModel):
    """Secret-safe configuration service refusal."""

    code: str
    message: str
    status: int
    family: str
    reason: str | None = None


class ConfigErrorEnvelope(_StrictModel):
    error: ConfigErrorDetail
