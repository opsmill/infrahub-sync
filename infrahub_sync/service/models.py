"""Server projections into the neutral Sync HTTP contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.client.models import (
    ApplyRunRequest,
    ArtifactListResource,
    CancelRunRequest,
    ConfigErrorDetail,
    ConfigErrorEnvelope,
    ConfigMutationRequest,
    CreateRunRequest,
    ErrorDetail,
    ErrorEnvelope,
    OrchestrationSummary,
    PlanOperationResource,
    PlanResource,
    PlanSummaryResource,
    PublicExecutionLink,
    PublicRunResource,
    ResultsResource,
    RunResource,
    ServiceStatusResource,
    VerifyRunRequest,
    VersionResource,
    WorkerStatusResource,
)

if TYPE_CHECKING:
    from infrahub_sync.product_store import PrefectExecutionLink, ProductRun

__all__ = [
    "ApplyRunRequest",
    "ArtifactListResource",
    "CancelRunRequest",
    "ConfigErrorDetail",
    "ConfigErrorEnvelope",
    "ConfigMutationRequest",
    "CreateRunRequest",
    "ErrorDetail",
    "ErrorEnvelope",
    "OrchestrationSummary",
    "PlanOperationResource",
    "PlanResource",
    "PlanSummaryResource",
    "PublicExecutionLink",
    "PublicRunResource",
    "ResultsResource",
    "RunResource",
    "ServiceStatusResource",
    "VerifyRunRequest",
    "VersionResource",
    "WorkerStatusResource",
    "public_execution_link",
    "public_run_resource",
]


def public_execution_link(link: PrefectExecutionLink) -> PublicExecutionLink:
    """Project one persistence record without internal worker identities."""
    return PublicExecutionLink.model_validate(
        link.model_dump(
            include={
                "flow_run_id",
                "deployment_id",
                "purpose",
                "attempt",
                "last_observed_state",
                "last_observed_at",
            }
        )
    )


def public_run_resource(run: ProductRun) -> PublicRunResource:
    """Project a store run into the standalone public wire resource."""
    return PublicRunResource.model_validate(
        {
            **run.model_dump(exclude={"prefect_executions"}),
            "prefect_executions": tuple(public_execution_link(link) for link in run.prefect_executions),
        }
    )
