"""Put Sync's schema extension and configurations into a live Infrahub, for the live tiers.

Configurations are created and changed in Infrahub, not through the Sync API, so a live
test that needs one writes the `SyncConfiguration` node itself, the way an operator would.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from infrahub_sync.platform.records import CONFIGURATION_KIND

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sdk import InfrahubClientSync

SYNC_SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "sync.yml"


class SyncSchemaLoadError(RuntimeError):
    """Infrahub refused the Sync schema extension."""


def load_sync_schema(client: InfrahubClientSync, branch: str | None = None) -> None:
    """Load `schema/sync.yml` and wait for Infrahub to converge on it; a no-op once loaded."""
    response = client.schema.load(
        schemas=[yaml.safe_load(SYNC_SCHEMA.read_text(encoding="utf-8"))], branch=branch, wait_until_converged=True
    )
    if response.errors:
        msg = f"Infrahub refused schema/sync.yml: {response.errors}"
        raise SyncSchemaLoadError(msg)


def put_configuration(
    client: InfrahubClientSync,
    name: str,
    content: Mapping[str, Any] | str,
    *,
    branch: str | None = None,
) -> str:
    """Create or replace a configuration's document in Infrahub, returning the node id.

    `content` is the declared package, as a mapping or as the YAML or JSON text an
    operator would paste. The default branch is used unless one is named.
    """
    document = content if isinstance(content, str) else yaml.safe_dump(dict(content), sort_keys=False)
    found = client.filters(kind=CONFIGURATION_KIND, branch=branch, name__value=name)
    if found:
        node = found[0]
        node.document.value = document  # ty: ignore[invalid-assignment]  # the SDK types this node union-wide
    else:
        node = client.create(kind=CONFIGURATION_KIND, branch=branch, data={"name": name, "document": document})
    node.save()
    return str(node.id)
