"""Creates must not collapse the full loaded source population, including warm runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel
from diffsync.store import BaseStore

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncInstance
from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.incremental import apply_changed_rows
from infrahub_sync.cache.parquet_io import write_resource_side
from infrahub_sync.plan.derive import operations_from_diff, warn_missing_convergence_key
from infrahub_sync.plan.errors import (
    DestinationIdentityCollisionError,
    PlanVerificationError,
    SourcePeerUnresolvedError,
    UnkeyedCreateRefusedError,
)
from infrahub_sync.plan.keying import refuse_source_identity_collisions
from infrahub_sync.potenda import Potenda

if TYPE_CHECKING:
    from pathlib import Path


class Site(DiffSyncModel):
    """A site distinguished by name."""

    _modelname: ClassVar[str] = "TestSite"
    _identifiers: ClassVar[tuple[str, ...]] = ("name",)
    name: str


class Rack(DiffSyncModel):
    """A source rack distinguished by its site and name."""

    _modelname: ClassVar[str] = "TestRack"
    _identifiers: ClassVar[tuple[str, ...]] = ("name", "site")
    _attributes: ClassVar[tuple[str, ...]] = ("description",)
    name: str
    site: str | None
    description: str = "desired"
    local_id: str | None = None


class Device(DiffSyncModel):
    """A source device with a site-qualified identity."""

    _modelname: ClassVar[str] = "TestDevice"
    _identifiers: ClassVar[tuple[str, ...]] = ("name", "site")
    name: str | None
    site: str


class Interface(DiffSyncModel):
    """An interface whose peer identity nests a device and site."""

    _modelname: ClassVar[str] = "InterfacePhysical"
    _identifiers: ClassVar[tuple[str, ...]] = ("name", "device")
    _attributes: ClassVar[tuple[str, ...]] = ("description",)
    name: str
    device: str
    description: str = "desired"
    local_id: str | None = None


class Population(Adapter):
    """In-memory product comparison and incremental load, with no server writes."""

    TestSite = Site
    TestRack = Rack
    TestDevice = Device
    InterfacePhysical = Interface
    top_level: ClassVar[list[str]] = ["TestSite", "TestRack", "TestDevice", "InterfacePhysical"]

    @staticmethod
    def cursor_tier_for(_kind: str) -> CursorTier:
        """Support the timestamp cursor used by the warm-run fixture."""
        return CursorTier.TIMESTAMP

    @staticmethod
    def safe_cursor_before_load(_kind: str) -> CursorState:
        """Guarantee the synthetic fixture's bound precedes its changed records."""
        return CursorState(CursorTier.TIMESTAMP, "2026-09-29T00:00:00Z", safe=True)

    def model_loader(self, model_name: str, model: Any) -> None:  # noqa: ANN401
        """Leave already seeded peers in place."""

    @staticmethod
    def list_changed_since(kind: str, _cursor: CursorState) -> list[dict[str, str]]:
        """Only the new rack changed after the previous snapshot."""
        return [{"name": "same", "site": "site-b", "description": "desired"}] if kind == "TestRack" else []


def config() -> SyncInstance:
    """The mapped identifiers used by both the real diff and the population gate."""
    entries = [
        ("TestSite", ["name"], {"name": None}),
        ("TestRack", ["name", "site"], {"name": None, "site": "TestSite", "description": None}),
        ("TestDevice", ["name", "site"], {"name": None, "site": "TestSite"}),
        ("InterfacePhysical", ["name", "device"], {"name": None, "device": "TestDevice", "description": None}),
    ]
    return SyncInstance(
        name="collision-check",
        directory=".",
        source=SyncAdapter(name="netbox", settings={}),
        destination=SyncAdapter(name="infrahub", settings={}),
        schema_mapping=[
            SchemaMappingModel(
                name=kind,
                mapping=kind,
                identifiers=identifiers,
                fields=[
                    SchemaMappingField(name=field, mapping=field, reference=peer) for field, peer in fields.items()
                ],
            )
            for kind, identifiers, fields in entries
        ],
    )


def populations(*, nested: bool, existing: str, distinct: bool = False) -> tuple[Population, Population, str]:
    """Two source identities, with zero or one represented at the destination."""
    source, destination = Population(name="source"), Population(name="destination")
    destination.type = "Infrahub"
    destination.schema = {  # ty: ignore[unresolved-attribute]
        "TestSite": SimpleNamespace(human_friendly_id=["name__value"]),
        "TestRack": SimpleNamespace(human_friendly_id=["name__value"]),
        "TestDevice": SimpleNamespace(human_friendly_id=["site__name__value", "name__value"]),
        "InterfacePhysical": SimpleNamespace(human_friendly_id=["device__name__value", "name__value"]),
    }
    for adapter in (source, destination):
        for site in ("site-a", "site-b"):
            adapter.add(Site(name=site))
            if nested:
                adapter.add(Device(name="edge1", site=site))
    if nested:
        first = Interface(name="eth0", device="edge1__site-a")
        second = Interface(name="eth1" if distinct else "eth0", device="edge1__site-b")
    else:
        first = Rack(name="same", site="site-a")
        second = Rack(name="other" if distinct else "same", site="site-b")
    source.add(first)
    source.add(second)
    if existing != "none":
        destination.add(
            first.model_copy(
                update={"description": "old" if existing == "update" else "desired", "local_id": "recorded-id"}
            )
        )
    return source, destination, first.get_type()


def engine(source: Population, destination: Population) -> Potenda:
    """A real engine with no artifact storage identity."""
    return Potenda(
        source=source, destination=destination, config=config(), top_level=list(source.top_level), show_progress=False
    )


@pytest.mark.parametrize("nested", [False, True], ids=["rack", "interface-device-site"])
@pytest.mark.parametrize("existing", ["none", "update", "unchanged"])
@pytest.mark.parametrize("artifact", [False, True], ids=["no-artifact", "saved-artifact"])
def test_full_source_population_refuses_lossy_creates(
    *, nested: bool, existing: str, artifact: bool, tmp_path: Path
) -> None:
    """A real diff may omit a colliding source record entirely; planning still refuses."""
    source, destination, kind = populations(nested=nested, existing=existing)
    potenda = engine(source, destination)
    if artifact:
        potenda.run_dir = tmp_path
        potenda.run_id = "collision-run"
    diff = potenda.diff()
    actions = [element.action for element in diff.children[kind].values()]
    assert actions.count("create") == (2 if existing == "none" else 1)
    if existing == "update":
        operations = operations_from_diff(
            diff, config=config(), tier_of=lambda _: 0, source_adapter=source, destination_adapter=destination
        )
        assert next(op for op in operations if op.action == "update").destination_id == "recorded-id"
    with pytest.raises(DestinationIdentityCollisionError) as error:
        potenda.write_plan(diff)
    message = str(error.value)
    assert kind in message
    if existing != "none":
        assert "actual destination match key" in message
        assert "Declared source identity" in message
        assert f"mapping identifiers): {'device, name' if nested else 'name, site'}." in message
        assert (
            f"Other loaded source record identifiers: '{'eth0__edge1__site-a' if nested else 'same__site-a'}'"
            in message
        )
        assert "Source identity identifiers" not in message
        assert ("device__site__name__value" if nested else "site__name__value") in message
        assert error.value.wrote is False
    assert not (tmp_path / "plan" / "manifest.json").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_distinct_match_keys_are_allowed_without_an_artifact(*, nested: bool) -> None:
    """Both direct and nested projections allow genuinely distinct destination identities."""
    source, destination, _ = populations(nested=nested, existing="unchanged", distinct=True)
    potenda = engine(source, destination)
    assert potenda.write_plan(potenda.diff()) is None


def test_warm_incremental_population_includes_unchanged_collision(tmp_path: Path) -> None:
    """Hydrated source records participate even though the delta contains only a new rack."""
    source, destination, _ = populations(nested=False, existing="unchanged")
    source.remove(source.get("TestRack", "same__site-a"))
    source.remove(source.get("TestRack", "same__site-b"))
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "schema-sub-hash.txt").write_text("schema-hash")
    (previous / "cursors.json").write_text(json.dumps({"A": {"TestRack": "safe-v1:TIMESTAMP:2026-09-29T00:00:00Z"}}))
    write_resource_side(
        run_dir=previous,
        side="A",
        resource="TestRack",
        rows=[{"name": "same", "site": "site-a", "description": "desired"}],
        source_ids=["same__site-a"],
        extract_ts=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    potenda = engine(source, destination)
    potenda._prev_run_resolved = True
    potenda._prev_run_cached = previous
    potenda._schema_subhash = "schema-hash"
    potenda.load_one_side(side="A", adapter=source)
    assert potenda._side_full_extract["A"] is False
    assert len(source.get_all("TestRack")) == 2
    with pytest.raises(DestinationIdentityCollisionError, match="distinct loaded source record"):
        potenda.write_plan(potenda.diff())


def test_missing_peer_value_in_population_warns_and_proceeds(caplog: pytest.LogCaptureFixture) -> None:
    """An unchanged record with no mapped peer value cannot prove a collision."""
    source, destination, _ = populations(nested=False, existing="unchanged", distinct=True)
    source.add(Rack(name="same", site=None))
    destination.add(Rack(name="same", site=None, local_id="other-id"))
    destination.schema["TestRack"].human_friendly_id = ["site__name__value", "name__value"]  # ty: ignore[unresolved-attribute]
    potenda = engine(source, destination)
    assert potenda.write_plan(potenda.diff()) is None
    assert "supplies no value" in caplog.text
    assert "site__name__value" in caplog.text


def test_missing_nested_peer_value_warns_and_proceeds(caplog: pytest.LogCaptureFixture) -> None:
    """Null in an unchanged device's mapped identity cannot prove an interface collision."""
    source, destination, _ = populations(nested=True, existing="unchanged", distinct=True)
    for adapter in (source, destination):
        peer = Device(name=None, site="site-a")
        adapter.add(peer)
        adapter.add(Interface(name="eth0", device=peer.get_unique_id(), local_id="missing-name-id"))
    potenda = engine(source, destination)
    assert potenda.write_plan(potenda.diff()) is None
    assert "supplies no value" in caplog.text
    assert "device__name__value" in caplog.text


@pytest.mark.parametrize("cached", [False, True], ids=["loaded", "cache-restored"])
def test_unchanged_missing_identity_peer_warns_and_writes_plan(
    *, cached: bool, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unchanged interface with a destination-only device cannot prove a collision."""
    source, destination, _ = populations(nested=True, existing="unchanged", distinct=True)
    ghost = Interface(name="eth0", device="ghost__site-a", local_id="ghost-interface-id")
    destination.add(Device(name="ghost", site="site-a"))
    destination.add(ghost)
    potenda = engine(source, destination)
    if cached:
        previous = tmp_path / "previous"
        previous.mkdir()
        (previous / "schema-sub-hash.txt").write_text("schema-hash")
        (previous / "cursors.json").write_text(
            json.dumps({"A": {"InterfacePhysical": "safe-v1:TIMESTAMP:2026-09-29T00:00:00Z"}})
        )
        write_resource_side(
            run_dir=previous,
            side="A",
            resource="InterfacePhysical",
            rows=[{"name": ghost.name, "device": ghost.device, "description": ghost.description}],
            source_ids=[ghost.get_unique_id()],
            extract_ts=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )
        potenda._prev_run_resolved = True
        potenda._prev_run_cached = previous
        potenda._schema_subhash = "schema-hash"
        potenda.load_one_side(side="A", adapter=source)
        assert potenda._side_full_extract["A"] is False
    else:
        source.add(ghost)
    potenda.run_dir = tmp_path / "current"
    potenda.run_id = "missing-peer-run"
    manifest = potenda.write_plan(potenda.diff())
    assert manifest is not None
    assert (potenda.run_dir / "plan" / "manifest.json").exists()
    assert (potenda.run_dir / "plan" / "operations.jsonl").exists()
    assert "unresolved identity peer" in caplog.text
    assert "InterfacePhysical" in caplog.text
    assert "allowed to proceed" in caplog.text
    assert "Add the peer's kind" not in caplog.text
    assert ghost.device not in caplog.text


@pytest.mark.parametrize("action", ["create", "update"])
def test_planned_missing_identity_peer_is_still_refused(action: str) -> None:
    """Population tolerance must not weaken the proof for changed records."""
    source, destination, _ = populations(nested=True, existing="unchanged", distinct=True)
    ghost = Interface(name="eth0", device="ghost__site-a")
    source.add(ghost)
    if action == "update":
        destination.add(ghost.model_copy(update={"description": "old", "local_id": "ghost-interface-id"}))
    potenda = engine(source, destination)
    with pytest.raises(SourcePeerUnresolvedError):
        potenda.write_plan(potenda.diff())


def test_non_infrahub_without_storage_skips_derivation() -> None:
    """Other destinations preserve the direct Python skip when no artifact is requested."""
    source, destination, _ = populations(nested=True, existing="unchanged", distinct=True)
    destination.type = "Other"
    source.add(Interface(name="eth0", device="missing-device"))
    potenda = engine(source, destination)
    assert potenda.write_plan(potenda.diff()) is None


def test_collision_identifiers_with_non_name_values_are_withheld() -> None:
    """Counterpart diagnostics must not expose non-name values through the store id."""
    source, destination, kind = populations(nested=False, existing="unchanged")
    operations = operations_from_diff(
        engine(source, destination).diff(),
        config=config(),
        tier_of=lambda _: 0,
        source_adapter=source,
        destination_adapter=destination,
    )
    operation = operations[0].model_copy(update={"identity": {"name": "same", "credential": "ours"}})
    sensitive = "synthetic-credential-marker"
    with pytest.raises(DestinationIdentityCollisionError) as error:
        refuse_source_identity_collisions(
            kind=kind,
            node=destination.schema[kind],  # ty: ignore[unresolved-attribute]
            creates=[operation],
            identities=[(f"same__{sensitive}", {"name": "same", "credential": sensitive})],
            declared_identity={"name", "credential"},
        )
    assert sensitive not in str(error.value)
    assert "source identity fingerprint" in str(error.value)


def test_alternative_uniqueness_constraint_does_not_mask_actual_match_key() -> None:
    """A finer uniqueness constraint cannot stop an upsert from matching on its HFID."""
    source, destination, _ = populations(nested=False, existing="unchanged")
    destination.schema["TestRack"].uniqueness_constraints = [["site__name__value", "name__value"]]  # ty: ignore[unresolved-attribute]
    potenda = engine(source, destination)
    with pytest.raises(DestinationIdentityCollisionError, match="actual destination match key"):
        potenda.write_plan(potenda.diff())


@pytest.mark.parametrize("missing", ["schema", "kind"])
def test_infrahub_schema_absence_cannot_skip_create_gate(missing: str) -> None:
    """An Infrahub create needs a cached schema even without an artifact directory."""
    source, destination, _ = populations(nested=False, existing="unchanged", distinct=True)
    if missing == "schema":
        del destination.schema  # ty: ignore[unresolved-attribute]
    else:
        del destination.schema["TestRack"]  # ty: ignore[unresolved-attribute]
    potenda = engine(source, destination)
    with pytest.raises(UnkeyedCreateRefusedError, match="schema"):
        potenda.write_plan(potenda.diff())


def test_infrahub_missing_config_cannot_skip_population_gate() -> None:
    """No parsed mapping means no proof of nested source identities."""
    source, destination, _ = populations(nested=False, existing="unchanged")
    potenda = engine(source, destination)
    potenda.config = None  # ty: ignore[invalid-assignment] - exercise the runtime guard for an unconfigured engine
    with pytest.raises(PlanVerificationError, match="parsed configuration"):
        potenda.write_plan(potenda.diff())


def test_updates_only_remain_keyed_by_recorded_id() -> None:
    """Finer source identities sharing a match key do not refuse an update-only plan."""
    source, destination, _ = populations(nested=False, existing="update")
    destination.add(Rack(name="same", site="site-b", description="old", local_id="second-id"))
    diff = engine(source, destination).diff()
    operations = operations_from_diff(
        diff, config=config(), tier_of=lambda _: 0, source_adapter=source, destination_adapter=destination
    )
    assert {op.destination_id for op in operations} == {"recorded-id", "second-id"}
    warn_missing_convergence_key(destination=destination, operations=operations, source_adapter=source, config=config())


class DetachedStore(BaseStore):
    """Store that hands back copies, like a serialising store, so edits persist only through update()."""

    def __init__(self, rack: Rack) -> None:
        super().__init__(name="detached")
        self.saved = rack.model_copy()

    def get(self, *, model: Any, identifier: Any) -> Rack:  # noqa: ANN401, ARG002
        return self.saved.model_copy()

    def update(self, *, obj: Any) -> None:  # noqa: ANN401
        self.saved = obj.model_copy()


def test_overlapping_delta_update_and_local_id_are_persisted() -> None:
    """A detached stored copy keeps the delta's attributes and local_id after the load."""
    adapter = Population(name="destination")
    store = DetachedStore(Rack(name="same", site="site-a", description="old", local_id="old-id"))
    adapter.store = store
    cursor = CursorState(CursorTier.TIMESTAMP, "2026-09-29T00:00:00Z", safe=True)
    adapter.list_changed_since = lambda *_: [  # ty: ignore[invalid-assignment]
        {"name": "same", "site": "site-a", "description": "new", "local_id": "new-id"}
    ]
    apply_changed_rows(adapter, "TestRack", Rack, cursor)
    assert (store.saved.description, store.saved.local_id) == ("new", "new-id")
