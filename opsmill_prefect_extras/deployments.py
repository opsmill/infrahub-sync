"""Apply declared workflow deployments to a Prefect server.

This module is deliberately limited to deployment convergence.  Applications
provide their own work-pool name, job variables, client configuration, and any
multi-replica coordination.  It creates neither pools nor workers and never
starts a flow.

The only workflow-feature import is :mod:`.workflows.definitions`, its common
data module.  In particular, applying deployments does not import the
catalogue or validation facades.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentCreate, DeploymentUpdate
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound

from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition

ApplyStatus = Literal["created", "updated", "unchanged", "failed"]
"""One definition's convergence verdict."""

_COMPARABLE_FIELDS: tuple[str, ...] = (
    "name",
    "tags",
    "schedules",
    "concurrency_options",
    "entrypoint",
    "work_pool_name",
    "job_variables",
)
"""Fields this feature owns and therefore reconciles."""

_SENTINEL_FLOW_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")
"""A local schema-validation stand-in; it is never sent to Prefect."""

__all__: list[str] = [
    "ApplyStatus",
    "DefinitionPreflightError",
    "DeploymentApplyConnectionError",
    "DeploymentApplyReport",
    "DeploymentApplyResult",
    "DeploymentClient",
    "DeploymentClientFactory",
    "DeploymentRecord",
    "apply_deployments",
]


class DeploymentRecord(Protocol):
    """The small part of Prefect's deployment response required for updates."""

    id: UUID

    # Any: Prefect's model serializer returns heterogeneous JSON-compatible data.
    def model_dump(self, *, mode: str, exclude_none: bool) -> Mapping[str, Any]:
        """Return a JSON-compatible deployment representation."""


class DeploymentClient(Protocol):
    """Injectable asynchronous seam over the four Prefect calls this feature uses."""

    async def create_flow_from_name(self, flow_name: str) -> UUID:
        """Create or retrieve the server flow identified by ``flow_name``."""

    # Any: Prefect deployment payload fields are deliberately heterogeneous.
    async def create_deployment(self, flow_id: UUID, **payload: Any) -> UUID:
        """Create a deployment; Prefect may upsert an existing identity."""

    async def read_deployment_by_name(self, name: str) -> DeploymentRecord:
        """Read by ``flow_name/deployment_name``, raising if it is absent."""

    async def update_deployment(
        self, deployment_id: UUID, deployment: DeploymentUpdate
    ) -> None:
        """Update the fields this feature owns on an existing deployment."""


DeploymentClientFactory = Callable[[], AbstractAsyncContextManager[DeploymentClient]]
"""A caller-injectable client context factory, useful for connection setup."""


class DeploymentApplyConnectionError(RuntimeError):
    """The client context could not open, so no definition was attempted.

    Attributes:
        cause_type: The class name of the opening failure.  Its value is not
            rendered, because client exceptions can contain credentials.
    """

    cause_type: str

    def __init__(self, cause: BaseException) -> None:
        """Describe a connection-opening failure without disclosing its value."""
        self.cause_type = type(cause).__name__
        super().__init__(
            "could not open the Prefect client before deployment apply began "
            f"({self.cause_type})"
        )


class DefinitionPreflightError(ValueError):
    """A definition no longer satisfies its key grammar at apply time."""


@dataclass(frozen=True, kw_only=True, slots=True)
class DeploymentApplyResult:
    """The convergence result for one workflow definition.

    Attributes:
        definition: The definition this outcome concerns.
        status: Whether it was created, updated, already unchanged, or failed.
        error_type: The failure class name when ``status`` is ``"failed"``.
            Exception values are intentionally never retained.
    """

    definition: WorkflowDefinition
    status: ApplyStatus
    error_type: str | None = None

    def __post_init__(self) -> None:
        """Keep successful and failed results internally consistent.

        Raises:
            ValueError: If a failure has no type or a success carries one.
        """
        if self.status == "failed" and self.error_type is None:
            raise ValueError("a failed deployment result needs an error type")
        if self.status != "failed" and self.error_type is not None:
            raise ValueError("a successful deployment result cannot have an error type")

    @property
    def key(self) -> str:
        """Return this result's ``flow_name/deployment_name`` identity."""
        return self.definition.key


@dataclass(frozen=True, kw_only=True, slots=True)
class DeploymentApplyReport:
    """The complete, ordered result of one deployment apply operation."""

    results: tuple[DeploymentApplyResult, ...]

    @property
    def created(self) -> tuple[DeploymentApplyResult, ...]:
        """Return outcomes for deployments newly created by this call."""
        return self._with_status("created")

    @property
    def updated(self) -> tuple[DeploymentApplyResult, ...]:
        """Return outcomes for deployments reconciled by this call."""
        return self._with_status("updated")

    @property
    def unchanged(self) -> tuple[DeploymentApplyResult, ...]:
        """Return outcomes already matching their declared payload."""
        return self._with_status("unchanged")

    @property
    def failed(self) -> tuple[DeploymentApplyResult, ...]:
        """Return outcomes whose individual deployment operation failed."""
        return self._with_status("failed")

    @property
    def is_successful(self) -> bool:
        """Whether every attempted definition converged successfully."""
        return not self.failed

    def _with_status(self, status: ApplyStatus) -> tuple[DeploymentApplyResult, ...]:
        """Select report results with one status."""
        return tuple(result for result in self.results if result.status == status)


async def apply_deployments(
    definitions: Iterable[WorkflowDefinition],
    *,
    work_pool_name: str,
    job_variables: Mapping[str, Any] | None = None,
    client: DeploymentClient | None = None,
    client_factory: DeploymentClientFactory | None = None,
) -> DeploymentApplyReport:
    """Converge definitions to deployments using caller-owned infrastructure.

    Each definition is read before any write because Prefect's create endpoint
    upserts an existing identity.  A match is unchanged, a difference is
    updated, and only a missing deployment is created.  Schema and definition
    preflight failures, plus server failures after apply starts, become
    per-definition ``failed`` results so later definitions continue.

    Args:
        definitions: Definitions or a ``WorkflowCatalogue`` to reconcile.
        work_pool_name: Existing Prefect work pool selected by the application.
        job_variables: Optional Prefect job variables owned by the application.
        client: An already-open Prefect client or offline fake client.
        client_factory: A context factory for a Prefect client.  Its opening
            errors raise :class:`DeploymentApplyConnectionError` before any
            definition is attempted.  Mutually exclusive with ``client``.

    Returns:
        An ordered report with one verdict for every supplied definition.

    Raises:
        ValueError: If both client injection options are supplied.
        DeploymentApplyConnectionError: If the selected client context cannot
            open before processing starts.
    """
    if client is not None and client_factory is not None:
        raise ValueError("pass either client or client_factory, not both")

    if client is not None:
        return await _apply_with_client(
            definitions,
            work_pool_name=work_pool_name,
            job_variables=job_variables,
            client=client,
        )

    manager = get_client() if client_factory is None else client_factory()
    entered = False
    try:
        async with manager as active_client:
            entered = True
            return await _apply_with_client(
                definitions,
                work_pool_name=work_pool_name,
                job_variables=job_variables,
                client=cast(DeploymentClient, active_client),
            )
    except Exception as exc:
        if not entered:
            raise DeploymentApplyConnectionError(exc) from None
        raise


async def _apply_with_client(
    definitions: Iterable[WorkflowDefinition],
    *,
    work_pool_name: str,
    job_variables: Mapping[str, Any] | None,
    client: DeploymentClient,
) -> DeploymentApplyReport:
    """Apply every definition after a client has opened successfully."""
    results: list[DeploymentApplyResult] = []
    for definition in definitions:
        results.append(
            await _apply_definition(
                definition,
                work_pool_name=work_pool_name,
                job_variables=job_variables,
                client=client,
            )
        )
    return DeploymentApplyReport(results=tuple(results))


async def _apply_definition(
    definition: WorkflowDefinition,
    *,
    work_pool_name: str,
    job_variables: Mapping[str, Any] | None,
    client: DeploymentClient,
) -> DeploymentApplyResult:
    """Apply one definition, retaining only an exception's safe type name."""
    try:
        payload = _validated_payload(
            definition, work_pool_name=work_pool_name, job_variables=job_variables
        )
        try:
            existing = await client.read_deployment_by_name(definition.key)
        except ObjectNotFound:
            flow_id = await client.create_flow_from_name(definition.flow_name)
            try:
                await client.create_deployment(flow_id, **payload)
            except ObjectAlreadyExists:
                # Some servers surface a create/create race as a conflict.
                # Prefect OSS upserts instead, so applications with multiple
                # replicas still own their coordination boundary.
                existing = await client.read_deployment_by_name(definition.key)
            else:
                return DeploymentApplyResult(definition=definition, status="created")

        if _matches(existing, payload):
            return DeploymentApplyResult(definition=definition, status="unchanged")
        await client.update_deployment(existing.id, _update(payload))
        return DeploymentApplyResult(definition=definition, status="updated")
    except Exception as exc:
        return DeploymentApplyResult(
            definition=definition, status="failed", error_type=type(exc).__name__
        )


def _validated_payload(
    definition: WorkflowDefinition,
    *,
    work_pool_name: str,
    job_variables: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Render the caller-owned payload after definition and Prefect checks."""
    _assert_definition_key_grammar(definition)
    # Any: workflow definitions render Prefect's heterogeneous input payload.
    payload: dict[str, Any] = definition.to_deployment_input()
    payload["work_pool_name"] = work_pool_name
    if job_variables is not None:
        payload["job_variables"] = dict(job_variables)
    DeploymentCreate(flow_id=_SENTINEL_FLOW_ID, **payload)
    return payload


def _assert_definition_key_grammar(definition: WorkflowDefinition) -> None:
    """Recheck the definition's two construction rules before an external call."""
    if "/" in definition.flow_name or "/" in definition.deployment_name:
        raise DefinitionPreflightError(
            "definition names must not contain the deployment address separator"
        )


def _matches(existing: DeploymentRecord, desired: Mapping[str, Any]) -> bool:
    """Compare the persisted deployment with the fields this feature owns."""
    existing_payload = existing.model_dump(mode="json", exclude_none=True)
    comparable_existing = {
        field: existing_payload[field]
        for field in _COMPARABLE_FIELDS
        if field in existing_payload
    }
    if "schedules" in comparable_existing:
        comparable_existing["schedules"] = _response_schedules(
            comparable_existing["schedules"]
        )
    normalized_existing = _normalised_payload(comparable_existing)
    desired_payload = _normalised_payload(desired)
    fields_match = all(
        normalized_existing.get(field) == desired_payload.get(field)
        for field in _COMPARABLE_FIELDS
    )
    return fields_match and _response_concurrency_limit(existing_payload) == (
        desired_payload.get("concurrency_limit")
    )


def _response_concurrency_limit(payload: Mapping[str, Any]) -> object:
    """Read a deployment limit from Prefect's response-shaped payload.

    Newer Prefect responses keep the deprecated ``concurrency_limit`` field as
    ``None`` and put the effective value under ``global_concurrency_limit``.
    Older response shapes may still carry the direct field, so that remains a
    fallback for the declared compatibility range.
    """
    global_limit = payload.get("global_concurrency_limit")
    if isinstance(global_limit, Mapping):
        return global_limit.get("limit")
    return payload.get("concurrency_limit")


def _response_schedules(value: object) -> object:
    """Drop server-owned identity and timestamp fields from schedule entries."""
    if not isinstance(value, list):
        return value
    owned_fields = ("schedule", "active", "parameters")
    normalized: list[object] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            normalized.append(entry)
            continue
        # Any: Prefect's JSON response mapping has heterogeneous values.
        response_entry = cast(Mapping[str, Any], entry)
        normalized.append(
            {
                field: response_entry[field]
                for field in owned_fields
                if field in response_entry
            }
        )
    return normalized


def _normalised_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Ask Prefect to normalize defaults before comparing a desired payload."""
    model = DeploymentCreate(flow_id=_SENTINEL_FLOW_ID, **payload)
    return cast(Mapping[str, Any], model.model_dump(mode="json", exclude_none=True))


def _update(payload: Mapping[str, Any]) -> DeploymentUpdate:
    """Build Prefect's named update schema from the fields this feature owns."""
    normalized = _normalised_payload(payload)
    update_fields = {
        field: normalized[field]
        for field in _COMPARABLE_FIELDS
        if field != "name" and field in normalized
    }
    # Setting this field explicitly also lets an update clear a prior limit.
    update_fields["concurrency_limit"] = normalized.get("concurrency_limit")
    return DeploymentUpdate(**update_fields)
