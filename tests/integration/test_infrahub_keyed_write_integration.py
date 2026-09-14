"""Keyed writes, proven against a live destination.

The offline harnesses prove what the client renders. What they cannot prove is what the
**server does with it**, and that is the whole substance of this change: a create carrying
every human-friendly-ID component converges rather than duplicating even though the mutation
carries no key at all, and an upsert carrying a recorded `id` updates in place. Both are
server behaviours; a recording transport cannot settle either.

Three cases, on the smallest fixture that can carry them:

1. a kind whose human-friendly ID **crosses a relationship** creates, and a second identical
   apply converges onto the same object rather than making a second one (AD067 closed);
2. an update carrying the recorded destination id renames an attribute **in place**;
3. a recorded id that matches no object is refused, and nothing is written.

**Everything this module touches lives on one branch it creates and deletes.** The sibling
modules load their throwaway schema onto `main`, which makes two concurrent runs — or a run
against an instance someone else is using — share a namespace: a `filters(kind=...)` teardown
there removes objects the run does not own. Here the branch name carries a per-run uuid, the
schema is loaded onto that branch alone, every read and write names it, and the branch is
deleted afterwards, taking its schema and its objects with it. `main` is never written.

The module skips itself without a configured destination, so it is inert in an ordinary run.
Only the destination is needed; each operation is a hand-built plan record driven straight
through `InfrahubAdapter.apply_planned_operation`, so no source adapter is involved. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    uv run pytest -m integration tests/integration/test_infrahub_keyed_write_integration.py
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
from infrahub_sync.plan.errors import StaleDestinationIdError
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

SITE_KIND = "TestUnkeyedSite"
DEVICE_KIND = "TestUnkeyedDevice"
MOUNT_KIND = "TestUnkeyedMount"
RENAMABLE_KIND = "TestUnkeyedRenamable"

# `TestUnkeyedDevice`'s human-friendly ID crosses the `site` relationship. The SDK renders no
# key at all for that shape — a peer supplied as a resolved node id renders as `{"id": ...}`
# with no `__typename`, so `get_human_friendly_id()` resolves to None — and it does not need
# to: the server matches on the components in `data`. That the destination really converges it
# is the first case below, and it is why AD067 is closed rather than worked around. The site is
# all-direct, so the peer it references is itself writable and the device's own keying is what
# each case measures.
#
# `TestUnkeyedMount` is the third shape, and it is what keeps the nested-peer *read* covered.
# Its own human-friendly ID is all-direct; the peer it references is the crossing kind.
# Resolving that peer is the only place PD-004's nested `<rel>__<attr>__value` filter spelling
# and AD043's nested `{peer_kind, identity}` walk run against a real destination.
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
            # `serial` is deliberately **not** an identity component or an HFID component: it
            # is what the in-place update changes. Changing `name` here would contradict the
            # operation's own identity, which the record refuses before any write.
            "attributes": [
                {"name": "name", "kind": "Text", "unique": False},
                {"name": "serial", "kind": "Text", "unique": False, "optional": True},
            ],
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
            # The rename case the recorded-id design exists for: the sync matches these on
            # `serial` while the destination's human-friendly ID is `name`, so renaming `name`
            # is an identity change to the destination and an ordinary attribute change to the
            # sync. Only a write keyed by the recorded id lands it on the right object.
            "name": "UnkeyedRenamable",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [
                {"name": "serial", "kind": "Text", "unique": True},
                {"name": "name", "kind": "Text", "unique": True},
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
class KeyedWriteScope:
    """One run's isolated destination scope."""

    client: Any
    adapter: InfrahubAdapter
    branch: str
    site_name: str
    device_name: str
    renamable_serial: str
    renamable_name: str


def _raise_for_status_without_redirect(response: requests.Response) -> None:
    """Refuse redirects before checking the response status."""
    if response.is_redirect or response.is_permanent_redirect:
        pytest.fail(f"Infrahub returned an unexpected redirect (HTTP {response.status_code})")
    response.raise_for_status()


def _branch_exists(address: str, token: str, branch: str) -> bool:
    """Whether the destination still lists `branch`."""
    response = requests.post(
        f"{address}/graphql",
        headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
        json={"query": "query { Branch { name } }"},
        timeout=30,
        allow_redirects=False,
    )
    _raise_for_status_without_redirect(response)
    branches = response.json().get("data", {}).get("Branch") or []
    return any(entry.get("name") == branch for entry in branches)


@pytest.fixture
def keyed_write_scope() -> Iterator[KeyedWriteScope]:
    """A branch this run owns, carrying the throwaway schema, one site and one device.

    The branch is the unit of isolation *and* of cleanup: deleting it removes the schema and
    every object created against it, so no teardown has to enumerate objects by kind and
    therefore none can remove another run's. `main` is never written.

    The device is created **directly through the SDK** rather than through a planned apply,
    because the cases below need it to *pre-exist*: one converges a planned create onto it and
    another updates it by its recorded id, and neither would be measuring anything if the
    fixture had established the object through the very path under test. It is also the only
    state in which the nested-peer read can be exercised at all.
    """
    address, token = _env_or_skip()
    suffix = uuid.uuid4().hex[:8]
    branch = f"keyed-write-{suffix}"
    # Created through a main-scoped client; every later call uses the branch-scoped one below.
    _make_client(address, token).branch.create(branch_name=branch, sync_with_git=False)
    client = _make_client(address, token, branch)
    try:
        schema_response = requests.post(
            f"{address}/api/schema/load?branch={branch}",
            headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
            json={"schemas": [_SCHEMA]},
            timeout=60,
            allow_redirects=False,
        )
        _raise_for_status_without_redirect(schema_response)
        _await_schema_kinds(client, branch, (SITE_KIND, DEVICE_KIND, MOUNT_KIND, RENAMABLE_KIND))

        site_name = f"unkeyed-site-{suffix}"
        site = client.create(kind=SITE_KIND, branch=branch, data={"name": site_name})
        site.save()
        device_name = f"unkeyed-device-{suffix}"
        device = client.create(
            kind=DEVICE_KIND, branch=branch, data={"name": device_name, "site": site.id, "serial": "sn-first"}
        )
        device.save()
        renamable_serial = f"unkeyed-serial-{suffix}"
        renamable_name = f"unkeyed-before-{suffix}"
        renamable = client.create(
            kind=RENAMABLE_KIND, branch=branch, data={"serial": renamable_serial, "name": renamable_name}
        )
        renamable.save()

        adapter = InfrahubAdapter.__new__(InfrahubAdapter)
        adapter.client = client
        adapter.schema = client.schema.all(branch=branch)
        adapter.source_node = None
        adapter.owner_node = None
        yield KeyedWriteScope(
            client=client,
            adapter=adapter,
            branch=branch,
            site_name=site_name,
            device_name=device_name,
            renamable_serial=renamable_serial,
            renamable_name=renamable_name,
        )
    finally:
        client.branch.delete(branch_name=branch)
        assert not _branch_exists(address, token, branch), (
            f"Branch {branch!r} survived teardown, so this run left destination state behind."
        )


def _device_update(device_name: str, site_name: str, *, destination_id: str, serial: str) -> PlannedOperation:
    """One planned update of the crossing kind, keyed by the id recorded for it.

    The payload restates `name` unchanged and varies `serial`. `name` is an identity component
    of this kind, and a record whose payload contradicts its own identity is refused at
    construction — correctly: the identity a reviewer approved would not be the value written.
    So the attribute that moves has to be one the identity does not carry.
    """
    identity = canonical_identity(
        {"name": device_name, "site": {"peer_kind": SITE_KIND, "identity": {"name": site_name}}},
        kind=DEVICE_KIND,
    )
    return PlannedOperation(
        operation_id=operation_id("update", DEVICE_KIND, identity),
        action="update",
        kind=DEVICE_KIND,
        identity=identity,
        tier=0,
        payload={"name": device_name, "serial": serial},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": site_name}])
        ],
        destination_id=destination_id,
    )


def _renamable_update(serial: str, *, destination_id: str, renamed: str) -> PlannedOperation:
    """One planned update that renames the destination's human-friendly ID itself.

    The sync's identity for this kind is `serial`; the destination's human-friendly ID is
    `name`. Renaming `name` therefore leaves the operation's identity untouched, so the record
    is coherent — and the write cannot be keyed by the human-friendly ID, because the value it
    would key on is the one being changed. The recorded id is the only thing that can land it
    on the right object.
    """
    identity = canonical_identity({"serial": serial}, kind=RENAMABLE_KIND)
    return PlannedOperation(
        operation_id=operation_id("update", RENAMABLE_KIND, identity),
        action="update",
        kind=RENAMABLE_KIND,
        identity=identity,
        tier=0,
        payload={"serial": serial, "name": renamed},
        destination_id=destination_id,
    )


def test_a_relationship_crossing_create_converges_instead_of_duplicating(
    keyed_write_scope: KeyedWriteScope,
) -> None:
    """AD067 closes: the server matches on the components in `data`, with no key on the wire.

    The second apply is the substance. A create-shaped upsert carries neither `id` nor
    `hfid` for this kind, so convergence is the server matching on the human-friendly-ID
    components the payload carries. If it did not, this would show as a count that climbed —
    which is exactly the silent duplicate the whole change exists to prevent.
    """
    scope = keyed_write_scope
    created_name = f"converged-{scope.device_name}"
    resolver = scope.adapter.new_peer_resolver()

    first = scope.adapter.apply_planned_operation(
        operation=_device_operation(created_name, scope.site_name), peers=resolver
    )
    second = scope.adapter.apply_planned_operation(
        operation=_device_operation(created_name, scope.site_name), peers=scope.adapter.new_peer_resolver()
    )

    assert second == first, "The second apply must converge onto the object the first created."
    matching = [
        node
        for node in scope.client.filters(kind=DEVICE_KIND, branch=scope.branch, populate_store=False)
        if node.name.value == created_name
    ]
    assert len(matching) == 1, f"The re-apply duplicated {created_name!r} at the destination: {matching}"


def test_an_update_keyed_by_its_recorded_id_renames_in_place(keyed_write_scope: KeyedWriteScope) -> None:
    """The recorded id is the write key, and the object it names is the one that changes."""
    scope = keyed_write_scope
    before = scope.client.count(kind=DEVICE_KIND, branch=scope.branch)
    seeded = next(
        node
        for node in scope.client.filters(kind=DEVICE_KIND, branch=scope.branch, populate_store=False)
        if node.name.value == scope.device_name
    )

    written = scope.adapter.apply_planned_operation(
        operation=_device_update(scope.device_name, scope.site_name, destination_id=seeded.id, serial="sn-second"),
        peers=scope.adapter.new_peer_resolver(),
    )

    assert written == seeded.id, "The update must write the object its recorded id names."
    assert scope.client.count(kind=DEVICE_KIND, branch=scope.branch) == before, (
        "An update keyed by id must change an object rather than add one."
    )
    reread = scope.client.get(kind=DEVICE_KIND, id=seeded.id, branch=scope.branch)
    assert reread.serial.value == "sn-second", "The change did not reach the object the id named."
    assert reread.name.value == scope.device_name, "The identity components must be written back unchanged."


def test_a_stale_recorded_id_is_refused_with_nothing_written(keyed_write_scope: KeyedWriteScope) -> None:
    """The server answers an unknown id with `NODE_NOT_FOUND` and creates nothing.

    That is what makes the refusal *proven* not to have written, rather than merely
    suspected — which is why it is classified as not-written rather than ambiguous (S6).
    """
    scope = keyed_write_scope
    before = sorted(
        node.name.value for node in scope.client.filters(kind=DEVICE_KIND, branch=scope.branch, populate_store=False)
    )
    seeded = next(
        node
        for node in scope.client.filters(kind=DEVICE_KIND, branch=scope.branch, populate_store=False)
        if node.name.value == scope.device_name
    )
    # A well-formed id proven to match no object: the seeded one with its last hex digit moved.
    stale = seeded.id[:-1] + ("0" if seeded.id[-1] != "0" else "1")
    assert scope.client.get(kind=DEVICE_KIND, id=stale, branch=scope.branch, raise_when_missing=False) is None, (
        "The stale id must be proven to match no object before the refusal is asserted."
    )

    with pytest.raises(StaleDestinationIdError) as refusal:
        scope.adapter.apply_planned_operation(
            operation=_device_update(scope.device_name, scope.site_name, destination_id=stale, serial="never-written"),
            peers=scope.adapter.new_peer_resolver(),
        )

    assert stale in str(refusal.value), f"The refusal must name the id it could not find: {refusal.value}"
    assert refusal.value.wrote is False, "The server created nothing, so this is not an ambiguous write."
    after = sorted(
        node.name.value for node in scope.client.filters(kind=DEVICE_KIND, branch=scope.branch, populate_store=False)
    )
    assert after == before, f"A refused stale-id update changed the {DEVICE_KIND} set: {before} -> {after}"


def test_a_keyed_consumer_resolves_a_crossing_peer_through_the_nested_filter(
    keyed_write_scope: KeyedWriteScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving a peer whose own key crosses a relationship is a nested read (AD043/PD-004).

    The *read* half, which the write cases above cannot reach: they prove the destination
    converges and updates such a kind, and this proves one can be **referenced** by identity.
    The consumer's own key is all-direct; the peer it names is the crossing kind, which
    pre-exists at the destination. Resolving that peer is the only place the nested
    `{peer_kind, identity}` walk turns into a nested `<rel>__<attr>__value` filter against a
    real server, and no offline harness can settle how the destination answers it.
    """
    scope = keyed_write_scope
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


def test_the_run_writes_nothing_outside_the_branch_it_owns(keyed_write_scope: KeyedWriteScope) -> None:
    """Concurrency safety, asserted rather than described.

    Two of these runs must be able to share one destination. That holds only if the schema and
    the site this run created are invisible on `main`: a run that wrote there would both see
    and delete another run's objects. Asserting the kind is absent from `main`'s schema is the
    strongest form — it is not merely that no object exists, but that none could.
    """
    scope = keyed_write_scope
    address, token = _env_or_skip()

    response = requests.get(
        f"{address}/api/schema?branch=main",
        headers={"X-INFRAHUB-KEY": token},
        timeout=30,
        allow_redirects=False,
    )
    _raise_for_status_without_redirect(response)
    main_kinds = {node["kind"] for node in response.json().get("nodes", [])}

    assert main_kinds & {SITE_KIND, DEVICE_KIND} == set(), (
        f"The throwaway schema reached 'main' ({sorted(main_kinds & {SITE_KIND, DEVICE_KIND})}), so a "
        "concurrent run would share this run's namespace."
    )
    assert scope.client.count(kind=SITE_KIND, branch=scope.branch) == 1, (
        "The site this run created must be visible on its own branch, or the test above proves nothing."
    )


def test_a_rename_of_the_destination_key_itself_lands_on_the_right_object(
    keyed_write_scope: KeyedWriteScope,
) -> None:
    """The case the recorded-id design exists for, end to end against the destination.

    `TestUnkeyedRenamable` is matched by the sync on `serial` and by the destination on `name`,
    which is the mismatch the ratified decision was chosen to cover. Renaming `name` therefore
    changes the destination's own human-friendly ID: a create-shaped convergent upsert would
    match nothing and add a second object, because the value it keys on is the value being
    changed. Only the recorded id lands it on the object that already exists.

    Asserted as a count **and** an identity: an unkeyed write would show here as two objects,
    one under each name.
    """
    scope = keyed_write_scope
    before = scope.client.count(kind=RENAMABLE_KIND, branch=scope.branch)
    seeded = next(
        node
        for node in scope.client.filters(kind=RENAMABLE_KIND, branch=scope.branch, populate_store=False)
        if node.serial.value == scope.renamable_serial
    )
    renamed = f"renamed-{scope.renamable_name}"

    written = scope.adapter.apply_planned_operation(
        operation=_renamable_update(scope.renamable_serial, destination_id=seeded.id, renamed=renamed),
        peers=scope.adapter.new_peer_resolver(),
    )

    assert written == seeded.id, "The rename must write the object its recorded id names."
    assert scope.client.count(kind=RENAMABLE_KIND, branch=scope.branch) == before, (
        "Renaming the destination's human-friendly ID must not create a second object."
    )
    reread = scope.client.get(kind=RENAMABLE_KIND, id=seeded.id, branch=scope.branch)
    assert reread.name.value == renamed, "The rename did not reach the object the id named."
    assert reread.serial.value == scope.renamable_serial, "The sync identity is unchanged by the rename."
