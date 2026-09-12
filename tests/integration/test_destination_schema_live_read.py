"""Live-read exercise for the bundled Infrahub destination-schema accessor.

The unit tests inject snapshots through the capability seam; this is the one opt-in
exercise against a real server, proving the live accessor returns the snapshot shape the
schema checks judge. Skipped automatically when ``INFRAHUB_ADDRESS`` +
``INFRAHUB_API_TOKEN`` are not set. Run locally with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    pytest tests/integration/test_destination_schema_live_read.py -m integration
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration import parse_configuration_package
from infrahub_sync.runtime_schema import CARDINALITIES, normalize_destination_schema

INFRAHUB_ADDRESS = os.environ.get("INFRAHUB_ADDRESS")
INFRAHUB_API_TOKEN = os.environ.get("INFRAHUB_API_TOKEN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN),
        reason="INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN are required for live schema reads",
    ),
]


def _live_package_data() -> dict[str, Any]:
    return {
        "format_version": 1,
        "configuration": {
            "name": "live-schema-read",
            "source": {"name": "netbox", "settings": {"url": "https://demo.netbox.dev"}},
            "destination": {
                "name": "infrahub",
                "settings": {"url": INFRAHUB_ADDRESS, "token": {"$credential": "infrahub-token"}},
            },
        },
        "credentials": {"infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"}},
    }


def test_the_live_accessor_returns_a_judgeable_snapshot() -> None:
    """The live read delivers every property the consumed-semantics projection reads.

    `human_friendly_id` and `uniqueness_constraints` are part of that projection — a
    change to either invalidates a saved plan — so a snapshot carrying only attributes and
    relationships would leave the plan guard comparing less than it claims to.
    """
    package = parse_configuration_package(_live_package_data())

    snapshot = capabilities_module._read_infrahub_destination_schema(package, "main")

    assert snapshot
    for entry in snapshot.values():
        assert set(entry) == {"human_friendly_id", "uniqueness_constraints", "attributes", "relationships"}
        assert all(isinstance(component, str) for component in entry["human_friendly_id"])
        for constraint in entry["uniqueness_constraints"]:
            assert all(isinstance(component, str) for component in constraint)
        for attribute in entry["attributes"].values():
            assert set(attribute) == {"kind", "optional", "default_value", "unique"}
            assert isinstance(attribute["kind"], str)
            assert isinstance(attribute["optional"], bool)
            assert isinstance(attribute["unique"], bool)
        for relationship in entry["relationships"].values():
            assert set(relationship) == {"peer", "cardinality", "optional", "kind"}
            assert isinstance(relationship["peer"], str)
            assert relationship["cardinality"] in CARDINALITIES
            assert isinstance(relationship["optional"], bool)
            assert isinstance(relationship["kind"], str)


def test_the_live_snapshot_normalizes_into_the_closed_domain() -> None:
    """The property the guard depends on: a live read is consumable without coercion."""
    package = parse_configuration_package(_live_package_data())

    snapshot = capabilities_module._read_infrahub_destination_schema(package, "main")
    normalized = normalize_destination_schema(snapshot)

    assert set(normalized.kinds) == set(snapshot)
