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

from infrahub_sync.configuration import parse_configuration_package
from infrahub_sync.configuration.capabilities import _read_infrahub_destination_schema

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
    package = parse_configuration_package(_live_package_data())

    snapshot = _read_infrahub_destination_schema(package, "main")

    assert snapshot
    for entry in snapshot.values():
        assert set(entry) == {"attributes", "relationships"}
        assert all(isinstance(kind, str) for kind in entry["attributes"].values())
        for relationship in entry["relationships"].values():
            assert set(relationship) == {"peer", "cardinality"}
