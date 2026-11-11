"""The plan problem pass, the dependency walk and the selection step (spec 019 research R3).

`find_problems` reads the source adapter's load report, the source store, the destination
schema and the derived operations, and returns the plan report: the problems of the nine
types, the records the plan leaves out and the records it keeps without one optional
relationship. It changes no store and no operation.

`select_operations` is the one place the report is applied. It is pure: it returns new
operations without the operations of left-out records, without any operation of an identity
that two or more Infrahub nodes hold, without the deletes of a kind the plan cannot compute
deletes for (FR-008), and without any reference to a left-out peer or to a peer the source
does not return.

No function here reads a connection setting or the text of an exception. Every reason is
rendered from one fixed template per problem type over typed fields (research R13).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from diffsync.exceptions import ObjectNotFound

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import _identity_from_keys, _resolve_references, unique_identity_groups
from infrahub_sync.plan.destination_only_peer import destination_only_peers
from infrahub_sync.plan.errors import SourcePeerUnresolvedError, UnformableDestinationIdentityError
from infrahub_sync.plan.keying import _component_field
from infrahub_sync.plan.models import (
    REPORT_REASON_LIMIT,
    KeptRecord,
    LeftOutRecord,
    PlannedOperation,
    PlanProblem,
    PlanReport,
    ProblemType,
    SourceRecordReference,
    is_not_in_source_peer,
    limited_report_value,
)
from infrahub_sync.plan.reference_candidates import reference_candidates

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from infrahub_sync import SyncConfig
    from infrahub_sync.load_report import LoadReportEntry
    from infrahub_sync.plan.destination_only_peer import DestinationOnlyPeers
    from infrahub_sync.plan.keying import IdentityGroup

logger = logging.getLogger(__name__)

# The longest text a source value takes inside a reason, so the reason stays readable.
_REASON_VALUE_LIMIT = 80

# A record is named by its kind, how it is named, and that name: its source ID, its unique ID
# in the source store, or the position of its load report entry. The middle element keeps a
# source ID apart from the other names, whatever text the source ID holds.
_RecordKey = tuple[str, str, str]


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
    "wrong_value_type": "{record}: the value {value} of {field} does not have the type the attribute needs.",
    "required_peer_excluded_by_filter": (
        "{record}: the required relationship {field} refers to {peer}, which {filter} removed."
    ),
    "identities_merge_in_destination": (
        "{record} and {others} have different source identities that Infrahub would store as one node."
    ),
    "peer_not_found_in_source": "{record}: the relationship {field} refers to {peer}, which the source does not return.",
    "destination_identity_ambiguous": (
        "Infrahub holds {count} {kind} nodes with the identity {identity}; the plan cannot tell which one {record} "
        "maps to, so it plans no operation for that identity."
    ),
    "transform_failed": (
        "{record}: {transform} of the {kind} mapping entry, which writes {field}, failed for this record, "
        "so the record is not loaded."
    ),
}


def _named(reference: SourceRecordReference) -> str:
    source_id = "with no source ID" if reference.source_id is None else f"source ID {reference.source_id}"
    return f"{reference.kind} record {source_id}"


def _shown(value: Any) -> str:
    text = canonical_json_bytes(limited_report_value(value)).decode("utf-8")
    return text if len(text) <= _REASON_VALUE_LIMIT else text[: _REASON_VALUE_LIMIT - 3] + "..."


def _filter_phrase(peer: SourceRecordReference | None, config_entry: str | None) -> str:
    """Name the filter that removed a peer: its position in the peer's mapping entry, never its expression."""
    if config_entry is None or peer is None:
        return "a configured filter"
    return f"the filter {config_entry} of the {peer.kind} mapping entry"


def _problem(  # pylint: disable=too-many-arguments  # the fields of one problem entry
    problem_type: ProblemType,
    record: SourceRecordReference,
    *,
    field_name: str | None = None,
    value: Any = None,
    allowed: Sequence[str] | None = None,
    peer: SourceRecordReference | None = None,
    other_records: Sequence[SourceRecordReference] = (),
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


# --- The source records the pass knows -------------------------------------------------


@dataclass
class _Record:
    """One source record: in the store, or recorded in the load report without being built."""

    key: _RecordKey
    source_id: str | None
    unique_id: str | None
    loaded: bool
    keys: Mapping[str, Any] | None
    identity: dict[str, Any] | None = None

    @property
    def kind(self) -> str:
        return self.key[0]

    def reference(self) -> SourceRecordReference:
        return SourceRecordReference(kind=self.kind, identity=self.identity, source_id=self.source_id)


def _source_key(kind: str, source_id: str) -> _RecordKey:
    return kind, "source_id", source_id


def _identity_bytes(identity: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(identity))


@dataclass
class _SourceView:
    """The records of the source store and of its load report, by key, unique ID and identity."""

    records: dict[_RecordKey, _Record] = field(default_factory=dict)
    by_unique_id: dict[tuple[str, str], _RecordKey] = field(default_factory=dict)
    by_identity: dict[tuple[str, bytes], _RecordKey] = field(default_factory=dict)
    # The record of each load report entry.
    by_entry: dict[int, _RecordKey] = field(default_factory=dict)

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

    def of_operation(self, operation: PlannedOperation) -> _Record | None:
        key = self.by_identity.get((operation.kind, _identity_bytes(operation.identity)))
        return None if key is None else self.records[key]

    def reference_of(self, reference: SourceRecordReference | None) -> SourceRecordReference | None:
        """A load report's reference to a record, with the identity the pass computed for it when known."""
        if reference is None:
            return None
        record = self.by_source_id(reference.kind, reference.source_id)
        return reference if record is None else record.reference()


def _build_view(
    *, source: Any, load_report: Sequence[LoadReportEntry], config: SyncConfig | None, peers: Any
) -> _SourceView:
    view = _SourceView()
    for kind in getattr(source, "top_level", ()) or ():
        for item in source.get_all(kind):
            local_id = getattr(item, "local_id", None)
            unique_id = item.get_unique_id()
            view.add(
                _Record(
                    key=_source_key(kind, str(local_id)) if local_id is not None else (kind, "unique_id", unique_id),
                    source_id=None if local_id is None else str(local_id),
                    unique_id=unique_id,
                    loaded=True,
                    keys=item.get_identifiers(),
                )
            )
    for index, entry in enumerate(load_report):
        kind, source_id = entry.record.kind, entry.record.source_id
        if entry.loaded:
            # An entry about a reference of a record the store holds.
            record = view.by_source_id(kind, source_id)
            if record is not None:
                view.by_entry[index] = record.key
            continue
        key = _source_key(kind, source_id) if source_id is not None else (kind, "load_report_entry", str(index))
        view.by_entry[index] = view.add(
            _Record(key=key, source_id=source_id, unique_id=None, loaded=False, keys=entry.record.identity)
        ).key
    for record in view.records.values():
        record.identity = _operation_identity(record, source=source, config=config, peers=peers)
    for record in view.records.values():
        if record.identity is not None:
            # A record the store holds comes first, so an identity names it before a refused duplicate.
            view.by_identity.setdefault((record.kind, _identity_bytes(record.identity)), record.key)
    return view


def _operation_identity(
    record: _Record, *, source: Any, config: SyncConfig | None, peers: Any
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
        )
        return _identity_from_keys(keys=record.keys, kind=record.kind, resolved=resolved)
    except (SourcePeerUnresolvedError, UnformableDestinationIdentityError):
        return None


def _requires(kind: str, field_name: str, *, source: Any, destination_schema: Any) -> bool:
    """Tell whether a reference leaves its record out when its peer is left out (FR-005).

    True for one of the mapping's identifiers, since the record cannot form its identity
    without that peer, for a component of the destination's human-friendly ID and for a
    relationship the destination marks `optional: false`. Otherwise the source model decides.
    """
    model = getattr(source, kind, None)
    if field_name in (getattr(model, "_identifiers", None) or ()):
        return True
    node = destination_schema.get(kind) if hasattr(destination_schema, "get") else None
    if field_name in {_component_field(component) for component in getattr(node, "human_friendly_id", None) or ()}:
        return True
    for relationship in getattr(node, "relationships", None) or ():
        if getattr(relationship, "name", None) == field_name:
            return not getattr(relationship, "optional", True)
    info = None if model is None else getattr(model, "model_fields", {}).get(field_name)
    return info is not None and info.is_required()


# --- Problems ----------------------------------------------------------------------------


@dataclass
class _Findings:
    """What the checks found, by record."""

    problems: list[PlanProblem] = field(default_factory=list)
    # Problem types that leave the record out, and every problem type of the record.
    hard: dict[_RecordKey, set[ProblemType]] = field(default_factory=dict)
    every: dict[_RecordKey, set[ProblemType]] = field(default_factory=dict)
    # Optional references to a peer the source does not return: the record is kept.
    not_found_optional: list[tuple[_RecordKey, LoadReportEntry]] = field(default_factory=list)
    # Records not built because a required peer was not built: left out as dependents.
    not_loaded_peer: list[tuple[_RecordKey, LoadReportEntry]] = field(default_factory=list)
    # Optional references to a peer that was not built: the record is kept.
    not_loaded_optional: list[tuple[_RecordKey, LoadReportEntry]] = field(default_factory=list)
    # Identities that two or more Infrahub nodes hold, by kind (`destination_identity_ambiguous`).
    ambiguous: set[tuple[str, bytes]] = field(default_factory=set)

    def add(self, problem: PlanProblem, keys: Iterable[_RecordKey], *, leaves_out: bool = True) -> None:
        self.problems.append(problem)
        for key in keys:
            self.every.setdefault(key, set()).add(problem.problem_type)
            if leaves_out:
                self.hard.setdefault(key, set()).add(problem.problem_type)


def _reference_order(reference: SourceRecordReference) -> tuple[str, bytes, str]:
    identity = b"" if reference.identity is None else _identity_bytes(reference.identity)
    return reference.kind, identity, reference.source_id or ""


def _group_problem(problem_type: ProblemType, members: Iterable[_Record], findings: _Findings) -> None:
    """One problem for a group of records the plan cannot tell apart, naming the first record in a fixed order."""
    ordered = sorted({member.key: member for member in members}.values(), key=lambda m: _reference_order(m.reference()))
    if not ordered:
        return
    problem = _problem(problem_type, ordered[0].reference(), other_records=[m.reference() for m in ordered[1:]])
    findings.add(problem, [member.key for member in ordered])


def _load_report_problems(
    load_report: Sequence[LoadReportEntry],
    *,
    view: _SourceView,
    source: Any,
    destination_schema: Any,
    findings: _Findings,
) -> None:
    duplicates: dict[tuple[str, bytes], dict[_RecordKey, _Record]] = {}
    for index, entry in enumerate(load_report):
        key = view.by_entry.get(index)
        if key is None:
            continue
        record = view.records[key]
        reference = record.reference()
        peer = view.reference_of(entry.peer)
        # A loaded record whose reference was resolved as optional, although the record needs
        # that peer, is left out like a record that was not built.
        optional = entry.loaded and not (
            entry.field is not None
            and _requires(entry.record.kind, entry.field, source=source, destination_schema=destination_schema)
        )
        if entry.reason == "wrong_value_type":
            findings.add(_problem("wrong_value_type", reference, field_name=entry.field, value=entry.value), [key])
        elif entry.reason == "duplicate_source_identity":
            existing = view.reference_of(entry.existing)
            kept = None if existing is None else view.by_source_id(existing.kind, existing.source_id)
            if kept is None and record.identity is not None:
                # The kept record has no source ID: find it by identity. The store's records come
                # first in `by_identity`, so the identity names the kept record, not this one.
                kept_key = view.by_identity.get((record.kind, _identity_bytes(record.identity)))
                kept = None if kept_key is None or kept_key == record.key else view.records[kept_key]
            group_identity = entry.record.identity or {}
            members = duplicates.setdefault((record.kind, _identity_bytes(group_identity)), {})
            members[record.key] = record
            if kept is not None:
                members[kept.key] = kept
        elif entry.reason == "required_peer_excluded_by_filter":
            problem = _problem(
                "required_peer_excluded_by_filter",
                reference,
                field_name=entry.field,
                peer=peer,
                config_entry=entry.config_entry,
            )
            findings.add(problem, [key])
        elif entry.reason == "transform_failed":
            problem = _problem("transform_failed", reference, field_name=entry.field, config_entry=entry.config_entry)
            findings.add(problem, [key])
        elif entry.reason == "peer_not_found_in_source":
            missing = None if entry.peer is None else entry.peer.model_copy(update={"identity": None})
            problem = _problem("peer_not_found_in_source", reference, field_name=entry.field, peer=missing)
            findings.add(problem, [key], leaves_out=not optional)
            if optional:
                findings.not_found_optional.append((key, entry))
        elif entry.reason == "peer_not_loaded":
            (findings.not_loaded_optional if optional else findings.not_loaded_peer).append((key, entry))
    for members in duplicates.values():
        _group_problem("duplicate_source_identity", members.values(), findings)


def _dropdown_problems(
    *,
    source: Any,
    view: _SourceView,
    dropdown_choices: Mapping[str, Mapping[str, Sequence[str]]],
    findings: _Findings,
) -> None:
    """Check every mapped Dropdown value of the source store against its choices (research R4)."""
    for kind in getattr(source, "top_level", ()) or ():
        choices_by_field = dropdown_choices.get(kind)
        if not choices_by_field:
            continue
        for item in source.get_all(kind):
            local_id = getattr(item, "local_id", None)
            key = (
                _source_key(kind, str(local_id)) if local_id is not None else (kind, "unique_id", item.get_unique_id())
            )
            record = view.records.get(key)
            if record is None:
                continue
            for field_name, choices in sorted(choices_by_field.items()):
                value = getattr(item, field_name, None)
                if value is None or (isinstance(value, str) and value in choices):
                    continue
                problem = _problem(
                    "dropdown_value_not_in_choices",
                    record.reference(),
                    field_name=field_name,
                    value=value,
                    allowed=sorted(choices),
                )
                findings.add(problem, [record.key])


def _identity_group_problems(groups: Sequence[IdentityGroup], view: _SourceView, findings: _Findings) -> None:
    """One problem per group of records the destination cannot tell apart (research R6)."""
    seen: set[frozenset[_RecordKey]] = set()
    for group in unique_identity_groups(groups):
        members: dict[_RecordKey, _Record] = {}
        for operation in group.operations:
            record = view.of_operation(operation)
            if record is not None:
                members.setdefault(record.key, record)
        for unique_id, _identity in group.source_records:
            key = view.by_unique_id.get((group.kind, unique_id))
            if key is not None:
                members.setdefault(key, view.records[key])
        member_key = frozenset(members)
        if not members or member_key in seen:
            continue
        seen.add(member_key)
        _group_problem(group.problem_type, members.values(), findings)


def _destination_ambiguity_problems(
    *, destination: Any, view: _SourceView, config: SyncConfig | None, findings: _Findings
) -> None:
    """List each identity that two or more destination nodes hold (`destination_identity_ambiguous`).

    The source record with that identity is left out. Without one, the problem names the
    identity as the destination forms it; the selection step plans no operation for it either.
    """
    for ambiguity in getattr(destination, "destination_identity_ambiguities", None) or ():
        key = view.by_unique_id.get((ambiguity.kind, ambiguity.unique_id))
        if key is not None:
            reference, keys = view.records[key].reference(), [key]
        else:
            identity = _destination_identity(destination, ambiguity.kind, ambiguity.identity, config)
            reference, keys = SourceRecordReference(kind=ambiguity.kind, identity=identity), []
        if reference.identity is not None:
            findings.ambiguous.add((ambiguity.kind, _identity_bytes(reference.identity)))
        problem = _problem(
            "destination_identity_ambiguous",
            reference,
            destination_ids=sorted(ambiguity.node_ids),
            identity=ambiguity.identity,
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
            local_id = getattr(item, "local_id", None)
            referrer = (
                _source_key(kind, str(local_id)) if local_id is not None else (kind, "unique_id", item.get_unique_id())
            )
            for field_name, peer_kinds in sorted(candidates.items()):
                value = getattr(item, field_name, None)
                for unique_id in value if isinstance(value, (list, tuple)) else [value]:
                    target = _reference_target(view, peer_kinds, unique_id)
                    if target is not None and target != referrer:
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


def _walk(  # pylint: disable=too-many-arguments  # the walk's inputs, all keyword-only
    *,
    source: Any,
    view: _SourceView,
    destination_schema: Any,
    config: SyncConfig | None,
    findings: _Findings,
) -> tuple[dict[_RecordKey, LeftOutRecord], list[KeptRecord]]:
    """Leave out the records with problems and their dependents at any depth; keep optional referrers (FR-005)."""
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
        left[key] = LeftOutRecord(
            record=view.records[key].reference(),
            cause="depends_on_left_out",
            depends_on=view.reference_of(entry.peer),
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
            if _requires(referrer[0], field_name, source=source, destination_schema=destination_schema):
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
    kept: dict[tuple[_RecordKey, str, str], KeptRecord] = {}
    for referrer, field_name, target in optional:
        if referrer not in left:
            kept.setdefault(
                (referrer, field_name, repr(target)),
                KeptRecord(
                    record=view.records[referrer].reference(),
                    cause="peer_left_out",
                    relationship=field_name,
                    left_out_peer=view.records[target].reference(),
                ),
            )
    _keep_load_report_referrers(findings, view, left, kept)
    return left, list(kept.values())


def _keep_load_report_referrers(
    findings: _Findings,
    view: _SourceView,
    left: Mapping[_RecordKey, LeftOutRecord],
    kept: dict[tuple[_RecordKey, str, str], KeptRecord],
) -> None:
    """Keep the records the read built without an optional link to a peer it did not build or the source lacks."""
    for causes, cause in (
        (findings.not_loaded_optional, "peer_left_out"),
        (findings.not_found_optional, "peer_not_found_in_source"),
    ):
        for key, entry in causes:
            if key in left or entry.peer is None or entry.field is None:
                continue
            peer = (
                view.reference_of(entry.peer)
                if cause == "peer_left_out"
                else entry.peer.model_copy(update={"identity": None})
            )
            assert peer is not None
            kept.setdefault(
                (key, entry.field, f"{peer.kind}:{peer.source_id}"),
                KeptRecord(
                    record=view.records[key].reference(),
                    cause=cause,
                    relationship=entry.field,
                    left_out_peer=peer,
                ),
            )


def find_problems(
    *,
    source: Any,
    destination: Any,
    config: SyncConfig | None,
    identity_groups: Sequence[IdentityGroup] = (),
    dropdown_choices: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    peers: DestinationOnlyPeers | None = None,
) -> PlanReport:
    """Find the problems of the plan and the records it leaves out and keeps (research R3).

    `source` is the source adapter: its store and its `load_report`. `destination` is the
    destination adapter: its schema decides which relationships are required and its
    `destination_identity_ambiguities` name the identities two or more of its nodes hold.
    `identity_groups` are what `warn_missing_convergence_key` returned for the derived
    operations. `dropdown_choices` are the choices of
    each mapped Dropdown attribute by kind and attribute; `None` means the run had no
    destination schema snapshot, so Dropdown values are not checked. `peers` is the plan's
    shared `DestinationOnlyPeers`; without it, the pass reads its own from `destination`.

    Nothing here writes to a store or changes an operation.
    """
    if peers is None and destination is not None:
        peers = destination_only_peers(destination)
    load_report = list(getattr(getattr(source, "load_report", None), "entries", None) or ())
    destination_schema = getattr(destination, "schema", None)
    view = _build_view(source=source, load_report=load_report, config=config, peers=peers)
    findings = _Findings()
    _load_report_problems(
        load_report, view=view, source=source, destination_schema=destination_schema, findings=findings
    )
    if dropdown_choices is None:
        logger.warning("Plan: Dropdown values were not checked: this run has no destination schema snapshot")
    else:
        _dropdown_problems(source=source, view=view, dropdown_choices=dropdown_choices, findings=findings)
    _identity_group_problems(identity_groups, view, findings)
    _destination_ambiguity_problems(destination=destination, view=view, config=config, findings=findings)
    left, kept = _walk(
        source=source, view=view, destination_schema=destination_schema, config=config, findings=findings
    )
    return PlanReport(
        problems=tuple(findings.problems),
        left_out=tuple(left.values()),
        kept=tuple(kept),
        unchanged_without_kept_relationships=_unchanged_without(
            kept, view=view, source=source, destination=destination
        ),
    )


def _unchanged_without(
    kept: Sequence[KeptRecord], *, view: _SourceView, source: Any, destination: Any
) -> frozenset[tuple[str, bytes]]:
    """The kept records whose source and destination values are equal once their dropped relationships are ignored.

    Their update would change only a relationship the plan writes without its peer, which
    the selection step removes, so the plan plans no update for them.
    """
    dropped: dict[tuple[str, bytes], set[str]] = {}
    for item in kept:
        if item.record.identity is not None:
            dropped.setdefault((item.record.kind, _identity_bytes(item.record.identity)), set()).add(item.relationship)
    unchanged: set[tuple[str, bytes]] = set()
    for (kind, identity), fields in dropped.items():
        key = view.by_identity.get((kind, identity))
        record = None if key is None else view.records[key]
        if record is None or record.unique_id is None or destination is None:
            continue
        try:
            source_item = source.store.get(model=kind, identifier=record.unique_id)
            destination_item = destination.store.get(model=kind, identifier=record.unique_id)
        except ObjectNotFound:
            continue
        source_values = {name: value for name, value in source_item.get_attrs().items() if name not in fields}
        destination_values = {name: value for name, value in destination_item.get_attrs().items() if name not in fields}
        if source_values == destination_values:
            unchanged.add((kind, identity))
    return frozenset(unchanged)


# --- The selection step --------------------------------------------------------------------


def _without_left_out_peers(
    operation: PlannedOperation, *, left: set[tuple[str, bytes]], dropped: set[str]
) -> PlannedOperation:
    """Remove from `operation` every reference to a left-out peer or to a peer the source does not return.

    A create keeps the other peers of a cardinality-many relationship and loses a
    cardinality-one relationship. An update loses the whole relationship, so a replace-set
    write cannot remove a peer from Infrahub. A relationship in `dropped` (a kept record's
    relationship written without its peer) is treated the same way whatever peers it names.
    """
    references = []
    changed = False
    for reference in operation.relationships or ():
        remaining = [
            peer
            for peer in reference.peers
            if not is_not_in_source_peer(peer) and (reference.peer_kind, _identity_bytes(peer)) not in left
        ]
        if len(remaining) == len(reference.peers) and reference.field not in dropped:
            references.append(reference)
            continue
        changed = True
        if operation.action == "create" and reference.cardinality == "many":
            references.append(reference.model_copy(update={"peers": remaining}))
    if not changed:
        return operation
    record = operation.model_dump()
    record["relationships"] = [reference.model_dump() for reference in references] or None
    # Validated again, so a removed reference that the identity needs is refused, never written.
    return PlannedOperation.model_validate(record)


def select_operations(operations: Sequence[PlannedOperation], report: PlanReport) -> list[PlannedOperation]:
    """Apply the plan report to the derived operations (research R3). Neither input is changed.

    Removes every operation of a left-out record, including a delete, so a left-out record
    that Infrahub holds stays as it is, and every operation of an identity that two or more
    Infrahub nodes hold. It computes no delete of a kind in `report.deletes_withheld_by_kind()`
    (FR-008): a left-out record without a known identity may be any node of its kind. From
    every other operation it removes the references to left-out peers and to peers the source
    does not return (`_without_left_out_peers`), and it plans no update of a kept record that
    would change nothing else (`PlanReport.unchanged_without_kept_relationships`).
    """
    left = {
        (item.record.kind, _identity_bytes(item.record.identity))
        for item in report.left_out
        if item.record.identity is not None
    }
    left |= {
        (problem.record.kind, _identity_bytes(problem.record.identity))
        for problem in report.problems
        if problem.problem_type == "destination_identity_ambiguous" and problem.record.identity is not None
    }
    dropped: dict[tuple[str, bytes], set[str]] = {}
    for item in report.kept:
        if item.record.identity is not None:
            dropped.setdefault((item.record.kind, _identity_bytes(item.record.identity)), set()).add(item.relationship)
    no_deletes = report.deletes_withheld_by_kind()
    withheld: dict[str, int] = {}
    selected: list[PlannedOperation] = []
    for operation in operations:
        key = (operation.kind, _identity_bytes(operation.identity))
        if key in left:
            continue
        if operation.action == "delete" and operation.kind in no_deletes:
            withheld[operation.kind] = withheld.get(operation.kind, 0) + 1
            continue
        if operation.action == "update" and key in report.unchanged_without_kept_relationships:
            continue
        selected.append(_without_left_out_peers(operation, left=left, dropped=dropped.get(key, set())))
    for kind, count in sorted(withheld.items()):
        logger.warning(
            "Plan: %d delete(s) of %s not planned, because a %s record left out of the plan has no known identity "
            "or Infrahub holds an identity of it more than once",
            count,
            kind,
            kind,
        )
    return selected


@dataclass(frozen=True)
class ProblemPassResult:
    """The plan report and the operations the plan writes (research R3)."""

    report: PlanReport
    operations: list[PlannedOperation]
    dropdown_values_checked: bool


def run_problem_pass(  # pylint: disable=too-many-arguments  # the pass's inputs (research R3)
    *,
    source: Any,
    destination: Any,
    operations: Sequence[PlannedOperation],
    config: SyncConfig | None,
    identity_groups: Sequence[IdentityGroup] = (),
    dropdown_choices: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    peers: DestinationOnlyPeers | None = None,
) -> ProblemPassResult:
    """Run `find_problems` and `select_operations`: the step a plan runs before it is written."""
    report = find_problems(
        source=source,
        destination=destination,
        config=config,
        identity_groups=identity_groups,
        dropdown_choices=dropdown_choices,
        peers=peers,
    )
    return ProblemPassResult(
        report=report,
        operations=select_operations(operations, report),
        dropdown_values_checked=dropdown_choices is not None,
    )


__all__ = ["ProblemPassResult", "find_problems", "run_problem_pass", "select_operations"]
