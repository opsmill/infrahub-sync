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
import re
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
import yaml
from diffsync.exceptions import ObjectNotFound
from typer.testing import CliRunner

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncInstance
from infrahub_sync import cli as cli_module
from infrahub_sync.cache import incremental as incremental_module
from infrahub_sync.cache.cursors import CursorTier
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.cli import app
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import (
    derive_deletes,
    operations_from_diff,
    reference_candidates,
    tier_of,
    warn_missing_convergence_key,
)
from infrahub_sync.plan.errors import (
    DuplicateOperationIdError,
    PeerNotFoundError,
    PlanArtifactError,
    SourcePeerUnresolvedError,
    UnformableDestinationIdentityError,
    UnserializablePayloadValueError,
)
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import SC006_MASKED_FIELDS
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME
from infrahub_sync.potenda import Potenda

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from click.testing import Result

    from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

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
        # `diff` and `sync` both log `Diff.str()` once the plan is written
        # (`infrahub_sync/cli.py:154`, `:274`), so the CLI cases below need it. Bound here
        # rather than declared as a method named `str`, which would shadow the builtin for
        # every annotation in this class body.
        self.str = partial(render_diff, self)

    def has_diffs(self) -> bool:
        return any(element.action for elements in self.children.values() for element in elements.values())


def render_diff(diff: _FakeDiff, indent: int = 0) -> str:
    """One line per actionable element — what `Diff.str()` returns, in miniature."""
    pad = " " * indent
    return "\n".join(
        f"{pad}{kind}: {element.action} {name}"
        for kind, elements in sorted(diff.children.items())
        for name, element in sorted(elements.items())
        if element.action
    )


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


class ConvergenceCase(NamedTuple):
    """One of SC-014's three cases, as a schema fixture plus what its warning must say."""

    run_id: str
    schema: dict[str, Any]
    fragments: tuple[str, ...]


# SC-014's evidence procedure is "three plan runs, one per case, asserting the warning's
# content and the run's successful outcome". Each schema below declares exactly **one** kind,
# so the kinds the plan also carries are skipped and the case's own warning is the only one
# emitted — which is what lets the count be asserted rather than a substring search that any
# extra warning would also satisfy.
SC014_CASES: dict[str, ConvergenceCase] = {
    "no-human-friendly-id": ConvergenceCase(
        run_id="20260727T0300-11111111",
        schema={"BuiltinTag": schema_node(human_friendly_id=None, uniqueness_constraints=[["name__value"]])},
        fragments=("BuiltinTag", "no human-friendly ID"),
    ),
    "identity-misses-a-component": ConvergenceCase(
        run_id="20260727T0301-22222222",
        schema={
            "BuiltinTag": schema_node(
                # `slug` is a mapped field but not one of the kind's identifiers, so the plan's
                # identity cannot supply this component.
                human_friendly_id=["name__value", "slug__value"],
                uniqueness_constraints=[["name__value"]],
            )
        },
        fragments=("BuiltinTag", "does not supply every human-friendly-ID component", "slug__value"),
    ),
    "no-covering-uniqueness-constraint": ConvergenceCase(
        run_id="20260727T0302-33333333",
        schema={"BuiltinTag": schema_node(human_friendly_id=["name__value"], uniqueness_constraints=[])},
        fragments=("BuiltinTag", "no uniqueness constraint"),
    ),
}


@pytest.mark.parametrize("case", list(SC014_CASES.values()), ids=list(SC014_CASES))
def test_each_convergence_key_case_warns_and_the_plan_run_still_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: ConvergenceCase,
) -> None:
    """T099 / SC-014: all three cases, each as a plan run, each asserted to succeed.

    The three content assertions elsewhere in this section call
    `warn_missing_convergence_key` directly — no plan run, no run outcome — so they evidence
    the message and nothing about the criterion's other half. SC-014's own evidence procedure
    is three **plan runs**, so each case is driven through the real `diff` command here and
    the successful outcome is read from the exit code and the recorded run state, not inferred
    from the absence of an exception.

    The run outcome is the point of the pairing: FR-024 raises this edge case from
    documentation to detection, and a detection that failed the run would make the plan
    unreachable for every kind whose destination schema happens to look like this.
    """
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan(schema=case.schema)

    potenda = build_potenda(config=config, source=source, destination=destination, run_id=case.run_id)
    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        result = invoke_command(
            "diff",
            config=config,
            potenda=potenda,
            project_dir=tmp_path / "project",
            monkeypatch=monkeypatch,
        )

    # The warning's content.
    messages = derive_warnings(caplog)
    assert len(messages) == 1, messages
    for fragment in case.fragments:
        assert fragment in messages[0], f"{fragment!r} missing from: {messages[0]}"

    # The run's successful outcome.
    assert result.exit_code == 0, result.output
    assert result.exception is None, f"an error escaped the plan run: {result.exception!r}"
    run_record = json.loads((plan_run_dir(potenda) / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "dry-run"
    manifest = read_manifest(plan_run_dir(potenda))
    assert set(manifest) == MANIFEST_KEYS, "the warning leaked into the manifest"
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
# T101 — FR-015: `sync` records deletes exactly as `diff` does, serial and tiered
# =======================================================================================


def _delete_records(potenda: Potenda) -> list[dict[str, Any]]:
    """Every recorded `delete` operation of a run's artifact, in stored order."""
    lines = [json.loads(line) for line in read_operations_bytes(plan_run_dir(potenda)).splitlines()]
    return [record for record in lines if record["action"] == "delete"]


def test_a_serial_and_a_tiered_sync_record_deletes_exactly_as_a_diff_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-015: the delete class is recorded, never executed — and identically in all three modes.

    This is structurally true today, because `write_plan` and the tier branch of
    `sync_in_tiers` are the only artifact-writing call sites and both go through
    `write_plan_artifact`. Structurally true and unasserted is exactly the shape that regresses
    silently: a second artifact-writing call site added later — a new execution mode, a
    resumed apply — would only have to forget `derive_deletes` to make `sync` record no delete
    at all, and FR-015's "explicit and reviewable" claim would be carried by nothing on the two
    paths an operator actually writes from.

    Asserted as the recorded delete **records**, not their count: a mode that recorded a delete
    for the wrong object, at the wrong tier, or with a differently derived identifier would
    keep the count and break the parity a reviewer relies on.
    """
    config = build_config()

    pin_extraction_decisions(monkeypatch, [False, False])
    dry_run = build_potenda(
        config=config,
        source=qualified_source(),
        destination=destination_with_orphan(),
        run_id="20260727T0400-11111111",
    )
    run_plan(dry_run)

    pin_extraction_decisions(monkeypatch, [False, False])
    serial = build_potenda(
        config=config,
        source=qualified_source(),
        destination=destination_with_orphan(),
        run_id="20260727T0401-22222222",
    )
    serial.sync_in_tiers(parallel=False)

    pin_extraction_decisions(monkeypatch, [False, False])
    tiered = build_potenda(
        config=config,
        source=qualified_source(),
        destination=destination_with_orphan(),
        run_id="20260727T0402-33333333",
        tiers=[{"BuiltinTag", "LocationSite"}, {"LocationRack"}, {"DcimDevice"}],
    )
    tiered.sync_in_tiers(parallel=True)

    # The precondition: the fixture's orphan gives all three runs a delete to record. Without
    # it every equality below would hold vacuously between three empty lists.
    recorded = _delete_records(dry_run)
    assert recorded, "the fixture's destination orphan produced no delete operation"
    assert {record["kind"] for record in recorded} == {"BuiltinTag"}

    assert _delete_records(serial) == recorded
    assert _delete_records(tiered) == recorded
    # And each mode recorded the delete class as computed, so review discloses it (AD056).
    for potenda in (dry_run, serial, tiered):
        assert read_manifest(plan_run_dir(potenda))["delete_operations_computed"] is True

    # Both sync modes really executed, and the artifact was already on disk when they did —
    # otherwise the parity above would be a parity between three dry runs (FR-001).
    assert dry_run.destination.sync_calls == []  # ty: ignore[unresolved-attribute]
    for potenda in (serial, tiered):
        assert potenda.destination.sync_calls  # ty: ignore[unresolved-attribute]
        assert all(potenda.destination.manifest_present_at_sync)  # ty: ignore[unresolved-attribute]


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

# ---------------------------------------------------------------------------------------
# Shared surface for T082-T085: a real CLI invocation, and a plan run that must fail
# ---------------------------------------------------------------------------------------

CLI_RUNNER = CliRunner()

# Typer renders `--help` through rich, which in some environments styles each hyphen of an
# option as its own ANSI span, so the literal flag string is absent from the raw output.
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")

# The adapter package, scanned by T085 so its fixture is tied to the eight adapters the
# regression is about rather than to one hand-written fake.
ADAPTERS_DIR = Path(__file__).resolve().parent.parent / "infrahub_sync" / "adapters"

# `rest_api_client.py` is a shared HTTP helper and `utils.py` a shared value helper;
# neither is an adapter.
NON_ADAPTER_MODULES = frozenset({"__init__", "rest_api_client", "utils"})


def strip_ansi(text: str) -> str:
    """Return `text` with ANSI SGR (colour) escape sequences removed."""
    return _ANSI_SGR_RE.sub("", text)


def write_config_file(config: SyncInstance, *, directory: Path) -> Path:
    """Write `config` out as `<directory>/config.yml`, where the CLI's loader finds it.

    The CLI resolves `--name`/`--directory` through `get_all_sync`, which parses every
    `config.yml` it globs, so the configuration a CLI case runs against has to exist on
    disk. Dumping the same `SyncInstance` the Potenda under test carries keeps the two from
    drifting — `directory` is excluded because `get_all_sync` supplies it from the file's
    own location.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.yml"
    body = config.model_dump(exclude={"directory"}, exclude_none=True)
    path.write_text(yaml.safe_dump(body, sort_keys=True), encoding="utf-8")
    return path


def invoke_command(  # noqa: PLR0913 — one parameter per axis a CLI case varies on
    command: str,
    *,
    config: SyncInstance,
    potenda: Potenda,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: Sequence[str] = (),
) -> Result:
    """Run a real `infrahub-sync <command>` against a real Potenda built on this module's fakes.

    Only adapter construction is substituted: `get_potenda_from_instance` is the seam the
    repository's other CLI tests use (`tests/test_cli_full_extract.py:64`), and everything
    the command does afterwards — the run file, `load_both_sides`, `diff`, `write_plan` and
    the artifact write — is the real code path. That is what makes the exit code these
    cases assert the exit code an operator would get.
    """
    write_config_file(config, directory=project_dir)

    def _fixed_potenda(**_kwargs: object) -> Potenda:
        return potenda

    monkeypatch.setattr(cli_module, "get_potenda_from_instance", _fixed_potenda)
    return CLI_RUNNER.invoke(app, [command, "--name", config.name, "--directory", str(project_dir), *extra])


def manifest_path(potenda: Potenda) -> Path:
    """Where the plan artifact's commit point would be for this run."""
    return plan_run_dir(potenda) / PLAN_DIR_NAME / MANIFEST_FILE_NAME


def failing_plan_run(  # noqa: PLR0913 — one parameter per axis a failing plan run varies on
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: SyncInstance,
    source: _FakeAdapter,
    destination: _FakeAdapter,
    run_id: str,
    error: type[PlanArtifactError],
) -> PlanArtifactError:
    """Drive one plan run that must fail, and assert it left no artifact behind.

    `manifest.json` is written last and is the artifact's commit point
    (`infrahub_sync/plan/writer.py:145-160`), so its absence is what "no partial artifact"
    means. `plan.parquet` is written first and unchanged (V23), so it is deliberately not
    asserted absent — the new artifact is the thing a refusal must not leave half-written.
    """
    pin_extraction_decisions(monkeypatch, [False, False])
    potenda = build_potenda(
        config=config,
        source=source,
        destination=destination,
        run_id=run_id,
        top_level=config.order,
    )
    with pytest.raises(error) as caught:
        run_plan(potenda)
    assert not manifest_path(potenda).exists(), "a failed derivation left a plan manifest behind"
    return caught.value


def references_by_field(operation: PlannedOperation) -> dict[str, RelationshipReference]:
    """An operation's relationship references, keyed by the field they belong to."""
    return {reference.field: reference for reference in operation.relationships or []}


# =======================================================================================
# T082 — AD046 / AD050: the peer kind is probed in the store, and both arms fail loudly
# =======================================================================================

# `DcimDevice.location` on the qualified path is declared twice with different references
# (`examples/netbox_to_infrahub/config.yml:212` -> LocationRack, `:254` -> LocationSite,
# V31). Sorted, because `reference_candidates` sorts and the probe order follows it.
LOCATION_CANDIDATES = ("LocationRack", "LocationSite")

DUPLICATE_ORDER: tuple[str, ...] = ("LocationSite", "LocationRack", "DcimDevice")


def duplicate_location_config(*references: str) -> SyncInstance:
    """A configuration declaring `DcimDevice` once per entry in `references`.

    Each entry gives `location` a different `reference`, so the mapping alone cannot say
    what kind a device's location is — which is the whole reason AD046 forbids reading the
    peer kind off the mapping and AD050 replaces it with a bounded store probe. Passing a
    single reference produces the one-candidate configuration.
    """
    return build_config(
        order=DUPLICATE_ORDER,
        schema_mapping=[
            mapping_entry("LocationSite", identifiers=["name"], fields={"name": None}),
            mapping_entry("LocationRack", identifiers=["name"], fields={"name": None}),
            *(
                mapping_entry(
                    "DcimDevice",
                    identifiers=["name", "location"],
                    fields={"name": None, "location": reference},
                )
                for reference in references
            ),
        ],
    )


def location_side(
    name: str,
    *,
    racks: Sequence[str] = (),
    sites: Sequence[str] = (),
    devices: Sequence[tuple[str, str]] = (),
) -> _FakeAdapter:
    """One side holding racks, sites, and devices located at a rack or a site by unique id."""
    return _FakeAdapter(
        name,
        [
            *(_FakeRecord("LocationRack", {"name": rack}) for rack in racks),
            *(_FakeRecord("LocationSite", {"name": site}) for site in sites),
            *(
                _FakeRecord("DcimDevice", {"name": device, "location": location}, {"model": "c9300"})
                for device, location in devices
            ),
        ],
    )


def derive_over(config: SyncInstance, source: _FakeAdapter) -> list[PlannedOperation]:
    """Derive operations for every source object of `config.order`, destination empty."""
    return operations_from_diff(
        diff_between(source=source, destination=_FakeAdapter("destination"), kinds=config.order),
        config=config,
        tier_of=resolver(top_level=config.order),
        source_adapter=source,
    )


def test_the_peer_kind_is_the_kind_that_actually_holds_the_peer() -> None:
    """AD046 / AD050 arm (a): the probe answers, and the mapping's `reference` cannot.

    Two `DcimDevice` entries declare `location` differently, one device is located at a
    rack and the other at a site. A derivation that read `peer_kind` off the mapping would
    have to pick one of the two declarations and would therefore label one of these two
    devices wrongly — whichever it picked. The probe assertion is separate and deliberate:
    with the *right* answer alone this case would also pass against an unprobed
    mapping read on a one-candidate set, so what is asserted is that the store was asked.
    """
    config = duplicate_location_config(*LOCATION_CANDIDATES)
    # Fixture precondition: the mapping really is ambiguous for this field.
    assert reference_candidates(config, "DcimDevice") == {"location": LOCATION_CANDIDATES}

    source = location_side("source", racks=["r1"], sites=["hq"], devices=[("d1", "r1"), ("d2", "hq")])
    operations = derive_over(config, source)

    devices = {operation.identity["name"]: operation for operation in operations if operation.kind == "DcimDevice"}
    assert set(devices) == {"d1", "d2"}

    rack_located = references_by_field(devices["d1"])["location"]
    site_located = references_by_field(devices["d2"])["location"]
    assert rack_located.peer_kind == "LocationRack"
    assert rack_located.peers == [{"name": "r1"}]
    assert site_located.peer_kind == "LocationSite"
    assert site_located.peers == [{"name": "hq"}]
    # The claim in one line: two peer kinds from one mapping field, so no single
    # mapping-declared value can produce this result.
    assert {rack_located.peer_kind, site_located.peer_kind} == set(LOCATION_CANDIDATES)

    # The identity carries the same probed kind, as a nested pair rather than a unique id.
    assert devices["d1"].identity["location"] == {"peer_kind": "LocationRack", "identity": {"name": "r1"}}
    assert devices["d2"].identity["location"] == {"peer_kind": "LocationSite", "identity": {"name": "hq"}}

    # The probe happened, for every candidate and both peers — asserted rather than
    # inferred from the answer (AD050).
    for unique_id in ("r1", "hq"):
        for candidate in LOCATION_CANDIDATES:
            assert (candidate, unique_id) in source.store.probes, (
                f"the store was never probed for {candidate} {unique_id!r}"
            )

    # And the two entries' operations stay distinguishable.
    assert devices["d1"].identity != devices["d2"].identity
    assert devices["d1"].operation_id != devices["d2"].operation_id


def test_a_peer_under_no_candidate_kind_fails_the_command_and_leaves_no_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AD050 arm (b): zero hits fails, naming kind, field, unique id and every candidate."""
    config = duplicate_location_config(*LOCATION_CANDIDATES)
    source = location_side("source", racks=["r1"], sites=["hq"], devices=[("d1", "ghost")])

    error = failing_plan_run(
        monkeypatch,
        config=config,
        source=source,
        destination=_FakeAdapter("destination"),
        run_id="20260726T2000-55555555",
        error=SourcePeerUnresolvedError,
    )

    message = str(error)
    for fragment in ("DcimDevice", "location", "ghost", *LOCATION_CANDIDATES):
        assert fragment in message
    assert error.next_action == SourcePeerUnresolvedError.ABSENT_NEXT_ACTION
    # Both candidates were probed before the refusal, so "not found" is a probed answer.
    for candidate in LOCATION_CANDIDATES:
        assert (candidate, "ghost") in source.store.probes


def test_a_peer_under_both_candidate_kinds_fails_rather_than_picking_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AD050 arm (c): more than one hit fails and names the match — no silent first pick."""
    config = duplicate_location_config(*LOCATION_CANDIDATES)
    # One unique id, two buckets: a rack and a site both named `dup`.
    source = location_side("source", racks=["dup"], sites=["dup"], devices=[("d1", "dup")])

    error = failing_plan_run(
        monkeypatch,
        config=config,
        source=source,
        destination=_FakeAdapter("destination"),
        run_id="20260726T2100-66666666",
        error=SourcePeerUnresolvedError,
    )

    message = str(error)
    for fragment in ("DcimDevice", "location", "dup", "more than one", *LOCATION_CANDIDATES):
        assert fragment in message
    assert error.next_action == SourcePeerUnresolvedError.AMBIGUOUS_NEXT_ACTION
    # Not the absent arm's remedy, which would send the operator to load a kind that is
    # already loaded twice over.
    assert error.next_action != SourcePeerUnresolvedError.ABSENT_NEXT_ACTION


def test_a_single_candidate_is_probed_and_never_used_as_a_fallback() -> None:
    """AD050's no-fallback rule: one declared candidate that does not hold the peer fails.

    Returning an unprobed sole candidate *is* the mapping-derived answer AD046 forbids, so
    the one-candidate set is probed like any other and its miss is a refusal rather than a
    default.
    """
    config = duplicate_location_config("LocationRack")
    assert reference_candidates(config, "DcimDevice") == {"location": ("LocationRack",)}
    # The peer exists — as a site, which is not the sole declared candidate.
    source = location_side("source", sites=["hq"], devices=[("d1", "hq")])

    with pytest.raises(SourcePeerUnresolvedError) as caught:
        derive_over(config, source)

    assert ("LocationRack", "hq") in source.store.probes, "the sole candidate was not probed"
    assert caught.value.next_action == SourcePeerUnresolvedError.ABSENT_NEXT_ACTION
    assert "LocationRack" in str(caught.value)


# =======================================================================================
# T083 — AD047 / AD071 / AD082: five derivation failures, fatal on `diff` as on `sync`
# =======================================================================================


class _ScriptedDiffDestination(_FakeAdapter):
    """A destination whose comparison result is fixed by the test.

    Three of T083's five failures cannot be produced by comparing two stores: an identity
    attribute that resolves to nothing, a payload value outside the canonical-value table,
    and two operations sharing an identifier all need an element shape a real comparison
    would not build from well-formed records. Scripting the `Diff` keeps everything
    downstream of the comparison — the real `write_plan`, the real derivation, the real
    writer — on the path an operator's `diff` takes.
    """

    def __init__(self, name: str, *, scripted: _FakeDiff) -> None:
        super().__init__(name)
        self.scripted = scripted

    def diff_from(self, source: _FakeAdapter, **_kwargs: object) -> _FakeDiff:  # noqa: ARG002
        return self.scripted


class _DerivationFailure(NamedTuple):
    """One FR-030 failure: the input that provokes it and the message it must carry."""

    config: SyncInstance
    source: _FakeAdapter
    destination: _FakeAdapter
    error: type[PlanArtifactError]
    next_action: str
    message_contains: tuple[str, ...]


def _unformable_identity_failure() -> _DerivationFailure:
    """An identity attribute that resolves to no value at all."""
    diff = _FakeDiff(
        {
            "BuiltinTag": [
                _FakeElement(kind="BuiltinTag", name="prod", keys={"name": None}, source_attrs={"slug": "prod"})
            ]
        }
    )
    return _DerivationFailure(
        config=build_config(),
        source=qualified_source(),
        destination=_ScriptedDiffDestination("destination", scripted=diff),
        error=UnformableDestinationIdentityError,
        next_action=UnformableDestinationIdentityError.next_action,
        message_contains=("BuiltinTag", "name"),
    )


def _absent_source_peer_failure() -> _DerivationFailure:
    """A relationship peer that is in no bucket of the loaded source store."""
    diff = _FakeDiff(
        {
            "LocationRack": [
                _FakeElement(
                    kind="LocationRack",
                    name="r1__ghost",
                    keys={"name": "r1", "site": "ghost"},
                    source_attrs={},
                )
            ]
        }
    )
    return _DerivationFailure(
        config=build_config(),
        source=qualified_source(),
        destination=_ScriptedDiffDestination("destination", scripted=diff),
        error=SourcePeerUnresolvedError,
        next_action=SourcePeerUnresolvedError.ABSENT_NEXT_ACTION,
        message_contains=("LocationRack", "site", "ghost", "LocationSite"),
    )


def _ambiguous_source_peer_failure() -> _DerivationFailure:
    """The same class's second condition: one unique id in two candidate buckets (AD082)."""
    diff = _FakeDiff(
        {
            "DcimDevice": [
                _FakeElement(
                    kind="DcimDevice",
                    name="d1__dup",
                    keys={"name": "d1", "location": "dup"},
                    source_attrs={},
                )
            ]
        }
    )
    return _DerivationFailure(
        config=duplicate_location_config(*LOCATION_CANDIDATES),
        source=location_side("source", racks=["dup"], sites=["dup"]),
        destination=_ScriptedDiffDestination("destination", scripted=diff),
        error=SourcePeerUnresolvedError,
        next_action=SourcePeerUnresolvedError.AMBIGUOUS_NEXT_ACTION,
        message_contains=("DcimDevice", "location", "dup", "more than one", *LOCATION_CANDIDATES),
    )


def _unserializable_payload_failure() -> _DerivationFailure:
    """A payload value whose Python type is outside the canonical-value table."""
    diff = _FakeDiff(
        {
            "BuiltinTag": [
                _FakeElement(
                    kind="BuiltinTag",
                    name="prod",
                    keys={"name": "prod"},
                    source_attrs={"description": object()},
                )
            ]
        }
    )
    return _DerivationFailure(
        config=build_config(),
        source=qualified_source(),
        destination=_ScriptedDiffDestination("destination", scripted=diff),
        error=UnserializablePayloadValueError,
        next_action=UnserializablePayloadValueError.next_action,
        message_contains=("BuiltinTag", "description", "object"),
    )


def _duplicate_operation_id_failure() -> _DerivationFailure:
    """Two comparison elements addressing one object with one action."""
    diff = _FakeDiff(
        {
            "BuiltinTag": [
                _FakeElement(kind="BuiltinTag", name="prod", keys={"name": "prod"}, source_attrs={"slug": "prod"}),
                _FakeElement(kind="BuiltinTag", name="prod-again", keys={"name": "prod"}, source_attrs={"slug": "p"}),
            ]
        }
    )
    return _DerivationFailure(
        config=build_config(),
        source=qualified_source(),
        destination=_ScriptedDiffDestination("destination", scripted=diff),
        error=DuplicateOperationIdError,
        next_action=DuplicateOperationIdError.next_action,
        message_contains=("BuiltinTag", "op_"),
    )


DERIVATION_FAILURES = [
    pytest.param(_unformable_identity_failure, id="unformable-destination-identity"),
    pytest.param(_absent_source_peer_failure, id="source-peer-absent"),
    pytest.param(_ambiguous_source_peer_failure, id="source-peer-ambiguous"),
    pytest.param(_unserializable_payload_failure, id="unserializable-payload-value"),
    pytest.param(_duplicate_operation_id_failure, id="duplicate-operation-id"),
]


def _assert_named_failure(raised: BaseException | None, case: _DerivationFailure) -> None:
    """Assert the failure is the named class, carries its next action, and names the cause."""
    assert isinstance(raised, PlanArtifactError), f"expected a plan-artifact failure, got {raised!r}"
    assert type(raised) is case.error
    message = str(raised)
    for fragment in case.message_contains:
        assert fragment in message, f"the message does not name {fragment!r}: {message}"
    # AD059/AD071: the next action, not only the kind and the cause.
    assert raised.next_action == case.next_action
    assert f"Next action: {case.next_action}" in message


@pytest.mark.parametrize("build_case", DERIVATION_FAILURES)
def test_a_derivation_failure_fails_the_diff_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_case: Callable[[], _DerivationFailure],
) -> None:
    """AD047: each FR-030 failure fails the non-mutating `diff` with a non-zero exit.

    No arm degrades to a warning: the command's run record ends `failed` and the plan
    artifact's commit point is absent, so nothing partial is left for a later `apply` to
    read.
    """
    case = build_case()
    potenda = build_potenda(
        config=case.config,
        source=case.source,
        destination=case.destination,
        run_id="20260726T2200-77777777",
        top_level=case.config.order,
    )

    result = invoke_command(
        "diff",
        config=case.config,
        potenda=potenda,
        project_dir=tmp_path / "project",
        monkeypatch=monkeypatch,
    )

    assert result.exit_code != 0, result.output
    _assert_named_failure(result.exception, case)
    assert not manifest_path(potenda).exists()
    run_record = json.loads((plan_run_dir(potenda) / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "failed"


@pytest.mark.parametrize("build_case", DERIVATION_FAILURES)
def test_a_derivation_failure_is_equally_hard_on_the_sync_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_case: Callable[[], _DerivationFailure],
) -> None:
    """AD047: the same five failures fail `sync` too, before any destination write."""
    case = build_case()
    potenda = build_potenda(
        config=case.config,
        source=case.source,
        destination=case.destination,
        run_id="20260726T2300-88888888",
        top_level=case.config.order,
    )

    result = invoke_command(
        "sync",
        config=case.config,
        potenda=potenda,
        project_dir=tmp_path / "project",
        monkeypatch=monkeypatch,
        extra=["--no-parallel"],
    )

    assert result.exit_code != 0, result.output
    _assert_named_failure(result.exception, case)
    assert not manifest_path(potenda).exists()
    assert case.destination.sync_calls == [], "the destination was written despite a failed derivation"


def test_the_diff_command_offers_no_continue_on_error_tolerance() -> None:
    """AD047: the tolerance option is declared on `sync` only (`infrahub_sync/cli.py:190`).

    If it existed on `diff` too, a derivation failure could degrade to a warning and emit a
    silently incomplete plan — which is exactly what AD047 forbids, on the most-run command
    in the tool.
    """
    diff_help = CLI_RUNNER.invoke(app, ["diff", "--help"])
    sync_help = CLI_RUNNER.invoke(app, ["sync", "--help"])
    assert diff_help.exit_code == 0
    assert sync_help.exit_code == 0
    assert "--continue-on-error" in strip_ansi(sync_help.output)
    assert "--continue-on-error" not in strip_ansi(diff_help.output)

    rejected = CLI_RUNNER.invoke(app, ["diff", "--name", "demo", "--continue-on-error"])
    # Click's usage error for an unknown option, not a run that tolerated anything.
    assert rejected.exit_code == 2


def test_the_source_side_failures_do_not_route_the_operator_at_the_destination() -> None:
    """AD071: reusing `PeerNotFoundError` here would hand out a remedy that fixes nothing.

    Both source-side classes must read differently from the destination-side miss, whose
    remedy is to create the peer at the destination — while the plan-time problem is that
    the peer is not in the loaded source store, or that its kind cannot be established.
    """
    destination_remedy = PeerNotFoundError.next_action
    assert "at the destination" in destination_remedy

    for next_action in (
        SourcePeerUnresolvedError.ABSENT_NEXT_ACTION,
        SourcePeerUnresolvedError.AMBIGUOUS_NEXT_ACTION,
        UnformableDestinationIdentityError.next_action,
    ):
        assert next_action != destination_remedy
        assert "at the destination" not in next_action

    # AD082's two conditions route to two remedies, not one.
    assert SourcePeerUnresolvedError.ABSENT_NEXT_ACTION != SourcePeerUnresolvedError.AMBIGUOUS_NEXT_ACTION


# =======================================================================================
# T084 — AD049 / AD050: a delete's identity is canonicalised, probed destination-side
# =======================================================================================


def nested_destination_orphans() -> _FakeAdapter:
    """A destination holding a device, its rack and that rack's site — none at the source.

    The only state a delete can be derived from: the object is at the destination and
    absent from the source, so every peer inside its identity is destination-only by
    construction (AD049).
    """
    return _FakeAdapter(
        "destination",
        [
            _FakeRecord("LocationSite", {"name": "hq"}),
            _FakeRecord("LocationRack", {"name": "r1", "site": "hq"}),
            _FakeRecord("DcimDevice", {"name": "d1", "rack": "r1__hq"}, {"model": "c9300"}),
        ],
    )


def test_a_derived_delete_identity_nests_peer_pairs_probed_against_the_destination() -> None:
    """AD049: the same recursive rule as any other operation, probed destination-side.

    A probe wired to the source store finds nothing there — the source is empty by
    construction — so this case fails rather than the derivation quietly recording a
    unique-id string.
    """
    source = _FakeAdapter("source")
    destination = nested_destination_orphans()

    derived = derive_deletes(
        kinds=KINDS,
        source_adapter=source,
        destination_adapter=destination,
        config=build_config(),
        tier_of=resolver(),
        destination_full_extract=True,
    )

    rack = operation_for(derived, "LocationRack")
    device = operation_for(derived, "DcimDevice")

    # One level of nesting, then two.
    assert rack.identity == {"name": "r1", "site": {"peer_kind": "LocationSite", "identity": {"name": "hq"}}}
    assert device.identity == {
        "name": "d1",
        "rack": {
            "peer_kind": "LocationRack",
            "identity": {
                "name": "r1",
                "site": {"peer_kind": "LocationSite", "identity": {"name": "hq"}},
            },
        },
    }
    # Never the peer's DiffSync unique-id string, at either level.
    assert b"r1__hq" not in canonical_json_bytes(device.identity)
    assert b"__" not in canonical_json_bytes(device.identity)

    # The identifier a reviewer is shown derives from the identity they are shown.
    assert device.operation_id == operation_id("delete", "DcimDevice", device.identity)
    assert rack.operation_id == operation_id("delete", "LocationRack", rack.identity)

    # The recursive rule reaches the identity only.
    for operation in derived:
        assert operation.action == "delete"
        assert operation.payload is None
        assert operation.relationships is None

    # AD050's probe ran against the destination store, and the source was never consulted.
    assert ("LocationRack", "r1__hq") in destination.store.probes
    assert ("LocationSite", "hq") in destination.store.probes
    assert source.store.probes == []


def test_a_delete_whose_nested_peer_is_absent_destination_side_fails_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AD050's zero-hit arm, destination-side: the same refusal T082 asserts source-side."""
    destination = _FakeAdapter("destination", [_FakeRecord("DcimDevice", {"name": "d1", "rack": "ghost"})])

    error = failing_plan_run(
        monkeypatch,
        config=build_config(),
        source=_FakeAdapter("source"),
        destination=destination,
        run_id="20260727T0000-99999999",
        error=SourcePeerUnresolvedError,
    )

    message = str(error)
    for fragment in ("DcimDevice", "rack", "ghost", "LocationRack"):
        assert fragment in message
    assert error.next_action == SourcePeerUnresolvedError.ABSENT_NEXT_ACTION
    assert ("LocationRack", "ghost") in destination.store.probes


def test_a_delete_whose_nested_peer_is_ambiguous_destination_side_fails_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AD050's multi-hit arm, destination-side: named, not silently resolved."""
    config = duplicate_location_config(*LOCATION_CANDIDATES)
    destination = location_side("destination", racks=["dup"], sites=["dup"], devices=[("d1", "dup")])

    error = failing_plan_run(
        monkeypatch,
        config=config,
        source=_FakeAdapter("source"),
        destination=destination,
        run_id="20260727T0100-aaaaaaab",
        error=SourcePeerUnresolvedError,
    )

    message = str(error)
    for fragment in ("DcimDevice", "location", "dup", "more than one", *LOCATION_CANDIDATES):
        assert fragment in message
    assert error.next_action == SourcePeerUnresolvedError.AMBIGUOUS_NEXT_ACTION


# =======================================================================================
# T085 — AD052: `diff` end to end against a destination that exposes no schema
# =======================================================================================


def test_only_the_infrahub_adapter_exposes_a_schema_attribute() -> None:
    """V38, asserted rather than assumed: the guard covers eight adapters, not a fake.

    `self.schema` is set by the Infrahub adapter alone
    (`infrahub_sync/adapters/infrahub.py:345`). If another adapter ever gained one this
    case would say so, and the regression below would no longer be the whole story.
    """
    modules = sorted(path.stem for path in ADAPTERS_DIR.glob("*.py") if path.stem not in NON_ADAPTER_MODULES)
    exposing = [
        module for module in modules if "self.schema" in (ADAPTERS_DIR / f"{module}.py").read_text(encoding="utf-8")
    ]

    assert exposing == ["infrahub"]
    assert len(modules) - len(exposing) == 8, f"expected eight schema-less adapters, found {modules}"


def test_the_diff_command_succeeds_end_to_end_against_a_destination_with_no_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AD052 regression: derivation is newly wired onto `diff` for **every** destination.

    Because AD047 makes a derivation failure fatal, an unguarded `destination.schema` read
    would turn `diff` — which works today on all eight schema-less adapters — into an
    `AttributeError` at a non-zero exit. Removing the guard in
    `warn_missing_convergence_key` fails this case on the exit code.

    The warning is asserted **absent**, not empty: a guard that emitted a warning naming
    nothing would tell an operator their destination has a convergence problem it cannot
    know anything about.
    """
    config = build_config()
    source = qualified_source()
    destination = destination_with_orphan()
    assert not hasattr(destination, "schema"), "fixture error: the destination must expose no schema"

    potenda = build_potenda(
        config=config,
        source=source,
        destination=destination,
        run_id="20260727T0200-bbbbbbbc",
    )

    with caplog.at_level(logging.DEBUG, logger=DERIVE_LOGGER):
        result = invoke_command(
            "diff",
            config=config,
            potenda=potenda,
            project_dir=tmp_path / "project",
            monkeypatch=monkeypatch,
        )

    assert result.exit_code == 0, result.output
    assert result.exception is None, f"an error escaped the diff path: {result.exception!r}"
    assert "AttributeError" not in result.output

    # The convergence-key warning is skipped outright.
    assert derive_warnings(caplog) == []

    # And the plan artifact was written all the same, deletes included.
    assert potenda._side_full_extract == {"A": True, "B": True}
    manifest = read_manifest(plan_run_dir(potenda))
    assert set(manifest) == MANIFEST_KEYS
    assert manifest["operations_count"] > 0
    assert manifest["delete_operations_computed"] is True
    run_record = json.loads((plan_run_dir(potenda) / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "dry-run"
