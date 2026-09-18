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
from typing import TYPE_CHECKING, Any, cast

import pytest
from diffsync.store.local import LocalStore

from infrahub_sync import SchemaMappingField, SchemaMappingModel

if TYPE_CHECKING:
    from diffsync.store import BaseStore

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

# The two store states a converter can meet without a matching peer. `get_all("BuiltinTag")`
# returns no objects for the untouched store, and one for the second: a `BuiltinTag` like the
# one being looked for, whose `local_id` is not the id the source object asks for.
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


# --- List peers, by the shape of their `id` -----------------------------------------------

SDK_ADAPTERS = ("netbox", "nautobot")
"""The adapters whose loaders convert provider records through an SDK `Record`."""

THIRD_PEER_IDS: dict[str, Any] = {
    "netbox": 22,
    "nautobot": "cccccccc-0000-0000-0000-000000000022",
    "genericrestapi": "tag-22",
}
THIRD_PEER_NAME = "mike"
"""Sorts between `SECOND_PEER_NAME` and `FIRST_PEER_NAME`, so a three-peer list only comes out
in this order if the converter really sorts rather than reversing or bucketing."""

# The two ways a list peer can arrive without an id the store can be searched for. Both leave
# `node.get("id", None)` returning `None`, and the lookup then runs against the string "None".
NO_USABLE_PEER_ID = (
    pytest.param({"name": FIRST_PEER_NAME}, id="id-key-absent"),
    pytest.param({"id": None, "name": FIRST_PEER_NAME}, id="id-is-null"),
)


# The falsy entries a peer list can carry besides an empty mapping.
BLANK_PEER_ENTRIES = (
    pytest.param(None, id="null"),
    pytest.param("", id="empty-string"),
    pytest.param(0, id="zero"),
)


def _peer_list_payload(adapter: str, peers: list[Any]) -> dict[str, Any]:
    """A source object whose `tags` mapping points at exactly `peers`."""
    return {"id": OBJECT_IDS[adapter], "name": "RIPE", "tags": peers}


@pytest.mark.parametrize("adapter", FAILING_ADAPTERS)
@pytest.mark.parametrize("peer", NO_USABLE_PEER_ID)
def test_list_peer_without_a_usable_id_is_looked_up_as_none_and_fails(adapter: str, peer: dict[str, Any]) -> None:
    """A list peer with no id is not skipped: it is searched for under the string "None"."""
    obj = _source_object(adapter, _peer_list_payload(adapter, [peer]))

    with pytest.raises(IndexError) as err:
        _convert(adapter, obj, LIST_FIELD, _store(adapter, {}))

    assert str(err.value) == "Unable to locate the node BuiltinTag None"


@pytest.mark.parametrize("peer", NO_USABLE_PEER_ID)
def test_nautobot_warns_for_a_list_peer_without_a_usable_id(
    peer: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Nautobot reaches the same "None" lookup and warns instead of raising."""
    obj = _source_object("nautobot", _peer_list_payload("nautobot", [peer]))

    with caplog.at_level("WARNING", logger="infrahub_sync.adapters.nautobot"):
        data = _convert("nautobot", obj, LIST_FIELD, _store("nautobot", {}))

    assert data["tags"] == []
    assert caplog.messages == ["Unable to locate the node tags None"]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_list_peer_with_a_zero_id_is_looked_up_not_skipped(adapter: str) -> None:
    """`0` is a falsy id, but it is still an id: the peer stored under "0" resolves."""
    obj = _source_object(adapter, _peer_list_payload(adapter, [{"id": 0, "name": FIRST_PEER_NAME}]))

    data = _convert(adapter, obj, LIST_FIELD, _store(adapter, {0: FIRST_PEER_NAME}))

    assert data["tags"] == [FIRST_PEER_NAME]


@pytest.mark.parametrize("adapter", FAILING_ADAPTERS)
def test_unstored_zero_id_list_peer_fails_naming_the_zero(adapter: str) -> None:
    """A zero id the store does not hold fails like any other unresolved peer."""
    obj = _source_object(adapter, _peer_list_payload(adapter, [{"id": 0, "name": FIRST_PEER_NAME}]))

    with pytest.raises(IndexError) as err:
        _convert(adapter, obj, LIST_FIELD, _store(adapter, {}))

    assert str(err.value) == "Unable to locate the node BuiltinTag 0"


def test_nautobot_warns_for_an_unstored_zero_id_list_peer(caplog: pytest.LogCaptureFixture) -> None:
    """Nautobot names the zero in its warning rather than reporting a missing id."""
    obj = _source_object("nautobot", _peer_list_payload("nautobot", [{"id": 0, "name": FIRST_PEER_NAME}]))

    with caplog.at_level("WARNING", logger="infrahub_sync.adapters.nautobot"):
        data = _convert("nautobot", obj, LIST_FIELD, _store("nautobot", {}))

    assert data["tags"] == []
    assert caplog.messages == ["Unable to locate the node tags 0"]


# --- Empty peer entries -------------------------------------------------------------------


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_an_empty_peer_entry_is_skipped_without_touching_the_store(adapter: str) -> None:
    """An empty mapping among the peers is dropped by the falsy check, not looked up.

    Were it looked up, NetBox and generic REST would raise on it before reaching the real peer.
    """
    first, _ = PEER_IDS[adapter]
    obj = _source_object(adapter, _peer_list_payload(adapter, [{}, {"id": first, "name": FIRST_PEER_NAME}]))

    data = _convert(adapter, obj, LIST_FIELD, _store(adapter, {first: FIRST_PEER_NAME}))

    assert data["tags"] == [FIRST_PEER_NAME]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_a_list_of_only_empty_peer_entries_emits_an_empty_list(adapter: str) -> None:
    """Every peer skipped still leaves the field present and empty, not absent."""
    obj = _source_object(adapter, _peer_list_payload(adapter, [{}, {}]))

    data = _convert(adapter, obj, LIST_FIELD, _store(adapter, {}))

    assert data["tags"] == []


@pytest.mark.parametrize("blank", BLANK_PEER_ENTRIES)
def test_genericrestapi_skips_blank_peer_entries(blank: object) -> None:
    """Generic REST passes decoded JSON straight through, so a blank entry stays blank and is
    skipped by the falsy check while the real peer beside it still resolves."""
    first, _ = PEER_IDS["genericrestapi"]
    payload = _peer_list_payload("genericrestapi", [blank, {"id": first, "name": FIRST_PEER_NAME}])

    data = _convert("genericrestapi", payload, LIST_FIELD, _store("genericrestapi", {first: FIRST_PEER_NAME}))

    assert data["tags"] == [FIRST_PEER_NAME]


@pytest.mark.parametrize("adapter", SDK_ADAPTERS)
@pytest.mark.parametrize("blank", BLANK_PEER_ENTRIES)
def test_sdk_peer_lists_mixing_blanks_and_mappings_reach_the_converter_as_records(adapter: str, blank: object) -> None:
    """A blank entry stops the SDK flattening the peer list, and the converter then fails.

    `Record.__iter__` only replaces a list of peers with dicts when *every* entry is a
    `Record`; one blank entry and the list is handed over untouched, still holding `Record`
    objects. `Record` has no `.get`, so the peer loop raises. Characterized as it stands, not
    as a contract worth relying on.
    """
    first, _ = PEER_IDS[adapter]
    obj = _source_object(adapter, _peer_list_payload(adapter, [blank, {"id": first, "name": FIRST_PEER_NAME}]))

    assert any(isinstance(peer, (NetboxRecord, NautobotRecord)) for peer in obj["tags"])

    with pytest.raises(AttributeError, match=r"has no attribute .get."):
        _convert(adapter, obj, LIST_FIELD, _store(adapter, {first: FIRST_PEER_NAME}))


# --- Peers that are not mappings ----------------------------------------------------------


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_a_list_peer_that_is_not_a_mapping_raises_attribute_error(adapter: str) -> None:
    """The peer loop calls `.get` on whatever it is handed and does not guard the call."""
    obj = _source_object(adapter, _peer_list_payload(adapter, ["not-a-mapping"]))

    with pytest.raises(AttributeError) as err:
        _convert(adapter, obj, LIST_FIELD, _store(adapter, {}))

    assert str(err.value) == "'str' object has no attribute 'get'"


# --- Ordering over more than two peers ----------------------------------------------------


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_three_list_peers_come_out_fully_sorted(adapter: str) -> None:
    """Three peers given in an order that is neither sorted nor its reverse."""
    first, second = PEER_IDS[adapter]
    third = THIRD_PEER_IDS[adapter]
    peers = {first: FIRST_PEER_NAME, second: SECOND_PEER_NAME, third: THIRD_PEER_NAME}
    obj = _source_object(
        adapter,
        _peer_list_payload(
            adapter,
            [
                {"id": first, "name": FIRST_PEER_NAME},
                {"id": third, "name": THIRD_PEER_NAME},
                {"id": second, "name": SECOND_PEER_NAME},
            ],
        ),
    )

    data = _convert(adapter, obj, LIST_FIELD, _store(adapter, peers))

    assert data["tags"] == [SECOND_PEER_NAME, THIRD_PEER_NAME, FIRST_PEER_NAME]


# --- The peer shapes the loaders actually hand to the converter ---------------------------


@pytest.mark.parametrize("adapter", SDK_ADAPTERS)
def test_sdk_loading_hands_the_peer_loop_mappings(adapter: str) -> None:
    """`model_loader` converts each record with `dict(node)` before the converter sees it.

    `Record.__iter__` replaces a nested `Record`, and a list of them, with dicts, so an
    ordinary peer list arrives as mappings. This is why the peer loop can call `.get` on a
    peer directly.
    """
    first, second = PEER_IDS[adapter]
    payload = _peer_list_payload(
        adapter, [{"id": first, "name": FIRST_PEER_NAME}, {"id": second, "name": SECOND_PEER_NAME}]
    )

    obj = _source_object(adapter, payload)

    assert [type(peer) for peer in obj["tags"]] == [dict, dict]
    assert obj["tags"] == [
        {"id": first, "name": FIRST_PEER_NAME},
        {"id": second, "name": SECOND_PEER_NAME},
    ]
