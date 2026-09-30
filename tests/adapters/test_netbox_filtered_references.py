"""NetBox references distinguish filtered peers from missing source records."""

from __future__ import annotations

from collections import UserDict
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import MagicMock

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingFilter, SchemaMappingModel, SyncAdapter, SyncConfig

if TYPE_CHECKING:
    from infrahub_sync.adapters.netbox import NetboxAdapter

pytest.importorskip("pynetbox")

from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel
from infrahub_sync.plan.derive import operations_from_diff


class BuiltinTag(NetboxModel):
    """Peer whose NetBox group determines whether its mapping retains it."""

    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    name: str


class OptionalOne(NetboxModel):
    """A cardinality-one relationship that permits an absent peer."""

    _modelname = "Example"
    _identifiers = ("name",)
    _attributes = ("peer",)
    name: str
    peer: str | None = None


class RequiredOne(NetboxModel):
    """A cardinality-one relationship that requires a peer."""

    _modelname = "Example"
    _identifiers = ("name",)
    _attributes = ("peer",)
    name: str
    peer: str


class OptionalMany(NetboxModel):
    """A cardinality-many relationship that permits an empty list."""

    _modelname = "Example"
    _identifiers = ("name",)
    _attributes = ("peer",)
    name: str
    peer: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


class RequiredMany(NetboxModel):
    """A cardinality-many relationship that needs a retained peer after filtering."""

    _modelname = "Example"
    _identifiers = ("name",)
    _attributes = ("peer",)
    name: str
    peer: list[str] = []  # noqa: RUF012 - `is_list` reads this default


class _Record(UserDict):
    """Make synthetic rows behave like pynetbox records under ``dict(row)``."""


class _TestAdapter(NetboxAdapter):
    """Set the two model kinds in the same order as a production source load."""

    top_level: ClassVar[list[str]] = ["BuiltinTag", "Example"]
    BuiltinTag: ClassVar[type[NetboxModel]] = BuiltinTag
    Example: ClassVar[type[NetboxModel]] = OptionalOne


PEERS = [
    {"id": 10, "name": "retained", "group": {"name": "group"}},
    {"id": 20, "name": "excluded", "group": None},
]


def _adapter(monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel], rows: list[dict[str, Any]]) -> NetboxAdapter:
    """Load a filtered peer endpoint and an owning endpoint through the real loader."""
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    config = SyncConfig(
        name="filtered-reference",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="BuiltinTag",
                mapping="extras.tags",
                filters=[SchemaMappingFilter(field="group", operation="is_not_empty")],
                fields=[SchemaMappingField(name="name", mapping="name")],
            ),
            SchemaMappingModel(
                name="Example",
                mapping="dcim.examples",
                fields=[
                    SchemaMappingField(name="name", mapping="name"),
                    SchemaMappingField(name="peer", mapping="peer", reference="BuiltinTag"),
                ],
            ),
        ],
    )
    _TestAdapter.Example = model
    adapter = _TestAdapter(target="source", adapter=SyncAdapter(name="netbox"), config=config)
    adapter.client.extras.tags.all.return_value = [_Record(row) for row in PEERS]
    adapter.client.dcim.examples.all.return_value = [_Record(row) for row in rows]
    adapter.model_loader("BuiltinTag", BuiltinTag)
    assert [peer.model_dump()["local_id"] for peer in adapter.get_all("BuiltinTag")] == ["10"]
    return adapter


@pytest.mark.parametrize(
    ("model", "value", "expected"),
    [
        (OptionalOne, {"id": 10}, "retained"),
        (RequiredOne, {"id": 10}, "retained"),
        (OptionalMany, [{"id": 10}], ["retained"]),
        (RequiredMany, [{"id": 10}], ["retained"]),
    ],
)
def test_retained_peer_resolves(
    monkeypatch: pytest.MonkeyPatch,
    model: type[NetboxModel],
    value: dict[str, int] | list[dict[str, int]],
    expected: str | list[str],
) -> None:
    """One and many references retain a peer that passed the filter."""
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    adapter.model_loader("Example", model)

    assert adapter.get_all("Example")[0].model_dump()["peer"] == expected


@pytest.mark.parametrize(
    ("model", "value", "expected"),
    [
        (OptionalOne, {"id": 20}, None),
        (OptionalMany, [{"id": 20}], []),
        (OptionalMany, [{"id": 10}, {"id": 20}], ["retained"]),
    ],
)
def test_optional_relationship_drops_excluded_peer(
    monkeypatch: pytest.MonkeyPatch,
    model: type[NetboxModel],
    value: dict[str, int] | list[dict[str, int]],
    expected: str | list[str] | None,
) -> None:
    """Optional relationships omit excluded peers and keep valid peers."""
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    adapter.model_loader("Example", model)

    assert adapter.get_all("Example")[0].model_dump()["peer"] == expected


@pytest.mark.parametrize("model", [RequiredOne, RequiredMany])
def test_required_relationship_with_only_excluded_peers_fails(
    monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel]
) -> None:
    """A configured filter is named when it leaves a required field empty."""
    value: Any = [{"id": 20}] if model is RequiredMany else {"id": 20}
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    with pytest.raises(ValueError) as err:
        adapter.model_loader("Example", model)

    assert str(err.value) == "Configured filter excluded all peers for required relationship peer: 20"


def test_required_many_keeps_a_valid_peer_when_another_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """One excluded peer does not invalidate a required list with a valid peer."""
    adapter = _adapter(monkeypatch, RequiredMany, [{"id": 1, "name": "owner", "peer": [{"id": 10}, {"id": 20}]}])

    adapter.model_loader("Example", RequiredMany)

    assert adapter.get_all("Example")[0].model_dump()["peer"] == ["retained"]


@pytest.mark.parametrize("model", [OptionalOne, RequiredOne, OptionalMany, RequiredMany])
def test_unexplained_missing_peer_stays_fatal(monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel]) -> None:
    """A peer absent from the source retains the original lookup error."""
    value: Any = [{"id": 99}] if model in {OptionalMany, RequiredMany} else {"id": 99}
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    with pytest.raises(IndexError) as err:
        adapter.model_loader("Example", model)

    expected = "BuiltinTag" if model in {OptionalMany, RequiredMany} else "peer"
    assert str(err.value) == f"Unable to locate the node {expected} 99"


def test_incremental_reference_checks_the_full_filtered_peer_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm run can classify a peer excluded before its changed-since window."""
    adapter = _adapter(monkeypatch, OptionalOne, [])
    adapter._complete_filtered_peers.clear()
    adapter._filtered_peer_ids.clear()
    payload = {"id": 1, "name": "owner", "peer": {"id": 20}}

    data = adapter.netbox_obj_to_diffsync(payload, adapter.config.schema_mapping[1], OptionalOne)

    assert data == {"local_id": "1", "name": "owner"}
    assert adapter._filtered_peer_ids["BuiltinTag"] == {"20"}


def test_filtered_optional_relationship_can_be_planned(monkeypatch: pytest.MonkeyPatch) -> None:
    """The extracted source can enter comparison and saved-plan derivation."""
    source = _adapter(monkeypatch, OptionalMany, [{"id": 1, "name": "owner", "peer": [{"id": 10}, {"id": 20}]}])
    source.model_loader("Example", OptionalMany)
    destination = _TestAdapter(target="destination", adapter=SyncAdapter(name="netbox"), config=source.config)

    operations = operations_from_diff(
        destination.diff_from(source),
        config=source.config,
        tier_of=lambda _kind: 0,
        source_adapter=source,
        destination_adapter=destination,
    )

    assert [(operation.action, operation.kind) for operation in operations] == [
        ("create", "BuiltinTag"),
        ("create", "Example"),
    ]
    assert operations[1].relationships is not None
    assert operations[1].relationships[0].peers == [{"name": "retained"}]
