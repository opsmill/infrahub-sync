"""ACI reference conversion without a provider client or optional provider SDKs."""

from __future__ import annotations

from typing import Any

import pytest
from diffsync.store.local import LocalStore

from infrahub_sync import SchemaMappingField, SchemaMappingModel
from infrahub_sync.adapters.aci import AciAdapter, AciModel


class AciPeer(AciModel):
    """A stored model that source records can reference."""

    _modelname = "AciPeer"
    _identifiers = ("name",)
    name: str


class AciRecord(AciModel):
    """An ACI-shaped model with scalar and list reference fields."""

    _modelname = "AciRecord"
    _identifiers = ("name",)
    _attributes = ("primary_peer", "peers")
    name: str
    primary_peer: str | None = None
    peers: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


SCALAR_FIELD = SchemaMappingField(
    name="primary_peer",
    mapping="attributes.primary",
    reference=AciPeer._modelname,
)
LIST_FIELD = SchemaMappingField(
    name="peers",
    mapping="attributes.peers",
    reference=AciPeer._modelname,
)


def _adapter(peers: dict[str, str]) -> AciAdapter:
    """Build an offline adapter backed by a real DiffSync `LocalStore`."""
    adapter = object.__new__(AciAdapter)
    adapter.store = LocalStore()
    for local_id, name in peers.items():
        peer = AciPeer(name=name)
        peer.local_id = local_id
        adapter.store.add(obj=peer)
    return adapter


def _convert(
    *,
    adapter: AciAdapter,
    fields: list[SchemaMappingField],
    attributes: dict[str, Any],
) -> dict[str, Any]:
    """Run the real ACI converter against its ordinary nested source shape."""
    mapping = SchemaMappingModel(name=AciRecord._modelname, fields=fields)
    source = {"id": "record-1", "attributes": attributes}
    return adapter.obj_to_diffsync(obj=source, mapping=mapping, model=AciRecord)


def test_empty_store_preserves_an_absent_scalar_reference() -> None:
    """An absent scalar reference remains `None` when the store is empty."""
    adapter = _adapter({})

    data = _convert(adapter=adapter, fields=[SCALAR_FIELD], attributes={})

    assert type(adapter.store.get_all(model=AciPeer._modelname)) is list
    assert data == {"local_id": "record-1", "primary_peer": None}


@pytest.mark.parametrize(
    "peers",
    [
        pytest.param({}, id="empty-store"),
        pytest.param({"other": "unrelated"}, id="peer-absent"),
    ],
)
def test_missing_list_peer_raises_the_existing_error(peers: dict[str, str]) -> None:
    """An unresolved list item raises the exact existing `ValueError`."""
    adapter = _adapter(peers)

    with pytest.raises(ValueError) as error:
        _convert(
            adapter=adapter,
            fields=[LIST_FIELD],
            attributes={"peers": ["missing"]},
        )

    assert str(error.value) == "Unable to locate the node AciPeer missing"


def test_scalar_reference_resolves_to_the_matching_peer_unique_id() -> None:
    """A scalar reference resolves by local id and emits the peer unique id."""
    adapter = _adapter({"peer-1": "zulu"})

    data = _convert(
        adapter=adapter,
        fields=[SCALAR_FIELD],
        attributes={"primary": "peer-1"},
    )

    assert data["primary_peer"] == "zulu"


def test_list_references_skip_falsy_items_and_sort_resolved_unique_ids() -> None:
    """List conversion skips falsy entries and sorts contrary to source order."""
    adapter = _adapter({"peer-1": "zulu", "peer-2": "alpha"})

    data = _convert(
        adapter=adapter,
        fields=[LIST_FIELD],
        attributes={
            "peers": [
                None,
                "",
                "peer-1",
                False,
                0,
                "peer-2",
            ]
        },
    )

    assert data["peers"] == ["alpha", "zulu"]
