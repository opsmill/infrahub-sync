"""Field selection in ``InfrahubAdapter.model_loader`` and ``list_existing_ids``.

Both methods name the fields they want in the SDK ``include`` argument, and both
feed the response to ``infrahub_node_to_diffsync``, which reads every mapped
attribute *and* every identifier. Identifiers and attributes are separate lists on
a DiffSync model, so a request built from only one of them omits fields the
converter needs.

Infrahub SDK 1.23.2 hides that: ``include`` is additive there — it opts extra
cardinality-many relationships into the query and never narrows the attribute
selection, so every attribute comes back whatever ``include`` says. These tests
therefore model the stricter contract, where the response carries only the
requested fields, and assert that the adapter asks for everything it reads. The
strict response is a boundary model of a future server/SDK contract, not a claim
about what 1.23.2 returns today.

The fake node here exposes exactly the requested fields: unrequested attributes
are absent from the node and from its schema's ``attribute_names``. This adapter
converts through ``self.schema[kind]`` rather than through the node's own schema,
so the client publishes the kinds it answered with and the harness reads that same
mapping.

The main-line suite also carried a guard asserting that an attributes-only peer
request leaves the peer unresolvable. It is not reproduced here: this adapter's
bounded peer hydration re-fetches a peer's identifiers by uuid, so the failure that
guard describes does not occur regardless of the request shape, and the resulting
``PeerIdentifierError`` contract is already covered by
``tests/adapters/test_infrahub_peer_identifier.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

from diffsync import Adapter

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SchemaMappingTransform,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel

# ---------------------------------------------------------------------------
# DiffSync models. Identifiers are deliberately absent from the attribute lists,
# which is the shape that discriminates an attributes-only request. DiffSync
# itself rejects a model whose identifiers and attributes overlap.
# ---------------------------------------------------------------------------


class LocationSite(InfrahubModel):
    """Peer model whose identifier is absent from its attribute list."""

    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("description",)

    name: str
    description: str | None = None


class InfraDevice(InfrahubModel):
    """Device with an identifier absent from its attributes and one peer."""

    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("description", "site")

    name: str
    description: str | None = None
    site: str | None = None


class InfraCircuit(InfrahubModel):
    """Model with two identifiers, so the request keeps their order."""

    _modelname = "InfraCircuit"
    _identifiers = ("name", "site")
    _attributes = ("description",)

    name: str
    site: str
    description: str | None = None


class InfraTag(InfrahubModel):
    """Model carrying identifiers only."""

    _modelname = "InfraTag"
    _identifiers = ("name",)
    _attributes = ()

    name: str


# ---------------------------------------------------------------------------
# Stand-ins for the SDK objects the adapter touches.
# ---------------------------------------------------------------------------


@dataclass
class FakeAttr:
    """Stand-in for an SDK attribute manager — only ``.value`` is read."""

    value: Any


@dataclass
class FakeAttrSchema:
    """Attribute schema entry, as read by ``_node_has_complete_attributes``."""

    name: str
    optional: bool = True


@dataclass
class FakeRelSchema:
    """Relationship schema entry, as read by the converter."""

    name: str
    peer: str
    cardinality: str = "one"


@dataclass
class FakeNodeSchema:
    """Stand-in for ``node._schema`` and for the adapter's schema entries."""

    kind: str
    attribute_names: list[str] = field(default_factory=list)
    attributes: list[FakeAttrSchema] = field(default_factory=list)
    relationships: list[FakeRelSchema] = field(default_factory=list)
    relationship_names: list[str] = field(default_factory=list)


@dataclass
class FakeRelatedNode:
    """Stand-in for ``RelatedNodeSync`` — only ``.id`` is read."""

    id: str | None


class StrictNode:
    """Node carrying only the fields the query asked for.

    Attributes that were not requested are absent from the node and from
    ``_schema.attribute_names``, which is how a strict ``include`` response would
    reach the converter.
    """

    def __init__(
        self,
        *,
        kind: str,
        node_id: str,
        attributes: dict[str, Any],
        relationships: dict[str, str] | None = None,
        rel_schemas: list[FakeRelSchema] | None = None,
    ) -> None:
        self.id = node_id
        self._kind = kind
        rel_schemas = rel_schemas or []
        self._schema = FakeNodeSchema(
            kind=kind,
            attribute_names=list(attributes),
            attributes=[FakeAttrSchema(name=name) for name in attributes],
            relationships=rel_schemas,
            relationship_names=[rel.name for rel in rel_schemas],
        )
        for name, value in attributes.items():
            setattr(self, name, FakeAttr(value=value))
        for name, peer_id in (relationships or {}).items():
            setattr(self, name, FakeRelatedNode(id=peer_id))

    def get_kind(self) -> str:
        return self._kind


class FakeSdkStore:
    """Stand-in for ``client.store``, keyed by ``(kind, key)``."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str], StrictNode] = {}
        self.set_calls: list[tuple[str, str]] = []

    def set(self, *, key: str, node: StrictNode) -> None:
        self._nodes[node.get_kind(), key] = node
        self.set_calls.append((node.get_kind(), key))

    def get(self, *, key: str, kind: str, raise_when_missing: bool = True) -> StrictNode | None:
        node = self._nodes.get((kind, key))
        if node is None and raise_when_missing:
            msg = f"{kind} {key} not in store"
            raise KeyError(msg)
        return node


@dataclass
class _Row:
    """One server row: its id, its full field set and its peer ids."""

    node_id: str
    attributes: dict[str, Any]
    relationships: dict[str, str] = field(default_factory=dict)


def _node_schema(kind: str, rows: list[_Row], rel_schemas: list[FakeRelSchema]) -> FakeNodeSchema:
    """The schema entry for ``kind``, built from the fields its rows carry."""

    names = list(dict.fromkeys(name for row in rows for name in row.attributes))
    return FakeNodeSchema(
        kind=kind,
        attribute_names=names,
        attributes=[FakeAttrSchema(name=name) for name in names],
        relationships=rel_schemas,
        relationship_names=[rel.name for rel in rel_schemas],
    )


class StrictClient:
    """Client stand-in whose ``all`` honours ``include`` strictly."""

    def __init__(self, rows: dict[str, list[_Row]], rel_schemas: dict[str, list[FakeRelSchema]] | None = None) -> None:
        self.store = FakeSdkStore()
        self._rows = rows
        self._rel_schemas = rel_schemas or {}
        self.all_calls: list[dict[str, Any]] = []
        # The adapter converts a node through `self.schema[kind]`, not through the
        # node's own schema, and `list_existing_ids` refuses a kind missing from it.
        # So the same fake node schemas the rows produce are published here, and the
        # harness reads this mapping as its schema.
        self.schemas: dict[str, FakeNodeSchema] = {
            kind: _node_schema(kind, rows_for_kind, self._rel_schemas.get(kind, []))
            for kind, rows_for_kind in rows.items()
        }

    def all(self, *, kind: str, include: list[str] | None = None, populate_store: bool = True) -> list[StrictNode]:
        self.all_calls.append({"kind": kind, "include": include, "populate_store": populate_store})
        requested = set(include or [])
        nodes = []
        for row in self._rows.get(kind, []):
            node = StrictNode(
                kind=kind,
                node_id=row.node_id,
                attributes={name: value for name, value in row.attributes.items() if name in requested},
                relationships=row.relationships,
                rel_schemas=self._rel_schemas.get(kind, []),
            )
            if populate_store:
                # The SDK registers fetched nodes under their node id.
                self.store.set(key=node.id, node=node)
            nodes.append(node)
        return nodes

    @staticmethod
    def get(**kwargs: Any) -> None:  # noqa: ANN401
        """Peer fallback fetch; no test here is meant to reach it."""

        msg = f"unexpected fallback fetch: {kwargs}"
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Adapter harness
# ---------------------------------------------------------------------------


class _Harness(InfrahubAdapter):
    """InfrahubAdapter with a real DiffSync store and no network setup."""

    def __init__(self, *, config: SyncConfig, client: StrictClient) -> None:
        Adapter.__init__(self)
        self.target = "test"
        self.config = config
        self.client = client
        self.schema = client.schemas  # ty: ignore[invalid-assignment]
        self.source_node = None
        self.owner_node = None
        self.continue_on_error = False
        # Normally set by `InfrahubAdapter.__init__`, which this harness bypasses.
        self._peer_unique_ids = {}


def _config(entries: dict[str, list[str]]) -> SyncConfig:
    """SyncConfig mapping each kind to the listed fields."""

    return SyncConfig(
        name="test",
        source=SyncAdapter(name="netbox", adapter="x:x"),
        destination=SyncAdapter(name="infrahub", adapter="x:x"),
        order=list(entries),
        schema_mapping=[
            SchemaMappingModel(
                name=kind,
                mapping=kind,
                identifiers=["name"],
                fields=[SchemaMappingField(name=name, mapping=name) for name in field_names],
            )
            for kind, field_names in entries.items()
        ],
    )


def _config_with_filter_and_transform(*, source_name: str) -> SyncConfig:
    """Config for ``LocationSite`` carrying a filter and a transform, with the
    given source adapter name (``"infrahub"`` for a same-type pair, anything
    else for a heterogeneous one)."""

    return SyncConfig(
        name="test",
        source=SyncAdapter(name=source_name, adapter="x:x"),
        destination=SyncAdapter(name="infrahub", adapter="x:x"),
        order=["LocationSite"],
        schema_mapping=[
            SchemaMappingModel(
                name="LocationSite",
                mapping="LocationSite",
                identifiers=["name"],
                filters=[SchemaMappingFilter(field="description", operation="==", value="east")],
                transforms=[SchemaMappingTransform(field="description", expression="{{ description.upper() }}")],
                fields=[
                    SchemaMappingField(name="name", mapping="name"),
                    SchemaMappingField(name="description", mapping="description"),
                ],
            )
        ],
    )


def _two_site_client() -> StrictClient:
    """Two ``LocationSite`` rows: one the filter keeps, one it would drop."""

    return StrictClient(
        rows={
            "LocationSite": [
                _Row("id-1", {"name": "dc-east", "description": "east"}),
                _Row("id-2", {"name": "dc-west", "description": "west"}),
            ]
        }
    )


_ModelT = TypeVar("_ModelT", bound=InfrahubModel)


def _loaded_names(adapter: _Harness, model: type[LocationSite]) -> list[str]:
    """Names of every loaded ``LocationSite``, narrowed from the store."""

    return [item.name for item in adapter.get_all(model) if isinstance(item, LocationSite)]


def test_model_loader_applies_filter_and_transform_for_the_source_role() -> None:
    """The logical source runs configured filters and transforms."""

    client = _two_site_client()
    adapter = _Harness(config=_config_with_filter_and_transform(source_name="infrahub"), client=client)
    adapter.target = "source"

    adapter.model_loader(model_name="LocationSite", model=LocationSite)

    loaded_names = _loaded_names(adapter, LocationSite)
    assert loaded_names == ["dc-east"]
    assert _loaded(adapter, LocationSite, "dc-east").description == "EAST"


def test_model_loader_skips_filter_and_transform_for_the_destination_role_same_adapter_type() -> None:
    """A same-type destination (Infrahub-to-Infrahub) loads its raw mapped state.

    Before the fix, the predicate compared ``config.source.name`` to the
    adapter's own type, which is true for both sides of an Infrahub-to-Infrahub
    sync. That let the destination run the source's filters and transforms too,
    corrupting the DiffSync comparison.
    """

    client = _two_site_client()
    adapter = _Harness(config=_config_with_filter_and_transform(source_name="infrahub"), client=client)
    adapter.target = "destination"

    adapter.model_loader(model_name="LocationSite", model=LocationSite)

    loaded_names = sorted(_loaded_names(adapter, LocationSite))
    assert loaded_names == ["dc-east", "dc-west"]
    assert _loaded(adapter, LocationSite, "dc-east").description == "east"
    assert _loaded(adapter, LocationSite, "dc-west").description == "west"


def test_model_loader_skips_filter_and_transform_for_a_heterogeneous_destination() -> None:
    """A heterogeneous pair (e.g. NetBox source, Infrahub destination) keeps its
    current behavior: the Infrahub destination still loads its raw mapped state."""

    client = _two_site_client()
    adapter = _Harness(config=_config_with_filter_and_transform(source_name="netbox"), client=client)
    adapter.target = "destination"

    adapter.model_loader(model_name="LocationSite", model=LocationSite)

    loaded_names = sorted(_loaded_names(adapter, LocationSite))
    assert loaded_names == ["dc-east", "dc-west"]
    assert _loaded(adapter, LocationSite, "dc-east").description == "east"


def _loaded(adapter: _Harness, model: type[_ModelT], unique_id: str) -> _ModelT:
    """Fetch a loaded object from the adapter store, narrowed to its own model."""

    obj = adapter.get(model, unique_id)
    assert isinstance(obj, model)
    return obj


def _include_for(client: StrictClient, kind: str) -> list[str] | None:
    """The ``include`` argument of the single ``all`` call for ``kind``."""

    calls = [call for call in client.all_calls if call["kind"] == kind]
    assert len(calls) == 1, f"expected one all() call for {kind}, got {len(calls)}"
    return calls[0]["include"]


# ---------------------------------------------------------------------------
# model_loader
# ---------------------------------------------------------------------------


def test_model_loader_requests_identifiers_alongside_attributes() -> None:
    client = StrictClient(rows={"LocationSite": [_Row("id-1", {"name": "dc-east", "description": "east"})]})
    adapter = _Harness(
        config=_config({"LocationSite": ["name", "description"]}),
        client=client,
    )

    adapter.model_loader(model_name="LocationSite", model=LocationSite)

    assert _include_for(client, "LocationSite") == ["name", "description"]
    assert client.all_calls[0]["populate_store"] is True


def test_model_loader_keeps_identifier_and_local_id_under_a_strict_response() -> None:
    client = StrictClient(rows={"LocationSite": [_Row("id-1", {"name": "dc-east", "description": "east"})]})
    adapter = _Harness(
        config=_config({"LocationSite": ["name", "description"]}),
        client=client,
    )

    adapter.model_loader(model_name="LocationSite", model=LocationSite)

    loaded = _loaded(adapter, LocationSite, "dc-east")
    assert loaded.name == "dc-east"
    assert loaded.description == "east"
    assert loaded.local_id == "id-1"
    assert ("LocationSite", "dc-east") in client.store.set_calls


def test_model_loader_keeps_identifier_order_for_a_multi_identifier_model() -> None:
    client = StrictClient(
        rows={"InfraCircuit": [_Row("id-1", {"name": "leaf1", "site": "dc-east", "description": "top"})]}
    )
    adapter = _Harness(
        config=_config({"InfraCircuit": ["name", "site", "description"]}),
        client=client,
    )

    adapter.model_loader(model_name="InfraCircuit", model=InfraCircuit)

    assert _include_for(client, "InfraCircuit") == ["name", "site", "description"]
    assert _loaded(adapter, InfraCircuit, "leaf1__dc-east").description == "top"


def test_model_loader_requests_identifiers_for_an_identifier_only_model() -> None:
    client = StrictClient(rows={"InfraTag": [_Row("id-1", {"name": "blue"})]})
    adapter = _Harness(
        config=_config({"InfraTag": ["name"]}),
        client=client,
    )

    adapter.model_loader(model_name="InfraTag", model=InfraTag)

    assert _include_for(client, "InfraTag") == ["name"]
    assert _loaded(adapter, InfraTag, "blue").local_id == "id-1"


def test_model_loader_resolves_a_relationship_peer_identifier_under_a_strict_response() -> None:
    """A peer loaded under a strict response must still carry its identifier.

    Peer resolution reads the peer node the earlier load put in the SDK store, so
    the peer's own ``include`` decides whether its identifier is there. The peer's
    attributes are optional, so the store-completeness re-fetch in
    ``resolve_peer_node`` does not fire and the ``include`` contract is what this
    measures.
    """

    client = StrictClient(
        rows={
            "LocationSite": [_Row("site-1", {"name": "dc-east", "description": "east"})],
            "InfraDevice": [
                _Row("dev-1", {"name": "leaf1", "description": "leaf"}, relationships={"site": "site-1"}),
            ],
        },
        rel_schemas={"InfraDevice": [FakeRelSchema(name="site", peer="LocationSite")]},
    )
    adapter = _Harness(
        config=_config({"LocationSite": ["name", "description"], "InfraDevice": ["name", "description", "site"]}),
        client=client,
    )
    adapter.LocationSite = LocationSite  # ty: ignore[unresolved-attribute]

    adapter.model_loader(model_name="LocationSite", model=LocationSite)
    adapter.model_loader(model_name="InfraDevice", model=InfraDevice)

    assert _loaded(adapter, InfraDevice, "leaf1").site == "dc-east"


# ---------------------------------------------------------------------------
# list_existing_ids
# ---------------------------------------------------------------------------


def test_list_existing_ids_requests_every_field_its_converter_reads() -> None:
    """The real converter builds the model, so attributes must be requested too."""

    client = StrictClient(
        rows={
            "InfraCircuit": [
                _Row("id-1", {"name": "leaf1", "site": "dc-east", "description": "top"}),
                _Row("id-2", {"name": "leaf2", "site": "dc-west", "description": "top"}),
            ]
        }
    )
    adapter = _Harness(
        config=_config({"InfraCircuit": ["name", "site", "description"]}),
        client=client,
    )
    adapter.InfraCircuit = InfraCircuit  # ty: ignore[unresolved-attribute]

    ids = list(adapter.list_existing_ids("InfraCircuit"))

    assert _include_for(client, "InfraCircuit") == ["name", "site", "description"]
    assert client.all_calls[0]["populate_store"] is False
    assert ids == ["leaf1__dc-east", "leaf2__dc-west"]


# ---------------------------------------------------------------------------
# Field union
# ---------------------------------------------------------------------------


def test_field_union_is_ordered_and_deduplicated() -> None:
    """DiffSync forbids a model whose identifiers and attributes overlap, so the
    union is checked here on a stand-in rather than through a model class."""

    from infrahub_sync.adapters.infrahub import identifier_and_attribute_fields

    class _Stub:
        _identifiers = ("name", "site")
        _attributes = ("site", "description", "name")

    assert identifier_and_attribute_fields(_Stub) == ["name", "site", "description"]  # ty: ignore[invalid-argument-type]
