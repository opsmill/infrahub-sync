"""Reference-field conversion in the NetBox, Nautobot and generic REST source adapters.

A schema-mapping field that declares `reference` resolves its value against the adapter's
DiffSync store. These tests run all three converters against a real `LocalStore` and pin what
each one emits, raises, or logs for an empty store, a store holding no matching peer, a
matching peer, a scalar reference, and several references at once. NetBox and the generic REST
adapter fail on a peer they cannot find; Nautobot warns and skips it.

They also pin the store contract those converters read: `get_all` hands back a list, so an
immediate copy of it is empty exactly when the list is.

The models below copy the shapes `examples/netbox_to_infrahub/netbox/sync_models.py` generates,
so the fields under test have real declared types and defaults.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from diffsync.store import BaseStore
from diffsync.store.local import LocalStore
from diffsync.store.redis import RedisStore

from infrahub_sync import SchemaMappingField, SchemaMappingModel

pytest.importorskip("pynetbox")
pytest.importorskip("pynautobot")

NetboxRecord = pytest.importorskip("pynetbox.core.response").Record
NautobotRecord = pytest.importorskip("pynautobot.core.response").Record

from infrahub_sync.adapters.genericrestapi import (  # noqa: E402
    GenericrestapiAdapter,
    GenericrestapiModel,
)
from infrahub_sync.adapters.nautobot import NautobotAdapter, NautobotModel  # noqa: E402
from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel  # noqa: E402

ADAPTERS = ("netbox", "nautobot", "genericrestapi")
FAILING_ADAPTERS = ("netbox", "genericrestapi")
"""The adapters that raise on a peer the store does not hold. Nautobot warns and skips."""


class NetboxBuiltinTag(NetboxModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    local_data: Any | None = None
    name: str


class NautobotBuiltinTag(NautobotModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    local_data: Any | None = None
    name: str


class GenericrestapiBuiltinTag(GenericrestapiModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    local_data: Any | None = None
    name: str


class NetboxOrganizationRIR(NetboxModel):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("primary_tag", "tags")
    local_data: Any | None = None
    name: str
    primary_tag: str | None = None
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


class NautobotOrganizationRIR(NautobotModel):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("primary_tag", "tags")
    local_data: Any | None = None
    name: str
    primary_tag: str | None = None
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


class GenericrestapiOrganizationRIR(GenericrestapiModel):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("primary_tag", "tags")
    local_data: Any | None = None
    name: str
    primary_tag: str | None = None
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


TAG_MODELS: dict[str, type[Any]] = {
    "netbox": NetboxBuiltinTag,
    "nautobot": NautobotBuiltinTag,
    "genericrestapi": GenericrestapiBuiltinTag,
}
RIR_MODELS: dict[str, type[Any]] = {
    "netbox": NetboxOrganizationRIR,
    "nautobot": NautobotOrganizationRIR,
    "genericrestapi": GenericrestapiOrganizationRIR,
}

# `local_id` is what the reference branch matches against, and each provider spells its
# identifiers differently: NetBox integers, Nautobot UUIDs, plain strings over generic REST.
PEER_IDS: dict[str, tuple[Any, Any]] = {
    "netbox": (20, 21),
    "nautobot": ("cccccccc-0000-0000-0000-000000000020", "cccccccc-0000-0000-0000-000000000021"),
    "genericrestapi": ("tag-20", "tag-21"),
}
OBJECT_IDS: dict[str, Any] = {
    "netbox": 2,
    "nautobot": "aaaaaaaa-0000-0000-0000-000000000002",
    "genericrestapi": "rir-2",
}

# The peer named for the first id sorts *after* the one named for the second, so a converter
# that emitted source order instead of sorted order would be visible.
FIRST_PEER_NAME = "zulu"
SECOND_PEER_NAME = "alpha"


def _source_object(adapter: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the object shape the adapter's loader hands to its converter.

    `netbox.py` and `nautobot.py` both load with `dict(node)` over a real SDK `Record`, and
    `Record` has no `.get`, so those converters only ever see the dict form. The generic REST
    adapter has no SDK and passes decoded JSON straight through.
    """
    if adapter == "netbox":
        return dict(NetboxRecord(payload, None, None))
    if adapter == "nautobot":
        return dict(NautobotRecord(payload, None, None))
    return payload


def _store(adapter: str, peers: dict[Any, str]) -> LocalStore:
    """A real `LocalStore` holding one peer per `local_id` -> name pair."""
    store = LocalStore()
    tag_model = TAG_MODELS[adapter]
    for local_id, name in peers.items():
        store.add(obj=tag_model(name=name, local_id=str(local_id)))
    return store


def _convert(
    adapter: str,
    obj: dict[str, Any],
    fields: list[SchemaMappingField],
    store: BaseStore,
) -> dict[str, Any]:
    """Run one adapter's converter offline, with no provider client."""
    model = RIR_MODELS[adapter]
    mapping = SchemaMappingModel(name=model._modelname, fields=fields)
    holder = SimpleNamespace(store=store)
    if adapter == "netbox":
        return NetboxAdapter.netbox_obj_to_diffsync(
            cast("NetboxAdapter", holder), obj, mapping, cast("type[NetboxModel]", model)
        )
    if adapter == "nautobot":
        return NautobotAdapter.nautobot_obj_to_diffsync(
            cast("NautobotAdapter", holder), obj, mapping, cast("type[NautobotModel]", model)
        )
    return GenericrestapiAdapter.obj_to_diffsync(
        cast("GenericrestapiAdapter", holder), obj, mapping, cast("type[GenericrestapiModel]", model)
    )


def _list_payload(adapter: str) -> dict[str, Any]:
    """A source object whose `tags` mapping points at both peers."""
    first, second = PEER_IDS[adapter]
    return {
        "id": OBJECT_IDS[adapter],
        "name": "RIPE",
        "tags": [{"id": first, "name": FIRST_PEER_NAME}, {"id": second, "name": SECOND_PEER_NAME}],
    }


def _scalar_payload(adapter: str) -> dict[str, Any]:
    """A source object whose `primary_tag` mapping points at the first peer."""
    first, _ = PEER_IDS[adapter]
    return {"id": OBJECT_IDS[adapter], "name": "RIPE", "primary_tag": {"id": first, "name": FIRST_PEER_NAME}}


LIST_FIELD = [SchemaMappingField(name="tags", mapping="tags", reference="BuiltinTag")]
SCALAR_FIELD = [SchemaMappingField(name="primary_tag", mapping="primary_tag", reference="BuiltinTag")]

# The two store states a converter can meet without a matching peer. Both leave `get_all`
# returning an empty list for `BuiltinTag`: an untouched store has nothing, and an unrelated
# peer is filed under its own model name.
NO_MATCHING_PEER = (
    pytest.param({}, id="empty-store"),
    pytest.param({"999": "unrelated"}, id="no-matching-peer"),
)


# --- The store contract the deleted guards relied on --------------------------------------


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("peer_count", [0, 1, 2])
def test_store_get_all_copy_is_empty_exactly_when_the_store_result_is(adapter: str, peer_count: int) -> None:
    """`get_all` returns a list, so copying it cannot change whether it is empty.

    This is the property the converters' reference branch is built on: it copies the result of
    `get_all` and then decides on the copy. A backend returning a one-shot iterator, or an
    object whose truth value ignored its length, would break that and is what the parametrized
    store states here would catch.
    """
    peers = dict(list({"20": FIRST_PEER_NAME, "21": SECOND_PEER_NAME}.items())[:peer_count])
    store = _store(adapter, peers)

    all_nodes_for_reference = store.get_all(model="BuiltinTag")
    nodes = [item for item in all_nodes_for_reference]  # noqa: C416 - the copy under test

    assert type(all_nodes_for_reference) is list
    assert len(nodes) == peer_count
    assert bool(nodes) == bool(all_nodes_for_reference)


def test_the_only_store_backends_are_the_two_the_repository_builds() -> None:
    """`LocalStore` and `RedisStore` are the whole set of stores a run can be given.

    `infrahub_sync/utils.py` builds one of exactly these two. A third backend would have to be
    re-checked against the list contract asserted above before the converters could trust it.
    """
    assert set(BaseStore.__subclasses__()) == {LocalStore, RedisStore}


# --- Peers the store holds ----------------------------------------------------------------


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_scalar_reference_resolves_to_the_peer_unique_id(adapter: str) -> None:
    """A scalar `reference` field emits the matching peer's unique id."""
    first, _ = PEER_IDS[adapter]
    obj = _source_object(adapter, _scalar_payload(adapter))
    store = _store(adapter, {first: FIRST_PEER_NAME})

    data = _convert(adapter, obj, SCALAR_FIELD, store)

    assert data["primary_tag"] == FIRST_PEER_NAME


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_list_reference_resolves_every_peer_in_sorted_order(adapter: str) -> None:
    """A list `reference` field emits every peer's unique id, sorted, not in source order."""
    first, second = PEER_IDS[adapter]
    obj = _source_object(adapter, _list_payload(adapter))
    store = _store(adapter, {first: FIRST_PEER_NAME, second: SECOND_PEER_NAME})

    data = _convert(adapter, obj, LIST_FIELD, store)

    assert data["tags"] == [SECOND_PEER_NAME, FIRST_PEER_NAME]
    assert data["tags"] == sorted(data["tags"])


# --- Peers the store does not hold --------------------------------------------------------


@pytest.mark.parametrize("adapter", FAILING_ADAPTERS)
@pytest.mark.parametrize("peers", NO_MATCHING_PEER)
def test_missing_scalar_peer_raises(adapter: str, peers: dict[Any, str]) -> None:
    """NetBox and generic REST fail on a scalar peer the store does not hold.

    Their messages differ: NetBox names the field, generic REST names the model class.
    """
    first, _ = PEER_IDS[adapter]
    obj = _source_object(adapter, _scalar_payload(adapter))
    expected = "primary_tag" if adapter == "netbox" else str(RIR_MODELS[adapter])

    with pytest.raises(IndexError) as err:
        _convert(adapter, obj, SCALAR_FIELD, _store(adapter, peers))

    assert str(err.value) == f"Unable to locate the node {expected} {first}"


@pytest.mark.parametrize("adapter", FAILING_ADAPTERS)
@pytest.mark.parametrize("peers", NO_MATCHING_PEER)
def test_missing_list_peer_raises(adapter: str, peers: dict[Any, str]) -> None:
    """NetBox and generic REST fail on the first list peer the store does not hold."""
    first, _ = PEER_IDS[adapter]
    obj = _source_object(adapter, _list_payload(adapter))

    with pytest.raises(IndexError) as err:
        _convert(adapter, obj, LIST_FIELD, _store(adapter, peers))

    assert str(err.value) == f"Unable to locate the node BuiltinTag {first}"


@pytest.mark.parametrize("peers", NO_MATCHING_PEER)
def test_nautobot_warns_and_drops_a_missing_scalar_peer(
    peers: dict[Any, str], caplog: pytest.LogCaptureFixture
) -> None:
    """Nautobot logs the unresolved scalar peer and leaves the field out of the payload."""
    first, _ = PEER_IDS["nautobot"]
    obj = _source_object("nautobot", _scalar_payload("nautobot"))

    with caplog.at_level("WARNING", logger="infrahub_sync.adapters.nautobot"):
        data = _convert("nautobot", obj, SCALAR_FIELD, _store("nautobot", peers))

    assert "primary_tag" not in data
    assert caplog.messages == [f"Unable to locate the node primary_tag {first}"]


@pytest.mark.parametrize("peers", NO_MATCHING_PEER)
def test_nautobot_warns_once_per_missing_list_peer_and_emits_an_empty_list(
    peers: dict[Any, str], caplog: pytest.LogCaptureFixture
) -> None:
    """Nautobot logs each unresolved list peer and still emits the field, empty."""
    first, second = PEER_IDS["nautobot"]
    obj = _source_object("nautobot", _list_payload("nautobot"))

    with caplog.at_level("WARNING", logger="infrahub_sync.adapters.nautobot"):
        data = _convert("nautobot", obj, LIST_FIELD, _store("nautobot", peers))

    assert data["tags"] == []
    assert caplog.messages == [
        f"Unable to locate the node tags {first}",
        f"Unable to locate the node tags {second}",
    ]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_one_missing_peer_does_not_lose_the_peers_that_resolved(adapter: str) -> None:
    """With only the second peer stored, NetBox and generic REST fail; Nautobot keeps the rest."""
    _, second = PEER_IDS[adapter]
    obj = _source_object(adapter, _list_payload(adapter))
    store = _store(adapter, {second: SECOND_PEER_NAME})

    if adapter == "nautobot":
        assert _convert(adapter, obj, LIST_FIELD, store)["tags"] == [SECOND_PEER_NAME]
        return

    with pytest.raises(IndexError):
        _convert(adapter, obj, LIST_FIELD, store)


# --- A reference that is not a peer lookup ------------------------------------------------


@pytest.mark.parametrize("adapter", FAILING_ADAPTERS)
def test_scalar_reference_to_a_plain_value_passes_straight_through(adapter: str) -> None:
    """NetBox and generic REST copy a non-dict scalar reference without consulting the store.

    Some sources reference a peer by its identifier directly rather than by `{"id": ...}`.
    """
    obj = _source_object(adapter, {"id": OBJECT_IDS[adapter], "name": "RIPE", "primary_tag": "edge-direct"})

    data = _convert(adapter, obj, SCALAR_FIELD, _store(adapter, {}))

    assert data["primary_tag"] == "edge-direct"


def test_nautobot_scalar_reference_to_a_plain_value_fails() -> None:
    """Nautobot has no non-dict branch: it calls `.get` on the value and fails.

    Characterized as it stands today, not as a contract worth relying on.
    """
    obj = _source_object("nautobot", {"id": OBJECT_IDS["nautobot"], "name": "RIPE", "primary_tag": "edge-direct"})

    with pytest.raises(AttributeError):
        _convert("nautobot", obj, SCALAR_FIELD, _store("nautobot", {}))
