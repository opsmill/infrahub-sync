"""Canonical, lossless interface-envelope projection for DB-006 tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

Surface = Literal["cli", "python", "managed"]

GENERATED_ID_FIELDS = frozenset({"run_id", "flow_run_id", "deployment_id", "receipt_id", "event_id"})
TIMESTAMP_FIELDS = frozenset({"started_at", "finished_at", "created_at", "updated_at", "last_observed_at"})


@dataclass(frozen=True, slots=True)
class CanonicalEnvelope:
    """Every named semantic field compared by the conformance matrix."""

    surface: Surface
    operation: str
    plan_fingerprint: str
    counts: Mapping[str, int]
    outcome: str
    destination_effects: Mapping[str, int]
    product_record: Mapping[str, Any]
    result: Mapping[str, Any]
    artifact_references: Sequence[Mapping[str, Any]]
    artifact_semantics: Mapping[str, Any]

    def normalized(self) -> dict[str, object]:
        """Normalize only generated identities/timestamps and discard the surface label."""
        data = asdict(self)
        data.pop("surface")
        return cast("dict[str, object]", _normalize(data))


def _normalize(value: object, *, field: str | None = None) -> object:
    if field in GENERATED_ID_FIELDS:
        return "<generated-id>" if value is not None else None
    if field in TIMESTAMP_FIELDS:
        return "<timestamp>" if value is not None else None
    if isinstance(value, Mapping):
        return {str(key): _normalize(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def assert_equivalent(envelopes: Sequence[CanonicalEnvelope]) -> None:
    """Require exact canonical equality after the approved bounded normalization."""
    if not envelopes:
        msg = "the conformance oracle requires at least one envelope"
        raise AssertionError(msg)
    expected = envelopes[0].normalized()
    disagreements = {
        envelope.surface: envelope.normalized() for envelope in envelopes[1:] if envelope.normalized() != expected
    }
    if disagreements:
        msg = (
            f"canonical interface disagreement: expected {envelopes[0].surface}={expected!r}; "
            f"observed={disagreements!r}"
        )
        raise AssertionError(msg)
