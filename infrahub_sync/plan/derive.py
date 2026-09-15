"""Plan derivation: a diffsync comparison result becomes planned operations.

Delivers FR-001's population, FR-002, FR-015, FR-024, FR-028's obligation levels and
FR-030's failure behavior. The module walks `diff.children` exactly as
`Potenda._diff_to_rows` does but keeps what that function discards — `element.keys`
becomes the destination identity, `element.type` the kind, `element.action` the action —
and it adds the two things a saved plan needs and a Parquet row set never carried: a
payload that can key its own upsert, and relationship peers named by kind and identity.

Four rules in here are load-bearing and each is enforced where it is stated:

- **The payload is the union of `element.keys` and `element.source_attrs`** (AD042).
  `source_attrs` comes from `get_attrs()`, whose contract excludes the fields in
  `_identifiers`, and the generator strips identifiers out of `_attributes` — so a payload
  taken from `source_attrs` alone carries **no identity field at all**: the destination's
  human-friendly ID cannot be formed, the upsert is unkeyed, and every re-apply
  duplicates. Today's create path converges only because it passes both.
- **A peer is named by kind and identity, recursively** (AD043). A peer identity component
  that is itself a reference records a nested `{"peer_kind": …, "identity": …}` pair to
  whatever depth the configuration nests, so no consumer ever splits a DiffSync unique-id
  on `__`.
- **A peer's kind is probed, never read from the mapping** (AD046, AD050). `DcimDevice` is
  declared by two schema-mapping entries with different `location` references in the
  shipped NetBox example, so the mapping alone is ambiguous. The probe is bounded to the kinds the mapping declares for that field across
  every entry for the owning kind, and **zero hits and more than one hit both fail the
  command** — with no fallback to the mapping-declared kind, not even for a single
  candidate, because an unprobed sole candidate is the mapping-derived answer AD046
  forbids. The one exception is a peer **absent from the loaded store**: where the
  destination's own key for a sole candidate kind is a single field the mapping identifies
  it by, `destination_only_peer` records that field's value literally instead of refusing.
  Nothing is inferred there — the kind and the identity both come from the destination
  schema, and apply resolves the pair against the destination before writing. A peer the
  store does hold still takes the probed path, whatever its value.
- **A derivation failure fails the command, on `diff` as on `sync`** (AD047). There is no
  tolerance option here: `--continue-on-error` is declared on `sync` only while derivation
  also runs under `diff`, and degrading to warn-and-drop would emit a silently incomplete
  plan.

The walk is **one level deep**, as `_diff_to_rows`' is. An element that carries child
elements is refused rather than silently flattened — see `_refuse_child_elements`.

Deletes come from `derive_deletes` and from nowhere else: an element whose action is
`delete` is skipped while walking the diff (FR-015), so a delete is recorded once and never
enters the comparison result the write path consumes (FR-016).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from diffsync.exceptions import ObjectNotFound

from infrahub_sync.plan.canonical import canonical_json_bytes, canonical_value
from infrahub_sync.plan.destination_only_peer import (
    DestinationOnlyPeers,
    destination_only_identity,
    destination_only_peers,
)
from infrahub_sync.plan.errors import (
    MissingDestinationIdError,
    SourcePeerUnresolvedError,
    UnformableDestinationIdentityError,
    UnwalkedDiffChildrenError,
)
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.keying import (
    _component_field,
    _refuse_destination_identity_collisions,
    refuse_unkeyed_create,
)
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from infrahub_sync import SyncConfig

logger = logging.getLogger(__name__)

# The action an element carries when the object exists at the destination and not at the
# source. Skipped while walking the diff so deletes come from `derive_deletes` alone.
DIFF_DELETE_ACTION = "delete"
# The diff action whose operation is keyed by a recorded destination id (plan format 3).
DIFF_UPDATE_ACTION = "update"


class _ResolvedReference(NamedTuple):
    """One reference-bearing field, resolved into a peer kind and peer identities."""

    peer_kind: str
    # Nested `{"peer_kind", "identity"}` pairs, canonically ordered by peer identity.
    pairs: list[dict[str, Any]]
    is_many: bool

    def reference(self, field: str) -> RelationshipReference:
        """Render this resolution as the record an operation carries."""
        return RelationshipReference(
            field=field,
            peer_kind=self.peer_kind,
            cardinality="many" if self.is_many else "one",
            peers=[pair["identity"] for pair in self.pairs],
        )

    def identity_value(self) -> Any:
        """Render this resolution as the value it takes inside a destination identity."""
        if self.is_many:
            return list(self.pairs)
        return self.pairs[0]


def reference_candidates(config: SyncConfig | None, kind: str) -> dict[str, tuple[str, ...]]:
    """Candidate peer kinds per reference-bearing field of `kind`, sorted (AD050).

    The candidate set for a field is every kind the configuration declares as that field's
    `reference` across **every** `schema_mapping` entry whose `name` is `kind` —
    `{LocationRack, LocationSite}` for `DcimDevice.location` on the qualified path. Sorted
    so the probe order, and therefore the wording of a failure, is deterministic.
    """
    if config is None:
        return {}
    by_field: dict[str, set[str]] = {}
    for entry in config.schema_mapping:
        if entry.name != kind:
            continue
        for field in entry.fields:
            if field.reference:
                by_field.setdefault(field.name, set()).add(field.reference)
    return {name: tuple(sorted(kinds)) for name, kinds in by_field.items()}


def _can_key_a_store_lookup(unique_id: Any) -> bool:
    """Whether the store can be asked about `unique_id` at all.

    `BaseStore._get_uid` takes a `str` identifier as the uid directly and expands anything
    else as the mapping of identifier keys, `create_unique_id(**identifier)`. A value that is
    neither — a bool, a number, a sequence — makes that expansion raise `TypeError` out of
    the store instead of reporting the peer missing, so the question is settled here rather
    than by catching what the store throws.

    Such a value is **absent** in the only sense the store can report: no entry it holds can
    be reached by it. That is the arm the destination-only rule narrows, and a value it does
    not admit keeps the ordinary absent refusal.
    """
    return isinstance(unique_id, (str, Mapping))


def _probe_peer_kind(
    *,
    store: Any,
    candidates: tuple[str, ...],
    unique_id: Any,
    owning_kind: str,
    field: str,
) -> tuple[str, Any] | None:
    """Probe `candidates` in `store` for `unique_id` and return the single hit (AD050).

    `BaseStore.get` and `LocalStore.get` both require a `model` and select the per-model
    bucket before touching the identifier, and the only kind-free call,
    `get_all_model_names()`, enumerates kinds rather than answering for a unique-id — so an
    entry cannot be asked for its own kind and the candidate set has to be probed.

    `None` where no candidate holds the peer, including where `unique_id` cannot key a
    lookup in the first place. That outcome is returned rather than raised because the
    caller, not the probe, decides what it means: a peer absent from the loaded source store
    can still be recorded literally, and every other absent peer refuses there.

    Raises:
        SourcePeerUnresolvedError: more than one candidate holds the peer (the **ambiguous**
            arm), so its kind cannot be established.
    """
    if not _can_key_a_store_lookup(unique_id):
        return None

    hits: list[tuple[str, Any]] = []
    for candidate in candidates:
        try:
            hits.append((candidate, store.get(model=candidate, identifier=unique_id)))
        except ObjectNotFound:
            continue

    if not hits:
        return None
    if len(hits) > 1:
        tried = ", ".join(candidates) if candidates else "<none declared by the schema mapping>"
        found = ", ".join(candidate for candidate, _peer in hits)
        msg = (
            f"The peer {unique_id!r} referenced by field {field!r} of kind {owning_kind!r} is present "
            f"under more than one candidate peer kind ({found}), so its kind cannot be established. "
            f"Candidate peer kinds tried: {tried}."
        )
        raise SourcePeerUnresolvedError.ambiguous(msg)
    return hits[0]


def _peer_pair(
    *,
    store: Any,
    config: SyncConfig | None,
    owning_kind: str,
    field: str,
    candidates: tuple[str, ...],
    unique_id: Any,
    chain: tuple[tuple[str, Any], ...],
    peers: DestinationOnlyPeers | None,
) -> dict[str, Any]:
    """Return the nested `{"peer_kind", "identity"}` pair naming one peer (AD043).

    Recurses: a component of the peer's own identity that is itself a reference becomes
    another nested pair, to whatever depth the configuration nests. `chain` carries the
    `(kind, unique_id)` pairs already open, so a configuration whose identifiers reference
    each other in a cycle fails with a message rather than a `RecursionError`.

    Raises:
        SourcePeerUnresolvedError: the peer is in none of the candidate kinds' buckets and
            `destination_only_identity` does not admit it (the **absent** arm).
    """
    probed = _probe_peer_kind(
        store=store,
        candidates=candidates,
        unique_id=unique_id,
        owning_kind=owning_kind,
        field=field,
    )
    if probed is None:
        if len(candidates) == 1:
            literal = destination_only_identity(
                config=config,
                peer_kind=candidates[0],
                unique_id=unique_id,
                owning_kind=owning_kind,
                field=field,
                peers=peers,
            )
            if literal is not None:
                return literal
        tried = ", ".join(candidates) if candidates else "<none declared by the schema mapping>"
        msg = (
            f"The peer {unique_id!r} referenced by field {field!r} of kind {owning_kind!r} is not "
            f"present in the loaded store under any candidate peer kind. Candidate peer kinds "
            f"tried: {tried}."
        )
        raise SourcePeerUnresolvedError.absent(msg)
    peer_kind, peer = probed
    if (peer_kind, unique_id) in chain:
        opened = " -> ".join(f"{kind}:{uid!r}" for kind, uid in (*chain, (peer_kind, unique_id)))
        msg = (
            f"No destination identity can be formed for kind {owning_kind!r} field {field!r}: the "
            f"configuration's identifiers reference each other in a cycle ({opened})."
        )
        raise UnformableDestinationIdentityError(
            msg,
            next_action=(
                "Remove the reference from one of those kinds' `identifiers` in the schema mapping so "
                "the identity is finite."
            ),
        )

    identifiers = peer.get_identifiers()
    if not identifiers:
        msg = (
            f"The peer {unique_id!r} of kind {peer_kind!r}, referenced by field {field!r} of kind "
            f"{owning_kind!r}, carries no identifiers, so no peer identity can be recorded for it."
        )
        raise UnformableDestinationIdentityError(msg)

    resolved = _resolve_references(
        values=identifiers,
        candidates=reference_candidates(config, peer_kind),
        store=store,
        config=config,
        owning_kind=peer_kind,
        chain=(*chain, (peer_kind, unique_id)),
        peers=peers,
    )
    return {
        "peer_kind": peer_kind,
        "identity": _identity_from_keys(keys=identifiers, kind=peer_kind, resolved=resolved),
    }


def _resolve_one_reference(
    *,
    value: Any,
    store: Any,
    config: SyncConfig | None,
    owning_kind: str,
    field: str,
    candidates: tuple[str, ...],
    chain: tuple[tuple[str, Any], ...],
    peers: DestinationOnlyPeers | None,
) -> _ResolvedReference:
    """Resolve one reference-bearing field's value into a peer kind and peer identities.

    Cardinality follows the mapped value's shape — a list is `many`, anything else is `one`.
    A many reference's peers are ordered canonically by peer identity so two derivations of
    the same input encode identically (AD003, FR-005). An **empty** many set resolves
    trivially, whatever the mapping's candidate count — see the branch below.

    Raises:
        SourcePeerUnresolvedError: the resolved peers span more than one kind, or the field
            names an empty set and the mapping declares no candidate kind at all.
    """
    is_many = isinstance(value, (list, tuple))
    unique_ids = list(value) if is_many else [value]
    pairs = [
        _peer_pair(
            store=store,
            config=config,
            owning_kind=owning_kind,
            field=field,
            candidates=candidates,
            unique_id=unique_id,
            chain=chain,
            peers=peers,
        )
        for unique_id in unique_ids
    ]
    pairs.sort(key=lambda pair: canonical_json_bytes(pair["identity"]))

    peer_kinds = {pair["peer_kind"] for pair in pairs}
    if len(peer_kinds) > 1:
        msg = (
            f"Field {field!r} of kind {owning_kind!r} names peers of more than one kind "
            f"({', '.join(sorted(peer_kinds))}), which one relationship reference cannot record."
        )
        raise SourcePeerUnresolvedError.ambiguous(msg)
    if peer_kinds:
        peer_kind = peer_kinds.pop()
    elif candidates:
        # An empty peer set is **trivially resolved**: it names no
        # peer, so no candidate kind has to be chosen for one. It is the deliberately empty
        # set the replace-set write acts on (FR-028.2), and that write is keyed by the
        # relationship's field rather than by a peer kind — `peers: []` empties the set
        # identically whichever candidate labels it. So the first candidate in the sorted
        # order labels the reference, deterministically, and AD046's "never read the peer
        # kind off the mapping" stands unchanged for every non-empty set, where a peer
        # exists to probe and mislabelling it would bind the wrong object.
        peer_kind = candidates[0]
    else:
        msg = (
            f"Field {field!r} of kind {owning_kind!r} names an empty peer set and the schema mapping "
            f"declares no candidate peer kind for it at all, so the reference cannot be recorded."
        )
        raise SourcePeerUnresolvedError.ambiguous(msg)
    return _ResolvedReference(peer_kind=peer_kind, pairs=pairs, is_many=is_many)


def _resolve_references(
    *,
    values: Mapping[str, Any],
    candidates: Mapping[str, tuple[str, ...]],
    store: Any,
    config: SyncConfig | None,
    owning_kind: str,
    chain: tuple[tuple[str, Any], ...] = (),
    peers: DestinationOnlyPeers | None = None,
) -> dict[str, _ResolvedReference]:
    """Resolve every reference-bearing field of `values` that carries a value.

    A field whose value is `None` is **absent** and resolves to nothing: it enters neither
    the payload nor the relationship set. An empty list is a different thing — a
    deliberately empty peer set — and is resolved (FR-028.2).

    `peers` defaults to unavailable, which refuses every peer the store does not hold. A
    derived delete leaves it there deliberately: its peers are destination-only by
    construction and probed against the destination store, so the rule has nothing to add
    and enabling it would only widen what a delete admits (AD049).
    """
    resolved: dict[str, _ResolvedReference] = {}
    for field in sorted(candidates):
        if field not in values:
            continue
        value = values[field]
        if value is None:
            continue
        resolved[field] = _resolve_one_reference(
            value=value,
            store=store,
            config=config,
            owning_kind=owning_kind,
            field=field,
            candidates=candidates[field],
            chain=chain,
            peers=peers,
        )
    return resolved


def _refuse_child_elements(*, element: Any, kind: str) -> None:
    """Refuse a comparison element that carries child elements.

    The walk below is one level deep. diffsync hangs an element's children off its
    `child_diff` (`.venv/…/diffsync/diff.py`), populated for models that declare
    `_children`; nothing generated by this repository declares any, so this guard is silent
    on every path in it. Where it is not silent, the alternative is a plan that omits every
    child change with no signal at all — so the condition fails the command instead of
    being dropped quietly (FR-001, AD047).

    Raises:
        UnwalkedDiffChildrenError: the element carries at least one child element.
    """
    child_diff = getattr(element, "child_diff", None)
    get_children = getattr(child_diff, "get_children", None)
    if get_children is None:
        return
    children = list(get_children())
    if not children:
        return
    child_kinds = sorted({getattr(child, "type", None) or "<unknown>" for child in children})
    msg = (
        f"The comparison element for kind {kind!r} named {getattr(element, 'name', '<unnamed>')!r} carries "
        f"{len(children)} child element(s) of kind(s) {', '.join(child_kinds)}, and plan derivation walks "
        f"the comparison one level deep — so those changes would be missing from the plan with nothing in "
        f"it to say so."
    )
    raise UnwalkedDiffChildrenError(msg)


def _identity_from_keys(
    *,
    keys: Mapping[str, Any],
    kind: str,
    resolved: Mapping[str, _ResolvedReference],
) -> dict[str, Any]:
    """Build the canonical destination identity for one operation (FR-028.3, AD043, AD049).

    Every key of `keys` contributes a component. A key that is a reference contributes the
    nested `{"peer_kind", "identity"}` pair `resolved` holds for it, never the peer's
    DiffSync unique-id string. The same rule applies to a derived delete; only the store the
    pairs were probed against differs (AD049).

    Raises:
        UnformableDestinationIdentityError: the key set is empty, or a component resolved to
            no value at all.
    """
    if not keys:
        msg = (
            f"No destination identity can be formed for an operation on kind {kind!r}: the "
            "comparison carried no identity attributes for it."
        )
        raise UnformableDestinationIdentityError(msg)

    identity: dict[str, Any] = {}
    for name, value in keys.items():
        reference = resolved.get(name)
        if reference is not None:
            if not reference.pairs:
                msg = (
                    f"No destination identity can be formed for an operation on kind {kind!r}: its "
                    f"identity attribute {name!r} is a relationship naming an empty peer set."
                )
                raise UnformableDestinationIdentityError(msg)
            identity[name] = reference.identity_value()
            continue
        if value is None:
            msg = (
                f"No destination identity can be formed for an operation on kind {kind!r}: its "
                f"identity attribute {name!r} resolved to no value."
            )
            raise UnformableDestinationIdentityError(msg)
        identity[name] = value
    return canonical_identity(identity, kind=kind)


def tier_of(kind: str, *, tiers: Sequence[set[str]] | None, top_level: Sequence[str]) -> int:
    """Return the dependency tier `kind` belongs to (FR-028.1, PD-007).

    The index of the tier set containing the kind when the engine computed tiers; when the
    configuration declares an explicit `order:` there are no tiers and the tier is the
    kind's index in `top_level`. Either way the field is present and deterministic.

    Bind `tiers` and `top_level` with `functools.partial` to obtain the one-argument
    callable the derivation functions take.

    Raises:
        ValueError: the kind appears in neither the tiers nor `top_level`, which means the
            comparison produced a kind the engine was never told to synchronize.
    """
    if tiers:
        for index, tier in enumerate(tiers):
            if kind in tier:
                return index
        msg = f"Kind {kind!r} is in none of the {len(tiers)} computed dependency tiers."
        raise ValueError(msg)
    if kind in top_level:
        return list(top_level).index(kind)
    msg = f"Kind {kind!r} is not in the synchronization order {list(top_level)!r}."
    raise ValueError(msg)


def _destination_ids_for_kind(destination_adapter: Any, kind: str) -> dict[str, str | None]:
    """The destination node id each object of `kind` carries, by its DiffSync unique id.

    The id lives on the destination model's `local_id`, which a live destination load sets
    and the warm path restores from the side-B snapshot. `None` where the model carries
    none: absent and present-but-unset are the same refusal, and telling them apart would
    only change the wording.
    """
    records = list(destination_adapter.get_all(kind)) if hasattr(destination_adapter, "get_all") else []
    for record in records:
        _require_shortname_is_the_unique_id(record, kind=kind)
    return {record.get_unique_id(): getattr(record, "local_id", None) for record in records}


def _require_shortname_is_the_unique_id(record: Any, *, kind: str) -> None:
    """Refuse a destination model whose shortname is not its unique id.

    The update's id is looked up by `element.name`, and DiffSync sets that from
    `get_shortname()` — which its own source notes is "NOT guaranteed globally unique". It
    equals `get_unique_id()` only while `_shortname` is unset, which is true of every model
    the generator emits today and is the reason the lookup works at all.

    A model that later declares `_shortname` would break that silently and completely: every
    lookup would miss, every update would be refused as having no recorded destination id, and
    the message would blame the destination load. So the assumption is checked where it is
    relied on rather than left to be rediscovered from that symptom.
    """
    shortname = record.get_shortname() if hasattr(record, "get_shortname") else None
    if shortname is None:
        return
    unique_id = record.get_unique_id()
    if shortname != unique_id:
        msg = (
            f"Destination kind {kind!r} declares a DiffSync '_shortname', so its element name "
            f"{shortname!r} is not its unique id {unique_id!r}. Plan derivation looks an update's "
            "recorded destination id up by element name, so every update of this kind would be refused. "
            "Remove '_shortname' from the model, or key this lookup by unique id on both sides."
        )
        raise AssertionError(msg)


def _require_destination_id(
    *,
    destination_ids: Mapping[str, str | None],
    unique_id: str,
    kind: str,
    identity: Mapping[str, Any],
) -> str:
    """The recorded destination id for one update, or the refusal that names what is missing.

    An update reaches apply keyed by this id alone. Recording the operation without one
    would leave apply to issue the same create-shaped convergent upsert a create issues,
    which the server matches on HFID components — and which silently creates a second object
    when one of those components is absent from the payload (G1 M6). So a missing id is a
    plan-time refusal rather than a warning.
    """
    recorded = destination_ids.get(unique_id)
    if isinstance(recorded, str) and recorded:
        return recorded
    found = "no destination object for this identity" if unique_id not in destination_ids else repr(recorded)
    msg = (
        f"Planned update of {kind!r} object {dict(identity)!r} carries no destination id: {found}. "
        "An update is keyed by the destination id recorded for it at plan time, so this operation "
        "cannot be recorded."
    )
    raise MissingDestinationIdError(msg)


def _warn_dropped_cardinality_one_clear(
    *,
    action: str,
    kind: str,
    identity: Mapping[str, Any],
    candidates: Mapping[str, tuple[str, ...]],
    values: Mapping[str, Any],
) -> None:
    """`[S7]` Disclose a null cardinality-one peer that the plan cannot represent.

    `_resolve_references` treats a `None`-valued reference field as absent, so the derived
    operation carries no reference for it and the apply leaves the destination relationship
    alone. On a **create** that is the established and correct omission — there is nothing to
    clear. On an **update** the null is a real difference between the two sides that the plan
    silently drops, and plan format 3 adds no encoding for a clear, so the only honest thing
    to do is say so here, where the null is still visible.
    """
    if action != "update":
        return
    dropped = sorted(field for field in candidates if field in values and values[field] is None)
    if not dropped:
        return
    logger.warning(
        "Plan: update of destination kind %s (%s) drops null cardinality-one relationship field(s) %s. "
        "The plan format cannot represent a relationship clear, so the destination relationship is "
        "not changed. Clear it at the destination if the clear was intended",
        kind,
        ", ".join(f"{name}={value!r}" for name, value in sorted(identity.items())),
        ", ".join(dropped),
    )


def operations_from_diff(  # pylint: disable=redefined-outer-name
    diff: Any,
    *,
    config: SyncConfig | None,
    tier_of: Callable[[str], int],
    source_adapter: Any,
    destination_adapter: Any,
) -> list[PlannedOperation]:
    """Derive the create and update operations a comparison result proposes (FR-002).

    Walks `diff.children` as `Potenda._diff_to_rows` does. An element with no action is a
    no-op and is skipped; an element whose action is `delete` is skipped too, so deletes
    come from `derive_deletes` alone (FR-015).

    `tier_of` is the one-argument tier resolver — `functools.partial(tier_of, tiers=…,
    top_level=…)` binds this module's own function into it.

    Raises:
        UnwalkedDiffChildrenError: an element carries child elements, which this walk does
            not descend into.
        UnformableDestinationIdentityError: an operation's destination identity cannot be
            formed.
        SourcePeerUnresolvedError: a relationship peer is absent from the loaded source
            store, or its kind is ambiguous across the candidate kinds (AD082).
        UnserializablePayloadValueError: a payload value is outside the canonical-value
            table.
    """
    store = source_adapter.store
    operations: list[PlannedOperation] = []
    children = diff.children
    # Read once per derivation: the destination key a destination-only peer is recorded
    # against does not change mid-plan, and the warning is de-duplicated across the whole run.
    peers = destination_only_peers(destination_adapter)
    # Read once per kind rather than per element: an update's id is looked up by unique id,
    # and the destination store is already fully in memory by the time derivation runs.
    destination_ids_by_kind: dict[str, dict[str, str | None]] = {}
    for group, elements_by_name in children.items():
        for element in elements_by_name.values():
            kind = getattr(element, "type", None) or group
            # Before the action filter, because an element with no action of its own can
            # still carry children that have one.
            _refuse_child_elements(element=element, kind=kind)
            action = getattr(element, "action", None) or ""
            if not action or action == DIFF_DELETE_ACTION:
                continue
            keys: Mapping[str, Any] = getattr(element, "keys", None) or {}
            source_attrs: Mapping[str, Any] = getattr(element, "source_attrs", None) or {}
            # AD042. `source_attrs` alone carries no identity field, so the union is what
            # makes the upsert keyed rather than what makes the record tidy.
            merged = {**keys, **source_attrs}

            candidates = reference_candidates(config, kind)
            resolved = _resolve_references(
                values=merged,
                candidates=candidates,
                store=store,
                config=config,
                owning_kind=kind,
                peers=peers,
            )
            identity = _identity_from_keys(keys=keys, kind=kind, resolved=resolved)
            _warn_dropped_cardinality_one_clear(
                action=action, kind=kind, identity=identity, candidates=candidates, values=merged
            )
            payload = {
                name: canonical_value(value, kind=kind, field=name)
                for name, value in merged.items()
                if name not in candidates
            }
            references = [resolved[field].reference(field) for field in sorted(resolved)]
            destination_id = None
            if action == DIFF_UPDATE_ACTION:
                if kind not in destination_ids_by_kind:
                    destination_ids_by_kind[kind] = _destination_ids_for_kind(destination_adapter, kind)
                destination_id = _require_destination_id(
                    destination_ids=destination_ids_by_kind[kind],
                    unique_id=element.name,
                    kind=kind,
                    identity=identity,
                )
            operations.append(
                PlannedOperation(
                    operation_id=operation_id(action, kind, identity),
                    action=action,
                    kind=kind,
                    identity=identity,
                    tier=tier_of(kind),
                    payload=payload,
                    relationships=references or None,
                    destination_id=destination_id,
                )
            )
    return operations


def derive_deletes(  # pylint: disable=redefined-outer-name
    *,
    kinds: Sequence[str],
    source_adapter: Any,
    destination_adapter: Any,
    config: SyncConfig | None,
    tier_of: Callable[[str], int],
    destination_full_extract: bool,
) -> list[PlannedOperation]:
    """Derive the deletes a plan records, or none at all (FR-015, FR-016, AD049).

    Per kind: the destination store's identities minus the source store's, enumerated the
    way `Potenda._write_side_snapshot` already enumerates a side. Derived **only** when the
    destination side ran a full extract — an incremental destination extract holds a partial
    picture, and a set difference taken against it would invent deletes. When no deletes are
    derived the caller records `delete_operations_computed: false`, so "there are none" and
    "they were not computed" stay distinguishable (SC-017).

    A delete carries **no** payload and no relationships, and never enters the comparison
    result the write path consumes, which is what makes FR-016 structural. Its identity,
    however, goes through the same recursive canonicalisation as any other operation's, with
    nested peer kinds probed against the **destination** store: a delete exists precisely
    because the object is at the destination and absent from the source, so its peers are
    destination-only by construction (AD049).

    Raises:
        UnformableDestinationIdentityError: a destination object's identity cannot be formed.
        SourcePeerUnresolvedError: a nested peer is absent from, or ambiguous across, the
            destination store's candidate buckets.
    """
    if not destination_full_extract:
        logger.info(
            "Plan: no delete operations derived — the destination side did not run a full extract, so "
            "a set difference against it would invent deletes"
        )
        return []

    store = destination_adapter.store
    operations: list[PlannedOperation] = []
    for kind in kinds:
        source_unique_ids = {record.get_unique_id() for record in source_adapter.get_all(kind)}
        for record in destination_adapter.get_all(kind):
            if record.get_unique_id() in source_unique_ids:
                continue
            keys: Mapping[str, Any] = record.get_identifiers()
            resolved = _resolve_references(
                values=keys,
                candidates=reference_candidates(config, kind),
                store=store,
                config=config,
                owning_kind=kind,
            )
            identity = _identity_from_keys(keys=keys, kind=kind, resolved=resolved)
            operations.append(
                PlannedOperation(
                    operation_id=operation_id(DIFF_DELETE_ACTION, kind, identity),
                    action=DIFF_DELETE_ACTION,
                    kind=kind,
                    identity=identity,
                    tier=tier_of(kind),
                )
            )
    return operations


def _identity_attributes_by_kind(operations: Sequence[PlannedOperation]) -> dict[str, set[str]]:
    """Identity attribute names the plan supplies per destination kind.

    Intersected across a kind's operations, so a component missing from any one of them is
    reported rather than masked by a sibling that happens to carry it.
    """
    by_kind: dict[str, set[str]] = {}
    for operation in operations:
        attributes = set(operation.identity)
        if operation.kind in by_kind:
            by_kind[operation.kind] &= attributes
        else:
            by_kind[operation.kind] = attributes
    return by_kind


def _destination_keys(node: Any) -> list[tuple[str, ...]]:
    """Every key the destination could converge a kind on, as mapping field names.

    The kind's human-friendly ID and each of its uniqueness constraints, each reduced from
    component paths to the mapping field names the plan's identity is keyed by. Sorted and
    deduplicated so which key a warning names does not depend on schema iteration order.
    """
    constraints = getattr(node, "uniqueness_constraints", None) or []
    human_friendly_id = getattr(node, "human_friendly_id", None)
    declared = [*constraints, *([human_friendly_id] if human_friendly_id else [])]
    keys = {tuple(sorted({_component_field(component) for component in key})) for key in declared if key}
    return sorted(keys)


def _merged_identity_counts(operations: Sequence[PlannedOperation], *, key: tuple[str, ...]) -> tuple[int, int]:
    """How many source objects collide, and onto how many destination identities.

    The plan's operations for one kind, grouped by their identity projected onto `key` — what
    the destination will actually distinguish them by. Returns the number of operations that
    land in a group of more than one, and the number of such groups. Deletes are excluded:
    they are destination objects the source no longer has, not source objects being written.
    """
    groups: dict[bytes, int] = {}
    for operation in operations:
        if operation.action == DIFF_DELETE_ACTION:
            continue
        projection = {name: value for name, value in operation.identity.items() if name in key}
        encoded = canonical_json_bytes(projection, kind=operation.kind)
        groups[encoded] = groups.get(encoded, 0) + 1
    collided = [count for count in groups.values() if count > 1]
    return sum(collided), len(collided)


def _warn_identity_finer_than_destination_key(
    *,
    kind: str,
    node: Any,
    supplied: set[str],
    operations: Sequence[PlannedOperation],
) -> None:
    """Warn where the destination cannot tell the plan's identities apart.

    FR-024's two arms both test whether the *destination's* key is covered by the plan's
    identity — `HFID ⊄ identity`, an unkeyed write that duplicates. This is the other
    direction, `identity ⊄ HFID`: the sync distinguishes source objects more finely than the
    destination does, so distinct source objects converge onto **one** destination object and
    the surplus is silently lost. On the qualified path thirteen `LocationRack` objects named
    `Comms closet`, one per site, become one — exit 0, no signal.

    FR-024's condition is *satisfied* in exactly this case, which is why it needs its own
    arm. The warning names the kind, the identity attributes the destination cannot
    distinguish, and how many of this plan's own source objects already share a destination
    identity — a count of what would be lost rather than a caution about what might be.

    Skipped where the kind declares no key at all: there is nothing to be finer than, and
    FR-024's first arm already reports that write as unkeyed. Warning only; the resolution
    (tighten the destination schema, loosen the mapping, or override per kind) is a
    per-deployment decision and is not part of plan derivation.
    """
    keys = _destination_keys(node)
    if not keys or any(supplied <= set(key) for key in keys):
        return

    # The key the destination will really converge on: the one that accounts for most of the
    # plan's identity. Ties break on the sorted order, so the choice is deterministic.
    closest = min(keys, key=lambda key: (-len(supplied & set(key)), key))
    uncovered = sorted(supplied - set(closest))
    collided, destination_identities = _merged_identity_counts(operations, key=closest)
    if collided:
        identities = "identity" if destination_identities == 1 else "identities"
        observed = (
            f"{kind}: {collided} source objects share {destination_identities} destination {identities}, "
            f"and all but one of each group will be lost"
        )
    else:
        observed = (
            f"{kind}: no two of this plan's operations share a destination identity yet, but two source "
            f"objects differing only in those attributes would silently become one"
        )
    logger.warning(
        "Plan: the plan's identity for destination kind %s (%s) is finer than every key the destination "
        "can converge it on (%s), so distinct source objects merge instead of duplicating; the destination "
        "does not distinguish: %s. %s",
        kind,
        ", ".join(sorted(supplied)),
        ", ".join(closest),
        ", ".join(uncovered),
        observed,
    )


def warn_missing_convergence_key(*, destination: Any, operations: Sequence[PlannedOperation]) -> None:
    """Prove every create keys itself, and warn about what only an update risks (FR-024, AD044).

    A **create** is matched by the destination on the components its payload carries, so a
    payload missing one does not match the existing object: the server reports success and
    creates a second one, and nothing downstream can detect it. That cannot be a warning, so
    every create is proven here, per operation, and refused where the proof fails —
    `UnkeyedCreateRefusedError` for a create that cannot key itself, and
    `DestinationIdentityCollisionError` where several creates project onto one destination
    identity and would silently converge into one object.

    An **update** is keyed by the destination id recorded for it at plan time, so none of
    these conditions can make it write the wrong object. Every arm therefore stays a warning
    for updates, which is what makes kinds with no human-friendly ID usable at all:

    1. the kind declares no `human_friendly_id`, or the plan's identity does not supply every
       one of its components;
    2. no `uniqueness_constraints` entry is covered by the plan's identity attributes — a
       different condition, because a kind with a complete human-friendly ID and no
       uniqueness constraint still duplicates silently;
    3. no key the destination declares covers the plan's identity — the opposite direction,
       where source objects **merge** rather than duplicate; see
       `_warn_identity_finer_than_destination_key`.

    **Guarded on the destination exposing a schema at all (AD052)**, since `self.schema` is
    defined on the Infrahub adapter and on no other while derivation runs for every
    destination. Where no schema is exposed the guard is skipped, and skipping it is never an
    error — the same posture the warnings have always had.

    Warnings stay off the manifest, so they remain outside `plan_checksum` and SC-006's byte
    comparison. A refusal fails the command instead (AD047), before any artifact is written.
    """
    schema = getattr(destination, "schema", None)
    if not schema:
        logger.debug("Plan: the destination exposes no schema, so the convergence-key warning is skipped (AD052)")
        return

    by_kind = _identity_attributes_by_kind(operations)
    for kind in sorted(by_kind):
        node = schema.get(kind) if hasattr(schema, "get") else None
        if node is None:
            logger.debug("Plan: the destination schema declares no kind %s; convergence-key guard skipped", kind)
            continue
        of_kind = [operation for operation in operations if operation.kind == kind]
        creates = [operation for operation in of_kind if operation.action == "create"]
        for operation in creates:
            refuse_unkeyed_create(operation, node=node)
        _refuse_destination_identity_collisions(kind=kind, node=node, creates=creates)

        # The merge direction applies to every action: a create whose identity is finer than
        # the destination's key converges onto an object it did not mean just as an update
        # does, and a single such create is the case the collision refusal above deliberately
        # leaves as a warning.
        _warn_identity_finer_than_destination_key(kind=kind, node=node, supplied=by_kind[kind], operations=of_kind)

        # The two arms below are about a write that might **duplicate**, which a create can no
        # longer do — it was proven above — so they narrow to the operations that are still
        # only warned about. Updates specifically, not "everything that is not a create": a
        # delete is recorded and never executed (ADR 0004), so letting one into this set
        # would both narrow the intersection and produce a message about how updates are
        # keyed for a kind this plan only deletes.
        updates = [operation for operation in of_kind if operation.action == "update"]
        if not updates:
            continue
        supplied = set.intersection(*(set(operation.identity) for operation in updates))
        readable = ", ".join(sorted(supplied))

        human_friendly_id = getattr(node, "human_friendly_id", None)
        if not human_friendly_id:
            logger.warning(
                "Plan: destination kind %s declares no human-friendly ID, so its updates are keyed by "
                "their recorded destination id alone",
                kind,
            )
        else:
            missing = [component for component in human_friendly_id if _component_field(component) not in supplied]
            if missing:
                logger.warning(
                    "Plan: the plan's identity for destination kind %s (%s) does not supply every "
                    "human-friendly-ID component; missing: %s. Its updates are keyed by their recorded "
                    "destination id, so they still write the object they mean",
                    kind,
                    readable,
                    ", ".join(missing),
                )

        constraints = getattr(node, "uniqueness_constraints", None) or []
        covered = any(
            {_component_field(component) for component in constraint} <= supplied for constraint in constraints
        )
        if not covered:
            logger.warning(
                "Plan: destination kind %s declares no uniqueness constraint covered by the plan's "
                "identity attributes (%s), so a duplicate keyed on them would not be refused at the "
                "destination",
                kind,
                readable,
            )
