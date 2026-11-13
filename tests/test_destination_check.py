"""The destination check of an interrupted run's `verify` (spec 022, research R7), offline.

`compare_planned_operation` turns one planned create or update and the node read for it into
`matches`, `differs` or `missing`. `InfrahubAdapter.read_planned_nodes` reads those nodes,
grouped by kind, against a real `InfrahubClientSync` whose query edge alone answers from an
in-memory destination.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any

import pytest
from infrahub_sdk.node import InfrahubNodeSync

from infrahub_sync.plan.destination_check import (
    VERIFY_REPORT_LIMIT,
    DestinationNode,
    DestinationNodeReader,
    PlannedNodeRead,
    compare_planned_operation,
    reported_operations,
    verification_report,
)
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
from tests.adapters.test_infrahub_planned_write import (
    DEFAULT_DESTINATION_ID,
    DEVICE_KIND,
    SCHEMAS,
    SITE_KIND,
    TAG_KIND,
    TEAM_KIND,
    RecordingClient,
    device_operation,
    make_adapter,
    make_operation,
    team_operation,
)

CANARY = "destination-check-canary-value"


def _site_update(description: str = "planned") -> Any:  # noqa: ANN401
    return make_operation(
        kind=SITE_KIND,
        action="update",
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": description},
    )


def _site_delete() -> PlannedOperation:
    identity = canonical_identity({"name": "gone"}, kind=SITE_KIND)
    return PlannedOperation(
        operation_id=operation_id("delete", SITE_KIND, identity),
        action="delete",
        kind=SITE_KIND,
        identity=identity,
        tier=0,
    )


def _site_create(name: str, description: str = "planned") -> Any:  # noqa: ANN401
    return make_operation(kind=SITE_KIND, identity={"name": name}, payload={"name": name, "description": description})


# ======================================================================================
# The comparison
# ======================================================================================


def test_no_node_is_missing() -> None:
    assert compare_planned_operation(_site_update(), PlannedNodeRead(None)) == "missing"


def test_a_node_holding_every_planned_value_matches() -> None:
    node = DestinationNode(attributes={"name": "site-a", "description": "planned"})

    assert compare_planned_operation(_site_update(), PlannedNodeRead(node)) == "matches"


def test_one_changed_value_differs() -> None:
    node = DestinationNode(attributes={"name": "site-a", "description": "changed at the destination"})

    assert compare_planned_operation(_site_update(), PlannedNodeRead(node)) == "differs"


def test_a_field_the_plan_does_not_write_is_not_compared() -> None:
    node = DestinationNode(attributes={"name": "site-a", "description": "planned", "comment": "anything"})

    assert compare_planned_operation(_site_update(), PlannedNodeRead(node)) == "matches"


def test_a_planned_field_the_read_does_not_hold_differs() -> None:
    node = DestinationNode(attributes={"name": "site-a"})

    assert compare_planned_operation(_site_update(), PlannedNodeRead(node)) == "differs"


def test_an_ip_value_read_as_an_ipaddress_object_matches_its_planned_string() -> None:
    operation = make_operation(
        kind=SITE_KIND,
        action="update",
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": "10.0.0.1/32"},
    )
    node = DestinationNode(attributes={"name": "site-a", "description": ipaddress.ip_interface("10.0.0.1/32")})

    assert compare_planned_operation(operation, PlannedNodeRead(node)) == "matches"


def test_cardinality_many_peers_compare_as_a_set() -> None:
    operation = team_operation(["tag-a", "tag-b"])
    node = DestinationNode(attributes={"name": "team-a"}, peers={"members": frozenset({"id-b", "id-a"})})

    matching = PlannedNodeRead(node, planned_peers={"members": frozenset({"id-a", "id-b"})})
    surplus = PlannedNodeRead(node, planned_peers={"members": frozenset({"id-a"})})

    assert compare_planned_operation(operation, matching) == "matches"
    assert compare_planned_operation(operation, surplus) == "differs"


def test_a_planned_peer_that_resolves_to_no_node_differs() -> None:
    operation = team_operation(["tag-a"])
    node = DestinationNode(attributes={"name": "team-a"}, peers={"members": frozenset()})

    assert compare_planned_operation(operation, PlannedNodeRead(node, planned_peers={"members": None})) == "differs"


def test_deletes_are_never_reported() -> None:
    delete = _site_delete()
    create = _site_create("site-b")

    reported, truncated = reported_operations([delete, create], range(2))

    assert reported == (create,)
    assert truncated == 0


def test_a_window_above_the_limit_reports_the_first_operations_and_counts_the_rest() -> None:
    operations = [_site_create(f"site-{index}") for index in range(VERIFY_REPORT_LIMIT + 7)]

    reported, truncated = reported_operations(operations, range(len(operations)))

    assert reported == tuple(operations[:VERIFY_REPORT_LIMIT])
    assert truncated == 7


def test_the_report_names_each_operation_and_its_state_and_never_a_value() -> None:
    operation = _site_update(description=CANARY)
    node = DestinationNode(attributes={"name": "site-a", "description": f"{CANARY}-changed"})

    report = verification_report([operation], {operation.operation_id: PlannedNodeRead(node)})

    assert report == [
        {"operation_id": operation.operation_id, "kind": SITE_KIND, "action": "update", "state": "differs"}
    ]
    assert CANARY not in json.dumps(report)


# ======================================================================================
# The Infrahub read
# ======================================================================================


class DestinationClient(RecordingClient):
    """A real client whose queries answer from `nodes`, per kind, as the destination would filter them."""

    def __init__(self, nodes: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__()
        self.nodes = nodes

    def filters(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002
        if kwargs.get("populate_store"):
            return []
        self.events.append(("filters", kwargs))
        if self.filter_error is not None:
            raise self.filter_error
        kind = kwargs["kind"]
        return [
            InfrahubNodeSync(client=self, schema=SCHEMAS[kind], data=data)
            for data in self.nodes.get(kind, [])
            if _matches(data, kwargs)
        ]

    def node_reads(self, kind: str) -> list[dict[str, Any]]:
        """The queries of `kind` that read planned nodes, not the peer resolver's."""
        return [
            payload
            for name, payload in self.events
            if name == "filters" and payload["kind"] == kind and "include" in payload
        ]


def _value(data: dict[str, Any], name: str) -> Any:  # noqa: ANN401
    attribute = data.get(name)
    return attribute.get("value") if isinstance(attribute, dict) else None


def _matches(data: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, wanted in query.items():
        if key in {"kind", "populate_store", "include", "exclude"}:
            continue
        if key == "ids":
            found = data["id"] in wanted
        elif key.endswith("__values"):
            found = _value(data, key.removesuffix("__values")) in wanted
        elif key.endswith("__ids"):
            related = data.get(key.removesuffix("__ids"))
            found = isinstance(related, dict) and related.get("id") in wanted
        elif key.endswith("__value"):
            found = _value(data, key.removesuffix("__value")) == wanted
        else:
            msg = f"unexpected filter {key!r}"
            raise AssertionError(msg)
        if not found:
            return False
    return True


def _site(node_id: str, name: str, description: str = "planned") -> dict[str, Any]:
    return {"id": node_id, "name": {"value": name}, "description": {"value": description}}


def _tag(node_id: str, name: str) -> dict[str, Any]:
    return {"id": node_id, "name": {"value": name}}


def test_the_infrahub_adapter_is_a_destination_node_reader() -> None:
    assert isinstance(make_adapter(DestinationClient({})), DestinationNodeReader)


def test_updates_are_read_by_recorded_id_in_one_query_per_kind() -> None:
    client = DestinationClient({SITE_KIND: [_site(DEFAULT_DESTINATION_ID, "site-a")]})
    update = _site_update()

    reads = make_adapter(client).read_planned_nodes([update])

    assert compare_planned_operation(update, reads[update.operation_id]) == "matches"
    [query] = client.node_reads(SITE_KIND)
    assert query["ids"] == [DEFAULT_DESTINATION_ID]
    # Only the fields the operation writes are selected.
    assert "comment" in query["exclude"]
    assert "description" not in query["exclude"]


def test_an_update_whose_node_was_deleted_is_missing_and_a_changed_one_differs() -> None:
    client = DestinationClient({SITE_KIND: [_site(DEFAULT_DESTINATION_ID, "site-a", description="changed")]})
    changed = _site_update()
    deleted = make_operation(kind=TEAM_KIND, action="update", identity={"name": "team-a"}, payload={"name": "team-a"})

    reads = make_adapter(client).read_planned_nodes([changed, deleted])

    assert compare_planned_operation(changed, reads[changed.operation_id]) == "differs"
    assert compare_planned_operation(deleted, reads[deleted.operation_id]) == "missing"


def test_creates_of_one_kind_are_read_in_one_query_and_matched_on_their_whole_key() -> None:
    client = DestinationClient(
        {SITE_KIND: [_site("id-1", "site-1"), _site("id-2", "site-2", description="changed"), _site("id-9", "site-9")]}
    )
    written, changed, absent = _site_create("site-1"), _site_create("site-2"), _site_create("site-3")

    reads = make_adapter(client).read_planned_nodes([written, changed, absent])

    assert [
        compare_planned_operation(operation, reads[operation.operation_id]) for operation in (written, changed, absent)
    ] == [
        "matches",
        "differs",
        "missing",
    ]
    [query] = client.node_reads(SITE_KIND)
    assert query["name__values"] == ["site-1", "site-2", "site-3"]


def test_a_create_keyed_across_a_relationship_is_matched_on_its_resolved_peer() -> None:
    client = DestinationClient(
        {
            SITE_KIND: [_site("site-a-id", "site-a"), _site("site-b-id", "site-b")],
            DEVICE_KIND: [
                {"id": "dev-1", "name": {"value": "dev"}, "site": {"id": "site-b-id", "__typename": SITE_KIND}},
                {"id": "dev-2", "name": {"value": "dev"}, "site": {"id": "site-a-id", "__typename": SITE_KIND}},
            ],
        }
    )
    on_site_a = device_operation("dev", site_name="site-a")

    reads = make_adapter(client).read_planned_nodes([on_site_a])

    read = reads[on_site_a.operation_id]
    assert read.node is not None
    assert read.planned_peers == {"site": frozenset({"site-a-id"})}
    assert compare_planned_operation(on_site_a, read) == "matches"


def test_a_create_whose_key_peer_does_not_exist_is_missing() -> None:
    client = DestinationClient({SITE_KIND: [], DEVICE_KIND: []})
    operation = device_operation("dev", site_name="site-that-was-never-written")

    reads = make_adapter(client).read_planned_nodes([operation])

    assert compare_planned_operation(operation, reads[operation.operation_id]) == "missing"
    assert client.node_reads(DEVICE_KIND) == []


@pytest.mark.parametrize(
    ("destination_members", "expected"),
    [
        (["tag-b-id", "tag-a-id"], "matches"),
        (["tag-a-id"], "differs"),
        (["tag-a-id", "tag-b-id", "tag-c-id"], "differs"),
    ],
)
def test_cardinality_many_members_are_compared_as_a_set_of_peers(destination_members: list[str], expected: str) -> None:
    client = DestinationClient(
        {
            TAG_KIND: [_tag("tag-a-id", "tag-a"), _tag("tag-b-id", "tag-b"), _tag("tag-c-id", "tag-c")],
            TEAM_KIND: [
                {
                    "id": DEFAULT_DESTINATION_ID,
                    "name": {"value": "team-a"},
                    "members": [{"id": member, "__typename": TAG_KIND} for member in destination_members],
                }
            ],
        }
    )
    operation = team_operation(["tag-a", "tag-b"])

    reads = make_adapter(client).read_planned_nodes([operation])

    assert compare_planned_operation(operation, reads[operation.operation_id]) == expected
    [query] = client.node_reads(TEAM_KIND)
    assert query["include"] == ["members"]


def test_a_planned_peer_that_matches_no_node_differs_and_raises_nothing() -> None:
    client = DestinationClient(
        {
            TAG_KIND: [_tag("tag-a-id", "tag-a")],
            TEAM_KIND: [
                {
                    "id": DEFAULT_DESTINATION_ID,
                    "name": {"value": "team-a"},
                    "members": [{"id": "tag-a-id", "__typename": TAG_KIND}],
                }
            ],
        }
    )
    operation = team_operation(["tag-a", "tag-never-written"])

    reads = make_adapter(client).read_planned_nodes([operation])

    assert reads[operation.operation_id].planned_peers == {"members": None}
    assert compare_planned_operation(operation, reads[operation.operation_id]) == "differs"


def test_deletes_are_not_read() -> None:
    client = DestinationClient({})
    delete = _site_delete()

    assert make_adapter(client).read_planned_nodes([delete]) == {}
    assert client.events == []


def test_a_failed_query_raises_to_the_caller() -> None:
    client = DestinationClient({})
    client.filter_error = ConnectionError("the destination at https://user:secret@host is unreachable")

    with pytest.raises(ConnectionError):
        make_adapter(client).read_planned_nodes([_site_update()])


def test_a_relationship_reference_shape_is_kept_by_the_read() -> None:
    """The read resolves each reference's peers with the same identity the apply writes with."""
    operation = make_operation(
        kind=TEAM_KIND,
        action="update",
        identity={"name": "team-a"},
        payload={"name": "team-a"},
        relationships=[RelationshipReference(field="members", peer_kind=TAG_KIND, cardinality="many", peers=[])],
    )
    client = DestinationClient(
        {TEAM_KIND: [{"id": DEFAULT_DESTINATION_ID, "name": {"value": "team-a"}, "members": []}]}
    )

    reads = make_adapter(client).read_planned_nodes([operation])

    # `peers: []` plans an emptied set, which an empty destination set matches.
    assert compare_planned_operation(operation, reads[operation.operation_id]) == "matches"
