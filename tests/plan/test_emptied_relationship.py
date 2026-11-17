"""A plan records a one-peer relationship that the source empties (spec 019 T023).

FR-005: an update gains a one-peer reference with no peer when the destination node holds a peer
and the source record holds none. FR-007: never on a create. FR-008: the plan step writes no
warning for it. FR-013a: a one-peer relationship that the source model or the destination schema
requires, and that the source empties, leaves the record out with a
`required_relationship_empty_in_source` problem, and keeps its destination node. Research R6 and
R7 give the conditions: no reference when both sides are empty, when the peer was left out of the
plan (a kept record), or when a configured filter removed the peer.

Before this feature, a source record with no peer for a required one-peer relationship gave one
of two results, depending on how the adapter wrote it. A field left out of the record data
failed the whole plan with `RecordLoadError` (pydantic `missing`). A field set to `None` left the
record out as `wrong_value_type` (pydantic `string_type` with input `None`).
"""

from __future__ import annotations

import logging
from collections import UserDict
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingFilter, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.plan.derive import derive_deletes, operations_from_diff
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
from infrahub_sync.plan.problems import find_problems, select_operations

pytest.importorskip("pynetbox")

from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel

DERIVE_LOGGER = "infrahub_sync.plan.derive"


class Tag(NetboxModel):
    """The peer kind. Its mapping keeps only the tags that belong to a group."""

    _modelname = "Tag"
    _identifiers = ("name",)
    name: str


class OptionalOne(NetboxModel):
    """An optional one-peer relationship."""

    _modelname = "Example"
    _identifiers = ("name",)
    _attributes = ("peer",)
    name: str
    peer: str | None = None


class RequiredOne(NetboxModel):
    """A required one-peer relationship."""

    _modelname = "Example"
    _identifiers = ("name",)
    _attributes = ("peer",)
    name: str
    peer: str


class _Record(UserDict):
    """A synthetic row that behaves like a pynetbox record under `dict(row)`."""


class _Adapter(NetboxAdapter):
    top_level: ClassVar[list[str]] = ["Tag", "Example"]
    Tag: ClassVar[type[NetboxModel]] = Tag
    Example: ClassVar[type[NetboxModel]] = OptionalOne


TAGS = [
    {"id": 10, "name": "kept", "group": {"name": "group"}},
    {"id": 20, "name": "filtered", "group": None},
    # A tag whose name has the wrong type: the read cannot build it (`peer_not_loaded`).
    {"id": 30, "name": {"not": "a name"}, "group": {"name": "group"}},
]


def _config() -> SyncConfig:
    return SyncConfig(
        name="emptied-relationship",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="Tag",
                mapping="extras.tags",
                filters=[SchemaMappingFilter(field="group", operation="is_not_empty")],
                fields=[SchemaMappingField(name="name", mapping="name")],
            ),
            SchemaMappingModel(
                name="Example",
                mapping="dcim.examples",
                fields=[
                    SchemaMappingField(name="name", mapping="name"),
                    SchemaMappingField(name="peer", mapping="peer", reference="Tag"),
                ],
            ),
        ],
    )


def _source(monkeypatch: pytest.MonkeyPatch, model: type[NetboxModel], peer: dict[str, int] | None) -> NetboxAdapter:
    """Read the tags and one `Example` record named `owner` through the real loader."""
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    monkeypatch.setattr(_Adapter, "Example", model)
    adapter = _Adapter(target="source", adapter=SyncAdapter(name="netbox"), config=_config())
    adapter.client.extras.tags.all.return_value = [_Record(row) for row in TAGS]
    adapter.client.dcim.examples.all.return_value = [_Record({"id": 1, "name": "owner", "peer": peer})]
    adapter.model_loader("Tag", Tag)
    adapter.model_loader("Example", model)
    return adapter


def _destination(source: NetboxAdapter, model: type[NetboxModel], peer: str | None) -> NetboxAdapter:
    """A destination that holds the tags `kept` and `filtered`, and `owner` with `peer`."""
    destination = _Adapter(target="destination", adapter=SyncAdapter(name="netbox"), config=source.config)
    destination.add(Tag.model_validate({"local_id": "d-10", "name": "kept"}))
    destination.add(Tag.model_validate({"local_id": "d-20", "name": "filtered"}))
    destination.add(model.model_validate({"local_id": "d-1", "name": "owner", "peer": peer}))
    return destination


def _plan(source: NetboxAdapter, destination: NetboxAdapter) -> tuple[list[PlannedOperation], Any]:
    """Derive the operations, find the problems and apply the selection step, as the plan step does."""
    operations = operations_from_diff(
        destination.diff_from(source),
        config=source.config,
        tier_of=lambda _kind: 0,
        source_adapter=source,
        destination_adapter=destination,
    )
    operations += derive_deletes(
        kinds=["Tag", "Example"],
        source_adapter=source,
        destination_adapter=destination,
        config=source.config,
        tier_of=lambda _kind: 0,
        destination_full_extract=True,
    )
    report = find_problems(source=source, destination=destination, config=source.config)
    return select_operations(operations, report), report


def _example_operations(operations: list[PlannedOperation]) -> list[PlannedOperation]:
    return [operation for operation in operations if operation.kind == "Example"]


EMPTIED = RelationshipReference(field="peer", peer_kind="Tag", cardinality="one", peers=[])


def test_an_update_empties_a_one_peer_relationship_the_source_no_longer_holds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-005, FR-008: the update records the emptied relationship, and no warning is logged."""
    source = _source(monkeypatch, OptionalOne, None)
    destination = _destination(source, OptionalOne, "kept")

    with caplog.at_level(logging.WARNING, logger=DERIVE_LOGGER):
        operations, report = _plan(source, destination)

    updates = _example_operations(operations)
    assert [(operation.action, operation.payload, operation.relationships) for operation in updates] == [
        ("update", {"name": "owner"}, [EMPTIED])
    ]
    assert updates[0].relationships is not None
    assert updates[0].relationships[0].empties_one_peer
    assert [problem for problem in report.problems if problem.record.kind == "Example"] == []
    assert [record for record in caplog.records if record.name == DERIVE_LOGGER] == []


def test_no_reference_when_both_sides_are_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-peer relationship empty on both sides gives no operation."""
    source = _source(monkeypatch, OptionalOne, None)
    destination = _destination(source, OptionalOne, None)

    operations, _report = _plan(source, destination)

    assert _example_operations(operations) == []


def test_a_create_records_no_emptied_relationship(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-007: a record created without a peer holds no entry for that relationship."""
    source = _source(monkeypatch, OptionalOne, None)
    destination = _destination(source, OptionalOne, "kept")
    destination.remove(destination.get(OptionalOne, "owner"))

    operations, _report = _plan(source, destination)

    assert [(operation.action, operation.relationships) for operation in _example_operations(operations)] == [
        ("create", None)
    ]


def test_a_required_relationship_the_source_empties_leaves_the_record_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-013a: no update and no delete for the record, and a problem that names the record and the field."""
    with caplog.at_level(logging.WARNING):
        source = _source(monkeypatch, RequiredOne, None)
        destination = _destination(source, RequiredOne, "kept")
        operations, report = _plan(source, destination)

    assert source.get_all("Example") == []
    assert [
        (entry.reason, entry.record.source_id, entry.field, entry.loaded)
        for entry in source.load_report.entries
        if entry.record.kind == "Example"
    ] == [("required_relationship_empty_in_source", "1", "peer", False)]
    assert _example_operations(operations) == []
    problems = [problem for problem in report.problems if problem.record.kind == "Example"]
    assert [(problem.problem_type, problem.record.source_id, problem.field) for problem in problems] == [
        ("required_relationship_empty_in_source", "1", "peer")
    ]
    assert problems[0].reason == (
        "Example record source ID 1: the source holds no peer for the required relationship peer, so the plan "
        "does not change this record."
    )
    left_out = [(item.record.kind, item.record.identity) for item in report.left_out]
    assert ("Example", {"name": "owner"}) in left_out
    assert [record for record in caplog.records if record.name == DERIVE_LOGGER] == []


def test_a_required_relationship_set_to_none_is_the_same_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """An adapter that writes `None` for the field, instead of leaving it out, gets the same entry."""
    monkeypatch.setattr(
        _Adapter,
        "record_to_data",
        lambda _self, record, _element, _model: {"local_id": str(record["id"]), "name": record["name"], "peer": None},
    )
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    monkeypatch.setattr(_Adapter, "Example", RequiredOne)
    source = _Adapter(target="source", adapter=SyncAdapter(name="netbox"), config=_config())
    source.client.dcim.examples.all.return_value = [_Record({"id": 1, "name": "owner", "peer": None})]

    source.model_loader("Example", RequiredOne)

    assert [(entry.reason, entry.field, entry.record.identity) for entry in source.load_report.entries] == [
        ("required_relationship_empty_in_source", "peer", {"name": "owner"})
    ]


def test_a_missing_required_attribute_still_fails_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a required one-peer relationship is routed to the new problem; other refusals stay as before."""
    from infrahub_sync import RecordLoadError

    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    source = _Adapter(target="source", adapter=SyncAdapter(name="netbox"), config=_config())
    source.client.extras.tags.all.return_value = [_Record({"id": 40, "name": None, "group": {"name": "group"}})]

    with pytest.raises(RecordLoadError):
        source.model_loader("Tag", Tag)


def test_a_required_reference_without_a_mapping_still_fails_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reference field with neither `mapping` nor `static` is a configuration error, not an empty peer."""
    from infrahub_sync import RecordLoadError

    config = _config()
    config.schema_mapping[1].fields[1].mapping = None
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    monkeypatch.setattr(_Adapter, "Example", RequiredOne)
    source = _Adapter(target="source", adapter=SyncAdapter(name="netbox"), config=config)
    source.client.dcim.examples.all.return_value = [_Record({"id": 1, "name": "owner", "peer": None})]

    with pytest.raises(RecordLoadError):
        source.model_loader("Example", RequiredOne)


def test_no_reference_when_the_peer_was_left_out_and_the_record_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A peer the read could not build keeps the record without that relationship; the plan does not empty it."""
    source = _source(monkeypatch, OptionalOne, {"id": 30})
    destination = _destination(source, OptionalOne, "kept")

    operations, report = _plan(source, destination)

    assert [(item.record.source_id, item.relationship) for item in report.kept] == [("1", "peer")]
    assert _example_operations(operations) == []


def test_no_reference_when_a_configured_filter_removed_the_peer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Research R7: the source adapter records the filtered reference, and the plan leaves the relationship alone."""
    source = _source(monkeypatch, OptionalOne, {"id": 20})
    destination = _destination(source, OptionalOne, "filtered")

    with caplog.at_level(logging.WARNING, logger=DERIVE_LOGGER):
        operations, _report = _plan(source, destination)

    assert source.filtered_peer_fields == frozenset({("Example", "1", "peer")})
    # `tests/adapters/test_netbox_filtered_peer_fields.py` holds the same result: the update
    # carries no reference, so the relationship is left as it is.
    assert [(operation.action, operation.relationships) for operation in _example_operations(operations)] == [
        ("update", None)
    ]
    assert [record for record in caplog.records if record.name == DERIVE_LOGGER] == []


def test_a_kind_that_refers_to_a_filtered_peer_reads_cold(monkeypatch: pytest.MonkeyPatch) -> None:
    """The peer kind's read is unclean, so a kind that refers to it reads cold and records the filtered peer again."""
    source = _source(monkeypatch, OptionalOne, {"id": 20})

    assert not source.kind_read_is_clean("Tag")
    source.discard_kind("Example", entries_from=len(source.load_report.entries))
    assert source.filtered_peer_fields == frozenset()


def _require_peer_in_destination(destination: NetboxAdapter) -> None:
    destination.schema = {  # ty: ignore[unresolved-attribute] -- the destination schema the plan step reads
        "Example": SimpleNamespace(human_friendly_id=None, relationships=[SimpleNamespace(name="peer", optional=False)])
    }


def test_a_relationship_only_the_destination_schema_requires_leaves_the_record_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-013a: the source model marks the field optional and the destination schema requires it.

    Derivation records no emptied relationship, and the problem pass gives the record a
    `required_relationship_empty_in_source` problem, so the plan holds no operation for it and
    keeps its destination node.
    """
    source = _source(monkeypatch, OptionalOne, None)
    destination = _destination(source, OptionalOne, "kept")
    _require_peer_in_destination(destination)

    derived = operations_from_diff(
        destination.diff_from(source),
        config=source.config,
        tier_of=lambda _kind: 0,
        source_adapter=source,
        destination_adapter=destination,
    )
    with caplog.at_level(logging.WARNING, logger=DERIVE_LOGGER):
        operations, report = _plan(source, destination)

    assert [(operation.action, operation.relationships) for operation in _example_operations(derived)] == [
        ("update", None)
    ]
    assert _example_operations(operations) == []
    problems = [problem for problem in report.problems if problem.record.kind == "Example"]
    assert [(problem.problem_type, problem.record.source_id, problem.field) for problem in problems] == [
        ("required_relationship_empty_in_source", "1", "peer")
    ]
    left_out = [(item.record.identity, item.cause) for item in report.left_out if item.record.kind == "Example"]
    assert left_out == [({"name": "owner"}, "has_problem")]
    assert [record for record in caplog.records if record.name == DERIVE_LOGGER] == []


def test_no_problem_when_the_destination_requires_the_relationship_and_holds_no_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both sides are empty: the plan changes nothing for the relationship and reports no problem."""
    source = _source(monkeypatch, OptionalOne, None)
    destination = _destination(source, OptionalOne, None)
    _require_peer_in_destination(destination)

    operations, report = _plan(source, destination)

    assert _example_operations(operations) == []
    assert [problem for problem in report.problems if problem.record.kind == "Example"] == []


def test_no_reference_when_the_owner_record_is_not_in_the_source_store(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Whether a filter removed the peer is not known, so the relationship is not emptied, and one warning is logged.

    The source adapter recorded a filtered peer for another record, and the source store no longer
    holds the record of the update when derivation looks up its source ID.
    """
    source = _source(monkeypatch, OptionalOne, None)
    destination = _destination(source, OptionalOne, "kept")
    diff = destination.diff_from(source)
    monkeypatch.setattr(_Adapter, "filtered_peer_fields", frozenset({("Example", "9", "peer")}))
    source.remove(source.get(OptionalOne, "owner"))

    with caplog.at_level(logging.WARNING, logger=DERIVE_LOGGER):
        operations = operations_from_diff(
            diff,
            config=source.config,
            tier_of=lambda _kind: 0,
            source_adapter=source,
            destination_adapter=destination,
        )

    assert [(operation.action, operation.relationships) for operation in _example_operations(operations)] == [
        ("update", None)
    ]
    warnings = [record.getMessage() for record in caplog.records if record.name == DERIVE_LOGGER]
    assert len(warnings) == 1
    assert "Example relationship peer" in warnings[0]
    assert "owner" not in warnings[0]
