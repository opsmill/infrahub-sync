"""Canonical, lossless interface-envelope projection for DB-006 tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

Surface = Literal["cli", "python", "managed"]

RUN_ID_SCHEMA_PATHS = (
    ("product_record", "run_id"),
    ("product_record", "artifact_refs", "*", "run_id"),
    ("result", "run_id"),
    ("artifact_references", "*", "run_id"),
    ("artifact_semantics", "run_id"),
)

NORMALIZED_SCHEMA_PATHS = frozenset(
    {
        *RUN_ID_SCHEMA_PATHS,
        ("product_record", "started_at"),
        ("product_record", "finished_at"),
        ("product_record", "artifact_refs", "*", "created_at"),
        ("product_record", "prefect_executions", "*", "flow_run_id"),
        ("product_record", "prefect_executions", "*", "deployment_id"),
        ("product_record", "prefect_executions", "*", "last_observed_at"),
        ("artifact_references", "*", "created_at"),
    }
)


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
        _require_one_run_identity(data)
        return cast("dict[str, object]", _normalize(data))


def _path_values(value: object, path: tuple[str, ...]) -> list[object]:
    if not path:
        return [value]
    head, *tail = path
    if head == "*":
        if not isinstance(value, (list, tuple)):
            return []
        return [item for child in value for item in _path_values(child, tuple(tail))]
    if not isinstance(value, Mapping):
        return []
    mapping = cast("Mapping[str, object]", value)
    if head not in mapping:
        return []
    return _path_values(mapping[head], tuple(tail))


def _require_one_run_identity(data: Mapping[str, object]) -> None:
    aliases = [identity for path in RUN_ID_SCHEMA_PATHS for identity in _path_values(data, path)]
    if any(not isinstance(alias, str) for alias in aliases):
        msg = f"one envelope contains a non-string run identity alias: {aliases!r}"
        raise AssertionError(msg)
    identities = set(cast("list[str]", aliases))
    if len(identities) > 1:
        msg = f"one envelope contains inconsistent run identity aliases: {sorted(identities)!r}"
        raise AssertionError(msg)


def _normalize(value: object, *, path: tuple[str, ...] = ()) -> object:
    if path in NORMALIZED_SCHEMA_PATHS:
        return "<generated>" if value is not None else None
    if isinstance(value, Mapping):
        return {str(key): _normalize(item, path=(*path, str(key))) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item, path=(*path, "*")) for item in value]
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
