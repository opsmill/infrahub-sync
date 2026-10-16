"""Plans of valid data stay the same when the record loader changes (spec 009 T002, T029).

The baseline under `tests/data/loader_parity/` was written by this module with the code as it
was before the shared record loader of spec 009 (research R3). Each case plans one valid
fixture of `tests/test_potenda_plan_artifact.py` through the real `Potenda` plan path and
compares the saved operations, re-encoded through `canonical_json_bytes`, with the baseline
byte for byte. `tests/test_potenda_tiers.py` holds no fixture that is planned: its two tests
only build a `Potenda`, so it adds no case here.

Those fixtures use fake adapters that never call an adapter's loader. A second set of cases
loads valid data through the real `NetboxAdapter` and `InfrahubAdapter` loaders, with fake
pynetbox and Infrahub SDK clients, so a change in record conversion or peer resolution changes
their plan. Their baselines were written with the NetBox and Infrahub loaders as they were
before the adapters moved onto the shared loader step (spec 009 T016, T023).

To write the baseline again, set `INFRAHUB_SYNC_WRITE_LOADER_PARITY=1`. Do that only when a
change to the plan is intended, and say so in the pull request.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

import pytest
from diffsync import Adapter
from infrahub_sdk.schema.main import AttributeKind, NodeSchemaAPI

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SchemaMappingTransform,
    SyncAdapter,
    SyncConfig,
    SyncInstance,
)
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel
from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.potenda import Potenda
from tests.adapters.test_infrahub_loader_fields import FakeAttr, FakeRelatedNode, FakeSdkStore
from tests.test_potenda_plan_artifact import (
    _FakeAdapter,
    build_config,
    build_potenda,
    derivation_destination,
    destination_mirroring_source,
    destination_with_orphan,
    plan_run_dir,
    qualified_source,
    read_operations_bytes,
    run_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

BASELINE_DIRECTORY = Path(__file__).resolve().parent.parent / "data" / "loader_parity"
WRITE_BASELINE_VARIABLE = "INFRAHUB_SYNC_WRITE_LOADER_PARITY"


class ParityCase(NamedTuple):
    """One valid fixture pair: the baseline file name and the two sides to plan."""

    name: str
    source: Callable[[], _FakeAdapter]
    destination: Callable[[], _FakeAdapter]


CASES: tuple[ParityCase, ...] = (
    ParityCase("creates_into_empty_destination", qualified_source, lambda: _FakeAdapter("destination")),
    ParityCase("updates_against_stale_destination", qualified_source, destination_mirroring_source),
    ParityCase("no_difference_against_identical_destination", qualified_source, derivation_destination),
    ParityCase("creates_and_one_delete", qualified_source, destination_with_orphan),
)


@pytest.fixture(autouse=True)
def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the engine's run directory and sidecar files under the test's own directory."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def canonical_operations(operations_bytes: bytes) -> bytes:
    """Re-encode each saved operation through the plan's canonical encoding, one per line."""
    lines = [line for line in operations_bytes.splitlines() if line.strip()]
    return b"".join(canonical_json_bytes(json.loads(line)) + b"\n" for line in lines)


def plan_operations(case: ParityCase) -> bytes:
    """Plan the case with the current code and return its canonical operations."""
    potenda = build_potenda(
        config=build_config(),
        source=case.source(),
        destination=case.destination(),
        run_id="20261015T0900-00000009",
    )
    run_plan(potenda)
    return canonical_operations(read_operations_bytes(plan_run_dir(potenda)))


def assert_matches_baseline(name: str, actual: bytes) -> None:
    """Compare canonical operations with the stored baseline, or write it when asked to."""
    baseline = BASELINE_DIRECTORY / f"{name}.jsonl"

    if os.environ.get(WRITE_BASELINE_VARIABLE) == "1":
        BASELINE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(actual)

    assert baseline.is_file(), f"no baseline for {name}; set {WRITE_BASELINE_VARIABLE}=1 to write it"
    assert actual == baseline.read_bytes()


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_plan_of_valid_data_matches_the_loader_baseline(case: ParityCase) -> None:
    """The canonical operations of a valid fixture equal the baseline taken before the loader change."""
    assert_matches_baseline(case.name, plan_operations(case))


# ---------------------------------------------------------------------------------------
# Cases that load through the real NetBox and Infrahub adapter loaders
#
# The fake adapters above never call an adapter's loader, so they cannot show a change in
# how a loader converts a record or resolves a peer. The cases below build the real
# `NetboxAdapter` and `InfrahubAdapter`, replace only their clients (a fake pynetbox API and
# a fake Infrahub SDK client, no network), and let `Potenda.load_both_sides` call the
# adapters' own `load` and `model_loader`. The baselines were written with the NetBox and
# Infrahub loaders as they were before spec 009 moved them onto the shared loader step.
# ---------------------------------------------------------------------------------------

REAL_KINDS: tuple[str, ...] = ("BuiltinTag", "LocationSite", "LocationRack", "DcimDevice")


class NetboxTag(NetboxModel):
    """NetBox tag: an identifier and an optional attribute."""

    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None


class NetboxSite(NetboxModel):
    """NetBox site: a transformed attribute and a cardinality-many tag relationship."""

    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("description", "tags")
    name: str
    description: str | None = None
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


class NetboxRack(NetboxModel):
    """NetBox rack: a required site relationship inside its identity and a nested-path attribute."""

    _modelname = "LocationRack"
    _identifiers = ("name", "site")
    _attributes = ("role",)
    name: str
    site: str
    role: str | None = None


class NetboxDevice(NetboxModel):
    """NetBox device: optional and required one relationships, a many relationship, a filter."""

    _modelname = "DcimDevice"
    _identifiers = ("name",)
    _attributes = ("status", "description", "rack", "site", "tags")
    name: str
    status: str | None = None
    description: str | None = None
    rack: str | None = None
    site: str
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


class InfrahubTag(InfrahubModel):
    """Infrahub counterpart of `NetboxTag`."""

    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None


class InfrahubSite(InfrahubModel):
    """Infrahub counterpart of `NetboxSite`."""

    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("description", "tags")
    name: str
    description: str | None = None
    tags: list[str] | None = []  # noqa: RUF012 - mirrors the generated models


class InfrahubRack(InfrahubModel):
    """Infrahub counterpart of `NetboxRack`."""

    _modelname = "LocationRack"
    _identifiers = ("name", "site")
    _attributes = ("role",)
    name: str
    site: str
    role: str | None = None


class InfrahubDevice(InfrahubModel):
    """Infrahub counterpart of `NetboxDevice`."""

    _modelname = "DcimDevice"
    _identifiers = ("name",)
    _attributes = ("status", "description", "rack", "site", "tags")
    name: str
    status: str | None = None
    description: str | None = None
    rack: str | None = None
    site: str
    tags: list[str] | None = []  # noqa: RUF012 - mirrors the generated models


def _field(name: str, mapping: str | None = None, reference: str | None = None) -> SchemaMappingField:
    return SchemaMappingField(name=name, mapping=mapping or name, reference=reference)


def real_mapping(*, source: str) -> list[SchemaMappingModel]:
    """The four kinds' mapping; filters and transforms are written for the source adapter.

    A NetBox filter reads the raw record (`status.value`), an Infrahub filter reads the
    converted record (`status`), so the filter path depends on which adapter is the source.
    """
    status_path = "status.value" if source == "netbox" else "status"
    return [
        SchemaMappingModel(
            name="BuiltinTag",
            mapping="extras.tags",
            identifiers=["name"],
            fields=[_field("name"), _field("description")],
        ),
        SchemaMappingModel(
            name="LocationSite",
            mapping="dcim.sites",
            identifiers=["name"],
            transforms=[
                SchemaMappingTransform(
                    field="description", expression="{{ description.upper() if description else None }}"
                )
            ],
            fields=[_field("name"), _field("description"), _field("tags", reference="BuiltinTag")],
        ),
        SchemaMappingModel(
            name="LocationRack",
            mapping="dcim.racks",
            identifiers=["name", "site"],
            fields=[_field("name"), _field("site", reference="LocationSite"), _field("role", "role.name")],
        ),
        SchemaMappingModel(
            name="DcimDevice",
            mapping="dcim.devices",
            identifiers=["name"],
            filters=[SchemaMappingFilter(field=status_path, operation="!=", value="offline")],
            fields=[
                _field("name"),
                _field("status", status_path),
                _field("description"),
                _field("rack", reference="LocationRack"),
                _field("site", reference="LocationSite"),
                _field("tags", reference="BuiltinTag"),
            ],
        ),
    ]


# NetBox rows as pynetbox returns them. `old1` is offline, so the device filter removes it; it
# references a rack only it uses, which shows that a filtered record resolves no peer.
NETBOX_ROWS: dict[str, list[dict[str, Any]]] = {
    "extras.tags": [
        {"id": 1, "name": "blue", "description": "Blue tag"},
        {"id": 2, "name": "red", "description": "Red tag"},
        {"id": 3, "name": "green", "description": None},
    ],
    "dcim.sites": [
        {"id": 10, "name": "dc-east", "description": "east site", "tags": [{"id": 1}, {"id": 2}]},
        {"id": 11, "name": "dc-west", "description": None, "tags": []},
    ],
    "dcim.racks": [
        {"id": 20, "name": "r1", "site": {"id": 10}, "role": {"name": "compute"}},
        {"id": 21, "name": "r1", "site": {"id": 11}, "role": None},
        {"id": 22, "name": "r2", "site": {"id": 10}, "role": {"name": "network"}},
    ],
    "dcim.devices": [
        {
            "id": 30,
            "name": "leaf1",
            "status": {"value": "active"},
            "description": "leaf one",
            "rack": {"id": 20},
            "site": {"id": 10},
            "tags": [{"id": 2}, {"id": 1}],
        },
        {
            "id": 31,
            "name": "leaf2",
            "status": {"value": "planned"},
            "description": None,
            "rack": None,
            "site": {"id": 11},
            "tags": [],
        },
        {
            "id": 32,
            "name": "spine1",
            "status": {"value": "active"},
            "description": "spine",
            "rack": {"id": 22},
            "site": {"id": 10},
            "tags": [{"id": 3}],
        },
        {
            "id": 33,
            "name": "old1",
            "status": {"value": "offline"},
            "description": "retired",
            "rack": {"id": 21},
            "site": {"id": 11},
            "tags": [{"id": 1}],
        },
    ],
}


class _FakeEndpoint:
    """A pynetbox endpoint: `all()` returns fresh copies, so a transform never edits the fixture."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._rows)


def fake_netbox_api(rows: Mapping[str, list[dict[str, Any]]]) -> SimpleNamespace:
    """A pynetbox API object whose `app.endpoint` paths serve the given rows."""
    apps: dict[str, dict[str, _FakeEndpoint]] = {}
    for path, endpoint_rows in rows.items():
        app, endpoint = path.split(".")
        apps.setdefault(app, {})[endpoint] = _FakeEndpoint(endpoint_rows)
    return SimpleNamespace(**{app: SimpleNamespace(**endpoints) for app, endpoints in apps.items()})


class ParityNetboxAdapter(NetboxAdapter):
    """The real NetBox adapter with the parity models and a fake pynetbox client."""

    top_level: ClassVar[list[str]] = list(REAL_KINDS)
    BuiltinTag = NetboxTag
    LocationSite = NetboxSite
    LocationRack = NetboxRack
    DcimDevice = NetboxDevice

    def _create_netbox_client(self, adapter: SyncAdapter) -> Any:  # noqa: ANN401, ARG002, PLR6301 - overrides the client factory
        """Serve `NETBOX_ROWS` instead of opening a session to a NetBox server."""
        return fake_netbox_api(NETBOX_ROWS)


# --- Infrahub SDK stand-ins -------------------------------------------------------------


@dataclass
class _FakeManyManager:
    """`RelationshipManagerSync` stand-in: already initialised, with peers carrying an id."""

    peers: list[FakeRelatedNode]
    initialized: bool = True


class _SdkNode:
    """`InfrahubNodeSync` stand-in carrying every attribute and relationship of its kind."""

    def __init__(self, *, kind: str, schema: NodeSchemaAPI, row: _SdkRow) -> None:
        self.id = row.node_id
        self._kind = kind
        self._schema = schema
        for name in schema.attribute_names:
            setattr(self, name, FakeAttr(value=row.attributes.get(name)))
        for relationship in schema.relationships:
            if relationship.cardinality == "many":
                peers = [FakeRelatedNode(id=peer_id) for peer_id in row.many.get(relationship.name, [])]
                setattr(self, relationship.name, _FakeManyManager(peers=peers))
            else:
                setattr(self, relationship.name, FakeRelatedNode(id=row.one.get(relationship.name)))

    def get_kind(self) -> str:
        return self._kind


@dataclass
class _SdkRow:
    """One Infrahub node: its id, attribute values, and peer node ids per relationship."""

    node_id: str
    attributes: dict[str, Any]
    one: dict[str, str] = field(default_factory=dict)
    many: dict[str, list[str]] = field(default_factory=dict)


def _schema(
    namespace: str, name: str, *, attributes: list[str], relationships: list[tuple[str, str, str]], hfid: list[str]
) -> NodeSchemaAPI:
    """A real SDK node schema: text attributes and `(name, peer, cardinality)` relationships."""
    return NodeSchemaAPI.model_validate(
        {
            "name": name,
            "namespace": namespace,
            "human_friendly_id": hfid,
            "attributes": [
                {"name": attribute, "kind": AttributeKind.TEXT, "optional": attribute != "name"}
                for attribute in attributes
            ],
            "relationships": [
                {"name": rel_name, "peer": peer, "cardinality": cardinality, "optional": True, "kind": "Attribute"}
                for rel_name, peer, cardinality in relationships
            ],
        }
    )


INFRAHUB_SCHEMAS: dict[str, NodeSchemaAPI] = {
    "BuiltinTag": _schema("Builtin", "Tag", attributes=["name", "description"], relationships=[], hfid=["name__value"]),
    "LocationSite": _schema(
        "Location",
        "Site",
        attributes=["name", "description"],
        relationships=[("tags", "BuiltinTag", "many")],
        hfid=["name__value"],
    ),
    "LocationRack": _schema(
        "Location",
        "Rack",
        attributes=["name", "role"],
        relationships=[("site", "LocationSite", "one")],
        hfid=["site__name__value", "name__value"],
    ),
    "DcimDevice": _schema(
        "Dcim",
        "Device",
        attributes=["name", "status", "description"],
        relationships=[
            ("rack", "LocationRack", "one"),
            ("site", "LocationSite", "one"),
            ("tags", "BuiltinTag", "many"),
        ],
        hfid=["name__value"],
    ),
}


class FakeInfrahubClient:
    """`InfrahubClientSync` stand-in: `all` returns full nodes and fills the SDK store."""

    def __init__(self, rows: Mapping[str, list[_SdkRow]]) -> None:
        self.store = FakeSdkStore()
        self._rows = rows

    def all(self, *, kind: str, include: list[str] | None = None, populate_store: bool = True) -> list[_SdkNode]:  # noqa: ARG002
        nodes = [_SdkNode(kind=kind, schema=INFRAHUB_SCHEMAS[kind], row=row) for row in self._rows.get(kind, [])]
        if populate_store:
            for node in nodes:
                self.store.set(key=node.id, node=node)  # ty: ignore[invalid-argument-type]
        return nodes

    @staticmethod
    def get(**kwargs: Any) -> None:  # noqa: ANN401
        """Every peer is in the store, so a fallback fetch means the loader changed."""
        msg = f"unexpected fallback fetch: {kwargs}"
        raise AssertionError(msg)


class ParityInfrahubAdapter(InfrahubAdapter):
    """The real Infrahub adapter with the parity models and a fake SDK client.

    `InfrahubAdapter.__init__` opens an SDK client and reads accounts and the schema from a
    server, so this sets the same attributes without it, as `tests/adapters/
    test_infrahub_loader_fields.py` does.
    """

    top_level: ClassVar[list[str]] = list(REAL_KINDS)
    BuiltinTag = InfrahubTag
    LocationSite = InfrahubSite
    LocationRack = InfrahubRack
    DcimDevice = InfrahubDevice

    def __init__(  # pylint: disable=super-init-not-called
        self, *, target: str, config: SyncConfig, rows: Mapping[str, list[_SdkRow]]
    ) -> None:
        Adapter.__init__(self)
        self.target = target
        self.config = config
        self.client = FakeInfrahubClient(rows)
        self.schema = dict(INFRAHUB_SCHEMAS)
        self.source_node = None
        self.owner_node = None
        self._peer_unique_ids = {}


# The same records as `NETBOX_ROWS`, held by an Infrahub instance used as a source.
INFRAHUB_SOURCE_ROWS: dict[str, list[_SdkRow]] = {
    "BuiltinTag": [
        _SdkRow("tag-blue", {"name": "blue", "description": "Blue tag"}),
        _SdkRow("tag-red", {"name": "red", "description": "Red tag"}),
        _SdkRow("tag-green", {"name": "green", "description": None}),
    ],
    "LocationSite": [
        _SdkRow("site-east", {"name": "dc-east", "description": "east site"}, many={"tags": ["tag-blue", "tag-red"]}),
        _SdkRow("site-west", {"name": "dc-west", "description": None}),
    ],
    "LocationRack": [
        _SdkRow("rack-east-r1", {"name": "r1", "role": "compute"}, one={"site": "site-east"}),
        _SdkRow("rack-west-r1", {"name": "r1", "role": None}, one={"site": "site-west"}),
        _SdkRow("rack-east-r2", {"name": "r2", "role": "network"}, one={"site": "site-east"}),
    ],
    "DcimDevice": [
        _SdkRow(
            "dev-leaf1",
            {"name": "leaf1", "status": "active", "description": "leaf one"},
            one={"rack": "rack-east-r1", "site": "site-east"},
            many={"tags": ["tag-red", "tag-blue"]},
        ),
        _SdkRow("dev-leaf2", {"name": "leaf2", "status": "planned", "description": None}, one={"site": "site-west"}),
        _SdkRow(
            "dev-spine1",
            {"name": "spine1", "status": "active", "description": "spine"},
            one={"rack": "rack-east-r2", "site": "site-east"},
            many={"tags": ["tag-green"]},
        ),
        _SdkRow(
            "dev-old1",
            {"name": "old1", "status": "offline", "description": "retired"},
            one={"rack": "rack-west-r1", "site": "site-east"},
            many={"tags": ["tag-blue"]},
        ),
    ],
}

# A destination that has drifted from the source: a changed tag description, a site with one
# tag fewer, a rack and a device missing, `leaf2` linked to a rack the source does not link,
# `leaf1` identical, and `old1` (filtered out of the source) still present.
INFRAHUB_STALE_DESTINATION_ROWS: dict[str, list[_SdkRow]] = {
    "BuiltinTag": [
        _SdkRow("d-tag-blue", {"name": "blue", "description": "Blue tag"}),
        _SdkRow("d-tag-red", {"name": "red", "description": "Old red"}),
    ],
    "LocationSite": [
        _SdkRow("d-site-east", {"name": "dc-east", "description": "EAST SITE"}, many={"tags": ["d-tag-blue"]}),
        _SdkRow("d-site-west", {"name": "dc-west", "description": None}),
    ],
    "LocationRack": [
        _SdkRow("d-rack-east-r1", {"name": "r1", "role": "compute"}, one={"site": "d-site-east"}),
        _SdkRow("d-rack-west-r1", {"name": "r1", "role": None}, one={"site": "d-site-west"}),
    ],
    "DcimDevice": [
        _SdkRow(
            "d-dev-leaf1",
            {"name": "leaf1", "status": "active", "description": "leaf one"},
            one={"rack": "d-rack-east-r1", "site": "d-site-east"},
            many={"tags": ["d-tag-blue", "d-tag-red"]},
        ),
        _SdkRow(
            "d-dev-leaf2",
            {"name": "leaf2", "status": "planned", "description": None},
            one={"rack": "d-rack-west-r1", "site": "d-site-west"},
        ),
        _SdkRow(
            "d-dev-old1",
            {"name": "old1", "status": "offline", "description": "retired"},
            one={"rack": "d-rack-west-r1", "site": "d-site-west"},
        ),
    ],
}


def real_config(*, source: str) -> SyncInstance:
    """A parsed configuration for the real-adapter cases, with the given source adapter name."""
    return SyncInstance(
        name="loader-parity",
        directory="/nonexistent/examples/loader-parity",
        source=SyncAdapter(name=source, settings={"url": "https://source.invalid"}),
        destination=SyncAdapter(name="infrahub", settings={"url": "https://destination.invalid"}),
        order=list(REAL_KINDS),
        schema_mapping=real_mapping(source=source),
    )


def netbox_source(config: SyncInstance) -> Adapter:
    return ParityNetboxAdapter(target="source", adapter=config.source, config=config)


def infrahub_source(config: SyncInstance) -> Adapter:
    return ParityInfrahubAdapter(target="source", config=config, rows=INFRAHUB_SOURCE_ROWS)


def empty_infrahub_destination(config: SyncInstance) -> Adapter:
    return ParityInfrahubAdapter(target="destination", config=config, rows={})


def stale_infrahub_destination(config: SyncInstance) -> Adapter:
    return ParityInfrahubAdapter(target="destination", config=config, rows=INFRAHUB_STALE_DESTINATION_ROWS)


class RealAdapterCase(NamedTuple):
    """One case planned through real adapter loaders: baseline name, source kind, both sides."""

    name: str
    source_name: str
    source: Callable[[SyncInstance], Adapter]
    destination: Callable[[SyncInstance], Adapter]


REAL_ADAPTER_CASES: tuple[RealAdapterCase, ...] = (
    RealAdapterCase("netbox_source_into_empty_infrahub", "netbox", netbox_source, empty_infrahub_destination),
    RealAdapterCase("netbox_source_against_stale_infrahub", "netbox", netbox_source, stale_infrahub_destination),
    RealAdapterCase("infrahub_source_against_stale_infrahub", "infrahub", infrahub_source, stale_infrahub_destination),
)


def plan_real_adapter_operations(case: RealAdapterCase) -> bytes:
    """Load both real adapters through `Potenda`, plan, and return the canonical operations."""
    config = real_config(source=case.source_name)
    run_id = "20261016T0900-00000009"
    directory = cache_root_for(config.name) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    potenda = Potenda(
        source=case.source(config),
        destination=case.destination(config),
        config=config,
        top_level=list(REAL_KINDS),
        run_dir=directory,
        run_id=run_id,
        show_progress=False,
        concurrent_load=False,
    )
    potenda.load_both_sides()
    potenda.write_plan(potenda.diff())
    return canonical_operations(read_operations_bytes(directory))


@pytest.mark.parametrize("case", REAL_ADAPTER_CASES, ids=[case.name for case in REAL_ADAPTER_CASES])
def test_plan_through_real_adapter_loaders_matches_the_loader_baseline(case: RealAdapterCase) -> None:
    """Valid data loaded by the real NetBox and Infrahub loaders plans as it did before the loader change."""
    actual = plan_real_adapter_operations(case)
    assert actual, f"{case.name} planned no operation, so it cannot detect a loader change"
    assert_matches_baseline(case.name, actual)
