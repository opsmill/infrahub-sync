"""The plan problem pass, the dependency walk and the selection step (spec 009 research R7).

`find_problems` reads the load reports, the source store, the destination schema record and
the derived operations, and returns the plan report: the problems of the nine types, the
records the plan leaves out and the records it keeps without one optional relationship. It
changes no store and no operation.

`select_operations` is the one place the report is applied. It is pure: it removes the
operations of left-out records, of identities that two destination nodes hold, and the deletes
of records the load could not build, removes
from a kept record's operation the relationship to a left-out peer, and returns new
operations without any left-out peer placeholder.

No function here reads a connection setting or the text of an exception. Every reason is
rendered from one fixed template per problem type over typed fields (research R13).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from diffsync.exceptions import ObjectNotFound

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import (
    _identity_attributes_by_kind,
    _identity_from_keys,
    _resolve_references,
    _source_identities,
)
from infrahub_sync.plan.destination_ambiguity import destination_identity_ambiguities
from infrahub_sync.plan.destination_only_peer import destination_only_peers
from infrahub_sync.plan.errors import SourcePeerUnresolvedError, UnformableDestinationIdentityError
from infrahub_sync.plan.keying import (
    _component_field,
    destination_identity_collision_groups,
    destination_keys,
    merged_identity_groups,
    source_identity_collision_groups,
)
from infrahub_sync.plan.left_out_peer import LEFT_OUT_PEER_KEY, LeftOutPeers, is_left_out_peer
from infrahub_sync.plan.models import (
    REPORT_REASON_LIMIT,
    KeptRecord,
    LeftOutRecord,
    PlannedOperation,
    PlanProblem,
    PlanRecordReference,
    PlanReport,
    ProblemType,
    limited_report_value,
)
from infrahub_sync.plan.reference_candidates import reference_candidates
from infrahub_sync.runtime_schema.domain import DestinationSchemaSnapshot, normalize_destination_schema

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from infrahub_sync import LoadReportEntry, SyncConfig
    from infrahub_sync.plan.keying import IdentityGroup

logger = logging.getLogger(__name__)

# The longest text a source value takes inside a reason, so the reason stays readable.
_REASON_VALUE_LIMIT = 80

# A record is named by its kind, how it is named, and that name: its source ID, its unique ID in
# the source store, its identity, or the position of its load report entry. The middle element
# keeps a source ID apart from the other names, whatever text the source ID holds.
_RecordKey = tuple[str, str, str]


def _source_key(kind: str, source_id: str) -> _RecordKey:
    return kind, "source_id", source_id


def _store_key(kind: str, item: Any) -> _RecordKey:
    local_id = getattr(item, "local_id", None)
    return _source_key(kind, str(local_id)) if local_id is not None else (kind, "unique_id", item.get_unique_id())


def _report_key(index: int, entry: LoadReportEntry) -> _RecordKey:
    """The key of a record the load could not build, from its load report entry.

    Without a source ID, the record is named by its identity, so the several entries of one
    record name it once. A refused duplicate has the identity of the record the store kept, and
    a record without identity has nothing else to name it: each is named by its entry.
    """
    if entry.source_id is not None:
        return _source_key(entry.kind, entry.source_id)
    if entry.identity is not None and entry.reason != "duplicate_source_identity":
        return entry.kind, "identity", canonical_json_bytes(entry.identity).decode("utf-8")
    return entry.kind, "load_report_entry", str(index)


_REASON_TEMPLATES: Mapping[ProblemType, str] = {
    "dropdown_value_not_in_choices": (
        "{record}: the value {value} of {field} is not one of the choices of the Dropdown attribute: {allowed}."
    ),
    "duplicate_source_identity": (
        "{record} has the same source identity as {others}; the plan cannot tell which record is correct."
    ),
    "destination_identity_collision": (
        "{record} and {others} would form the same Infrahub human-friendly ID; the plan cannot tell which "
        "record is correct."
    ),
    "wrong_value_type": "{record}: the value {value} of {field} has the wrong type ({error_type}).",
    "required_peer_excluded_by_filter": (
        "{record}: the required relationship {field} refers to {peer}, which {filter} removed."
    ),
    "identities_merge_in_destination": (
        "{record} and {others} have different source identities that Infrahub would store as one node."
    ),
    "peer_not_found_in_source": "{record}: the relationship {field} refers to {peer}, which the source does not return.",
    "destination_identity_ambiguous": (
        "Infrahub holds {count} {kind} objects with the identity {identity}; the plan cannot tell which one {record} "
        "maps to, so it plans no operation for that identity."
    ),
    "transform_failed": (
        "{record}: {transform} of the {kind} mapping entry, which writes {field}, failed for this "
        "record, so the record is not loaded."
    ),
}


def _named(reference: PlanRecordReference) -> str:
    source_id = "with no source ID" if reference.source_id is None else f"source ID {reference.source_id}"
    return f"{reference.kind} record {source_id}"


def _shown(value: Any) -> str:
    text = canonical_json_bytes(limited_report_value(value)).decode("utf-8")
    return text if len(text) <= _REASON_VALUE_LIMIT else text[: _REASON_VALUE_LIMIT - 3] + "..."


def _problem(  # pylint: disable=too-many-arguments  # the fields of one problem entry
    problem_type: ProblemType,
    record: PlanRecordReference,
    *,
    field_name: str | None = None,
    value: Any = None,
    allowed: Sequence[str] | None = None,
    peer: PlanRecordReference | None = None,
    other_records: Sequence[PlanRecordReference] = (),
    error_type: str | None = None,
    destination_ids: Sequence[str] | None = None,
    identity: Mapping[str, Any] | None = None,
    config_entry: str | None = None,
) -> PlanProblem:
    """Build one problem, its reason rendered from the fixed template of its type."""
    others = ", ".join(_named(other) for other in other_records[:3])
    if len(other_records) > 3:
        others += f" and {len(other_records) - 3} other record(s)"
    reason = _REASON_TEMPLATES[problem_type].format(
        record=_named(record),
        value=_shown(value),
        field=field_name,
        allowed=", ".join(allowed or ()),
        peer=_named(peer) if peer is not None else "",
        others=others,
        error_type=error_type or "",
        count=len(destination_ids or ()),
        kind=record.kind,
        identity=_shown(dict(identity or {})),
        transform="a transform" if config_entry is None else f"the transform {config_entry}",
        filter=_filter_phrase(peer, config_entry),
    )
    return PlanProblem(
        problem_type=problem_type,
        record=record,
        field=field_name,
        value=value,
        allowed=list(allowed) if allowed is not None else None,
        peer=peer,
        other_records=list(other_records),
        destination_ids=list(destination_ids) if destination_ids is not None else None,
        config_entry=config_entry,
        reason=reason[:REPORT_REASON_LIMIT],
    )


def _filter_phrase(peer: PlanRecordReference | None, config_entry: str | None) -> str:
    """Name the filter that removed a peer: its position in the peer's mapping entry, never its value."""
    if config_entry is None or peer is None:
        return "a configured filter"
    return f"the filter {config_entry} of the {peer.kind} mapping entry"


# --- Left-out peer placeholders --------------------------------------------------------


def resolve_left_out_placeholders(value: Any, lookup: Callable[[str, str], Any]) -> Any:
    """Replace each placeholder pair of a record the load could not build by its identity.

    `lookup(peer_kind, source_id)` returns the record's identity, or `None` to keep the
    placeholder. A placeholder of a peer that is not in the source has no identity and stays.
    """
    if isinstance(value, Mapping):
        identity = value.get("identity")
        if set(value) == {"peer_kind", "identity"} and is_left_out_peer(identity):
            marker = identity[LEFT_OUT_PEER_KEY]
            source_id = marker.get("source_id")
            if marker.get("cause") == "not_loaded" and source_id is not None:
                resolved = lookup(value["peer_kind"], source_id)
                if resolved is not None:
                    return {"peer_kind": value["peer_kind"], "identity": resolved}
            return value
        return {key: resolve_left_out_placeholders(item, lookup) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [resolve_left_out_placeholders(item, lookup) for item in value]
    return value


# --- The source records the pass knows -------------------------------------------------


@dataclass
class _Record:
    """One source record: in the store, or recorded in the load report without being built."""

    key: _RecordKey
    source_id: str | None
    unique_id: str | None
    loaded: bool
    keys: Mapping[str, Any] | None
    raw_identity: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None

    @property
    def kind(self) -> str:
        return self.key[0]

    def reference(self) -> PlanRecordReference:
        return PlanRecordReference(kind=self.kind, identity=self.identity, source_id=self.source_id)


@dataclass
class _SourceView:
    """The records of the source store and of its load report, by key, unique ID and identity."""

    records: dict[_RecordKey, _Record] = field(default_factory=dict)
    by_unique_id: dict[tuple[str, str], _RecordKey] = field(default_factory=dict)
    by_identity: dict[tuple[str, bytes], _RecordKey] = field(default_factory=dict)
    # The record of each load report entry, and the record the store kept for a refused duplicate.
    by_entry: dict[int, _RecordKey] = field(default_factory=dict)
    kept_duplicate: dict[int, _RecordKey] = field(default_factory=dict)

    def add(self, record: _Record) -> _Record:
        existing = self.records.get(record.key)
        if existing is not None:
            return existing
        self.records[record.key] = record
        if record.unique_id is not None:
            self.by_unique_id.setdefault((record.kind, record.unique_id), record.key)
        return record

    def by_source_id(self, kind: str, source_id: str | None) -> _Record | None:
        return None if source_id is None else self.records.get(_source_key(kind, source_id))

    def lookup_not_loaded(self, peer_kind: str, source_id: str) -> dict[str, Any] | None:
        record = self.by_source_id(peer_kind, source_id)
        return None if record is None else record.identity

    def of_operation(self, operation: PlannedOperation) -> _Record | None:
        key = self.by_identity.get((operation.kind, self.operation_key(operation.identity)))
        return None if key is None else self.records[key]

    def operation_key(self, identity: Mapping[str, Any]) -> bytes:
        return canonical_json_bytes(resolve_left_out_placeholders(identity, self.lookup_not_loaded))


def _build_view(
    *, source: Any, load_report: Sequence[LoadReportEntry], config: SyncConfig | None, destination: Any
) -> _SourceView:
    view = _SourceView()
    for kind in getattr(source, "top_level", ()) or ():
        for item in source.get_all(kind):
            local_id = getattr(item, "local_id", None)
            view.add(
                _Record(
                    key=_store_key(kind, item),
                    source_id=None if local_id is None else str(local_id),
                    unique_id=item.get_unique_id(),
                    loaded=True,
                    keys=item.get_identifiers(),
                )
            )
    for index, entry in enumerate(load_report):
        model = getattr(source, entry.kind, None)
        if entry.loaded:
            loaded_key = _loaded_entry_key(entry, model=model, view=view)
            if loaded_key is not None:
                view.by_entry[index] = loaded_key
            continue
        unique_id = None
        if entry.identity is not None and model is not None and entry.reason != "duplicate_source_identity":
            unique_id = model.create_unique_id(**entry.identity)
        record = _Record(
            key=_report_key(index, entry),
            source_id=entry.source_id,
            unique_id=unique_id,
            loaded=False,
            keys=entry.identity,
        )
        view.by_entry[index] = view.add(record).key
        kept = _kept_duplicate(entry, model=model, view=view)
        if kept is not None:
            view.kept_duplicate[index] = kept
    # Identities as the operations show them, then with each not-loaded peer resolved.
    left_out_peers = LeftOutPeers.from_adapter(source)
    peers = destination_only_peers(destination) if destination is not None else None
    for record in view.records.values():
        record.raw_identity = _operation_identity(
            record, source=source, config=config, peers=peers, left_out=left_out_peers
        )
    for record in view.records.values():
        if record.raw_identity is None:
            continue
        record.identity = resolve_left_out_placeholders(record.raw_identity, _lookup_raw(view))
    for record in view.records.values():
        if record.identity is not None:
            view.by_identity.setdefault((record.kind, canonical_json_bytes(record.identity)), record.key)
    return view


def _loaded_entry_key(entry: LoadReportEntry, *, model: Any, view: _SourceView) -> _RecordKey | None:
    """The store record of an entry made for a record that loaded: by its source ID, else by its identity."""
    if entry.source_id is not None:
        return _source_key(entry.kind, entry.source_id)
    if entry.identity is None or model is None:
        return None
    return view.by_unique_id.get((entry.kind, model.create_unique_id(**entry.identity)))


def _kept_duplicate(entry: LoadReportEntry, *, model: Any, view: _SourceView) -> _RecordKey | None:
    """The record the store kept for a refused duplicate: by its source ID, else by its identity."""
    existing = entry.existing
    if entry.reason != "duplicate_source_identity" or existing is None:
        return None
    if existing.source_id is not None:
        record = view.by_source_id(entry.kind, existing.source_id)
        return None if record is None else record.key
    if existing.identity is None or model is None:
        return None
    return view.by_unique_id.get((entry.kind, model.create_unique_id(**existing.identity)))


def _lookup_raw(view: _SourceView) -> Callable[[str, str], Any]:
    """Resolve a not-loaded peer's identity recursively, from the identities as derived."""

    def lookup(peer_kind: str, source_id: str) -> Any:
        record = view.by_source_id(peer_kind, source_id)
        if record is None or record.raw_identity is None:
            return None
        return resolve_left_out_placeholders(record.raw_identity, lookup)

    return lookup


def _operation_identity(
    record: _Record, *, source: Any, config: SyncConfig | None, peers: Any, left_out: LeftOutPeers
) -> dict[str, Any] | None:
    """Derive a record's identity exactly as `operations_from_diff` derives an operation's."""
    if not record.keys:
        return None
    try:
        resolved = _resolve_references(
            values=record.keys,
            candidates=reference_candidates(config, record.kind),
            store=source.store,
            config=config,
            owning_kind=record.kind,
            peers=peers,
            left_out=left_out,
        )
        return _identity_from_keys(keys=record.keys, kind=record.kind, resolved=resolved)
    except (SourcePeerUnresolvedError, UnformableDestinationIdentityError):
        return None


# --- The destination schema facts the pass reads ---------------------------------------


def _snapshot(schema_record: DestinationSchemaSnapshot | Mapping[str, Any] | None) -> DestinationSchemaSnapshot | None:
    if schema_record is None or isinstance(schema_record, DestinationSchemaSnapshot):
        return schema_record
    return normalize_destination_schema(schema_record)


@dataclass(frozen=True)
class _Schema:
    """The destination schema record of the run, and the destination's own schema for identities."""

    snapshot: DestinationSchemaSnapshot | None
    destination_schema: Any

    def node(self, kind: str) -> Any:
        if self.snapshot is not None and kind in self.snapshot.kinds:
            return self.snapshot.kinds[kind]
        if self.destination_schema is not None and hasattr(self.destination_schema, "get"):
            return self.destination_schema.get(kind)
        return None

    def requires(self, kind: str, field_name: str, source: Any) -> bool:
        """Tell whether a reference leaves its record out when its peer is left out (research R7).

        True for one of the mapping's identifiers, since the record cannot form its identity
        without that peer, for a relationship the destination marks `optional: false` and for a
        component of the destination's human-friendly ID. Otherwise the source model decides.
        """
        model = getattr(source, kind, None)
        if field_name in (getattr(model, "_identifiers", None) or ()):
            return True
        node = self.node(kind)
        if field_name in {_component_field(component) for component in getattr(node, "human_friendly_id", None) or ()}:
            return True
        for relationship in getattr(node, "relationships", None) or ():
            if getattr(relationship, "name", None) == field_name:
                return not getattr(relationship, "optional", True)
        info = None if model is None else model.model_fields.get(field_name)
        return info is not None and info.is_required()


# --- Problems ----------------------------------------------------------------------------


@dataclass
class _Findings:
    """What the checks found, by record."""

    problems: list[PlanProblem] = field(default_factory=list)
    # Problem types that leave the record out, and every problem type of the record.
    hard: dict[_RecordKey, set[ProblemType]] = field(default_factory=dict)
    every: dict[_RecordKey, set[ProblemType]] = field(default_factory=dict)
    # Optional references to a peer that is not in the source: the record is kept.
    not_found_optional: list[tuple[_RecordKey, LoadReportEntry]] = field(default_factory=list)
    # Records not loaded because a peer was not loaded: left out as dependents.
    not_loaded_peer: list[tuple[_RecordKey, LoadReportEntry]] = field(default_factory=list)
    # Optional references to a peer that was not loaded and has no identity: the record is kept.
    not_loaded_optional: list[tuple[_RecordKey, LoadReportEntry]] = field(default_factory=list)

    def add(self, problem: PlanProblem, keys: Iterable[_RecordKey], *, leaves_out: bool = True) -> None:
        self.problems.append(problem)
        for key in keys:
            self.every.setdefault(key, set()).add(problem.problem_type)
            if leaves_out:
                self.hard.setdefault(key, set()).add(problem.problem_type)


def _peer_reference(entry: LoadReportEntry, view: _SourceView) -> PlanRecordReference | None:
    if entry.peer is None:
        return None
    identity = None if entry.peer.source_id is None else view.lookup_not_loaded(entry.peer.kind, entry.peer.source_id)
    return PlanRecordReference(kind=entry.peer.kind, identity=identity, source_id=entry.peer.source_id)


def _load_report_problems(  # pylint: disable=too-many-arguments  # the pass's inputs, keyword-only
    load_report: Sequence[LoadReportEntry], *, view: _SourceView, schema: _Schema, source: Any, findings: _Findings
) -> None:
    for index, entry in enumerate(load_report):
        record_key = view.by_entry.get(index)
        record = None if record_key is None else view.records.get(record_key)
        if record is None:
            continue
        reference = record.reference()
        peer = _peer_reference(entry, view)
        # A loaded record whose reference was resolved as optional, although the record needs
        # that peer (an identifier, for example), is left out like a record that was not built.
        optional = entry.loaded and not (entry.field is not None and schema.requires(entry.kind, entry.field, source))
        if entry.reason == "wrong_value_type":
            problem = _problem(
                "wrong_value_type", reference, field_name=entry.field, value=entry.value, error_type=entry.error_type
            )
            findings.add(problem, [record.key])
        elif entry.reason == "duplicate_source_identity":
            kept_key = view.kept_duplicate.get(index)
            existing = None if kept_key is None else view.records[kept_key]
            others = [existing.reference()] if existing is not None else []
            problem = _problem("duplicate_source_identity", reference, other_records=others)
            findings.add(problem, [record.key, *([existing.key] if existing is not None else [])])
        elif entry.reason == "required_peer_excluded_by_filter":
            problem = _problem(
                entry.reason, reference, field_name=entry.field, peer=peer, config_entry=entry.config_entry
            )
            findings.add(problem, [record.key])
        elif entry.reason == "transform_failed":
            problem = _problem(entry.reason, reference, field_name=entry.field, config_entry=entry.config_entry)
            findings.add(problem, [record.key])
        elif entry.reason == "peer_not_found_in_source":
            problem = _problem(entry.reason, reference, field_name=entry.field, peer=peer)
            findings.add(problem, [record.key], leaves_out=not optional)
            if optional:
                findings.not_found_optional.append((record.key, entry))
        elif entry.reason == "peer_not_loaded":
            (findings.not_loaded_optional if optional else findings.not_loaded_peer).append((record.key, entry))


def _dropdown_problems(*, source: Any, view: _SourceView, schema: _Schema, findings: _Findings) -> None:
    """Check every mapped Dropdown value of the source store against its choices (research R6)."""
    if schema.snapshot is None:
        logger.debug("Plan: no destination schema record, so the Dropdown check is skipped")
        return
    for kind in getattr(source, "top_level", ()) or ():
        node = schema.snapshot.kinds.get(kind)
        model = getattr(source, kind, None)
        if node is None or model is None:
            continue
        dropdowns = [
            attribute
            for attribute in node.attributes
            if attribute.choices is not None and attribute.name in model.model_fields
        ]
        if not dropdowns:
            continue
        for item in source.get_all(kind):
            record = view.records.get(_store_key(kind, item))
            if record is None:
                continue
            for attribute in dropdowns:
                value = getattr(item, attribute.name, None)
                if value is None and attribute.optional:
                    continue
                if isinstance(value, str) and value in (attribute.choices or ()):
                    continue
                problem = _problem(
                    "dropdown_value_not_in_choices",
                    record.reference(),
                    field_name=attribute.name,
                    value=value,
                    allowed=attribute.choices,
                )
                findings.add(problem, [record.key])


def _closest_key(keys: Sequence[tuple[str, ...]], supplied: set[str]) -> tuple[str, ...]:
    """The declared key closest to the plan's identity, as the merge warning chooses it."""
    return min(keys, key=lambda key: (-len(supplied & set(key)), key))


def _identity_groups(
    *, operations: Sequence[PlannedOperation], source: Any, config: SyncConfig | None, destination: Any
) -> list[tuple[ProblemType, IdentityGroup]]:
    """Return the identity groups of research R8, each group of records once, in check order."""
    schema = getattr(destination, "schema", None) if destination is not None else None
    if not schema or not hasattr(schema, "get"):
        return []
    found: list[tuple[ProblemType, IdentityGroup]] = [
        ("destination_identity_collision", group)
        for group in destination_identity_collision_groups(schema=schema, operations=operations)
    ]
    peers = destination_only_peers(destination)
    by_kind = _identity_attributes_by_kind(operations)
    for kind in sorted(by_kind):
        node = schema.get(kind)
        if node is None:
            continue
        of_kind = [operation for operation in operations if operation.kind == kind]
        creates = [operation for operation in of_kind if operation.action == "create"]
        if creates and getattr(node, "human_friendly_id", None) and source is not None:
            identities = _source_identities(kind=kind, source_adapter=source, config=config, peers=peers)
            found.extend(
                ("identities_merge_in_destination", group)
                for group in source_identity_collision_groups(
                    kind=kind, node=node, creates=creates, identities=identities
                )
            )
        keys = destination_keys(node)
        supplied = by_kind[kind]
        if keys and not any(supplied <= set(key) for key in keys):
            found.extend(
                ("identities_merge_in_destination", group)
                for group in merged_identity_groups(of_kind, kind=kind, key=_closest_key(keys, supplied))
            )
    unique: list[tuple[ProblemType, IdentityGroup]] = []
    seen: set[frozenset[tuple[str, bytes]]] = set()
    for problem_type, group in found:
        member_key = group.member_key()
        if member_key in seen:
            continue
        seen.add(member_key)
        unique.append((problem_type, group))
    return unique


def _identity_group_problems(
    groups: Sequence[tuple[ProblemType, IdentityGroup]], view: _SourceView, findings: _Findings
) -> None:
    for problem_type, group in groups:
        members: dict[_RecordKey, _Record] = {}
        for operation in group.operations:
            record = view.of_operation(operation)
            if record is not None:
                members.setdefault(record.key, record)
        for unique_id, _ in group.source_records:
            key = view.by_unique_id.get((group.kind, unique_id))
            if key is not None:
                members.setdefault(key, view.records[key])
        if not members:
            continue
        ordered = sorted(members.values(), key=lambda record: _reference_order(record.reference()))
        problem = _problem(
            problem_type, ordered[0].reference(), other_records=[record.reference() for record in ordered[1:]]
        )
        findings.add(problem, members)


def _destination_ambiguity_problems(
    *, destination: Any, view: _SourceView, config: SyncConfig | None, findings: _Findings
) -> None:
    """List each identity that two or more destination nodes hold (`destination_identity_ambiguous`).

    The source record with that identity is left out. Without one, the problem names the
    identity as the destination forms it, so the selection step plans no delete for it either.
    """
    for ambiguity in destination_identity_ambiguities(destination):
        key = view.by_unique_id.get((ambiguity.kind, ambiguity.unique_id))
        if key is not None:
            reference, keys = view.records[key].reference(), [key]
        else:
            identity = _destination_identity(destination, ambiguity.kind, ambiguity.identifiers, config)
            reference, keys = PlanRecordReference(kind=ambiguity.kind, identity=identity), []
        problem = _problem(
            "destination_identity_ambiguous",
            reference,
            destination_ids=ambiguity.node_ids,
            identity=ambiguity.identifiers,
        )
        findings.add(problem, keys)


def _destination_identity(
    destination: Any, kind: str, identifiers: Mapping[str, Any], config: SyncConfig | None
) -> dict[str, Any] | None:
    """Form an identity from the destination store, as `derive_deletes` forms a delete's."""
    try:
        resolved = _resolve_references(
            values=identifiers,
            candidates=reference_candidates(config, kind),
            store=destination.store,
            config=config,
            owning_kind=kind,
        )
        return _identity_from_keys(keys=identifiers, kind=kind, resolved=resolved)
    except (SourcePeerUnresolvedError, UnformableDestinationIdentityError):
        return None


def _reference_order(reference: PlanRecordReference) -> tuple[str, bytes, str]:
    identity = b"" if reference.identity is None else canonical_json_bytes(reference.identity)
    return reference.kind, identity, reference.source_id or ""


# --- The dependency walk -------------------------------------------------------------------


def _referrers(
    *, source: Any, view: _SourceView, config: SyncConfig | None
) -> dict[_RecordKey, list[tuple[_RecordKey, str]]]:
    """Map each record to the store records that refer to it, with the reference field."""
    referrers: dict[_RecordKey, list[tuple[_RecordKey, str]]] = {}
    for kind in getattr(source, "top_level", ()) or ():
        candidates = reference_candidates(config, kind)
        if not candidates:
            continue
        for item in source.get_all(kind):
            referrer = _store_key(kind, item)
            for field_name, peer_kinds in sorted(candidates.items()):
                value = getattr(item, field_name, None)
                for unique_id in value if isinstance(value, (list, tuple)) else [value]:
                    target = _reference_target(view, peer_kinds, unique_id)
                    if target is not None:
                        referrers.setdefault(target, []).append((referrer, field_name))
    return referrers


def _reference_target(view: _SourceView, peer_kinds: Sequence[str], unique_id: object) -> _RecordKey | None:
    """The record a reference value names, among the candidate peer kinds."""
    if not isinstance(unique_id, str):
        return None
    for peer_kind in peer_kinds:
        target = view.by_unique_id.get((peer_kind, unique_id))
        if target is not None:
            return target
    return None


def _keep_load_report_referrers(
    findings: _Findings,
    view: _SourceView,
    left: Mapping[_RecordKey, LeftOutRecord],
    kept: dict[tuple[_RecordKey, str, _RecordKey | str], KeptRecord],
) -> None:
    """Keep the records the load built without an optional link to a missing or left-out peer (FR-005)."""
    for key, entry in findings.not_loaded_optional:
        peer = _peer_reference(entry, view)
        if key in left or peer is None or entry.field is None:
            continue
        target = view.by_source_id(peer.kind, peer.source_id)
        kept.setdefault(
            (key, entry.field, target.key if target is not None else f"{peer.kind}:{peer.source_id}"),
            KeptRecord(
                record=view.records[key].reference(),
                cause="peer_left_out",
                relationship=entry.field,
                left_out_peer=target.reference() if target is not None else peer,
            ),
        )
    for key, entry in findings.not_found_optional:
        peer = _peer_reference(entry, view)
        if key in left or peer is None or entry.field is None:
            continue
        kept.setdefault(
            (key, entry.field, f"{peer.kind}:{peer.source_id}"),
            KeptRecord(
                record=view.records[key].reference(),
                cause="peer_not_found_in_source",
                relationship=entry.field,
                left_out_peer=peer,
            ),
        )


def _walk(  # pylint: disable=too-many-arguments  # the walk's inputs, all keyword-only
    *, source: Any, view: _SourceView, schema: _Schema, config: SyncConfig | None, findings: _Findings
) -> tuple[dict[_RecordKey, LeftOutRecord], list[KeptRecord]]:
    """Leave out the records with problems and their dependents; keep optional referrers (research R7)."""
    left: dict[_RecordKey, LeftOutRecord] = {}
    for key in sorted(findings.hard):
        record = view.records[key]
        left[key] = LeftOutRecord(
            record=record.reference(),
            cause="has_problem",
            problem_types=sorted(findings.every[key]),
            loaded=record.loaded,
        )
    for key, entry in findings.not_loaded_peer:
        if key in left:
            continue
        peer = _peer_reference(entry, view)
        left[key] = LeftOutRecord(
            record=view.records[key].reference(),
            cause="depends_on_left_out",
            depends_on=peer,
            relationship=entry.field,
            loaded=view.records[key].loaded,
        )
    referrers = _referrers(source=source, view=view, config=config)
    optional: list[tuple[_RecordKey, str, _RecordKey]] = []
    pending = sorted(left)
    while pending:
        target = pending.pop(0)
        for referrer, field_name in referrers.get(target, ()):
            if referrer in left:
                continue
            if schema.requires(referrer[0], field_name, source):
                left[referrer] = LeftOutRecord(
                    record=view.records[referrer].reference(),
                    cause="depends_on_left_out",
                    depends_on=view.records[target].reference(),
                    relationship=field_name,
                    loaded=True,
                )
                pending.append(referrer)
            else:
                optional.append((referrer, field_name, target))
    kept: dict[tuple[_RecordKey, str, _RecordKey | str], KeptRecord] = {}
    for referrer, field_name, target in optional:
        if referrer not in left:
            kept.setdefault(
                (referrer, field_name, target),
                KeptRecord(
                    record=view.records[referrer].reference(),
                    cause="peer_left_out",
                    relationship=field_name,
                    left_out_peer=view.records[target].reference(),
                ),
            )
    _keep_load_report_referrers(findings, view, left, kept)
    return left, list(kept.values())


def _mark_relationship_only_updates(
    kept: Sequence[KeptRecord],
    *,
    operations: Sequence[PlannedOperation],
    view: _SourceView,
    source: Any,
    destination: Any,
) -> list[KeptRecord]:
    """Mark the kept records whose update changes nothing but the relationships written without a peer."""
    if destination is None:
        return list(kept)
    fields_by_key: dict[_RecordKey, set[str]] = {}
    for item in kept:
        record = view.by_identity.get((item.record.kind, canonical_json_bytes(item.record.identity or {})))
        if record is not None:
            fields_by_key.setdefault(record, set()).add(item.relationship)
    only: set[_RecordKey] = set()
    for operation in operations:
        if operation.action != "update":
            continue
        record = view.of_operation(operation)
        if record is None or record.key not in fields_by_key or record.unique_id is None:
            continue
        try:
            source_item = source.store.get(model=record.kind, identifier=record.unique_id)
            destination_item = destination.store.get(model=record.kind, identifier=record.unique_id)
        except ObjectNotFound:
            continue
        dropped = fields_by_key[record.key]
        source_attrs = {name: value for name, value in source_item.get_attrs().items() if name not in dropped}
        destination_attrs = {name: value for name, value in destination_item.get_attrs().items() if name not in dropped}
        if source_attrs == destination_attrs:
            only.add(record.key)
    marked: list[KeptRecord] = []
    for item in kept:
        record = view.by_identity.get((item.record.kind, canonical_json_bytes(item.record.identity or {})))
        marked.append(item.model_copy(update={"relationship_only_update": True}) if record in only else item)
    return marked


def find_problems(  # pylint: disable=too-many-arguments  # the pass's inputs (research R2 step 4)
    source_load_report: Sequence[LoadReportEntry],
    destination_load_report: Sequence[LoadReportEntry],
    source: Any,
    schema_record: DestinationSchemaSnapshot | Mapping[str, Any] | None,
    operations: Sequence[PlannedOperation],
    *,
    config: SyncConfig | None,
    destination: Any = None,
) -> PlanReport:
    """Find the problems of the plan and the records it leaves out and keeps (research R7).

    `source` is the source adapter holding the source store. `schema_record` is the
    destination schema record the run read; without one the Dropdown check is skipped and the
    source models decide which references are required. `destination` is the destination
    adapter: its schema drives the identity checks of research R8 and its store tells an
    update that changes only a kept relationship. Nothing here writes to a store.

    The destination side records nothing in its load report (research R3), so
    `destination_load_report` holds no source problem; it is read to log what it holds.
    """
    if destination_load_report:
        logger.debug("Plan: the destination load report holds %d entry(ies)", len(destination_load_report))
    view = _build_view(source=source, load_report=source_load_report, config=config, destination=destination)
    schema = _Schema(snapshot=_snapshot(schema_record), destination_schema=getattr(destination, "schema", None))
    findings = _Findings()
    _load_report_problems(source_load_report, view=view, schema=schema, source=source, findings=findings)
    _dropdown_problems(source=source, view=view, schema=schema, findings=findings)
    groups = _identity_groups(operations=operations, source=source, config=config, destination=destination)
    _identity_group_problems(groups, view, findings)
    _destination_ambiguity_problems(destination=destination, view=view, config=config, findings=findings)
    left, kept = _walk(source=source, view=view, schema=schema, config=config, findings=findings)
    kept = _mark_relationship_only_updates(
        kept, operations=operations, view=view, source=source, destination=destination
    )
    return PlanReport(problems=findings.problems, left_out=list(left.values()), kept=kept).ordered()


# --- The selection step --------------------------------------------------------------------


def select_operations(operations: Sequence[PlannedOperation], report: PlanReport) -> list[PlannedOperation]:
    """Apply the plan report to the derived operations (research R7 point 3).

    Removes every operation of a left-out record, including a delete, so a left-out record
    that exists in Infrahub stays as it is, and every operation of an identity that two or more
    Infrahub nodes hold (`destination_identity_ambiguous`), whether or not the source has it. When a record the load could not build has no
    known identity, it removes every delete of its kind, since that record may be any node of
    the kind (FR-008). From every other operation it removes the peers
    that are left out or not in the source: a create keeps the other peers of a cardinality-
    many relationship and loses a cardinality-one relationship; an update loses the whole
    relationship, so a replace-set write cannot remove the left-out peer from Infrahub. An
    update whose only change was such a relationship is removed. Neither input is changed.
    """
    identities = {
        (item.record.kind, item.record.source_id): item.record.identity
        for item in report.left_out
        if item.record.source_id is not None and item.record.identity is not None
    }

    def lookup(peer_kind: str, source_id: str) -> Any:
        return identities.get((peer_kind, source_id))

    def key(kind: str, identity: Mapping[str, Any]) -> tuple[str, bytes]:
        return kind, canonical_json_bytes(resolve_left_out_placeholders(identity, lookup))

    left = {key(item.record.kind, item.record.identity) for item in report.left_out if item.record.identity is not None}
    ambiguous = [
        problem.record for problem in report.problems if problem.problem_type == "destination_identity_ambiguous"
    ]
    left |= {key(record.kind, record.identity) for record in ambiguous if record.identity is not None}
    kept: dict[tuple[str, bytes], list[KeptRecord]] = {}
    for item in report.kept:
        if item.record.identity is not None:
            kept.setdefault(key(item.record.kind, item.record.identity), []).append(item)

    def is_left_out(peer_kind: str, peer: Mapping[str, Any]) -> bool:
        return is_left_out_peer(peer) or key(peer_kind, peer) in left

    # A record the load could not build and whose identity is not known may be any Infrahub
    # node of its kind, so the plan deletes none of that kind (FR-008).
    no_deletes = {item.record.kind for item in report.left_out if item.record.identity is None and not item.loaded}
    no_deletes |= {record.kind for record in ambiguous if record.identity is None}
    withheld: dict[str, int] = {}
    selected: list[PlannedOperation] = []
    for operation in operations:
        operation_key = key(operation.kind, operation.identity)
        if operation_key in left:
            continue
        if operation.action == "delete" and operation.kind in no_deletes:
            withheld[operation.kind] = withheld.get(operation.kind, 0) + 1
            continue
        kept_here = kept.get(operation_key, [])
        if operation.action == "update" and kept_here and all(item.relationship_only_update for item in kept_here):
            continue
        dropped = {item.relationship for item in kept_here}
        references = []
        changed = False
        for reference in operation.relationships or ():
            remaining = [peer for peer in reference.peers if not is_left_out(reference.peer_kind, peer)]
            if len(remaining) == len(reference.peers) and reference.field not in dropped:
                references.append(reference)
                continue
            changed = True
            if operation.action == "create" and reference.cardinality == "many":
                references.append(reference.model_copy(update={"peers": remaining}))
        if changed:
            operation = operation.model_copy(update={"relationships": references or None})  # noqa: PLW2901
        selected.append(operation)
    for kind, count in sorted(withheld.items()):
        logger.warning(
            "Plan: %d delete(s) of %s not planned, because a %s record left out of the plan has no known identity",
            count,
            kind,
            kind,
        )
    return selected
