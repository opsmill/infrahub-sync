"""The closed normalized destination-schema domain the runtime model path consumes.

One immutable value per run. It carries only the facts Sync consumes — kind name,
ordered ``human_friendly_id`` and ``uniqueness_constraints`` component paths, and every
attribute and relationship property that can change a constructed model or a planned
write — so no SDK object, response text, or credential reaches the builder or the
fingerprint.

Normalization is total over the JSON-native snapshot the destination accessor returns:
a value outside the domain raises :class:`UnsupportedSchemaSemanticsError` rather than
being coerced. Members are ordered by name, so snapshot delivery order cannot change a
normalized snapshot or anything derived from one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from .errors import UnsupportedSchemaSemanticsError

CARDINALITIES = frozenset({"one", "many"})


@dataclass(frozen=True, slots=True)
class NormalizedAttribute:
    """One destination attribute, with every property a model or write depends on."""

    name: str
    kind: str
    optional: bool
    default_value: Any
    unique: bool


@dataclass(frozen=True, slots=True)
class NormalizedRelationship:
    """One destination relationship, with its peer, shape, and relationship kind."""

    name: str
    peer: str
    cardinality: str
    optional: bool
    kind: str


@dataclass(frozen=True, slots=True)
class NormalizedKind:
    """One destination kind and its identity paths, members ordered by name.

    Spelled ``kind`` rather than ``name`` because the shipping generator helpers the
    model builder reuses read a node's kind under that name.
    """

    kind: str
    human_friendly_id: tuple[str, ...]
    uniqueness_constraints: tuple[tuple[str, ...], ...]
    attributes: tuple[NormalizedAttribute, ...]
    relationships: tuple[NormalizedRelationship, ...]


@dataclass(frozen=True, slots=True)
class DestinationSchemaSnapshot:
    """One immutable destination schema, keyed by kind name."""

    kinds: Mapping[str, NormalizedKind]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", dict(self.kinds))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DestinationSchemaSnapshot):
            return NotImplemented
        return dict(self.kinds) == dict(other.kinds)

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.kinds)))


def _refuse(detail: str) -> NoReturn:
    msg = f"destination schema snapshot is outside the supported domain: {detail}"
    raise UnsupportedSchemaSemanticsError(msg)


def _require_mapping(value: object, *, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _refuse(detail)
    for key in value:
        if not isinstance(key, str):
            _refuse(detail)
    return cast("Mapping[str, Any]", value)


def _require_bool(value: object, *, detail: str) -> bool:
    if not isinstance(value, bool):
        _refuse(detail)
    return value


def _require_str(value: object, *, detail: str) -> str:
    if not isinstance(value, str):
        _refuse(detail)
    return value


def _require_json_default(value: object, *, detail: str) -> Any:
    """Accept only a JSON-native default, so a model default is reproducible."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_require_json_default(item, detail=detail) for item in value]
    if isinstance(value, Mapping):
        return {
            _require_str(key, detail=detail): _require_json_default(item, detail=detail) for key, item in value.items()
        }
    _refuse(detail)


def _component_paths(value: object, *, kind: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _refuse(f"kind {kind!r} declares a non-list component path")
    return tuple(_require_str(item, detail=f"kind {kind!r} declares a non-string path component") for item in value)


def _normalized_attribute(name: str, entry: object, *, kind: str) -> NormalizedAttribute:
    detail = f"kind {kind!r} attribute {name!r}"
    member = _require_mapping(entry, detail=detail)
    missing = {"kind", "optional", "default_value", "unique"} - set(member)
    if missing:
        _refuse(f"{detail} is missing {sorted(missing)!r}")
    return NormalizedAttribute(
        name=name,
        kind=_require_str(member["kind"], detail=detail),
        optional=_require_bool(member["optional"], detail=detail),
        default_value=_require_json_default(member["default_value"], detail=detail),
        unique=_require_bool(member["unique"], detail=detail),
    )


def _normalized_relationship(name: str, entry: object, *, kind: str) -> NormalizedRelationship:
    detail = f"kind {kind!r} relationship {name!r}"
    member = _require_mapping(entry, detail=detail)
    missing = {"peer", "cardinality", "optional", "kind"} - set(member)
    if missing:
        _refuse(f"{detail} is missing {sorted(missing)!r}")
    cardinality = _require_str(member["cardinality"], detail=detail)
    if cardinality not in CARDINALITIES:
        _refuse(f"{detail} declares cardinality {cardinality!r}")
    return NormalizedRelationship(
        name=name,
        peer=_require_str(member["peer"], detail=detail),
        cardinality=cardinality,
        optional=_require_bool(member["optional"], detail=detail),
        kind=_require_str(member["kind"], detail=detail),
    )


def normalize_destination_schema(snapshot: Mapping[str, Any]) -> DestinationSchemaSnapshot:
    """Normalize one JSON-native destination snapshot into the closed domain.

    Raises:
        UnsupportedSchemaSemanticsError: a member, property, or value the domain does
            not declare.
    """
    _require_mapping(snapshot, detail="snapshot root is not a mapping of kind names")
    kinds: dict[str, NormalizedKind] = {}
    for kind, entry in snapshot.items():
        member = _require_mapping(entry, detail=f"kind {kind!r} is not a mapping")
        missing = {"human_friendly_id", "uniqueness_constraints", "attributes", "relationships"} - set(member)
        if missing:
            _refuse(f"kind {kind!r} is missing {sorted(missing)!r}")
        raw_constraints = member["uniqueness_constraints"]
        if not isinstance(raw_constraints, Sequence) or isinstance(raw_constraints, (str, bytes)):
            _refuse(f"kind {kind!r} declares non-list uniqueness constraints")
        attributes = _require_mapping(member["attributes"], detail=f"kind {kind!r} attributes")
        relationships = _require_mapping(member["relationships"], detail=f"kind {kind!r} relationships")
        kinds[kind] = NormalizedKind(
            kind=kind,
            human_friendly_id=_component_paths(member["human_friendly_id"] or (), kind=kind),
            uniqueness_constraints=tuple(_component_paths(item, kind=kind) for item in raw_constraints),
            attributes=tuple(_normalized_attribute(name, attributes[name], kind=kind) for name in sorted(attributes)),
            relationships=tuple(
                _normalized_relationship(name, relationships[name], kind=kind) for name in sorted(relationships)
            ),
        )
    return DestinationSchemaSnapshot(kinds=kinds)
