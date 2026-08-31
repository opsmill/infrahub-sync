"""Per-side, per-run DiffSync model classes built in memory from one snapshot.

Construction reproduces the shipping generator exactly inside the closed attribute-kind
domain below: the same identifiers, the same ``_attributes``, the same annotations and
defaults, and the same generated-equivalent intermediate base that owns ``local_id`` and
``local_data`` only when the resolved model base does not already carry them. Field
inclusion and identity come from the generator's own helpers, so the two mechanisms
cannot drift apart; only the materialization differs — ``type(...)`` instead of rendered
text.

No ``_children`` mapping is emitted, matching the generator: saved-plan derivation
refuses nested child diffs, so runtime construction must not activate them.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from infrahub_sync.generator import get_attributes, get_identifiers, has_field, has_node

from .domain import NormalizedAttribute, NormalizedKind, NormalizedRelationship
from .errors import UnsupportedSchemaSemanticsError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from diffsync import DiffSyncModel
    from infrahub_sdk.schema import NodeSchema

    from infrahub_sync import SyncConfig

    from .domain import DestinationSchemaSnapshot

# The closed attribute-kind domain. It is the generator's ``ATTRIBUTE_KIND_MAP` plus the
# four string-like kinds the maintained NetBox schema really declares — ``Dropdown``,
# ``MacAddress``, ``IPHost`` and ``IPNetwork`` — which the generator reaches only through
# its unknown-kind-to-``str`` fallback. Naming them keeps those qualified kinds without
# keeping the fallback: a kind outside this table refuses rather than becoming a string.
ATTRIBUTE_TYPE_DOMAIN: Mapping[str, Any] = MappingProxyType(
    {
        "Text": str,
        "String": str,
        "TextArea": str,
        "DateTime": str,
        "HashedPassword": str,
        "Dropdown": str,
        "MacAddress": str,
        "IPHost": str,
        "IPNetwork": str,
        "Number": int,
        "Integer": int,
        "Boolean": bool,
        "Checkbox": bool,
        "List": list[Any],
    }
)

_REQUIRED = object()


def _attribute_field(attribute: NormalizedAttribute, *, kind: str) -> tuple[Any, Any]:
    """Return the annotation and default the generator would render for an attribute."""
    try:
        python_type = ATTRIBUTE_TYPE_DOMAIN[attribute.kind]
    except KeyError:
        msg = (
            f"destination kind {kind!r} maps attribute {attribute.name!r} of kind "
            f"{attribute.kind!r}, which is outside the supported attribute kinds"
        )
        raise UnsupportedSchemaSemanticsError(msg) from None
    if not attribute.optional:
        return python_type, _REQUIRED
    return python_type | None, attribute.default_value


def _relationship_field(relationship: NormalizedRelationship) -> tuple[Any, Any]:
    """Return the annotation and default the generator would render for a relationship."""
    if relationship.cardinality == "one":
        return (str | None, None) if relationship.optional else (str, _REQUIRED)
    return (list[str] | None, []) if relationship.optional else (list[str], [])


def _intermediate_base(model_base: type[DiffSyncModel]) -> type[DiffSyncModel]:
    """Build the generated file's ``_GeneratedModelBase`` over a resolved model base."""
    declared = getattr(model_base, "model_fields", {})
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {}
    if "local_id" not in declared:
        annotations["local_id"] = str | None
        namespace["local_id"] = None
    if "local_data" not in declared:
        annotations["local_data"] = Any | None
        namespace["local_data"] = None
    namespace["__annotations__"] = annotations
    return cast("type[DiffSyncModel]", type("_GeneratedModelBase", (model_base,), namespace))


def build_runtime_models(
    *,
    snapshot: DestinationSchemaSnapshot,
    configuration: SyncConfig,
    model_base: type[DiffSyncModel],
) -> dict[str, type[DiffSyncModel]]:
    """Build one side's fresh ``{kind: model class}`` mapping for one run.

    Every call returns new class objects over a new intermediate base, so two
    configurations sharing a kind name — or a rebuild after a schema change — cannot
    reach each other's classes. Nothing is cached, registered, or written.

    Raises:
        UnsupportedSchemaSemanticsError: a mapped attribute declares a kind outside
            :data:`ATTRIBUTE_TYPE_DOMAIN`.
    """
    intermediate = _intermediate_base(model_base)
    models: dict[str, type[DiffSyncModel]] = {}
    for kind, node in sorted(snapshot.kinds.items()):
        # The generator helpers read a node's kind, attributes and relationships, which
        # the normalized kind presents under the same names.
        view = cast("NodeSchema", node)
        identifiers = get_identifiers(node=view, config=configuration)
        if not identifiers or not has_node(config=configuration, name=kind):
            continue
        annotations: dict[str, Any] = {}
        namespace: dict[str, Any] = {
            "_modelname": kind,
            "_identifiers": tuple(identifiers),
            "_attributes": tuple(sorted(get_attributes(node=view, config=configuration) or ())),
        }
        for member in (*node.attributes, *node.relationships):
            if not has_field(config=configuration, name=kind, field=member.name):
                continue
            annotation, default = (
                _attribute_field(member, kind=kind)
                if isinstance(member, NormalizedAttribute)
                else _relationship_field(member)
            )
            annotations[member.name] = annotation
            if default is not _REQUIRED:
                namespace[member.name] = default
        namespace["__annotations__"] = annotations
        models[kind] = cast("type[DiffSyncModel]", type(kind, (intermediate,), namespace))
    return models


def mapped_attribute_kinds(snapshot: DestinationSchemaSnapshot, configuration: SyncConfig) -> set[str]:
    """Return every attribute kind the configuration maps on a declared kind."""
    return {
        attribute.kind
        for node in snapshot.kinds.values()
        for attribute in node.attributes
        if has_field(config=configuration, name=node.kind, field=attribute.name)
    }


__all__ = ["ATTRIBUTE_TYPE_DOMAIN", "NormalizedKind", "build_runtime_models", "mapped_attribute_kinds"]
