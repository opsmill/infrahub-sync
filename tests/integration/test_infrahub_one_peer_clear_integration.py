"""Pin, live, that an Upsert carrying `<field>: null` removes a one-peer relationship.

Research R8 of spec 019 writes an emptied optional cardinality-one relationship inside the one
Upsert of the operation (ADR 0010, ADR 0012): the destination id goes into the create data
before `client.create`, which makes the SDK treat the node as existing, and the field is then
set to `None`. With that, the SDK renders `<field>: null` in the Upsert input. Whether the
server removes the edge for `<field>: null` is a fact about Infrahub that no offline test can
settle; this test settles it.

**If this test fails on the read-back, Infrahub 1.11.9 keeps the edge for `<field>: null`**,
and research R8 must be amended before the write path is changed.

Everything is written to a branch that the fixture creates and deletes, so nothing reaches
`main`. The module skips itself without a configured destination. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    uv run pytest -m integration tests/integration/test_infrahub_one_peer_clear_integration.py
"""

from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
import requests

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sdk import InfrahubClientSync
    from infrahub_sdk.node import RelatedNodeSync

pytestmark = pytest.mark.integration

TEAM_KIND = "TestClearTeam"
TAG_KIND = "TestClearTag"
LEAD_RELATIONSHIP = "lead"

_SCHEMA = {
    "version": "1.0",
    "nodes": [
        {
            "name": "ClearTag",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
        },
        {
            "name": "ClearTeam",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
            "relationships": [
                {
                    "name": LEAD_RELATIONSHIP,
                    "peer": TAG_KIND,
                    "cardinality": "one",
                    "kind": "Generic",
                    "optional": True,
                    "identifier": "testclearteam__lead",
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
    """Block until the destination serves every one of `kinds` on `branch`."""
    deadline = time.monotonic() + timeout
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
            msg = f"Destination did not serve {sorted(missing)} within {timeout:.0f}s of a successful schema load."
            raise AssertionError(msg)
        time.sleep(1.0)


def _make_client(address: str, token: str, branch: str | None = None) -> InfrahubClientSync:
    from infrahub_sdk import Config, InfrahubClientSync

    settings: dict[str, Any] = {"address": address, "api_token": token}
    if branch is not None:
        settings["default_branch"] = branch
    return InfrahubClientSync(config=Config(**settings))


def _destination_lead_id(client: InfrahubClientSync, team_id: str, branch: str) -> str | None:
    """The destination's current `lead` peer id, or None when the relationship is empty."""
    node = client.get(kind=TEAM_KIND, id=team_id, branch=branch, include=[LEAD_RELATIONSHIP])
    lead: RelatedNodeSync | None = getattr(node, LEAD_RELATIONSHIP)
    return lead.id if lead else None


@pytest.fixture
def live_clear_fixture() -> Iterator[tuple[InfrahubClientSync, str, str, str]]:
    """A branch holding the throwaway schema, one tag and one team whose `lead` is that tag.

    Yields `(client, team_id, team_name, branch)`. Deleting the branch is the teardown.
    """
    address, token = _env_or_skip()
    suffix = uuid.uuid4().hex[:8]
    branch = f"clear-{suffix}"
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
        tag = client.create(kind=TAG_KIND, branch=branch, data={"name": f"clear-tag-{suffix}"})
        tag.save()
        team_name = f"clear-team-{suffix}"
        team = client.create(kind=TEAM_KIND, branch=branch, data={"name": team_name, LEAD_RELATIONSHIP: tag.id})
        team.save()
        assert team.id is not None
        assert _destination_lead_id(client, team.id, branch) == tag.id, (
            "Precondition: `lead` must be set at the destination before clearing it can be measured."
        )
        yield client, team.id, team_name, branch
    finally:
        lifecycle_client.branch.delete(branch)


def test_upsert_with_null_one_peer_relationship_removes_the_edge(
    live_clear_fixture: tuple[InfrahubClientSync, str, str, str],
) -> None:
    """Build the Upsert as research R8 describes, check it renders `lead: null`, apply it, read back."""
    client, team_id, team_name, branch = live_clear_fixture

    node_schema = client.schema.get(kind=TEAM_KIND, branch=branch)
    create_data = client.schema.generate_payload_create(schema=node_schema, data={"name": team_name})
    # The destination id in the create data makes the SDK build the node as an existing one,
    # which is what lets it render a cleared optional cardinality-one relationship.
    create_data["id"] = team_id
    node = client.create(kind=TEAM_KIND, branch=branch, data=create_data)
    setattr(node, LEAD_RELATIONSHIP, None)

    rendered = node._generate_input_data(exclude_hfid=True)["data"]["data"]
    assert rendered == {"id": team_id, "name": {"value": team_name}, LEAD_RELATIONSHIP: None}, (
        f"The SDK did not render exactly the id, the name and `{LEAD_RELATIONSHIP}: null`: {rendered!r}"
    )

    node.save(allow_upsert=True)

    assert node.id == team_id, "The Upsert must converge on the existing team, not create another one."
    assert _destination_lead_id(client, team_id, branch) is None, (
        "The Upsert rendered `lead: null` but the destination kept the edge. Research R8 does not "
        "hold for this Infrahub version and must be amended."
    )
