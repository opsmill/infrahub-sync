"""An unkeyed planned operation is refused before it touches a live destination.

The offline harnesses prove the gate reads the SDK's rendered mutation and raises. What they
cannot prove is that nothing reached the server: a fixture holds no destination state, so
"zero mutation calls" is asserted against a recording transport rather than against Infrahub.
This module closes that gap on the smallest possible fixture — one throwaway schema, one
planned create, one count read back.

**Everything this module touches lives on one branch it creates and deletes.** The sibling
modules load their throwaway schema onto `main`, which makes two concurrent runs — or a run
against an instance someone else is using — share a namespace: a `filters(kind=...)` teardown
there removes objects the run does not own. Here the branch name carries a per-run uuid, the
schema is loaded onto that branch alone, every read and write names it, and the branch is
deleted afterwards, taking its schema and its objects with it. `main` is never written.

The module skips itself without a configured destination. Only the destination is needed; the
operation is a hand-built plan record driven straight through
`InfrahubAdapter.apply_planned_operation`, so no source adapter is involved. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    uv run pytest -m integration tests/integration/test_infrahub_unkeyed_refusal_integration.py
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
import requests

from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.plan.errors import UnkeyedWriteRefusedError
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

SITE_KIND = "TestUnkeyedSite"
DEVICE_KIND = "TestUnkeyedDevice"
MOUNT_KIND = "TestUnkeyedMount"

# `TestUnkeyedDevice`'s human-friendly ID crosses the `site` relationship, which is exactly the
# shape the SDK cannot render a key for: a peer supplied as a resolved node id renders as
# `{"id": ...}` with no `__typename`, so `get_human_friendly_id()` resolves to None. The site is
# all-direct so the peer it references is itself writable and the refusal under test is the
# device's alone.
#
# `TestUnkeyedMount` is the third shape, and it is what keeps the nested-peer *read* covered.
# Its own human-friendly ID is all-direct, so it renders a key and is written; the peer it
# references is the crossing kind. Resolving that peer is the only place PD-004's nested
# `<rel>__<attr>__value` filter spelling and AD043's nested `{peer_kind, identity}` walk run
# against a real destination — a kind may be unwritable and still be perfectly readable.
_SCHEMA = {
    "version": "1.0",
    "nodes": [
        {
            "name": "UnkeyedSite",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
        },
        {
            "name": "UnkeyedDevice",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["site__name__value", "name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": False}],
            "relationships": [
                {
                    "name": "site",
                    "peer": SITE_KIND,
                    "cardinality": "one",
                    "kind": "Attribute",
                    "optional": False,
                },
            ],
        },
        {
            "name": "UnkeyedMount",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
            "relationships": [
                {
                    "name": "device",
                    "peer": DEVICE_KIND,
                    "cardinality": "one",
                    "kind": "Attribute",
                    "optional": False,
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


def _await_schema_kinds(client: Any, branch: str, kinds: tuple[str, ...], timeout: float = 120.0) -> None:  # noqa: ANN401
    """Block until the client can resolve every one of `kinds` on `branch`.

    `POST /api/schema/load` returns once the payload is accepted, not once the kinds it
    declares are resolvable, and a create issued in that window fails as a missing schema
    rather than as the refusal under test. The SDK's own `wait_until_converged` settles the
    server side; the loop then re-fetches until the *client's* view agrees, which is the view
    `client.create` actually reads. Both are needed when a second run is loading its own
    branch's schema against the same instance at the same time.
    """
    client.schema.wait_until_converged(branch=branch)
    deadline = time.monotonic() + timeout
    while True:
        missing = set(kinds) - set(client.schema.all(branch=branch, refresh=True))
        if not missing:
            return
        if time.monotonic() >= deadline:
            msg = f"Branch {branch!r} did not serve {sorted(missing)} within {timeout:.0f}s of a successful load."
            raise AssertionError(msg)
        time.sleep(1.0)


def _make_client(address: str, token: str, branch: str = "main") -> Any:  # noqa: ANN401 — the SDK client is dynamic
    """A sync Infrahub client scoped to `branch`, imported lazily for base installs.

    The default branch is what the planned-write surface inherits: `apply_planned_operation`
    names no branch of its own, so scoping the client is what keeps every write this run
    issues inside the branch it owns.
    """
    from infrahub_sdk import Config, InfrahubClientSync

    return InfrahubClientSync(config=Config(address=address, api_token=token, default_branch=branch))


def _device_operation(device_name: str, site_name: str) -> PlannedOperation:
    """One planned create for the kind whose convergence key crosses `site`."""
    identity = canonical_identity(
        {"name": device_name, "site": {"peer_kind": SITE_KIND, "identity": {"name": site_name}}},
        kind=DEVICE_KIND,
    )
    return PlannedOperation(
        operation_id=operation_id("create", DEVICE_KIND, identity),
        action="create",
        kind=DEVICE_KIND,
        identity=identity,
        tier=0,
        payload={"name": device_name},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": site_name}])
        ],
    )


def _mount_operation(mount_name: str, device_name: str, site_name: str) -> PlannedOperation:
    """One planned create for the keyed consumer, referencing the crossing peer.

    The reference carries the peer's identity as AD043 records it — a nested
    `{peer_kind, identity}` pair, because the peer's own key crosses `site` — so resolving it
    is what forces the nested filter spelling at the destination.
    """
    peer_identity = {"name": device_name, "site": {"peer_kind": SITE_KIND, "identity": {"name": site_name}}}
    identity = canonical_identity({"name": mount_name}, kind=MOUNT_KIND)
    return PlannedOperation(
        operation_id=operation_id("create", MOUNT_KIND, identity),
        action="create",
        kind=MOUNT_KIND,
        identity=identity,
        tier=0,
        payload={"name": mount_name},
        relationships=[
            RelationshipReference(field="device", peer_kind=DEVICE_KIND, cardinality="one", peers=[peer_identity])
        ],
    )


@dataclass(frozen=True)
class UnkeyedScope:
    """One run's isolated destination scope."""

    client: Any
    adapter: InfrahubAdapter
    branch: str
    site_name: str
    device_name: str


def _branch_exists(address: str, token: str, branch: str) -> bool:
    """Whether the destination still lists `branch`."""
    response = requests.post(
        f"{address}/graphql",
        headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
        json={"query": "query { Branch { name } }"},
        timeout=30,
    )
    response.raise_for_status()
    branches = response.json().get("data", {}).get("Branch") or []
    return any(entry.get("name") == branch for entry in branches)


@pytest.fixture
def unkeyed_scope() -> Iterator[UnkeyedScope]:
    """A branch this run owns, carrying the throwaway schema, one site and one device.

    The branch is the unit of isolation *and* of cleanup: deleting it removes the schema and
    every object created against it, so no teardown has to enumerate objects by kind and
    therefore none can remove another run's. `main` is never written.

    The device is created **directly through the SDK**, not through a planned apply: its key
    crosses a relationship, so the write surface would refuse it — which is the very thing the
    refusal case asserts. Seeding it directly is what makes it a *pre-existing* peer, which is
    the only state in which the nested-peer read can be exercised at all.
    """
    address, token = _env_or_skip()
    suffix = uuid.uuid4().hex[:8]
    branch = f"unkeyed-refusal-{suffix}"
    # Created through a main-scoped client; every later call uses the branch-scoped one below.
    _make_client(address, token).branch.create(branch_name=branch, sync_with_git=False)
    client = _make_client(address, token, branch)
    try:
        schema_response = requests.post(
            f"{address}/api/schema/load?branch={branch}",
            headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
            json={"schemas": [_SCHEMA]},
            timeout=60,
        )
        schema_response.raise_for_status()
        _await_schema_kinds(client, branch, (SITE_KIND, DEVICE_KIND, MOUNT_KIND))

        site_name = f"unkeyed-site-{suffix}"
        site = client.create(kind=SITE_KIND, branch=branch, data={"name": site_name})
        site.save()
        device_name = f"unkeyed-device-{suffix}"
        device = client.create(kind=DEVICE_KIND, branch=branch, data={"name": device_name, "site": site.id})
        device.save()

        adapter = InfrahubAdapter.__new__(InfrahubAdapter)
        adapter.client = client
        adapter.schema = client.schema.all(branch=branch)
        adapter.source_node = None
        adapter.owner_node = None
        yield UnkeyedScope(client=client, adapter=adapter, branch=branch, site_name=site_name, device_name=device_name)
    finally:
        client.branch.delete(branch_name=branch)
        assert not _branch_exists(address, token, branch), (
            f"Branch {branch!r} survived teardown, so this run left destination state behind."
        )


def test_an_unkeyed_planned_operation_is_refused_without_touching_the_destination(
    unkeyed_scope: UnkeyedScope,
) -> None:
    """The refusal is proven against a live server, not against a recording transport.

    The count is read back from the destination on both sides of the refusal, so "no mutation
    was attempted" is a statement about Infrahub's state rather than about what the client
    chose to send. A second apply is included because the failure this gate exists to prevent
    is a *duplicate* on re-apply: an unkeyed write that silently succeeded would show here as
    a count that climbed.
    """
    scope = unkeyed_scope
    before = scope.client.count(kind=DEVICE_KIND, branch=scope.branch)

    for attempt in range(2):
        with pytest.raises(UnkeyedWriteRefusedError) as refusal:
            scope.adapter.apply_planned_operation(
                operation=_device_operation("unkeyed-device-a", scope.site_name),
                peers=scope.adapter.new_peer_resolver(),
            )
        assert DEVICE_KIND in str(refusal.value), f"The refusal must name the destination kind: {refusal.value}"
        assert scope.client.count(kind=DEVICE_KIND, branch=scope.branch) == before, (
            f"Apply attempt {attempt + 1} changed the destination count for {DEVICE_KIND}, so the "
            "operation mutated the destination before the gate refused it."
        )

    assert scope.client.filters(kind=DEVICE_KIND, branch=scope.branch, populate_store=False) == [], (
        f"A refused operation left an object of kind {DEVICE_KIND} at the destination."
    )


def test_a_keyed_consumer_resolves_a_crossing_peer_through_the_nested_filter(
    unkeyed_scope: UnkeyedScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kind that cannot be written can still be read, and resolving it is nested (AD043/PD-004).

    This is the semantic half the refusal case cannot cover. The consumer's own key is
    all-direct, so its planned write renders keyed and is issued; the peer it names is the
    crossing kind, which pre-exists at the destination. Resolving that peer is the only place
    the nested `{peer_kind, identity}` walk turns into a nested `<rel>__<attr>__value` filter
    against a real server, and no offline harness can settle how the destination answers it.
    """
    scope = unkeyed_scope
    mount_name = f"unkeyed-mount-{scope.branch.rsplit('-', maxsplit=1)[-1]}"
    queries: list[dict[str, Any]] = []
    real_filters = scope.client.filters

    def recording_filters(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — SDK passthrough
        queries.append(dict(kwargs))
        return real_filters(*args, **kwargs)

    monkeypatch.setattr(scope.client, "filters", recording_filters)
    node_id = scope.adapter.apply_planned_operation(
        operation=_mount_operation(mount_name, scope.device_name, scope.site_name),
        peers=scope.adapter.new_peer_resolver(),
    )
    monkeypatch.undo()

    nested = [
        query
        for query in queries
        if query.get("kind") == DEVICE_KIND and query.get("site__name__value") == scope.site_name
    ]
    assert nested, (
        f"No destination query resolved {DEVICE_KIND!r} through the nested "
        f"'site__name__value' filter. PD-004's spelling was not exercised. Queries issued: {queries}"
    )
    assert nested[0].get("name__value") == scope.device_name, (
        f"The nested query must also pin the peer's own direct component: {nested[0]}"
    )

    written = scope.client.get(kind=MOUNT_KIND, id=node_id, branch=scope.branch, include=["device"])
    assert written.name.value == mount_name, "The keyed consumer was not written as planned."
    assert written.device.id is not None, "The consumer was written without the peer it referenced."
    assert scope.client.count(kind=MOUNT_KIND, branch=scope.branch) == 1


def test_the_run_writes_nothing_outside_the_branch_it_owns(unkeyed_scope: UnkeyedScope) -> None:
    """Concurrency safety, asserted rather than described.

    Two of these runs must be able to share one destination. That holds only if the schema and
    the site this run created are invisible on `main`: a run that wrote there would both see
    and delete another run's objects. Asserting the kind is absent from `main`'s schema is the
    strongest form — it is not merely that no object exists, but that none could.
    """
    scope = unkeyed_scope
    address, token = _env_or_skip()

    response = requests.get(f"{address}/api/schema?branch=main", headers={"X-INFRAHUB-KEY": token}, timeout=30)
    response.raise_for_status()
    main_kinds = {node["kind"] for node in response.json().get("nodes", [])}

    assert main_kinds & {SITE_KIND, DEVICE_KIND} == set(), (
        f"The throwaway schema reached 'main' ({sorted(main_kinds & {SITE_KIND, DEVICE_KIND})}), so a "
        "concurrent run would share this run's namespace."
    )
    assert scope.client.count(kind=SITE_KIND, branch=scope.branch) == 1, (
        "The site this run created must be visible on its own branch, or the test above proves nothing."
    )
