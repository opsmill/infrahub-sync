"""Nautobot: a reversed peer list gives the same operations (spec 019 T013, FR-023).

The record goes through `NautobotAdapter.record_to_data` and the operations through
`operations_from_diff`; see `tests/adapters/peer_order.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.adapters.peer_order import PEER_NAMES, adapter_class, plan_peer_order

pytest.importorskip("pynautobot")

NautobotRecord = pytest.importorskip("pynautobot.core.response").Record

from infrahub_sync.adapters.nautobot import NautobotAdapter, NautobotModel  # noqa: E402

PEER_IDS = (
    "cccccccc-0000-0000-0000-000000000020",
    "cccccccc-0000-0000-0000-000000000021",
    "cccccccc-0000-0000-0000-000000000022",
)
RECORD_ID = "aaaaaaaa-0000-0000-0000-000000000002"


class NautobotPeerTag(NautobotModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    local_data: Any | None = None
    name: str


class NautobotPeerOwner(NautobotModel):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("tags",)
    local_data: Any | None = None
    name: str
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


ADAPTER = adapter_class(NautobotAdapter, NautobotPeerTag, NautobotPeerOwner)


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    """The loader converts `dict(record)` of a pynautobot `Record`."""
    return dict(NautobotRecord(payload, None, None))


@pytest.mark.parametrize(
    "destination_tags",
    [None, sorted(PEER_NAMES), ["alpha"]],
    ids=["create", "unchanged", "update"],
)
def test_a_reversed_peer_list_gives_the_same_operations(destination_tags: list[str] | None) -> None:
    operations = [
        plan_peer_order(
            ADAPTER, peer_ids=ids, record_id=RECORD_ID, source_object=_record, destination_tags=destination_tags
        )
        for ids in (PEER_IDS, tuple(reversed(PEER_IDS)))
    ]

    assert operations[0] == operations[1]
    if destination_tags == sorted(PEER_NAMES):
        assert operations[0] == []
    else:
        (operation,) = operations[0]
        assert operation.relationships is not None
        assert [peer["name"] for peer in operation.relationships[0].peers] == sorted(PEER_NAMES)
