"""FIX-001 (OQ-4) — pin the destination Update mutation's replace semantics, live.

The planned-write flush is a targeted `<kind>Update` carrying the plan's cardinality-many
peer sets (ADR-0003). Nothing about a peer *removal* ever reaches the wire — the SDK's
`RelationshipManagerBase._generate_input_data` renders only the surviving peer list, with no
removal directive — so surplus peers are removed **iff the destination's Update mutation
replaces a relationship list rather than merging it**. That is a fact about the server no
offline harness can settle, and this test is what pins it: it shrinks a cardinality-many peer
set N → fewer and N → 0 through the planned-write surface and asserts the surplus peers are
gone at the destination.

**If this test ever fails on the peer-set assertions, Infrahub has been proven to merge
rather than replace**, and OQ-4's named escalation applies: implement explicit per-peer
removal mutations. Do not weaken the assertions.

Same posture as the sibling `test_infrahub_node_to_diffsync_integration.py`: a throwaway
schema is loaded, nodes are created against it and deleted afterwards, and the module skips
itself without a configured destination. Only the destination is needed — the operations are
hand-built plan records driven straight through `InfrahubAdapter.apply_planned_operation`,
so no NetBox source is involved. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    uv run pytest -m integration tests/integration/test_infrahub_replace_set_shrink_integration.py
"""

from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
import requests

from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

DESTINATION_BRANCH = "main"
TEAM_KIND = "TestShrinkTeam"
TAG_KIND = "TestShrinkTag"

# One cardinality-many relationship, and an all-direct human-friendly ID on both kinds so the
# convergent upsert is keyed and every apply converges on one destination object — without
# which "the surplus peers are gone" could be true of a fresh duplicate instead.
_SCHEMA = {
    "version": "1.0",
    "nodes": [
        {
            "name": "ShrinkTag",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
        },
        {
            "name": "ShrinkTeam",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
            "relationships": [
                {
                    "name": "members",
                    "peer": TAG_KIND,
                    "cardinality": "many",
                    "kind": "Generic",
                    "optional": True,
                },
            ],
        },
    ],
}


def _env_or_skip() -> tuple[str, str]:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        pytest.skip("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set")
    return address, token


def _await_schema_kinds(address: str, token: str, kinds: tuple[str, ...], timeout: float = 90.0) -> None:
    """Block until the destination serves every one of `kinds`.

    `POST /api/schema/load` returns once the payload is accepted, not once the kinds it
    declares are queryable. Creating a node in that window fails with `SchemaNotFoundError`,
    which reads as a broken destination rather than a slow one — and the window is widest
    exactly where it matters, on a freshly reset instance running the whole `-m integration`
    suite, where a sibling module's schema load is still settling when this one lands.
    """
    deadline = time.monotonic() + timeout
    missing = set(kinds)
    while True:
        response = requests.get(
            f"{address}/api/schema?branch={DESTINATION_BRANCH}",
            headers={"X-INFRAHUB-KEY": token},
            timeout=30,
        )
        response.raise_for_status()
        missing = set(kinds) - {node["kind"] for node in response.json().get("nodes", [])}
        if not missing:
            return
        if time.monotonic() >= deadline:
            msg = (
                f"Destination did not serve {sorted(missing)} within {timeout:.0f}s of a successful "
                f"schema load — the throwaway schema this test measures against is not in place."
            )
            raise AssertionError(msg)
        time.sleep(1.0)


def _make_client(address: str, token: str) -> Any:  # noqa: ANN401 — the SDK client is dynamically typed
    """A sync Infrahub client, imported lazily so unit-only runs need no SDK extras."""
    from infrahub_sdk import Config, InfrahubClientSync

    return InfrahubClientSync(config=Config(address=address, api_token=token))


def _team_operation(team_name: str, tag_names: list[str]) -> PlannedOperation:
    """One planned operation reconciling the team's `members` to exactly `tag_names`.

    Built the way the artifact records one — canonical identity, derived identifier — and
    with `action="update"` for the shrinking applies' shape; the write surface routes create
    and update through the same convergent upsert either way.
    """
    identity = canonical_identity({"name": team_name}, kind=TEAM_KIND)
    action = "create" if tag_names else "update"
    return PlannedOperation(
        operation_id=operation_id(action, TEAM_KIND, identity),
        action=action,
        kind=TEAM_KIND,
        identity=identity,
        tier=0,
        payload={"name": team_name},
        relationships=[
            RelationshipReference(
                field="members",
                peer_kind=TAG_KIND,
                cardinality="many",
                peers=[{"name": tag_name} for tag_name in tag_names],
            )
        ],
    )


def _destination_peer_ids(client: Any, team_id: str) -> set[str]:  # noqa: ANN401 — SDK node
    """The destination's current `members` peer ids for the team, read back independently."""
    node = client.get(kind=TEAM_KIND, id=team_id, branch=DESTINATION_BRANCH, include=["members"])
    return set(node.members.peer_ids)


@pytest.fixture
def live_shrink_fixture() -> Iterator[tuple[Any, InfrahubAdapter, dict[str, str], str]]:
    """Throwaway schema plus three tags at the destination; torn down afterwards.

    Yields `(client, adapter, tag_ids_by_name, team_name)`. The team itself is created by the
    test's first planned apply — that create *is* the N-peer starting state under test.
    """
    address, token = _env_or_skip()
    suffix = uuid.uuid4().hex[:8]

    schema_response = requests.post(
        f"{address}/api/schema/load?branch={DESTINATION_BRANCH}",
        headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
        json={"schemas": [_SCHEMA]},
        timeout=60,
    )
    schema_response.raise_for_status()
    _await_schema_kinds(address, token, (TAG_KIND, TEAM_KIND))

    client = _make_client(address, token)
    tag_ids: dict[str, str] = {}
    created: list[Any] = []
    try:
        for index in range(3):
            name = f"shrink-tag-{suffix}-{index}"
            tag = client.create(kind=TAG_KIND, branch=DESTINATION_BRANCH, data={"name": name})
            tag.save()
            tag_ids[name] = tag.id
            created.append(tag)

        # The adapter with only the state the planned-write surface reads — the same
        # `__new__` construction the sibling integration module uses to skip the
        # CoreAccount lookups of full `__init__`.
        adapter = InfrahubAdapter.__new__(InfrahubAdapter)
        adapter.client = client
        adapter.schema = client.schema.all(branch=DESTINATION_BRANCH)
        adapter.source_node = None
        adapter.owner_node = None

        team_name = f"shrink-team-{suffix}"
        yield client, adapter, tag_ids, team_name
    finally:
        for team in client.filters(kind=TEAM_KIND, branch=DESTINATION_BRANCH, populate_store=False):
            team.delete()
        for tag in created:
            tag.delete()


def test_shrinking_a_cardinality_many_peer_set_removes_surplus_peers(
    live_shrink_fixture: tuple[Any, InfrahubAdapter, dict[str, str], str],
) -> None:
    """FIX-001/OQ-4: the pin. N → fewer and N → 0, surplus peers gone at the destination.

    One test rather than three, because the shrink is a *sequence*: the N-peer state each
    shrink starts from is the previous apply's observed outcome, so a failure names the exact
    transition that broke. Every peer-set observation is read back from the destination with
    an independent query — never off the adapter's in-memory state, which AD075 exists to
    distrust.
    """
    client, adapter, tag_ids, team_name = live_shrink_fixture
    names = sorted(tag_ids)

    # N = 3: the starting state, written through the same planned-write surface.
    team_id = adapter.apply_planned_operation(
        operation=_team_operation(team_name, names), peers=adapter.new_peer_resolver()
    )
    assert _destination_peer_ids(client, team_id) == set(tag_ids.values()), (
        "Precondition: the destination must hold all three peers before any shrink is measured."
    )

    # N -> fewer: two surplus peers must be gone.
    kept = names[0]
    shrunk_id = adapter.apply_planned_operation(
        operation=_team_operation(team_name, [kept]), peers=adapter.new_peer_resolver()
    )
    assert shrunk_id == team_id, (
        "The shrinking apply must converge on the same destination object; a fresh duplicate "
        "would make the surplus-peer assertion vacuous."
    )
    observed = _destination_peer_ids(client, team_id)
    assert observed == {tag_ids[kept]}, (
        f"Shrinking members from 3 peers to 1 left {sorted(observed)} at the destination, expected "
        f"exactly {{{tag_ids[kept]!r}}}. The destination Update mutation did NOT replace the "
        "relationship list — ADR-0003's pinned semantics do not hold, and OQ-4's escalation "
        "(explicit per-peer removal mutations) applies."
    )

    # N -> 0: `peers: []` empties the set (AD085).
    emptied_id = adapter.apply_planned_operation(
        operation=_team_operation(team_name, []), peers=adapter.new_peer_resolver()
    )
    assert emptied_id == team_id
    observed = _destination_peer_ids(client, team_id)
    assert observed == set(), (
        f"Emptying members left {sorted(observed)} at the destination. `peers: []` must remove "
        "every remaining peer (AD085) under the Update mutation's replace semantics."
    )
