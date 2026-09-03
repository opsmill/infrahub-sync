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

from types import UnionType
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel, ConfigDict

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


_EMITTED_CONFIG = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


def _bounded_annotation(annotation: Any, bounded: dict[type[BaseModel], type[BaseModel]]) -> Any:
    """Rewrite one field annotation so every model inside it is bounded too.

    Bounding a resource one level deep is not bounding it: the leak simply moves into the
    first nested model that still accepts what it was not declared. So the rewrite walks
    the annotation — through unions, tuples, and anything else generic — and swaps each
    model it finds for that model's bounded twin.
    """
    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return _bounded_model(annotation, bounded)
        return annotation
    arguments = tuple(_bounded_annotation(argument, bounded) for argument in get_args(annotation))
    if origin in (Union, UnionType):
        return Union[arguments]
    return origin[arguments]


def _bounded_model(model: type[BaseModel], bounded: dict[type[BaseModel], type[BaseModel]]) -> type[BaseModel]:
    """Return the variant of `model` the server emits, bounded all the way down.

    The twin subclasses the original, so it stays assignable wherever the declared type is
    used and its fields cannot drift from the contract. Only the model-typed fields are
    redeclared, carrying their own `FieldInfo` so defaults and constraints come with them.
    """
    existing = bounded.get(model)
    if existing is not None:
        return existing
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {
        "model_config": _EMITTED_CONFIG,
        "__doc__": f"The bounded variant of `{model.__name__}` the server emits.",
    }
    bounded[model] = model  # guards a self-referential field while this twin is being built
    for name, field in model.model_fields.items():
        rewritten = _bounded_annotation(field.annotation, bounded)
        if rewritten is not field.annotation:
            annotations[name] = rewritten
            namespace[name] = field
    namespace["__annotations__"] = annotations
    twin = type(f"Emitted{model.__name__}", (model,), namespace)
    bounded[model] = twin
    return twin


def bounded_emitted_model(model: type[BaseModel]) -> type[BaseModel]:
    """Return the bounded variant of one resource the server emits."""
    return _bounded_model(model, {})


EmittedRunResource = cast("type[PublicRunResource]", bounded_emitted_model(PublicRunResource))
EmittedPlanResource = cast("type[PlanResource]", bounded_emitted_model(PlanResource))

# What the server builds out of data it did not write literally, and therefore what the
# boundedness check in the test suite walks.
EMITTED_RESOURCES: tuple[type[BaseModel], ...] = (EmittedRunResource, EmittedPlanResource)
# The run resource declares its executions as bounded, so the projection has to build them
# that way; a parent-class instance is not one of these.
_EmittedExecutionLink = cast(
    "type[PublicExecutionLink]", EmittedRunResource.model_fields["prefect_executions"].annotation.__args__[0]
)


def public_execution_link(link: PrefectExecutionLink) -> PublicExecutionLink:
    """Project one persistence record without internal worker identities."""
    return _EmittedExecutionLink.model_validate(
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
