"""Fill an unmapped IP namespace from the live Infrahub schema (`configs convert --live-schema`).

v2 never mapped `ip_namespace`, so Infrahub put every prefix and address v2 wrote into its
built-in `default` namespace. V3 refuses to plan a kind unless every component of a
destination uniqueness constraint is mapped. With `--live-schema`, the command reads the
destination schema and, for each mapped kind whose uniqueness constraint names an unmapped
cardinality-one relationship to an IP namespace, maps that relationship to the `default`
namespace and says so in the report.

The schema read is anonymous: the package's token reference is removed before the read, so
the command still reads no credential value. A read that fails is reported, and the package
is written without the fill.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from infrahub_sync.configuration.capabilities import DestinationSchemaReadError, get_adapter_capabilities
from infrahub_sync.configuration.conversion import ConversionResult, NoteEntry, RefusalEntry
from infrahub_sync.configuration.models import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.configuration.runtime import effective_destination_branch
from infrahub_sync.configuration.validation import collect_findings
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.errors import RuntimeSchemaError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync.runtime_schema.domain import DestinationSchemaSnapshot

# The name Infrahub gives the IP namespace it creates on every instance.
DEFAULT_IP_NAMESPACE = "default"
# The generic every IP namespace kind inherits, and the core kind Infrahub ships.
_IP_NAMESPACE_GENERIC = "BuiltinIPNamespace"
_IP_NAMESPACE_KINDS = frozenset({_IP_NAMESPACE_GENERIC, "IpamNamespace"})


@dataclass(frozen=True, slots=True)
class FilledRelationship:
    """One relationship the command mapped to the default IP namespace, or why it could not.

    ``unmapped`` names the uniqueness-constraint fields that are still not mapped after the
    fill. When it is not empty, the identifiers were left as they were and the kind still
    cannot plan.
    """

    kind: str
    field: str
    identifiers: tuple[str, ...] = ()
    skipped: str | None = None
    unmapped: tuple[str, ...] = ()


def read_live_destination_schema(package: Mapping[str, Any]) -> DestinationSchemaSnapshot:
    """Read the destination schema of a converted package without a token.

    Raises:
        DestinationSchemaReadError: the read failed, including a refused anonymous read.
    """
    anonymous = copy.deepcopy(dict(package))
    settings = anonymous["configuration"]["destination"].setdefault("settings", {})
    settings.pop("token", None)
    try:
        parsed = parse_configuration_package(anonymous)
    except ConfigurationPackageParseError:
        msg = "the converted package could not be read for the schema request"
        raise DestinationSchemaReadError(msg, reason="unconfigured") from None
    accessor = get_adapter_capabilities(parsed.configuration.destination.name).destination_schema_accessor
    if accessor is None:  # pragma: no cover - the conversion refuses any destination but infrahub
        msg = "the destination declares no schema read"
        raise DestinationSchemaReadError(msg, reason="unconfigured")
    branch = effective_destination_branch(parsed.configuration.destination.settings, None)
    raw = accessor(parsed, branch)
    try:
        return normalize_destination_schema(raw)
    except RuntimeSchemaError:
        msg = "the destination schema could not be read"
        raise DestinationSchemaReadError(msg, reason="rejected") from None


def fill_default_ip_namespaces(
    configuration: dict[str, Any], snapshot: DestinationSchemaSnapshot
) -> tuple[FilledRelationship, ...]:
    """Map each unmapped IP-namespace relationship of a uniqueness constraint to `default`, in place.

    The relationship becomes a static reference to the namespace kind, so the planner records
    the `default` namespace as a peer that exists only in Infrahub. The namespace kind gets a
    mapping with no source endpoint, which loads nothing from the source and gives the planner
    the kind to match on. The identifiers become the uniqueness constraint, because v2 wrote
    every node into one namespace and the destination matches on that constraint.
    """
    filled: list[FilledRelationship] = []
    mappings: list[dict[str, Any]] = configuration.get("schema_mapping") or []
    for mapping in list(mappings):
        node = snapshot.kinds.get(mapping.get("name"))
        if node is None:
            continue
        fields = mapping.setdefault("fields", [])
        for relationship in node.relationships:
            constraint = _constraint_with(relationship.name, node.uniqueness_constraints)
            if relationship.cardinality != "one" or relationship.read_only or constraint is None:
                continue
            if relationship.name in {field.get("name") for field in fields}:
                continue
            if not _is_ip_namespace(relationship.peer, snapshot):
                continue
            kind, name_attribute, reason = _namespace_kind(relationship.peer, snapshot)
            if reason is not None:
                filled.append(FilledRelationship(mapping["name"], relationship.name, skipped=reason))
                continue
            fields.append({"name": relationship.name, "static": DEFAULT_IP_NAMESPACE, "reference": kind})
            mapped = {field.get("name") for field in fields}
            unmapped = tuple(component for component in constraint if component not in mapped)
            if not unmapped:
                mapping["identifiers"] = list(constraint)
            _ensure_namespace_mapping(mappings, kind, name_attribute, before=mapping)
            filled.append(
                FilledRelationship(
                    mapping["name"],
                    relationship.name,
                    tuple(mapping.get("identifiers") or ()),
                    unmapped=unmapped,
                )
            )
    return tuple(filled)


def _constraint_with(field: str, constraints: tuple[tuple[str, ...], ...]) -> tuple[str, ...] | None:
    """Return the fields of the first uniqueness constraint that names ``field``."""
    for constraint in constraints:
        roots = tuple(component.split("__", 1)[0] for component in constraint)
        if field in roots:
            return roots
    return None


def _namespace_kind(peer: str, snapshot: DestinationSchemaSnapshot) -> tuple[str, str, str | None]:
    """Return the one concrete namespace kind and its name attribute, or why there is none."""
    kinds = snapshot.generic_peers.get(peer, ()) if peer in snapshot.generic_peers else (peer,)
    if len(kinds) != 1:
        return "", "", f"{peer} has {len(kinds)} concrete kinds, so the namespace kind is not known"
    kind = kinds[0]
    node = snapshot.kinds.get(kind)
    hfid = node.human_friendly_id if node is not None else ()
    if len(hfid) != 1 or not hfid[0].endswith("__value"):
        return "", "", f"{kind} has no single-attribute human-friendly ID to name the namespace by"
    return kind, hfid[0].removesuffix("__value"), None


def _ensure_namespace_mapping(
    mappings: list[dict[str, Any]], kind: str, name_attribute: str, *, before: dict[str, Any]
) -> None:
    """Add a mapping for the namespace kind with no source endpoint, unless one exists."""
    if any(mapping.get("name") == kind for mapping in mappings):
        return
    entry = {"name": kind, "identifiers": [name_attribute], "fields": [{"name": name_attribute}]}
    mappings.insert(mappings.index(before), entry)


def _is_ip_namespace(peer: str, snapshot: DestinationSchemaSnapshot) -> bool:
    return peer in _IP_NAMESPACE_KINDS or peer in snapshot.generic_peers.get(_IP_NAMESPACE_GENERIC, ())


def with_live_schema(result: ConversionResult) -> ConversionResult:
    """Return ``result`` with default IP namespaces filled from the live schema, and notes saying so."""
    if result.package is None:
        return result
    try:
        snapshot = read_live_destination_schema(result.package)
    except DestinationSchemaReadError as error:
        note = NoteEntry(
            f"--live-schema: the destination schema was not read ({error.reason}); "
            "no IP namespace was filled, so a kind whose uniqueness constraint needs one will not plan"
        )
        return replace(result, notes=(*result.notes, note))
    package = copy.deepcopy(dict(result.package))
    filled = fill_default_ip_namespaces(package["configuration"], snapshot)
    if not any(entry.skipped is None for entry in filled):
        return replace(result, notes=(*result.notes, *(_fill_note(entry) for entry in filled)))
    refusals = _check_filled(package)
    if refusals:
        return replace(result, package=None, refusals=refusals)
    notes = tuple(_fill_note(entry) for entry in filled)
    return replace(result, package=package, notes=(*result.notes, *notes))


def _fill_note(entry: FilledRelationship) -> NoteEntry:
    location = f"schema_mapping {entry.kind}.{entry.field}"
    if entry.skipped is not None:
        return NoteEntry(
            f"{location}: not mapped and needed by the destination uniqueness constraint; not filled: {entry.skipped}"
        )
    filled = (
        f"{location}: not mapped in config.yml and needed by the destination uniqueness constraint; "
        f"set to the {DEFAULT_IP_NAMESPACE!r} namespace, where v2 wrote it"
    )
    if entry.unmapped:
        return NoteEntry(
            f"{filled}; the uniqueness constraint also needs {', '.join(entry.unmapped)} mapped, "
            f"so {entry.kind} will not plan until it is; identifiers were not changed"
        )
    return NoteEntry(f"{filled}; identifiers are now {', '.join(entry.identifiers)}")


def _check_filled(package: Mapping[str, Any]) -> tuple[RefusalEntry, ...]:
    """Run the connection-free registration checks again on the filled package (spec FR-020)."""
    try:
        parsed = parse_configuration_package(copy.deepcopy(dict(package)))
    except ConfigurationPackageParseError as exc:
        return (RefusalEntry("package", f"the package with the filled namespace does not register: {exc}"),)
    return tuple(
        RefusalEntry(finding.location or "/", f"{finding.code}: {finding.message}")
        for finding in collect_findings(parsed)
        if finding.severity == "error"
    )
