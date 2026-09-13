"""Pin the destination Upsert mutation's replace semantics, live.

A planned operation is written by exactly one destination mutation: the convergent upsert,
carrying the plan's cardinality-many peer set (ADR-0012). Nothing about a peer *removal* ever
reaches the wire — the SDK renders only the surviving peer list, with no removal directive —
so surplus peers are removed **iff the destination's Upsert mutation replaces a relationship
list rather than merging it**. That is a fact about the server no offline harness can settle,
and this test is what pins it: it shrinks a cardinality-many peer set N → fewer and N → 0
through the planned-write surface and asserts the surplus peers are gone at the destination.

**If this test ever fails on the peer-set assertions, Infrahub has been proven to merge
rather than replace**, and the named escalation applies: implement explicit per-peer
removal mutations. Do not weaken the assertions.

Two further destination-state assertions ride along, because they are about what the write
carries rather than about a second write: every peer keeps the `is_protected` write property
the planned write sends, and an optional cardinality-one relationship (`lead`) that no
operation here maps is left exactly as it was found by applies that carry `members`.

Everything this module writes — the throwaway schema, the tags, the team — goes to a branch it
creates at fixture start and deletes at teardown, so **no schema load, node, relationship or
test-data write targets `main`**. Only `BranchCreate` and `BranchDelete` are issued in the
client's default-branch context, which is their administrative context rather than a data
write. The module skips itself without a configured destination. Only the destination is
needed — the operations are hand-built plan records driven straight through
`InfrahubAdapter.apply_planned_operation`, so no NetBox source is involved. Run with::

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
from infrahub_sync.plan.models import PlanAction, PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

TEAM_KIND = "TestShrinkTeam"
TAG_KIND = "TestShrinkTag"

# `members` is the cardinality-many relationship under replace-set. `lead` is an OPTIONAL
# CARDINALITY-ONE relationship no operation here maps — the shape a whole-node re-render nulls —
# so it is what makes "the write touches no unmapped destination field" decidable against a live
# destination. It carries an explicit identifier because both relationships point at the same
# peer kind and would otherwise be generated the same one.
#
# Both kinds carry an all-direct human-friendly ID so the convergent upsert is keyed and every
# apply converges on one destination object — without which "the surplus peers are gone" could
# be true of a fresh duplicate instead.
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
                {
                    "name": "lead",
                    "peer": TAG_KIND,
                    "cardinality": "one",
                    "kind": "Generic",
                    "optional": True,
                    "identifier": "testshrinkteam__lead",
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


def _await_schema_kinds(address: str, token: str, kinds: tuple[str, ...], branch: str, timeout: float = 90.0) -> None:
    """Block until the destination serves every one of `kinds` on `branch`.

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
            f"{address}/api/schema?branch={branch}",
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


def _make_client(address: str, token: str, branch: str | None = None) -> Any:  # noqa: ANN401 — dynamically typed
    """A sync Infrahub client, imported lazily so unit-only runs need no SDK extras.

    `branch` becomes the client's `default_branch`. That is how the adapter itself targets a
    branch (`InfrahubAdapter.__init__` puts the resolved branch in `sdk_config["default_branch"]`)
    and the only way the planned-write surface, which takes no branch argument, writes anywhere
    but `main`. A client built without `branch` is for the branch lifecycle alone.
    """
    from infrahub_sdk import Config, InfrahubClientSync

    settings: dict[str, Any] = {"address": address, "api_token": token}
    if branch is not None:
        settings["default_branch"] = branch
    return InfrahubClientSync(config=Config(**settings))


def _team_operation(team_name: str, tag_names: list[str], *, action: PlanAction) -> PlannedOperation:
    """One planned operation reconciling the team's `members` to exactly `tag_names`.

    Built the way the artifact records one — canonical identity, derived identifier. `action`
    is passed in rather than inferred from the peer count: a shrink to zero peers is an update
    of an existing team, not a create, and inferring it from `tag_names` would have made the
    N → 0 apply claim otherwise. The write surface routes create and update through the same
    convergent upsert either way.
    """
    identity = canonical_identity({"name": team_name}, kind=TEAM_KIND)
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


def _destination_peer_ids(client: Any, team_id: str, branch: str) -> set[str]:  # noqa: ANN401 — SDK node
    """The destination's current `members` peer ids for the team, read back independently."""
    node = client.get(kind=TEAM_KIND, id=team_id, branch=branch, include=["members"])
    return set(node.members.peer_ids)


def _destination_peer_protection(client: Any, team_id: str, branch: str) -> dict[str, Any]:  # noqa: ANN401 — SDK node
    """Each `members` peer's `is_protected` write property, read back from the destination.

    `property=True` is what makes the SDK request the relationship's write properties; without
    it every peer comes back with `is_protected` unset and the assertion would be vacuous. The
    test adapter has no source or owner account, so protection is the write property that is
    checkable live; `source` and `owner` are covered offline.
    """
    node = client.get(kind=TEAM_KIND, id=team_id, branch=branch, include=["members"], property=True)
    return {peer.id: peer.is_protected for peer in node.members.peers}


def _destination_lead_id(client: Any, team_id: str, branch: str) -> str | None:  # noqa: ANN401 — SDK node
    """The destination's current `lead` peer id, or None when the relationship is empty."""
    node = client.get(kind=TEAM_KIND, id=team_id, branch=branch, include=["lead"])
    return node.lead.id if node.lead else None


@pytest.fixture
def live_shrink_fixture() -> Iterator[tuple[Any, InfrahubAdapter, dict[str, str], str, str]]:
    """A branch of this module's own, holding the throwaway schema and three tags.

    Yields `(client, adapter, tag_ids_by_name, team_name, branch)`. The team itself is created
    by the test's first planned apply — that create *is* the N-peer starting state under test.

    The branch is the teardown: deleting it discards the schema, the tags and the team in one
    administrative call, and nothing this fixture or the test writes can reach `main`.

    Two clients, because the planned-write surface takes no branch argument: `lifecycle_client`
    is bound to no branch and issues only `BranchCreate` and `BranchDelete`, while `client`
    carries the branch as its `default_branch` so every schema read, node write and read-back —
    including the adapter's own — lands on the branch instead of `main`.
    """
    address, token = _env_or_skip()
    suffix = uuid.uuid4().hex[:8]
    branch = f"shrink-{suffix}"

    # The branch lifecycle is the only thing issued in the client's default-branch context.
    lifecycle_client = _make_client(address, token)
    lifecycle_client.branch.create(branch_name=branch, sync_with_git=False)
    try:
        schema_response = requests.post(
            f"{address}/api/schema/load?branch={branch}",
            headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
            json={"schemas": [_SCHEMA]},
            timeout=60,
        )
        schema_response.raise_for_status()
        _await_schema_kinds(address, token, (TAG_KIND, TEAM_KIND), branch=branch)

        client = _make_client(address, token, branch=branch)
        tag_ids: dict[str, str] = {}
        for index in range(3):
            name = f"shrink-tag-{suffix}-{index}"
            tag = client.create(kind=TAG_KIND, branch=branch, data={"name": name})
            tag.save()
            tag_ids[name] = tag.id

        # The adapter with only the state the planned-write surface reads — the same
        # `__new__` construction the sibling integration module uses to skip the
        # CoreAccount lookups of full `__init__`.
        adapter = InfrahubAdapter.__new__(InfrahubAdapter)
        adapter.client = client
        adapter.schema = client.schema.all(branch=branch)
        adapter.source_node = None
        adapter.owner_node = None

        team_name = f"shrink-team-{suffix}"
        yield client, adapter, tag_ids, team_name, branch
    finally:
        lifecycle_client.branch.delete(branch)


def test_shrinking_a_cardinality_many_peer_set_removes_surplus_peers(
    live_shrink_fixture: tuple[Any, InfrahubAdapter, dict[str, str], str, str],
) -> None:
    """The pin. N → fewer and N → 0, surplus peers gone at the destination.

    One test rather than three, because the shrink is a *sequence*: the N-peer state each
    shrink starts from is the previous apply's observed outcome, so a failure names the exact
    transition that broke. Every peer-set observation is read back from the destination with
    an independent query — never off the adapter's in-memory state, which AD075 exists to
    distrust.

    Two destination-state properties are measured along the same sequence: the peers the write
    creates carry `is_protected`, and `lead` — an optional cardinality-one relationship set out
    of band and mapped by no operation here — survives every apply untouched.
    """
    client, adapter, tag_ids, team_name, branch = live_shrink_fixture
    names = sorted(tag_ids)

    # N = 3: the starting state, written through the same planned-write surface.
    team_id = adapter.apply_planned_operation(
        operation=_team_operation(team_name, names, action="create"), peers=adapter.new_peer_resolver()
    )
    assert _destination_peer_ids(client, team_id, branch) == set(tag_ids.values()), (
        "Precondition: the destination must hold all three peers before any shrink is measured."
    )

    # The per-peer write metadata the planned write sends must be at the destination.
    protection = _destination_peer_protection(client, team_id, branch)
    assert len(protection) == len(tag_ids), (
        f"Expected the write property of all {len(tag_ids)} peers, read back {protection}."
    )
    assert all(protected is True for protected in protection.values()), (
        f"Every peer the planned write created must be protected at the destination, read back "
        f"{protection}. The write is not carrying the per-peer `is_protected` property."
    )

    # `lead` is set out of band, through the SDK, and mapped by none of the applies below.
    lead_id = tag_ids[names[-1]]
    team = client.get(kind=TEAM_KIND, id=team_id, branch=branch)
    team.lead = lead_id
    team.save()
    assert _destination_lead_id(client, team_id, branch) == lead_id, (
        "Precondition: `lead` must be set at the destination before an apply can be shown to leave it alone."
    )

    # N -> fewer: two surplus peers must be gone.
    kept = names[0]
    shrunk_id = adapter.apply_planned_operation(
        operation=_team_operation(team_name, [kept], action="update"), peers=adapter.new_peer_resolver()
    )
    assert shrunk_id == team_id, (
        "The shrinking apply must converge on the same destination object; a fresh duplicate "
        "would make the surplus-peer assertion vacuous."
    )
    observed = _destination_peer_ids(client, team_id, branch)
    assert observed == {tag_ids[kept]}, (
        f"Shrinking members from 3 peers to 1 left {sorted(observed)} at the destination, expected "
        f"exactly {{{tag_ids[kept]!r}}}. The destination Upsert mutation did NOT replace the "
        "relationship list — ADR-0012's pinned semantics do not hold, and the escalation "
        "(explicit per-peer removal mutations) applies."
    )
    assert _destination_lead_id(client, team_id, branch) == lead_id, (
        "The shrinking apply maps only `members`, so it must leave `lead` exactly as it found it. "
        "An unmapped optional cardinality-one relationship was cleared by the write."
    )

    # N -> 0: `peers: []` empties the set (AD085).
    emptied_id = adapter.apply_planned_operation(
        operation=_team_operation(team_name, [], action="update"), peers=adapter.new_peer_resolver()
    )
    assert emptied_id == team_id
    observed = _destination_peer_ids(client, team_id, branch)
    assert observed == set(), (
        f"Emptying members left {sorted(observed)} at the destination. `peers: []` must remove "
        "every remaining peer (AD085) under the Upsert mutation's replace semantics."
    )
    assert _destination_lead_id(client, team_id, branch) == lead_id, (
        "The emptying apply maps only `members`, so it must leave `lead` exactly as it found it. "
        "An unmapped optional cardinality-one relationship was cleared by the write."
    )
