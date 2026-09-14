"""Derivation records the destination id on updates, and discloses the clears it cannot record.

An update is keyed at apply by the destination object's Infrahub `id`, and plan time is the
only moment that id is knowable without re-reading the destination — which apply is forbidden
to do (FR-012). So derivation reads it off the destination store's model for the same unique
id and records it on the operation.

Where it cannot, the plan refuses. A missing id on an update would otherwise fall back to a
create-shaped convergent upsert, which is exactly the G1 M6 silent-duplicate case.

`[S7]` The other disclosure: a `None` cardinality-one peer on an update is dropped while
resolving references, so the derived payload carries no reference for that field and the
destination relationship is left alone. Plan format 3 adds no encoding for a clear, so the
only honest thing to do is say so while the null is still visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.derive import operations_from_diff
from tests.test_potenda_plan_artifact import (
    _FakeAdapter,
    _FakeDiff,
    _FakeElement,
    _FakeRecord,
    build_config,
    operation_for,
    qualified_source,
    resolver,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


class _KeyedRecord(_FakeRecord):
    """A destination record that carries the destination node id, as a live load leaves it."""

    def __init__(
        self,
        kind: str,
        identifiers: Mapping[str, Any],
        attrs: Mapping[str, Any] | None = None,
        *,
        local_id: str | None = DESTINATION_ID,
    ) -> None:
        super().__init__(kind, identifiers, attrs)
        self.local_id = local_id


def destination_with(*records: _FakeRecord) -> _FakeAdapter:
    """A destination adapter holding exactly `records`."""
    return _FakeAdapter("destination", list(records))


def tag_update_element(description: str = "production") -> _FakeElement:
    """One `BuiltinTag` element whose attributes differ from the destination's — an update."""
    return _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        source_attrs={"description": description, "slug": "prod"},
        dest_attrs={"description": "stale", "slug": "prod"},
    )


def derive(element: _FakeElement, destination: _FakeAdapter) -> list[Any]:
    """Derive operations for one element against `destination`."""
    return operations_from_diff(
        _FakeDiff({"BuiltinTag": [element]}),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
        destination_adapter=destination,
    )


def test_an_update_records_the_destination_id_the_destination_store_carries() -> None:
    """The id comes from the destination model for the same unique id, at plan time."""
    destination = destination_with(_KeyedRecord("BuiltinTag", {"name": "prod"}, {"description": "stale"}))

    operations = derive(tag_update_element(), destination)

    operation = operation_for(operations, "BuiltinTag")
    assert operation.action == "update"
    assert operation.destination_id == DESTINATION_ID


def test_a_create_records_no_destination_id() -> None:
    """A create has no destination object yet, so there is no id to record."""
    element = _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        source_attrs={"description": "production", "slug": "prod"},
    )

    operations = derive(element, destination_with())

    assert operation_for(operations, "BuiltinTag").destination_id is None


def test_an_update_whose_destination_record_has_no_local_id_is_refused() -> None:
    """Never a silent create: a missing id is named at plan time, before any write."""
    from infrahub_sync.plan.errors import MissingDestinationIdError

    destination = destination_with(
        _KeyedRecord("BuiltinTag", {"name": "prod"}, {"description": "stale"}, local_id=None)
    )

    with pytest.raises(MissingDestinationIdError) as excinfo:
        derive(tag_update_element(), destination)

    assert "BuiltinTag" in str(excinfo.value)
    assert "prod" in str(excinfo.value)


def test_an_update_whose_destination_record_has_an_empty_local_id_is_refused() -> None:
    """An empty string is not an id: it keys nothing at the destination."""
    from infrahub_sync.plan.errors import MissingDestinationIdError

    destination = destination_with(_KeyedRecord("BuiltinTag", {"name": "prod"}, {"description": "stale"}, local_id=""))

    with pytest.raises(MissingDestinationIdError):
        derive(tag_update_element(), destination)


def test_an_update_whose_destination_record_is_absent_is_refused() -> None:
    """A destination store with no matching object cannot key the update it proposes."""
    from infrahub_sync.plan.errors import MissingDestinationIdError

    with pytest.raises(MissingDestinationIdError):
        derive(tag_update_element(), destination_with())


# ---------------------------------------------------------------------------------------
# `[S7]` — the clear the plan cannot represent is disclosed where the null is still visible
# ---------------------------------------------------------------------------------------


def test_a_dropped_null_cardinality_one_peer_on_an_update_is_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The relationship is not cleared, and the plan says so rather than implying convergence."""
    element = _FakeElement(
        kind="DcimDevice",
        name="d1",
        keys={"name": "d1"},
        source_attrs={"model": "c9300", "rack": None},
        dest_attrs={"model": "c9200", "rack": "r1__hq"},
    )
    destination = destination_with(_KeyedRecord("DcimDevice", {"name": "d1"}, {"model": "c9200"}))

    with caplog.at_level("WARNING"):
        operations_from_diff(
            _FakeDiff({"DcimDevice": [element]}),
            config=build_config(),
            tier_of=resolver(),
            source_adapter=qualified_source(),
            destination_adapter=destination,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("rack" in message and "DcimDevice" in message for message in messages), (
        f"The dropped cardinality-one clear must be disclosed at plan time; got {messages}."
    )


def test_a_dropped_null_cardinality_one_peer_on_a_create_is_not_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A create has nothing to clear, so its omission is the established behaviour."""
    element = _FakeElement(
        kind="DcimDevice",
        name="d1",
        keys={"name": "d1"},
        source_attrs={"model": "c9300", "rack": None},
    )

    with caplog.at_level("WARNING"):
        operations_from_diff(
            _FakeDiff({"DcimDevice": [element]}),
            config=build_config(),
            tier_of=resolver(),
            source_adapter=qualified_source(),
            destination_adapter=destination_with(),
        )

    assert not any("rack" in record.getMessage() for record in caplog.records)
