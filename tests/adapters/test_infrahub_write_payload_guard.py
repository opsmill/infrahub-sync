"""No write payload carries `updated_at` or `_relation__updated_at`.

A plan reads the destination with `include_metadata=True`, and apply reads a node's current
peers before an update that adds edges. Infrahub's mutation inputs do not accept the
metadata fields, so the write path builds fresh payloads and never saves a node it read.
These cases keep it that way: the destination answers every read with change times, and no
rendered mutation may carry one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.adapters.infrahub import PeerResolver
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
from tests.adapters.test_infrahub_planned_write import (
    IMPORT_ACCOUNT_ID,
    OWNER_ID,
    SITE_KIND,
    TAG_KIND,
    TEAM_CREATE,
    TEAM_KIND,
    RecordingClient,
    make_adapter,
    make_operation,
    server_operation,
)

if TYPE_CHECKING:
    from infrahub_sdk.node import InfrahubNodeSync

UPDATED_AT = "2026-10-01T08:00:00+00:00"


class MetadataClient(RecordingClient):
    """A recording client whose reads answer with change times, as a read with metadata does."""

    def __init__(self) -> None:
        super().__init__()
        self.fetched: list[InfrahubNodeSync] = []

    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Answer the current-peer read, with `updated_at` on the node and on every edge."""
        node: InfrahubNodeSync = super().get(*args, **kwargs)
        for rel_name in node._relationships:
            related = getattr(node, rel_name)
            for peer in getattr(related, "peers", [related]):
                peer.updated_at = UPDATED_AT
        self.fetched.append(node)
        return node


TEAM_UPDATE = make_operation(
    kind=TEAM_KIND,
    action="update",
    identity={"name": "team-a"},
    payload={"name": "team-a", "description": "core"},
    relationships=[
        RelationshipReference(
            field="members", peer_kind=TAG_KIND, cardinality="many", peers=[{"name": "tag-b"}, {"name": "tag-c"}]
        )
    ],
)

SERVER_UPDATE = server_operation("server-a").model_copy(update={"action": "update", "destination_id": "server-id-1"})


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(TEAM_CREATE, id="create-many-edges"),
        pytest.param(server_operation("server-a"), id="create-one-edge"),
        pytest.param(TEAM_UPDATE, id="update-many-edges"),
        pytest.param(SERVER_UPDATE, id="update-one-edge"),
    ],
)
@pytest.mark.parametrize("owner", [None, OWNER_ID], ids=["no-owner", "owner"])
def test_no_write_payload_carries_a_change_time(operation: PlannedOperation, owner: str | None) -> None:
    """Neither a create nor an update sends `updated_at` or `_relation__updated_at`."""
    client = MetadataClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-2"]
    client.existing_peers[operation.kind, "site"] = ["site-id-1"]
    adapter = make_adapter(client, source=IMPORT_ACCOUNT_ID, owner=owner, protect=True)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    adapter.apply_planned_operation(operation=operation, peers=peers)

    assert len(client.mutations) == 1
    rendered = client.mutations[0][1]
    assert "updated_at" not in rendered, f"A write payload may carry no change time:\n{rendered}"
    if owner is not None and operation.action == "update":
        assert client.fetched, "Precondition: the update read the node's current peers."
        # Saving the node it read would send the change times, so the case is not vacuous.
        assert "_relation__updated_at" in str(client.fetched[0]._generate_input_data()), (
            "Precondition: the node read carries change times that would render if it were saved."
        )
