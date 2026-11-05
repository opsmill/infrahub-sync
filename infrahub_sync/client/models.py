"""Standalone request and resource models for the Sync HTTP API."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves resource annotations at runtime.
from math import isfinite
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    reason: str | None = Field(default=None, min_length=1)

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_registry_version(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 2**63 - 1:  # pylint: disable=unidiomatic-typecheck
            raise ValueError(_REGISTRY_VERSION_MESSAGE)
        return value


class VerifyRunRequest(_RequestModel):
    """Request verification of a saved run plan."""

    reason: str | None = Field(default=None, min_length=1)


class ApplyRunRequest(_RequestModel):
    """Request application of a reviewed plan checksum."""

    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_writes: bool = False
    branch: str | None = None
    reason: str | None = Field(default=None, min_length=1)


class CancelRunRequest(_RequestModel):
    """Request cancellation of an accepted run."""

    reason: str | None = Field(default=None, min_length=1)


class ConfigMutationRequest(_RequestModel):
    """Submit a configuration package with an optional audit reason."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)
    package: dict[str, Any]
    reason: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or not value.isprintable()):
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
    # Exposed directly rather than through ``results``: an operator deciding whether a run
    # needs reconciling must not have to parse failure evidence to find out.
    reconciliation_required: bool = False
    # The effective Infrahub branch the run writes to; ``None`` for runs created before
    # the store recorded it.
    destination_branch: str | None = None
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
    # Absent from a Sync API without a Prefect UI address, or one that predates the link.
    prefect_flow_run_url: str | None = None

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


class PrincipalResource(_ResourceModel):
    """Name the authenticated caller and whether it is an administrator."""

    actor: str
    administrator: bool


class RunResource(_ResourceModel):
    """Combine a public run record with its orchestration history."""

    run: PublicRunResource
    orchestration: tuple[OrchestrationSummary, ...]


class RunPage(_ResourceModel):
    """One page of the run list, newest first.

    Items carry the public run fields with empty ``artifact_refs`` and
    ``prefect_executions``; ``GET /runs/{run_id}`` returns them. ``next_cursor`` is ``None``
    on the last page.
    """

    items: tuple[PublicRunResource, ...]
    next_cursor: str | None = None


class PlanSummaryResource(_ResourceModel):
    """Validated saved-plan counts and delete-review disclosures."""

    by_action: dict[str, int]
    by_kind: dict[str, int]
    total: int = Field(ge=0)
    delete_operations_computed: bool
    deletes_not_executed: int = Field(ge=0)


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
