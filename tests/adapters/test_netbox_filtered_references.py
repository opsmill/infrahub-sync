"""NetBox references distinguish filtered peers from missing source records.

Since spec 019 (T017, T018, T021) a record whose required peer a filter removed, or whose peer
the source does not return, is recorded in the load report and the read continues; before, the
read raised `ValueError` or `IndexError`.
"""

from __future__ import annotations

from collections import UserDict
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import MagicMock

import pytest

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
    not_in_source_placeholder,
)

if TYPE_CHECKING:
    from infrahub_sync.adapters.netbox import NetboxAdapter

pytest.importorskip("pynetbox")

from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel
from infrahub_sync.plan.derive import derive_deletes, operations_from_diff


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


PLACEHOLDER = not_in_source_placeholder("BuiltinTag", "99")

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
    monkeypatch.setattr(_TestAdapter, "Example", model)
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
    caplog: pytest.LogCaptureFixture,
    model: type[NetboxModel],
    value: dict[str, int] | list[dict[str, int]],
    expected: str | list[str] | None,
) -> None:
    """Optional relationships omit excluded peers and keep valid peers."""
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    adapter.model_loader("Example", model)

    assert adapter.get_all("Example")[0].model_dump()["peer"] == expected
    assert EXCLUDED_PEER_LOG in caplog.text
    assert adapter.load_report.entries == []


EXCLUDED_PEER_LOG = "Example record 1 field peer (peer kind BuiltinTag, source ID 20)"


def _entries(adapter: NetboxAdapter) -> list[tuple[Any, ...]]:
    return [
        (
            entry.reason,
            entry.record.source_id,
            entry.field,
            entry.peer.source_id if entry.peer else None,
            entry.config_entry,
            entry.loaded,
        )
        for entry in adapter.load_report.entries
    ]


@pytest.mark.parametrize("model", [RequiredOne, RequiredMany])
def test_required_relationship_with_only_excluded_peers_is_recorded(
    monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel]
) -> None:
    """The record is `required_peer_excluded_by_filter`, naming the filter, and is not loaded."""
    value: Any = [{"id": 20}] if model is RequiredMany else {"id": 20}
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    adapter.model_loader("Example", model)

    assert adapter.get_all("Example") == []
    assert _entries(adapter) == [("required_peer_excluded_by_filter", "1", "peer", "20", "filters[0]", False)]


def test_required_many_keeps_a_valid_peer_when_another_is_excluded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One excluded peer does not invalidate a required list with a valid peer."""
    adapter = _adapter(monkeypatch, RequiredMany, [{"id": 1, "name": "owner", "peer": [{"id": 10}, {"id": 20}]}])

    adapter.model_loader("Example", RequiredMany)

    assert adapter.get_all("Example")[0].model_dump()["peer"] == ["retained"]
    assert EXCLUDED_PEER_LOG in caplog.text


@pytest.mark.parametrize("model", [RequiredOne, RequiredMany])
def test_a_required_peer_the_source_does_not_return_is_recorded(
    monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel]
) -> None:
    """The record is `peer_not_found_in_source` and is not loaded; the read continues."""
    value: Any = [{"id": 99}] if model is RequiredMany else {"id": 99}
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    adapter.model_loader("Example", model)

    assert adapter.get_all("Example") == []
    assert _entries(adapter) == [("peer_not_found_in_source", "1", "peer", "99", None, False)]


@pytest.mark.parametrize(("model", "expected"), [(OptionalOne, PLACEHOLDER), (OptionalMany, [PLACEHOLDER])])
def test_an_optional_peer_the_source_does_not_return_is_a_placeholder(
    monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel], expected: str | list[str]
) -> None:
    """The record is kept with a placeholder and a `peer_not_found_in_source` entry."""
    value: Any = [{"id": 99}] if model is OptionalMany else {"id": 99}
    adapter = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": value}])

    adapter.model_loader("Example", model)

    assert adapter.get_all("Example")[0].model_dump()["peer"] == expected
    assert _entries(adapter) == [("peer_not_found_in_source", "1", "peer", "99", None, True)]


def test_incremental_reference_checks_the_full_filtered_peer_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm run can classify a peer excluded before its changed-since window."""
    adapter = _adapter(monkeypatch, OptionalOne, [])
    # An incremental read starts with no kind read in full by this adapter.
    adapter.__dict__.pop("_spec019_read_state")
    adapter.__dict__.pop("_complete_filtered_peers", None)
    payload = {"id": 1, "name": "owner", "peer": {"id": 20}}

    data = adapter.record_to_data(payload, adapter.config.schema_mapping[1], OptionalOne)

    assert data == {"local_id": "1", "name": "owner"}
    assert adapter.load_report.filtered_ids["BuiltinTag"] == {"20": 0}


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


@pytest.mark.parametrize(
    ("model", "source_peer", "destination_peer", "expected_peers"),
    [
        (OptionalOne, {"id": 20}, "excluded", None),
        (OptionalMany, [{"id": 10}, {"id": 20}], ["retained", "excluded"], [{"name": "retained"}]),
    ],
)
def test_filtered_peer_plan_effect_on_existing_destination(
    monkeypatch: pytest.MonkeyPatch,
    model: type[NetboxModel],
    source_peer: dict[str, int] | list[dict[str, int]],
    destination_peer: str | list[str],
    expected_peers: list[dict[str, str]] | None,
) -> None:
    """The plan leaves a single link intact but replaces a many-peer set."""
    source = _adapter(monkeypatch, model, [{"id": 1, "name": "owner", "peer": source_peer}])
    source.model_loader("Example", model)
    destination = _TestAdapter(target="destination", adapter=SyncAdapter(name="netbox"), config=source.config)
    destination.add(BuiltinTag.model_validate({"local_id": "10", "name": "retained"}))
    destination.add(BuiltinTag.model_validate({"local_id": "20", "name": "excluded"}))
    destination.add(model.model_validate({"local_id": "1", "name": "owner", "peer": destination_peer}))

    operations = operations_from_diff(
        destination.diff_from(source),
        config=source.config,
        tier_of=lambda _kind: 0,
        source_adapter=source,
        destination_adapter=destination,
    )

    owner_updates = [
        operation for operation in operations if operation.kind == "Example" and operation.action == "update"
    ]
    assert len(owner_updates) == 1
    deletes = derive_deletes(
        kinds=["BuiltinTag", "Example"],
        source_adapter=source,
        destination_adapter=destination,
        config=source.config,
        tier_of=lambda _kind: 0,
        destination_full_extract=True,
    )
    assert [(operation.action, operation.kind, operation.identity) for operation in deletes] == [
        ("delete", "BuiltinTag", {"name": "excluded"})
    ]
    update = owner_updates[0]
    if expected_peers is None:
        assert update.relationships is None
        assert update.payload == {"name": "owner"}
    else:
        assert update.relationships is not None
        assert update.relationships[0].peers == expected_peers


def test_the_existing_id_sweep_leaves_no_load_report_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The soft-delete sweep converts records to compute IDs only; its reference entries are dropped."""
    adapter = _adapter(monkeypatch, OptionalOne, [{"id": 1, "name": "owner", "peer": {"id": 99}}])

    assert list(adapter.list_existing_ids("Example")) == ["owner"]
    assert adapter.load_report.entries == []


@pytest.mark.parametrize("unfiltered_first", [True, False], ids=["unfiltered-first", "filtered-first"])
def test_a_peer_an_unfiltered_entry_keeps_is_not_filtered_before_its_kind_is_read(
    monkeypatch: pytest.MonkeyPatch, *, unfiltered_first: bool
) -> None:
    """Tag 20 fails the filter of one BuiltinTag entry and passes the other, unfiltered one: it is not filtered."""
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    filtered = SchemaMappingModel(
        name="BuiltinTag",
        mapping="extras.tags",
        filters=[SchemaMappingFilter(field="group", operation="is_not_empty")],
        fields=[SchemaMappingField(name="name", mapping="name")],
    )
    unfiltered = SchemaMappingModel(
        name="BuiltinTag", mapping="extras.tags", fields=[SchemaMappingField(name="name", mapping="name")]
    )
    config = SyncConfig(
        name="unfiltered-entry",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[unfiltered, filtered] if unfiltered_first else [filtered, unfiltered],
    )
    adapter = _TestAdapter(target="source", adapter=SyncAdapter(name="netbox"), config=config)
    adapter.client.extras.tags.all.return_value = [_Record(row) for row in PEERS]

    assert adapter.filtered_peer_position("BuiltinTag", "20") == (False, None)
