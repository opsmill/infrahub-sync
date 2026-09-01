"""Covers model_loader()'s explicit re-set of the SDK client store after a bulk fetch.

`client.all(kind=..., populate_store=True)` fetches every node of a kind in one response
and, as a side effect, writes each into `client.store` for later peer lookups. Observed
against a real server: for a *same-kind self-reference* (e.g. `LocationLocation.parent ->
LocationLocation`), the store can end up holding a shallow stub for a node instead of its
full entry -- the SDK writes a reference-only stub while parsing another record's
relationship to that same node within the same response, and if that write lands after the
node's own full entry, it silently overwrites it. `_node_has_complete_attributes` only
checks non-optional *attributes*, not relationships, so the stub reads as "complete" and
the fallback re-fetch in `resolve_peer_node` never fires -- the stub's relationship comes
back empty and propagates as a missing required field.

`model_loader` re-sets the store from `nodes` itself right after the bulk fetch so this
call's own fully-populated objects are always what a same-kind peer lookup finds,
regardless of what the SDK wrote to the store while parsing the response.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from diffsync.exceptions import ObjectNotFound

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter


class _FakeStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], object] = {}
        self.set_calls: list[tuple[str, str]] = []

    def get(
        self,
        *,
        model: str | None = None,
        identifier: str | None = None,
        kind: str | None = None,
        key: str | None = None,
        raise_when_missing: bool = True,
    ) -> object | None:
        store_key = (model or kind or "", identifier or key or "")
        if store_key not in self._items:
            if raise_when_missing:
                msg = f"{store_key} not present in fake store"
                raise ObjectNotFound(msg)
            return None
        return self._items[store_key]

    def set(self, *, key: str, node: object) -> None:  # match client.store.set signature
        kind = node._schema.kind
        self.set_calls.append((kind, key))
        self._items[kind, key] = node
        if node_id := getattr(node, "id", None):
            self._items[kind, node_id] = node


def _make_sdk_node(kind: str, node_id: str, attrs: dict[str, object], parent_id: str | None) -> SimpleNamespace:
    node = SimpleNamespace(
        id=node_id,
        _schema=SimpleNamespace(
            kind=kind,
            attribute_names=list(attrs),
            attributes=[SimpleNamespace(name=name, optional=False) for name in attrs],
            relationships=[SimpleNamespace(name="parent", peer=kind, cardinality="one")],
        ),
    )
    for name, value in attrs.items():
        setattr(node, name, SimpleNamespace(value=value))
    node.parent = SimpleNamespace(id=parent_id)
    return node


class _FakeClient:
    """`.all()` seeds a stale stub for `stub_node_id` before returning the full nodes --
    reproducing the SDK behavior this fix works around."""

    def __init__(self, *, full_nodes: list[SimpleNamespace], stub_node: SimpleNamespace | None = None) -> None:
        self.store = _FakeStore()
        self._full_nodes = full_nodes
        self._stub_node = stub_node
        self.all_calls: list[dict[str, object]] = []

    def all(self, **kwargs: object) -> list[SimpleNamespace]:
        self.all_calls.append(kwargs)
        if self._stub_node is not None:
            self.store.set(key=str(self._stub_node.id), node=self._stub_node)
        return self._full_nodes

    def get(self, **_kwargs: object) -> object | None:  # noqa: PLR6301
        msg = "fallback client.get() should not be needed once the store holds full nodes"
        raise AssertionError(msg)


class _FakeDiffSyncModel:
    """Loosely mimics the generated pydantic model: `status` has no default, so
    constructing one without it must fail -- the same shape as the real bug, where
    `LocationLocation(**peer_data)` raised pydantic's "Field required" for `location_type`
    when `peer_data` came from a stub missing that relationship."""

    _identifiers = ("name",)
    _attributes = ("status", "parent")

    def __init__(self, **kwargs: object) -> None:
        if "status" not in kwargs:
            msg = "status: Field required"
            raise ValueError(msg)
        self._kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classmethod
    def create_unique_id(cls, **kwargs: object) -> str:
        return "|".join(str(kwargs[k]) for k in cls._identifiers)

    def get_unique_id(self) -> str:
        return type(self).create_unique_id(**self._kwargs)

    @classmethod
    def filter_records(cls, records: list[dict], **_kwargs: object) -> list[dict]:
        return records

    @classmethod
    def transform_records(cls, records: list[dict], **_kwargs: object) -> list[dict]:
        return records

    @classmethod
    def is_list(cls, name: str) -> bool:  # noqa: ARG003
        return False


class _Harness(InfrahubAdapter):
    """Exercise the real model_loader()/infrahub_node_to_diffsync() without a live server."""

    client: _FakeClient

    def __init__(self, *, client: _FakeClient) -> None:
        self.client = client
        self.store = _FakeStore()  # ty: ignore[invalid-assignment]
        self.continue_on_error = False
        self._peer_unique_ids = {}
        self._instances: list[Any] = []
        self.type = "Infrahub"
        self.LocationLocation = _FakeDiffSyncModel
        self.schema = {"LocationLocation": SimpleNamespace(kind="LocationLocation")}  # ty: ignore[invalid-assignment]
        self.config = SyncConfig(
            name="test",
            source=SyncAdapter(name="source", adapter="x:x"),
            destination=SyncAdapter(name="destination", adapter="x:x"),
            schema_mapping=[
                SchemaMappingModel(
                    name="LocationLocation",
                    mapping="dcim.locations",
                    identifiers=["name"],
                    fields=[
                        SchemaMappingField(name="name", mapping="name"),
                        SchemaMappingField(name="status", mapping="status"),
                        SchemaMappingField(name="parent", mapping="parent", reference="LocationLocation"),
                    ],
                ),
            ],
        )

    def update_or_add_model_instance(self, item: object) -> None:  # ty: ignore[invalid-method-override]
        self._instances.append(item)


def test_model_loader_resets_store_so_a_self_reference_finds_the_full_node() -> None:
    """Aisle-06 is listed *before* Room-05, so its `parent` relationship gets resolved
    while the store holds only the stale stub `client.all()` wrote -- unless model_loader's
    re-set has already run for the full `nodes` list by then.
    """
    room = _make_sdk_node("LocationLocation", "room-id", {"name": "Room-05", "status": "active"}, parent_id=None)
    aisle = _make_sdk_node(
        "LocationLocation", "aisle-id", {"name": "Aisle-06", "status": "active"}, parent_id="room-id"
    )
    # The stub the SDK writes while parsing Aisle-06's `parent` relationship to Room-05:
    # id and name only, same as a reference-only fragment of a larger response -- no
    # `status`, which `_FakeDiffSyncModel` (like the real generated model) requires.
    stale_room_stub = SimpleNamespace(
        id="room-id",
        _schema=SimpleNamespace(kind="LocationLocation", attribute_names=["name"], attributes=[], relationships=[]),
        name=SimpleNamespace(value="Room-05"),
    )

    client = _FakeClient(full_nodes=[aisle, room], stub_node=stale_room_stub)
    adapter = _Harness(client=client)

    # No ValueError("status: Field required") -- constructing Room-05's peer instance
    # must use the full node the re-set placed in the store, not the incomplete stub.
    adapter.model_loader("LocationLocation", _FakeDiffSyncModel)

    aisle_item = next(item for item in adapter._instances if item.name == "Aisle-06")
    assert aisle_item.parent == "Room-05"


def test_model_loader_re_sets_every_fetched_node_into_the_store() -> None:
    """The re-set loop itself: every node from `client.all()` lands back in the store,
    keyed by its own id, independent of any relationship resolution outcome."""
    room = _make_sdk_node("LocationLocation", "room-id", {"name": "Room-05", "status": "active"}, parent_id=None)
    aisle = _make_sdk_node(
        "LocationLocation", "aisle-id", {"name": "Aisle-06", "status": "active"}, parent_id="room-id"
    )
    client = _FakeClient(full_nodes=[room, aisle])
    adapter = _Harness(client=client)

    adapter.model_loader("LocationLocation", _FakeDiffSyncModel)

    assert ("LocationLocation", "room-id") in client.store.set_calls
    assert ("LocationLocation", "aisle-id") in client.store.set_calls
    assert client.store.get(kind="LocationLocation", key="room-id") is room
