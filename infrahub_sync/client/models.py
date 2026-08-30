"""Standalone request and resource models for the Sync HTTP API."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves resource annotations at runtime.
from math import isfinite
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Operation = Literal["plan", "verify", "apply", "sync"]
TerminalState = Literal["completed", "failed", "cancelled", "abandoned", "interrupted"]
TerminalOutcome = Literal["succeeded", "failed", "cancelled", "abandoned", "ambiguous"]

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
_JSON_PACKAGE_MESSAGE = "package must be recursively exact JSON-native"
_ARTIFACT_TIME_MESSAGE = "artifact-reference timestamps must include a timezone"
_EXECUTION_TIME_MESSAGE = "execution timestamps must include a timezone"
_ARTIFACT_KEY_MESSAGE = "artifact object and manifest keys must include the content digest"
_BINDING_MESSAGE = "configuration binding must be all absent or all present"
_FINISHED_RUN_MESSAGE = "a finished product record requires an outcome"
_TERMINAL_FIELDS_MESSAGE = "execution terminal fields must be all absent or all present"
_TERMINAL_VERDICT_MESSAGE = "execution terminal verdict is invalid"
_WORKER_STATUS_MESSAGE = "worker status is invalid"
_ARTIFACT_IDS_MESSAGE = "artifact IDs must be unique within a product record"
_ARTIFACT_OWNER_MESSAGE = "every artifact reference must belong to the product record's run ID"
_EXECUTION_IDS_MESSAGE = "execution IDs must be unique within a product record"


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
    reason: str = Field(min_length=1)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_registry_version(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 2**63 - 1:  # pylint: disable=unidiomatic-typecheck
            raise ValueError(_REGISTRY_VERSION_MESSAGE)
        return value


class VerifyRunRequest(_RequestModel):
    """Request verification of a saved run plan."""

    reason: str = Field(min_length=1)


class ApplyRunRequest(_RequestModel):
    """Request application of a reviewed plan checksum."""

    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_writes: bool = False
    branch: str | None = None
    reason: str = Field(min_length=1)


class CancelRunRequest(_RequestModel):
    """Request cancellation of an accepted run."""

    reason: str = Field(min_length=1)


class ConfigMutationRequest(_RequestModel):
    """Submit a configuration package with an audit reason."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)
    package: dict[str, Any]
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def _require_reason(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError(_REASON_MESSAGE)
        return value

    @field_validator("package")
    @classmethod
    def _require_json_package(cls, value: dict[str, Any]) -> dict[str, Any]:
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
    artifact_refs: tuple[ArtifactReferenceResource, ...] = ()
    prefect_executions: tuple[PublicExecutionLink, ...] = ()

    @field_validator("started_at", "finished_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _timezone(value, "product-record timestamps must include a timezone")

    @model_validator(mode="after")
    def _require_consistent_binding(self) -> PublicRunResource:
        binding = (self.config_id, self.registry_version, self.package_checksum)
        if any(value is None for value in binding) and any(value is not None for value in binding):
            raise ValueError(_BINDING_MESSAGE)
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


class WorkerStatusResource(_ResourceModel):
    """Report bounded worker availability details."""

    state: Literal["ready", "busy", "no-live-worker", "unavailable"]
    detail_available: bool
    live_workers: int | None = Field(default=None, ge=0, strict=True)
    queue_depth: int | None = Field(default=None, ge=0, strict=True)
    observed_at: datetime | None

    @model_validator(mode="after")
    def _require_status(self) -> WorkerStatusResource:
        details = (self.live_workers, self.queue_depth, self.observed_at)
        if not self.detail_available:
            if self.state != "unavailable" or any(value is not None for value in details):
                raise ValueError(_WORKER_STATUS_MESSAGE)
            return self
        if self.state == "unavailable" or any(value is None for value in details):
            raise ValueError(_WORKER_STATUS_MESSAGE)
        assert self.live_workers is not None
        assert self.queue_depth is not None
        if self.state == "no-live-worker" and self.live_workers != 0:
            raise ValueError(_WORKER_STATUS_MESSAGE)
        if self.state in {"ready", "busy"} and self.live_workers == 0:
            raise ValueError(_WORKER_STATUS_MESSAGE)
        if self.state == "ready" and self.queue_depth != 0:
            raise ValueError(_WORKER_STATUS_MESSAGE)
        if self.state == "busy" and self.queue_depth == 0:
            raise ValueError(_WORKER_STATUS_MESSAGE)
        return self


class ServiceStatusResource(_ResourceModel):
    """Report service readiness and worker availability."""

    service: Literal["ready"]
    worker: WorkerStatusResource


class RunResource(_ResourceModel):
    """Combine a public run record with its orchestration history."""

    run: PublicRunResource
    orchestration: tuple[OrchestrationSummary, ...]


class PlanResource(_ResourceModel):
    """Return a saved plan, checksum, summary, and operations."""

    run_id: str
    checksum: str
    checksum_ok: bool
    verification_notes: tuple[str, ...]
    summary: dict[str, Any]
    operations: tuple[dict[str, Any], ...]


class ResultsResource(_ResourceModel):
    """Return the recorded results for one run."""

    run_id: str
    results: dict[str, Any]


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

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "configuration timestamps must include a timezone")
        assert checked is not None
        return checked


class ConfigurationVersionResource(_ResourceModel):
    """Represent one immutable registered configuration version."""

    config_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    registry_version: int = Field(ge=1)
    package_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_content: dict[str, Any]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        checked = _timezone(value, "configuration-version timestamps must include a timezone")
        assert checked is not None
        return checked


class RegisteredConfigurationResource(_ResourceModel):
    """Return a newly registered configuration and its first version."""

    configuration: ConfigurationSummaryResource
    version: ConfigurationVersionResource


class RegisteredVersionResource(_ResourceModel):
    """Return a configuration version and whether it was newly created."""

    version: ConfigurationVersionResource
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


class ErrorDetail(_ResourceModel):
    """Parse the public fields in a general API error."""

    code: str
    message: str
    status: int
    run_id: str | None = None
    mutation_id: str | None = None


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


class ConfigErrorEnvelope(_ResourceModel):
    """Wrap a configuration API error response."""

    error: ConfigErrorDetail
