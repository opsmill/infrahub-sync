"""An unkeyed planned operation is refused before it touches a live destination.

The offline harnesses prove the gate reads the SDK's rendered mutation and raises. What they
cannot prove is that nothing reached the server: a fixture holds no destination state, so
"zero mutation calls" is asserted against a recording transport rather than against Infrahub.
This module closes that gap on the smallest possible fixture — one throwaway schema, one
planned create, one count read back.

Same posture as its siblings: a throwaway schema is loaded, everything it creates is deleted
afterwards, and the module skips itself without a configured destination. Only the destination
is needed; the operation is a hand-built plan record driven straight through
`InfrahubAdapter.apply_planned_operation`, so no source adapter is involved. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    uv run pytest -m integration tests/integration/test_infrahub_unkeyed_refusal_integration.py
"""

from __future__ import annotations

import os
import time
import uuid
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

DESTINATION_BRANCH = "main"
SITE_KIND = "TestUnkeyedSite"
DEVICE_KIND = "TestUnkeyedDevice"

# `TestUnkeyedDevice`'s human-friendly ID crosses the `site` relationship, which is exactly the
# shape the SDK cannot render a key for: a peer supplied as a resolved node id renders as
# `{"id": ...}` with no `__typename`, so `get_human_friendly_id()` resolves to None. The site is
# all-direct so the peer it references is itself writable and the refusal under test is the
# device's alone.
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
    declares are queryable, and a create issued in that window fails as a missing schema rather
    than as the refusal under test.
    """
    deadline = time.monotonic() + timeout
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
            msg = f"Destination did not serve {sorted(missing)} within {timeout:.0f}s of a successful schema load."
            raise AssertionError(msg)
        time.sleep(1.0)


def _make_client(address: str, token: str) -> Any:  # noqa: ANN401 — the SDK client is dynamically typed
    """A sync Infrahub client, imported lazily so unit-only runs need no SDK extras."""
    from infrahub_sdk import Config, InfrahubClientSync

    return InfrahubClientSync(config=Config(address=address, api_token=token))


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


@pytest.fixture
def live_unkeyed_fixture() -> Iterator[tuple[Any, InfrahubAdapter, str]]:
    """Throwaway schema plus one site at the destination; torn down afterwards.

    Yields `(client, adapter, site_name)`. No device is created: whether one can be is the
    question under test.
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
    _await_schema_kinds(address, token, (SITE_KIND, DEVICE_KIND))

    client = _make_client(address, token)
    site_name = f"unkeyed-site-{suffix}"
    site = client.create(kind=SITE_KIND, branch=DESTINATION_BRANCH, data={"name": site_name})
    site.save()
    try:
        adapter = InfrahubAdapter.__new__(InfrahubAdapter)
        adapter.client = client
        adapter.schema = client.schema.all(branch=DESTINATION_BRANCH)
        adapter.source_node = None
        adapter.owner_node = None
        yield client, adapter, site_name
    finally:
        for device in client.filters(kind=DEVICE_KIND, branch=DESTINATION_BRANCH, populate_store=False):
            device.delete()
        site.delete()


def test_an_unkeyed_planned_operation_is_refused_without_touching_the_destination(
    live_unkeyed_fixture: tuple[Any, InfrahubAdapter, str],
) -> None:
    """The refusal is proven against a live server, not against a recording transport.

    The count is read back from the destination on both sides of the refusal, so "no mutation
    was attempted" is a statement about Infrahub's state rather than about what the client
    chose to send. A second apply is included because the failure this gate exists to prevent
    is a *duplicate* on re-apply: an unkeyed write that silently succeeded would show here as
    a count that climbed.
    """
    client, adapter, site_name = live_unkeyed_fixture
    before = client.count(kind=DEVICE_KIND, branch=DESTINATION_BRANCH)

    for attempt in range(2):
        with pytest.raises(UnkeyedWriteRefusedError) as refusal:
            adapter.apply_planned_operation(
                operation=_device_operation("unkeyed-device-a", site_name),
                peers=adapter.new_peer_resolver(),
            )
        assert DEVICE_KIND in str(refusal.value), f"The refusal must name the destination kind: {refusal.value}"
        assert client.count(kind=DEVICE_KIND, branch=DESTINATION_BRANCH) == before, (
            f"Apply attempt {attempt + 1} changed the destination count for {DEVICE_KIND}, so the "
            "operation mutated the destination before the gate refused it."
        )

    assert client.filters(kind=DEVICE_KIND, branch=DESTINATION_BRANCH, populate_store=False) == [], (
        f"A refused operation left an object of kind {DEVICE_KIND} at the destination."
    )
