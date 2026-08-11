"""Adapters from executed interface observations to the DB-006 envelope."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from tests.conformance.oracle import CanonicalEnvelope, Surface

if TYPE_CHECKING:
    from infrahub_sync.api.v1 import RunResult as PublicRunResult
    from infrahub_sync.execution import RunResult as CoreRunResult
    from infrahub_sync.product_store import ProductRun


class DestinationMeasurement(Protocol):
    """Read the isolated destination's observed effects after an interface call."""

    def measure(self) -> Mapping[str, int]: ...


def _action_counts(values: Mapping[str, Any]) -> dict[str, int]:
    nested = values.get("by_action")
    source = nested if isinstance(nested, Mapping) else values
    return {action: int(source.get(action, 0)) for action in ("create", "update", "delete")}


def _product_envelope(
    *,
    surface: Surface,
    boundary_result: Mapping[str, Any],
    record: ProductRun,
    artifact: bytes,
    destination: DestinationMeasurement,
) -> CanonicalEnvelope:
    artifact_semantics = json.loads(artifact)
    return CanonicalEnvelope(
        surface=surface,
        operation=str(boundary_result["operation"]),
        plan_fingerprint=str(artifact_semantics["checksum"]),
        counts=_action_counts(boundary_result["counts"]),
        outcome=str(boundary_result["outcome"]),
        destination_effects=dict(destination.measure()),
        product_record=record.model_dump(mode="json"),
        result=dict(boundary_result),
        artifact_references=[item.model_dump(mode="json") for item in record.artifact_refs],
        artifact_semantics=artifact_semantics,
    )


def cli_product_envelope(  # noqa: PLR0913 - all six values are independently observed boundaries.
    *,
    core_result: CoreRunResult,
    exit_code: int,
    rendering: str,
    record: ProductRun,
    artifact: bytes,
    destination: DestinationMeasurement,
) -> CanonicalEnvelope:
    """Adapt the captured CLI/core result, CLI rendering, and measured destination."""
    if exit_code != 0 or "Traceback" in rendering:
        msg = f"CLI boundary was not successful: exit_code={exit_code}, rendering={rendering!r}"
        raise AssertionError(msg)
    if core_result.operation == "apply" and f"Applied run {core_result.run_id}" not in rendering:
        msg = "CLI apply rendering did not identify the applied run"
        raise AssertionError(msg)
    boundary = {
        "run_id": core_result.run_id,
        "operation": core_result.operation,
        "outcome": core_result.status,
        "counts": {key: core_result.summary[key] for key in ("create", "update", "delete")},
        "boundary_success": True,
    }
    return _product_envelope(
        surface="cli",
        boundary_result=boundary,
        record=record,
        artifact=artifact,
        destination=destination,
    )


def python_product_envelope(
    *,
    public_result: PublicRunResult,
    record: ProductRun,
    artifact: bytes,
    destination: DestinationMeasurement,
) -> CanonicalEnvelope:
    """Adapt the actual public-Python return and measured destination."""
    boundary = {
        "run_id": public_result.run_id,
        "operation": public_result.operation,
        "outcome": public_result.outcome,
        "counts": public_result.counts.model_dump(),
        "boundary_success": True,
    }
    return _product_envelope(
        surface="python",
        boundary_result=boundary,
        record=record,
        artifact=artifact,
        destination=destination,
    )


def managed_product_envelope(
    *,
    worker_result: Mapping[str, Any],
    record: ProductRun,
    artifact: bytes,
    destination: DestinationMeasurement,
) -> CanonicalEnvelope:
    """Adapt the actual managed worker result and measured destination."""
    operation = worker_result.get("operation", worker_result.get("stage"))
    summary = worker_result["summary"]
    if not isinstance(summary, Mapping):
        msg = "managed result summary must be a mapping"
        raise TypeError(msg)
    boundary = {
        "run_id": worker_result["run_id"],
        "operation": operation,
        "outcome": worker_result["outcome"],
        "counts": _action_counts(summary),
        "boundary_success": True,
    }
    return _product_envelope(
        surface="managed",
        boundary_result=boundary,
        record=record,
        artifact=artifact,
        destination=destination,
    )


def serialized_boundaries(*values: object) -> bytes:
    """Render producing boundaries for the recognizable-sentinel scan."""
    return json.dumps(values, default=str, sort_keys=True).encode()
