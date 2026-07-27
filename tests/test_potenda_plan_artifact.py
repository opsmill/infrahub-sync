"""Phase D evidence: what a plan run derives, and what it writes before the first write.

Covers T036 (derivation), T037 (SC-017, the delete-computation record), T038 (tier
assignment), T039 (SC-014, the convergence-key warning), T040 (AD039, the tier branch's
two loops) and T041 (SC-006, byte-identical re-plan). Each task has its own labelled
section below; the shared fakes and builders at the top of the module are deliberately
task-neutral so the remaining Phase D cases (T082-T085) append to this file without
re-inventing them.

Two things every case in here has to get right, and both are silent when missed:

- `Potenda.write_plan_artifact` returns `None` early when `run_dir`, `run_id` or `config`
  is falsy (`infrahub_sync/potenda/__init__.py:380-384`). The engine tests elsewhere in
  this repository construct `Potenda(config=None)`, so they never reach the artifact write
  at all. Every case here therefore supplies a real parsed `SyncInstance` **and** a run
  identity, and asserts against a manifest that was actually written.
- A comparison element's `source_attrs` is `get_attrs()`, which **excludes** the
  identifiers (`.venv/…/diffsync/__init__.py:340-347`). `_FakeElement` and `diff_between`
  reproduce that exclusion rather than papering over it, so AD042's payload union stays
  load-bearing in every case that drives a comparison instead of only in the case that
  asserts it directly.
"""

from __future__ import annotations

import json
import logging
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from diffsync.exceptions import ObjectNotFound

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncInstance
from infrahub_sync.cache import incremental as incremental_module
from infrahub_sync.cache.cursors import CursorTier
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import (
    derive_deletes,
    operations_from_diff,
    tier_of,
    warn_missing_convergence_key,
)
from infrahub_sync.plan.models import SC006_MASKED_FIELDS
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME
from infrahub_sync.potenda import Potenda

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

DERIVE_LOGGER = "infrahub_sync.plan.derive"

SYNC_NAME = "demo"

# The four destination kinds every case draws from, in dependency order. `LocationRack`
# carries a reference inside its own identifiers and `DcimDevice` references it, which is
# what gives AD043's recursion two levels to nest through.
KINDS: tuple[str, ...] = ("BuiltinTag", "LocationSite", "LocationRack", "DcimDevice")

# The manifest's complete key set. Asserted as an equality by T039, so a warning that
# leaked into the manifest — and therefore into `plan_checksum` and SC-006 — fails there.
MANIFEST_KEYS = frozenset(
    {
        "format_version",
        "run_id",
        "created_at",
        "config_version",
        "source_snapshot",
        "operations_count",
        "delete_operations_computed",
        "plan_checksum",
    }
)


# ---------------------------------------------------------------------------------------
# Shared fakes: a store, an adapter, a comparison element and a comparison result
# ---------------------------------------------------------------------------------------


class _FakeRecord:
    """Stand-in for a diffsync model instance held in an adapter's store.

    `get_unique_id` joins the identifier values with `__`, as diffsync does, which is what
    makes a reference-bearing field's mapped value a unique-id string that the derivation
    has to resolve rather than parse.
    """

    def __init__(self, kind: str, identifiers: Mapping[str, Any], attrs: Mapping[str, Any] | None = None) -> None:
        self.kind = kind
        self._identifiers = dict(identifiers)
        self._attrs = dict(attrs or {})

    def get_identifiers(self) -> dict[str, Any]:
        return dict(self._identifiers)

    def get_attrs(self) -> dict[str, Any]:
        return dict(self._attrs)

    def get_unique_id(self) -> str:
        return "__".join(str(value) for value in self._identifiers.values())


class _FakeStore:
    """diffsync store stand-in: per-kind buckets, `get` keyed by model **then** identifier.

    The signature matters. `BaseStore.get` and `LocalStore.get` both require `model` and
    select the per-model bucket before touching the identifier, which is why AD050's peer
    kind has to be probed rather than asked for — so a store here that answered without a
    model would let a derivation pass that cannot work against the real one. Every probe is
    recorded, so a test can assert the probe happened rather than infer it from the answer.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, _FakeRecord]] = {}
        self.probes: list[tuple[str, str]] = []

    def add(self, record: _FakeRecord) -> None:
        self._buckets.setdefault(record.kind, {})[record.get_unique_id()] = record

    def get(self, *, model: str, identifier: str) -> _FakeRecord:
        self.probes.append((model, identifier))
        try:
            return self._buckets[model][identifier]
        except KeyError as exc:
            msg = f"no {model} with identifier {identifier!r} is in this store"
            raise ObjectNotFound(msg) from exc


class _FakeAdapter:
    """Adapter stand-in with a store, per-kind records, and a recording comparison.

    `diff_from` compares against `self.top_level` and nothing else, so the `top_level`
    narrowing the tier branch applies around each `diff()` call is observable in the
    returned result's contents (T040). `sync_from` records both the kinds it was handed and
    whether the plan artifact was already on disk at that moment, which is FR-001's
    observable rather than a proxy for it.
    """

    def __init__(
        self,
        name: str,
        records: Iterable[_FakeRecord] = (),
        *,
        schema: Mapping[str, Any] | None = None,
        emit_deletes: bool = False,
    ) -> None:
        self.name = name
        self.top_level: list[str] = []
        self.store = _FakeStore()
        self._by_kind: dict[str, list[_FakeRecord]] = {}
        self.load_calls = 0
        self.sync_calls: list[tuple[str, ...]] = []
        self.manifest_present_at_sync: list[bool] = []
        self.emit_deletes = emit_deletes
        self.run_dir: Path | None = None
        for record in records:
            self.add(record)
        if schema is not None:
            self.schema = schema

    def __str__(self) -> str:
        return self.name

    def add(self, record: _FakeRecord) -> None:
        self._by_kind.setdefault(record.kind, []).append(record)
        self.store.add(record)

    def load(self) -> None:
        self.load_calls += 1

    def get_all(self, kind: str) -> list[_FakeRecord]:
        return list(self._by_kind.get(kind, []))

    def cursor_tier_for(self, resource: str) -> CursorTier:  # noqa: ARG002, PLR6301
        # No cursor tier, so `persist_cursors_for_run` writes nothing and a plan run needs
        # no prior-run scaffolding to complete.
        return CursorTier.NONE

    def diff_from(self, source: _FakeAdapter, **_kwargs: object) -> _FakeDiff:
        return diff_between(
            source=source,
            destination=self,
            kinds=list(self.top_level),
            include_deletes=self.emit_deletes,
        )

    def sync_from(self, _source: _FakeAdapter, *, diff: _FakeDiff | None = None, **_kwargs: object) -> _FakeDiff | None:
        self.sync_calls.append(() if diff is None else tuple(sorted(diff.children)))
        if self.run_dir is not None:
            self.manifest_present_at_sync.append((self.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).exists())
        return diff


class _FakeElement:
    """diffsync `DiffElement` stand-in, carrying the four fields the derivation reads.

    `action` is derived from the source/destination attribute pair by the same rule
    diffsync uses (`.venv/…/diffsync/diff.py:237-254`), so a delete element is produced the
    way the real comparison produces one — destination attributes and no source ones —
    rather than by declaring an action string.
    """

    def __init__(
        self,
        *,
        kind: str,
        name: str,
        keys: Mapping[str, Any],
        source_attrs: Mapping[str, Any] | None = None,
        dest_attrs: Mapping[str, Any] | None = None,
    ) -> None:
        self.type = kind
        self.name = name
        self.keys = dict(keys)
        self.source_attrs = None if source_attrs is None else dict(source_attrs)
        self.dest_attrs = None if dest_attrs is None else dict(dest_attrs)

    @property
    def action(self) -> str | None:
        if self.source_attrs is not None and self.dest_attrs is None:
            return "create"
        if self.source_attrs is None and self.dest_attrs is not None:
            return "delete"
        if self.source_attrs is not None and self.dest_attrs is not None and self.source_attrs != self.dest_attrs:
            return "update"
        return None

    def get_attrs_diffs(self) -> dict[str, dict[str, Any]]:
        """The changed attributes only — never the full attribute set.

        This is the narrow view `Potenda._diff_to_rows` writes into `plan.parquet`, and the
        reason T036 asserts the payload against an **update**: a payload derived from this
        would carry the one attribute that changed and drop the rest.
        """
        if self.source_attrs is not None and self.dest_attrs is not None:
            shared = sorted(set(self.source_attrs) & set(self.dest_attrs))
            changed = [key for key in shared if self.source_attrs[key] != self.dest_attrs[key]]
            return {
                "-": {key: self.dest_attrs[key] for key in changed},
                "+": {key: self.source_attrs[key] for key in changed},
            }
        if self.source_attrs is not None:
            return {"+": dict(self.source_attrs)}
        return {"-": dict(self.dest_attrs or {})}


class _FakeDiff:
    """Mirrors `diffsync.Diff`'s `{kind: {name: element}}` children mapping."""

    def __init__(self, elements_by_kind: Mapping[str, Sequence[_FakeElement]]) -> None:
        self.children = {
            kind: {element.name: element for element in elements} for kind, elements in elements_by_kind.items()
        }

    def has_diffs(self) -> bool:
        return any(element.action for elements in self.children.values() for element in elements.values())


def diff_between(
    *,
    source: _FakeAdapter,
    destination: _FakeAdapter,
    kinds: Sequence[str],
    include_deletes: bool = False,
) -> _FakeDiff:
    """Compare two fake adapters the way diffsync's comparison differ does.

    Only `kinds` are compared — the narrowing the tier branch applies is therefore visible
    in the result. `source_attrs` is `get_attrs()`, identifiers **excluded**, matching the
    diffsync contract; `keys` is `get_identifiers()`. Destination-only objects become delete
    elements only when `include_deletes` is set, because the engine's default flag set is
    `SKIP_UNMATCHED_DST` and a real comparison does not emit them.
    """
    elements_by_kind: dict[str, list[_FakeElement]] = {}
    for kind in kinds:
        source_records = {record.get_unique_id(): record for record in source.get_all(kind)}
        destination_records = {record.get_unique_id(): record for record in destination.get_all(kind)}
        elements: list[_FakeElement] = []
        for unique_id in sorted(source_records):
            record = source_records[unique_id]
            peer = destination_records.get(unique_id)
            elements.append(
                _FakeElement(
                    kind=kind,
                    name=unique_id,
                    keys=record.get_identifiers(),
                    source_attrs=record.get_attrs(),
                    dest_attrs=None if peer is None else peer.get_attrs(),
                )
            )
        if include_deletes:
            for unique_id in sorted(set(destination_records) - set(source_records)):
                record = destination_records[unique_id]
                elements.append(
                    _FakeElement(
                        kind=kind,
                        name=unique_id,
                        keys=record.get_identifiers(),
                        source_attrs=None,
                        dest_attrs=record.get_attrs(),
                    )
                )
        elements_by_kind[kind] = elements
    return _FakeDiff(elements_by_kind)


# ---------------------------------------------------------------------------------------
# Shared builders: a real parsed configuration, a Potenda with a run identity, a plan run
# ---------------------------------------------------------------------------------------


def mapping_entry(name: str, *, identifiers: Sequence[str], fields: Mapping[str, str | None]) -> SchemaMappingModel:
    """One `schema_mapping` entry; a field's value is its `reference`, or `None` if direct."""
    return SchemaMappingModel(
        name=name,
        mapping=name.lower(),
        identifiers=list(identifiers),
        fields=[
            SchemaMappingField(name=field, mapping=field, reference=reference) for field, reference in fields.items()
        ],
    )


def qualified_mapping() -> list[SchemaMappingModel]:
    """The four kinds' mapping, mirroring the shapes on the qualified path.

    `LocationRack.site` and `DcimDevice.rack` are identity-bearing references, so a
    `DcimDevice` identity nests a rack pair which itself nests a site pair — AD043's two
    levels. `DcimDevice.tags` is the cardinality-many reference.
    """
    return [
        mapping_entry(
            "BuiltinTag",
            identifiers=["name"],
            fields={"name": None, "description": None, "slug": None},
        ),
        mapping_entry("LocationSite", identifiers=["name"], fields={"name": None}),
        mapping_entry("LocationRack", identifiers=["name", "site"], fields={"name": None, "site": "LocationSite"}),
        mapping_entry(
            "DcimDevice",
            identifiers=["name", "rack"],
            fields={"name": None, "rack": "LocationRack", "tags": "BuiltinTag", "model": None},
        ),
    ]


def build_config(
    *,
    name: str = SYNC_NAME,
    order: Sequence[str] = KINDS,
    schema_mapping: Sequence[SchemaMappingModel] | None = None,
) -> SyncInstance:
    """A real parsed configuration — `write_plan_artifact` returns `None` without one."""
    return SyncInstance(
        name=name,
        directory="/nonexistent/examples/demo",
        source=SyncAdapter(name="netbox", settings={"url": "https://source.invalid"}),
        destination=SyncAdapter(name="infrahub", settings={"url": "https://destination.invalid"}),
        order=list(order),
        schema_mapping=list(qualified_mapping() if schema_mapping is None else schema_mapping),
    )


def resolver(*, tiers: Sequence[set[str]] | None = None, top_level: Sequence[str] = KINDS) -> Callable[[str], int]:
    """The one-argument tier resolver the derivation functions take."""
    return partial(tier_of, tiers=tiers, top_level=list(top_level))


def build_potenda(  # noqa: PLR0913 — one parameter per axis a plan run varies on
    *,
    config: SyncInstance,
    source: _FakeAdapter,
    destination: _FakeAdapter,
    run_id: str,
    top_level: Sequence[str] = KINDS,
    tiers: list[set[str]] | None = None,
    cls: type[Potenda] = Potenda,
) -> Potenda:
    """Build a Potenda with a real run identity under the test's cache root.

    The run directory is `cache_root_for(config.name) / run_id`, which is where
    `read_saved_plan` looks for it, so a plan written here is reviewable by run identifier
    without the test restating the layout.
    """
    directory = cache_root_for(config.name) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    destination.run_dir = directory
    return cls(
        source=source,  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=config,
        top_level=list(top_level),
        tiers=tiers,
        run_dir=directory,
        run_id=run_id,
        show_progress=False,
        concurrent_load=False,
    )


def run_plan(potenda: Potenda) -> None:
    """Drive one non-tier plan run: load both sides, compare, write both representations.

    `write_plan` is the real single-diff plan path — it writes `plan.parquet` unchanged and
    the saved artifact beside it — so this is the pathway a `diff` run takes, not a
    shortcut into the artifact writer.
    """
    potenda.load_both_sides()
    potenda.write_plan(potenda.diff())


def pin_extraction_decisions(monkeypatch: pytest.MonkeyPatch, decisions: Sequence[bool]) -> None:
    """Pin `should_use_incremental`'s answer, one entry per side load, in call order.

    `should_use_incremental` is the function whose answer *defines* the extraction mode:
    `Potenda.load_one_side` records `_side_full_extract[side] = not use_inc` from it
    (`infrahub_sync/potenda/__init__.py:198-213`). Pinning it therefore pins the mode
    through the real code path rather than by assigning the flag the code is supposed to
    set. It takes no `side` argument, so the pinning is by call order — deterministic here
    because every Potenda in this module is built with `concurrent_load=False`, which loads
    A then B (`:277-280`). Callers assert the resulting per-side flags anyway.
    """
    remaining = list(decisions)

    def _decide(**_kwargs: object) -> bool:
        if not remaining:
            msg = "should_use_incremental was called more often than this test pinned decisions for"
            raise AssertionError(msg)
        return remaining.pop(0)

    monkeypatch.setattr(incremental_module, "should_use_incremental", _decide)


def plan_run_dir(potenda: Potenda) -> Path:
    """The run directory a plan run wrote into, narrowed from `Potenda.run_dir`'s optional."""
    directory = potenda.run_dir
    assert directory is not None, "the Potenda under test was built without a run directory"
    return directory


def read_manifest(run_directory: Path) -> dict[str, Any]:
    """The manifest as written, read back as a mapping."""
    return json.loads((run_directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))


def read_operations_bytes(run_directory: Path) -> bytes:
    """`operations.jsonl`'s exact bytes."""
    return (run_directory / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()


def masked(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest with SC-006's two masked fields removed."""
    return {key: value for key, value in manifest.items() if key not in SC006_MASKED_FIELDS}


def derive_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Warning-or-worse messages emitted by the derivation module."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == DERIVE_LOGGER and record.levelno >= logging.WARNING
    ]


def operation_for(operations: Sequence[PlannedOperation], kind: str) -> PlannedOperation:
    """The single operation for `kind`, asserting there is exactly one."""
    matching = [operation for operation in operations if operation.kind == kind]
    assert len(matching) == 1, f"expected exactly one {kind} operation, got {len(matching)}"
    return matching[0]


@pytest.fixture(autouse=True)
def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every cache-root lookup at the test's own directory.

    `cache_root_for` honours `INFRAHUB_SYNC_CACHE_DIR`, and the engine writes the run-count
    and rowcount sidecars there, so without this a plan run would write into the working
    directory.
    """
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


# ---------------------------------------------------------------------------------------
# Fixtures for the source and destination sides
# ---------------------------------------------------------------------------------------


def qualified_source() -> _FakeAdapter:
    """A source holding one object of each kind, plus the two tags a device references."""
    return _FakeAdapter(
        "source",
        [
            _FakeRecord("BuiltinTag", {"name": "prod"}, {"description": "production", "slug": "prod"}),
            _FakeRecord("BuiltinTag", {"name": "alpha"}, {"description": "alpha", "slug": "alpha"}),
            _FakeRecord("BuiltinTag", {"name": "zeta"}, {"description": "zeta", "slug": "zeta"}),
            _FakeRecord("LocationSite", {"name": "hq"}, {}),
            _FakeRecord("LocationRack", {"name": "r1", "site": "hq"}, {}),
            _FakeRecord(
                "DcimDevice",
                {"name": "d1", "rack": "r1__hq"},
                {"model": "c9300", "tags": ["zeta", "alpha"]},
            ),
        ],
    )


def destination_with_orphan(*, schema: Mapping[str, Any] | None = None) -> _FakeAdapter:
    """A destination holding one object the source does not — the one derived delete."""
    return _FakeAdapter(
        "destination",
        [_FakeRecord("BuiltinTag", {"name": "stale"}, {"description": "left over", "slug": "stale"})],
        schema=schema,
    )


# =======================================================================================
# T036 — derivation: identity, payload, relationship references, deletes recorded once
# =======================================================================================


def test_payload_is_the_union_of_identity_keys_and_source_attributes() -> None:
    """AD042: the identity components are in the payload, so the upsert can key itself."""
    source = qualified_source()
    element = _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        # Exactly what the real comparison produces: `get_attrs()` excludes the identifiers.
        source_attrs={"description": "production", "slug": "prod"},
    )

    operations = operations_from_diff(
        _FakeDiff({"BuiltinTag": [element]}),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=source,
    )

    operation = operation_for(operations, "BuiltinTag")
    assert operation.action == "create"
    assert operation.identity == {"name": "prod"}
    assert operation.payload == {"name": "prod", "description": "production", "slug": "prod"}


def test_payload_is_not_narrowed_to_the_attribute_diff() -> None:
    """The payload is the full mapped attribute set, not `get_attrs_diffs`' changed subset."""
    element = _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        source_attrs={"description": "production", "slug": "prod"},
        dest_attrs={"description": "stale", "slug": "prod"},
    )
    # The narrow view `plan.parquet` records: one attribute, because one changed.
    assert element.get_attrs_diffs() == {"-": {"description": "stale"}, "+": {"description": "production"}}

    operations = operations_from_diff(
        _FakeDiff({"BuiltinTag": [element]}),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    operation = operation_for(operations, "BuiltinTag")
    assert operation.action == "update"
    assert operation.payload == {"name": "prod", "description": "production", "slug": "prod"}


def test_every_identity_key_is_in_the_payload_or_a_relationship_reference() -> None:
    """No identity component ends up in neither place — AD042's whole point.

    Asserted against elements whose `source_attrs` deliberately omit the identifiers, since
    that is what the comparison engine actually produces: a derivation that took its payload
    from `source_attrs` alone would leave `name` unaccounted for here rather than at a live
    destination.
    """
    diff = _FakeDiff(
        {
            "BuiltinTag": [
                _FakeElement(kind="BuiltinTag", name="prod", keys={"name": "prod"}, source_attrs={"slug": "prod"})
            ],
            "LocationRack": [
                # `site` is an identity component *and* a reference: it belongs in the
                # identity and in the relationship set, and in the payload it must not be.
                _FakeElement(
                    kind="LocationRack",
                    name="r1__hq",
                    keys={"name": "r1", "site": "hq"},
                    source_attrs={},
                )
            ],
        }
    )

    operations = operations_from_diff(
        diff,
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    assert len(operations) == 2
    for operation in operations:
        payload = operation.payload or {}
        referenced = {reference.field for reference in operation.relationships or ()}
        unaccounted = sorted(key for key in operation.identity if key not in payload and key not in referenced)
        assert unaccounted == [], f"{operation.kind} leaves identity keys {unaccounted} in neither place"

    rack = operation_for(operations, "LocationRack")
    assert set(rack.identity) == {"name", "site"}
    assert rack.payload == {"name": "r1"}
    assert [reference.field for reference in rack.relationships or ()] == ["site"]


def test_relationship_reference_records_peer_kind_and_peer_identity() -> None:
    """A reference names its peer by kind and identity, never by a unique-id string."""
    operations = operations_from_diff(
        _FakeDiff(
            {
                "LocationRack": [
                    _FakeElement(
                        kind="LocationRack",
                        name="r1__hq",
                        keys={"name": "r1", "site": "hq"},
                        source_attrs={},
                    )
                ]
            }
        ),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    reference = (operation_for(operations, "LocationRack").relationships or [])[0]
    assert reference.field == "site"
    assert reference.peer_kind == "LocationSite"
    assert reference.cardinality == "one"
    assert reference.peers == [{"name": "hq"}]


def test_cardinality_many_peers_are_canonically_ordered() -> None:
    """A many reference's peers are ordered by peer identity, not by source order."""
    operations = operations_from_diff(
        _FakeDiff(
            {
                "DcimDevice": [
                    _FakeElement(
                        kind="DcimDevice",
                        name="d1__r1__hq",
                        keys={"name": "d1", "rack": "r1__hq"},
                        # Source order is deliberately reversed relative to canonical order.
                        source_attrs={"model": "c9300", "tags": ["zeta", "alpha"]},
                    )
                ]
            }
        ),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    references = {
        reference.field: reference for reference in operation_for(operations, "DcimDevice").relationships or []
    }
    assert references["tags"].cardinality == "many"
    assert references["tags"].peers == [{"name": "alpha"}, {"name": "zeta"}]


def test_nested_peer_identity_component_is_a_pair_at_two_levels() -> None:
    """AD043 recurses: a peer identity component that is itself a reference nests a pair."""
    operations = operations_from_diff(
        _FakeDiff(
            {
                "DcimDevice": [
                    _FakeElement(
                        kind="DcimDevice",
                        name="d1__r1__hq",
                        keys={"name": "d1", "rack": "r1__hq"},
                        source_attrs={"model": "c9300", "tags": []},
                    )
                ]
            }
        ),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    operation = operation_for(operations, "DcimDevice")
    assert operation.identity == {
        "name": "d1",
        "rack": {
            "peer_kind": "LocationRack",
            "identity": {
                "name": "r1",
                "site": {"peer_kind": "LocationSite", "identity": {"name": "hq"}},
            },
        },
    }
    # And no consumer is ever handed a unique-id string to split on `__`.
    assert b"r1__hq" not in canonical_json_bytes(operation.identity)


def test_a_delete_element_in_the_diff_is_not_recorded_twice() -> None:
    """FR-015: deletes come from `derive_deletes` alone, so the diff's delete is skipped."""
    source = qualified_source()
    destination = destination_with_orphan()
    config = build_config()
    resolve = resolver()
    # A comparison that *does* emit destination-only objects, so the skip is exercised.
    diff = diff_between(source=source, destination=destination, kinds=KINDS, include_deletes=True)
    assert any(element.action == "delete" for elements in diff.children.values() for element in elements.values()), (
        "fixture error: the comparison carried no delete element to skip"
    )

    from_diff = operations_from_diff(diff, config=config, tier_of=resolve, source_adapter=source)
    derived = derive_deletes(
        kinds=KINDS,
        source_adapter=source,
        destination_adapter=destination,
        config=config,
        tier_of=resolve,
        destination_full_extract=True,
    )

    assert [operation.action for operation in from_diff if operation.action == "delete"] == []
    combined = [*from_diff, *derived]
    deletes = [operation for operation in combined if operation.action == "delete"]
    assert len(deletes) == 1
    assert deletes[0].identity == {"name": "stale"}
    identifiers = [operation.operation_id for operation in combined]
    assert len(identifiers) == len(set(identifiers))


def test_derived_deletes_carry_identifiers_and_no_payload() -> None:
    """FR-028.1: a delete records its destination identity and nothing else."""
    source = qualified_source()
    destination = destination_with_orphan()

    derived = derive_deletes(
        kinds=KINDS,
        source_adapter=source,
        destination_adapter=destination,
        config=build_config(),
        tier_of=resolver(),
        destination_full_extract=True,
    )

    assert len(derived) == 1
    delete = derived[0]
    assert delete.action == "delete"
    assert delete.kind == "BuiltinTag"
    assert delete.identity == {"name": "stale"}
    assert delete.payload is None
    assert delete.relationships is None


# =======================================================================================
# T037 — SC-017: the delete-computation record, full versus incremental destination
# =======================================================================================


def test_delete_computation_record_distinguishes_full_from_incremental_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-017: the same input yields deletes on a full destination extract and none on an
    incremental one, and the incremental plan says so rather than reading as "no deletes".

    The apply-side form of the last assertion — `summary["skipped_delete_count"] == 0` on
    the run record — is asserted at T054 and T065, on the Phase E/F apply path where an
    apply exists. What is decidable here is the value that count is derived from: a plan
    with no delete operation has nothing to skip, so no phantom delete can inflate it.
    """
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan()

    pin_extraction_decisions(monkeypatch, [False, False])
    full = build_potenda(config=config, source=source, destination=destination, run_id="20260726T1000-aaaaaaaa")
    run_plan(full)

    pin_extraction_decisions(monkeypatch, [False, True])
    incremental = build_potenda(config=config, source=source, destination=destination, run_id="20260726T1100-bbbbbbbb")
    run_plan(incremental)

    # The extraction mode each run actually took, per side.
    assert full._side_full_extract == {"A": True, "B": True}
    assert incremental._side_full_extract == {"A": True, "B": False}

    full_manifest = read_manifest(plan_run_dir(full))
    incremental_manifest = read_manifest(plan_run_dir(incremental))
    assert full_manifest["delete_operations_computed"] is True
    assert incremental_manifest["delete_operations_computed"] is False

    full_plan = read_saved_plan(sync_name=config.name, run_id="20260726T1000-aaaaaaaa", config=config)
    incremental_plan = read_saved_plan(sync_name=config.name, run_id="20260726T1100-bbbbbbbb", config=config)

    assert full_plan.summary().by_action.get("delete") == 1
    assert full_plan.summary().delete_operations_computed is True
    assert full_plan.summary().deletes_not_executed == 1

    # Both review depths read the disclosure from the plan they are handed: the summary
    # depth from `summary()`, the per-object depth from the manifest the same object
    # carries. Neither can render "no deletes" for a run that never computed them (AD056).
    assert incremental_plan.summary().delete_operations_computed is False
    assert incremental_plan.manifest.delete_operations_computed is False
    assert "delete" not in incremental_plan.summary().by_action
    assert [operation.action for operation in incremental_plan.operations() if operation.action == "delete"] == []
    # The value AD055's skipped-delete count is derived from: nothing to skip.
    assert incremental_plan.summary().deletes_not_executed == 0


# =======================================================================================
# T038 — tier assignment, with computed tiers and with an explicit order
# =======================================================================================


@pytest.mark.parametrize(
    ("tiers", "expected"),
    [
        pytest.param(
            [{"BuiltinTag", "LocationSite"}, {"LocationRack"}, {"DcimDevice"}],
            {"BuiltinTag": 0, "LocationSite": 0, "LocationRack": 1, "DcimDevice": 2},
            id="computed-tiers",
        ),
        pytest.param(
            None,
            {"BuiltinTag": 0, "LocationSite": 1, "LocationRack": 2, "DcimDevice": 3},
            id="explicit-order",
        ),
    ],
)
def test_tier_is_recorded_on_every_operation(
    monkeypatch: pytest.MonkeyPatch,
    tiers: list[set[str]] | None,
    expected: dict[str, int],
) -> None:
    """FR-028.1 / PD-007: the tier is the containing tier set's index, or the order index."""
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan()

    pin_extraction_decisions(monkeypatch, [False, False])
    potenda = build_potenda(
        config=config,
        source=source,
        destination=destination,
        run_id="20260726T1200-cccccccc",
        tiers=tiers,
    )
    run_plan(potenda)

    lines = [json.loads(line) for line in read_operations_bytes(plan_run_dir(potenda)).splitlines()]
    assert lines, "the plan artifact recorded no operations"
    for record in lines:
        assert "tier" in record, f"operation {record['operation_id']} carries no tier"
        assert record["tier"] == expected[record["kind"]]
    # Including the derived delete, which takes the same resolver.
    assert {record["kind"] for record in lines if record["action"] == "delete"} == {"BuiltinTag"}


# =======================================================================================
# T039 — SC-014: the convergence-key warning, all four FR-024 arms
# =======================================================================================


def schema_node(
    *, human_friendly_id: list[str] | None, uniqueness_constraints: list[list[str]] | None
) -> SimpleNamespace:
    """A destination schema node exposing only the two fields FR-024 reads."""
    return SimpleNamespace(human_friendly_id=human_friendly_id, uniqueness_constraints=uniqueness_constraints)


def test_warns_when_the_destination_kind_declares_no_human_friendly_id(caplog: pytest.LogCaptureFixture) -> None:
    """FR-024 condition 1a: no HFID at all."""
    schema = {"BuiltinTag": schema_node(human_friendly_id=None, uniqueness_constraints=[["name__value"]])}
    operations = operations_from_diff(
        _FakeDiff(
            {"BuiltinTag": [_FakeElement(kind="BuiltinTag", name="prod", keys={"name": "prod"}, source_attrs={})]}
        ),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        warn_missing_convergence_key(destination=_FakeAdapter("destination", schema=schema), operations=operations)

    messages = derive_warnings(caplog)
    assert len(messages) == 1
    assert "BuiltinTag" in messages[0]
    assert "no human-friendly ID" in messages[0]


def test_warns_when_the_plan_identity_misses_a_human_friendly_id_component(caplog: pytest.LogCaptureFixture) -> None:
    """FR-024 condition 1b: an HFID component the plan's identity does not supply."""
    schema = {
        "LocationRack": schema_node(
            human_friendly_id=["name__value", "site__name__value"],
            uniqueness_constraints=[["name__value"]],
        )
    }
    operations = operations_from_diff(
        _FakeDiff(
            {
                "LocationRack": [
                    # Identity carries `name` only, so `site__name__value` is unsupplied.
                    _FakeElement(kind="LocationRack", name="r1", keys={"name": "r1"}, source_attrs={})
                ]
            }
        ),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        warn_missing_convergence_key(destination=_FakeAdapter("destination", schema=schema), operations=operations)

    messages = derive_warnings(caplog)
    assert len(messages) == 1
    assert "LocationRack" in messages[0]
    assert "site__name__value" in messages[0]


@pytest.mark.parametrize(
    "uniqueness_constraints",
    [
        pytest.param([], id="no-constraint-at-all"),
        pytest.param([["name__value", "site__name__value"]], id="constraint-not-covered-by-the-identity"),
    ],
)
def test_warns_when_no_uniqueness_constraint_covers_the_plan_identity(
    caplog: pytest.LogCaptureFixture,
    uniqueness_constraints: list[list[str]],
) -> None:
    """FR-024 condition 2 — the brief's own condition, and a different one.

    The kind's `human_friendly_id` is **complete** here, so condition 1 stays silent: an
    implementation that had substituted condition 1 for condition 2 emits nothing and this
    case fails on the count.
    """
    schema = {
        "BuiltinTag": schema_node(human_friendly_id=["name__value"], uniqueness_constraints=uniqueness_constraints)
    }
    operations = operations_from_diff(
        _FakeDiff(
            {"BuiltinTag": [_FakeElement(kind="BuiltinTag", name="prod", keys={"name": "prod"}, source_attrs={})]}
        ),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
    )

    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        warn_missing_convergence_key(destination=_FakeAdapter("destination", schema=schema), operations=operations)

    messages = derive_warnings(caplog)
    assert len(messages) == 1
    assert "BuiltinTag" in messages[0]
    assert "uniqueness constraint" in messages[0]
    assert "human-friendly" not in messages[0]


def test_the_convergence_warning_stays_out_of_the_manifest_and_the_run_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SC-014: warning only — never a manifest field, and never a failed plan run."""
    schema = {kind: schema_node(human_friendly_id=None, uniqueness_constraints=[]) for kind in KINDS}
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan(schema=schema)

    pin_extraction_decisions(monkeypatch, [False, False])
    potenda = build_potenda(config=config, source=source, destination=destination, run_id="20260726T1300-dddddddd")
    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        run_plan(potenda)

    assert derive_warnings(caplog), "the fixture's schema should have produced warnings"
    manifest = read_manifest(plan_run_dir(potenda))
    assert set(manifest) == MANIFEST_KEYS
    assert manifest["operations_count"] > 0


def test_a_destination_exposing_no_schema_is_skipped_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AD052: every adapter but Infrahub exposes no `schema`, and `diff` must still work.

    A regression assertion: derivation is newly wired onto the non-mutating path for every
    destination and AD047 makes its failures fatal, so an unguarded `destination.schema`
    read would break the eight adapters that compare fine today. Removing the guard raises
    `AttributeError` out of `warn_missing_convergence_key` and fails here.
    """
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan()
    assert not hasattr(destination, "schema"), "fixture error: the destination must expose no schema"

    pin_extraction_decisions(monkeypatch, [False, False])
    potenda = build_potenda(config=config, source=source, destination=destination, run_id="20260726T1400-eeeeeeee")
    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        run_plan(potenda)

    assert derive_warnings(caplog) == []
    manifest = read_manifest(plan_run_dir(potenda))
    assert set(manifest) == MANIFEST_KEYS
    assert manifest["operations_count"] > 0


# =======================================================================================
# T040 — AD039: the tier branch computes every diff, writes the artifact, then executes
# =======================================================================================


class _RecordingPotenda(Potenda):
    """Potenda with the plan-run call sequence recorded, in order."""

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 — passed straight through to Potenda
        super().__init__(**kwargs)
        self.events: list[tuple[str, Any]] = []

    def diff(self):
        result = super().diff()
        self.events.append(("diff", tuple(sorted(result.children))))
        return result

    def write_plan_artifact(self, diffs):
        self.events.append(("write_plan_artifact", len(diffs)))
        return super().write_plan_artifact(diffs)

    def sync(self, diff=None):
        self.events.append(("sync", () if diff is None else tuple(sorted(diff.children))))
        return super().sync(diff=diff)


def test_the_tier_branch_computes_every_diff_and_writes_the_artifact_before_the_first_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AD039: two loops, and the narrowing sits in the compute loop.

    The single sequence equality below carries four claims at once: every tier's diff is
    computed before the artifact is written, the artifact is written before the first
    `sync_from`, each retained diff holds **exactly** its own tier's kinds — which is what
    proves the `top_level` narrowing was applied around `diff()` rather than moved into the
    execution loop — and the execution order is unchanged, tier by tier. A call-order-only
    assertion would pass just as happily against six identical full-destination diffs.
    """
    tiers = [{"BuiltinTag", "LocationSite"}, {"LocationRack"}, {"DcimDevice"}]
    config = build_config()
    source = qualified_source()
    destination = _FakeAdapter("destination")

    pin_extraction_decisions(monkeypatch, [False, False])
    potenda = build_potenda(
        config=config,
        source=source,
        destination=destination,
        run_id="20260726T1500-ffffffff",
        tiers=tiers,
        cls=_RecordingPotenda,
    )
    potenda.sync_in_tiers(parallel=True)

    assert potenda.events == [  # ty: ignore[unresolved-attribute]
        ("diff", ("BuiltinTag", "LocationSite")),
        ("diff", ("LocationRack",)),
        ("diff", ("DcimDevice",)),
        ("write_plan_artifact", 3),
        ("sync", ("BuiltinTag", "LocationSite")),
        ("sync", ("LocationRack",)),
        ("sync", ("DcimDevice",)),
    ]
    # The same claim read off the destination adapter rather than the engine wrapper: every
    # write saw a manifest already on disk, and the executed set per tier is the tier's.
    assert destination.sync_calls == [("BuiltinTag", "LocationSite"), ("LocationRack",), ("DcimDevice",)]
    assert destination.manifest_present_at_sync == [True, True, True]

    lines = [json.loads(line) for line in read_operations_bytes(plan_run_dir(potenda)).splitlines()]
    identifiers = [record["operation_id"] for record in lines]
    # One operation per source object, recorded once — not once per tier.
    assert len(identifiers) == 6
    assert len(identifiers) == len(set(identifiers))
    assert read_manifest(plan_run_dir(potenda))["operations_count"] == 6


# =======================================================================================
# T041 — SC-006 / Trap 1: two plan runs over identical input encode identically
# =======================================================================================


def _pinned_plan_run(  # noqa: PLR0913 — one parameter per axis a pinned plan run varies on
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: SyncInstance,
    source: _FakeAdapter,
    destination: _FakeAdapter,
    run_id: str,
    decisions: Sequence[bool],
) -> Potenda:
    """One plan run with the extraction mode pinned for both sides, in load order."""
    pin_extraction_decisions(monkeypatch, decisions)
    potenda = build_potenda(config=config, source=source, destination=destination, run_id=run_id)
    run_plan(potenda)
    return potenda


def test_two_plan_runs_over_identical_input_encode_identically(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-006, with Trap 1 disarmed: pin the mode on both runs and both sides, assert the
    pinning held, and only then compare bytes.

    `delete_operations_computed` sits inside `plan_checksum` and is **not** one of SC-006's
    two masked fields, so a run that silently switched to an incremental destination extract
    would differ for a reason that has nothing to do with encoding determinism. The pinning
    assertion below is what keeps this test measuring what SC-006 is about.
    """
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan()

    first = _pinned_plan_run(
        monkeypatch,
        config=config,
        source=source,
        destination=destination,
        run_id="20260726T1600-11111111",
        decisions=[False, False],
    )
    second = _pinned_plan_run(
        monkeypatch,
        config=config,
        source=source,
        destination=destination,
        run_id="20260726T1700-22222222",
        decisions=[False, False],
    )

    first_manifest = read_manifest(plan_run_dir(first))
    second_manifest = read_manifest(plan_run_dir(second))

    # The pinning held — asserted BEFORE anything is compared.
    assert first._side_full_extract == second._side_full_extract == {"A": True, "B": True}
    assert first_manifest["delete_operations_computed"] == second_manifest["delete_operations_computed"] is True

    assert read_operations_bytes(plan_run_dir(first)) == read_operations_bytes(plan_run_dir(second))
    assert masked(first_manifest) == masked(second_manifest)
    # And the mask is doing work rather than the two runs being the same directory twice.
    assert first_manifest["run_id"] != second_manifest["run_id"]
    assert first_manifest != second_manifest


def test_two_plan_runs_at_different_extraction_modes_are_expected_to_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for Trap 1: the pinning is load-bearing, not incidental.

    Without it the comparison above could pass by accident on a pair of runs that happened
    to take the same extraction mode. Here the destination side differs between the two
    runs and the artifacts differ accordingly — so if pinning were dropped, SC-006 would be
    measuring extraction-mode luck rather than encoding determinism.
    """
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan()

    full = _pinned_plan_run(
        monkeypatch,
        config=config,
        source=source,
        destination=destination,
        run_id="20260726T1800-33333333",
        decisions=[False, False],
    )
    incremental = _pinned_plan_run(
        monkeypatch,
        config=config,
        source=source,
        destination=destination,
        run_id="20260726T1900-44444444",
        decisions=[False, True],
    )

    assert full._side_full_extract == {"A": True, "B": True}
    assert incremental._side_full_extract == {"A": True, "B": False}

    full_manifest = read_manifest(plan_run_dir(full))
    incremental_manifest = read_manifest(plan_run_dir(incremental))
    assert full_manifest["delete_operations_computed"] != incremental_manifest["delete_operations_computed"]
    assert read_operations_bytes(plan_run_dir(full)) != read_operations_bytes(plan_run_dir(incremental))
    assert masked(full_manifest) != masked(incremental_manifest)


# =======================================================================================
# T082-T085 append below this line. The fakes and builders above are the shared surface.
# =======================================================================================
