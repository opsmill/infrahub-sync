"""Server projections into the neutral Sync HTTP contract.

The client's resource models tolerate fields they do not declare, so a client stays
readable against a newer server. The server may not: what it emits is an operator-facing
surface, and it has to own everything that can appear in there. So every resource the
server *builds* from data it did not write literally — a store record's fields, a retained
artifact's bytes — is validated through a strict variant of the same model. Adding an
internal field to a durable record therefore forces an explicit decision about publishing
it, instead of publishing it silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ConfigDict

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


class EmittedRunResource(PublicRunResource):
    """The run resource the server emits: only its declared fields may appear."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class EmittedPlanResource(PlanResource):
    """The plan resource the server emits, read back from a retained artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def public_run_resource(run: ProductRun) -> EmittedRunResource:
    """Project a store run into the self-contained public wire resource.

    The projection copies the record's fields wholesale, so the strict variant is what
    keeps a future internal field from reaching an operator without anyone choosing to
    publish it.
    """
    return EmittedRunResource.model_validate(
        {
            **run.model_dump(exclude={"prefect_executions"}),
            "prefect_executions": tuple(public_execution_link(link) for link in run.prefect_executions),
        }
    )
