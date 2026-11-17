"""List the nodes that a fresh Infrahub holds before any import writes to it.

Research R2 of spec 019 needs one fact that only a live Infrahub can give: which nodes of a
mappable kind Infrahub creates itself, and which mark tells them apart from a node that an
operator or an import created. This probe creates a branch, reads every node of every node kind
whose name does not start with `Core` or `Internal`, with the node metadata (creator and
creation time) and, for a kind whose schema declares a read-only `default` attribute (the
`BuiltinIPNamespace` generic), that attribute. It then creates one `BuiltinTag` through the API
token, as a control whose creator differs from that of the nodes Infrahub creates itself, and
deletes the branch afterwards.

The probe writes only to its own branch. Set `INFRAHUB_BUILTIN_NODES_OUTPUT` to a file path to
save the result as JSON; the committed result is
`tests/data/infrahub_builtin_nodes/infrahub-<version>.json`. The JSON holds node kinds, ids,
display labels, node metadata and the `default` value, never a token. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    INFRAHUB_BUILTIN_NODES_OUTPUT=tests/data/infrahub_builtin_nodes/infrahub-1.11.9.json \\
    uv run pytest -m integration tests/integration/test_infrahub_builtin_nodes_integration.py
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.schema import NodeSchemaAPI

if TYPE_CHECKING:
    from infrahub_sdk import InfrahubClientSync
    from infrahub_sdk.node import InfrahubNodeSync
    from infrahub_sdk.node.property import NodeProperty

pytestmark = pytest.mark.integration

EXCLUDED_PREFIXES = ("Core", "Internal")
DEFAULT_ATTRIBUTE = "default"
OUTPUT_VARIABLE = "INFRAHUB_BUILTIN_NODES_OUTPUT"
CONTROL_KIND = "BuiltinTag"


def _env_or_skip() -> tuple[str, str]:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        pytest.skip("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set")
    return address, token


def _make_client(address: str, token: str) -> InfrahubClientSync:
    from infrahub_sdk import Config, InfrahubClientSync

    return InfrahubClientSync(config=Config(address=address, api_token=token))


def _infrahub_version(client: InfrahubClientSync) -> str:
    response = client.execute_graphql(query="query { InfrahubInfo { version } }")
    return str(response["InfrahubInfo"]["version"])


def _account(reference: NodeProperty | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    return {"id": reference.id, "kind": reference.typename, "display_label": reference.display_label}


def _has_read_only_default(schema: NodeSchemaAPI) -> bool:
    return any(attribute.name == DEFAULT_ATTRIBUTE and attribute.read_only for attribute in schema.attributes)


def _node_record(node: InfrahubNodeSync, *, read_default: bool) -> dict[str, Any]:
    metadata = node.get_node_metadata()
    record: dict[str, Any] = {
        "id": node.id,
        "display_label": node.display_label,
        "hfid": node.hfid,
        "created_at": metadata.created_at if metadata else None,
        "created_by": _account(metadata.created_by) if metadata else None,
        "updated_at": metadata.updated_at if metadata else None,
        "updated_by": _account(metadata.updated_by) if metadata else None,
    }
    if read_default:
        record[DEFAULT_ATTRIBUTE] = getattr(node, DEFAULT_ATTRIBUTE).value
    return record


def _probe(client: InfrahubClientSync, branch: str) -> dict[str, list[dict[str, Any]]]:
    """Return the nodes of every probed kind on `branch`, by kind, for kinds that hold any."""
    schemas = client.schema.all(branch=branch, refresh=True)
    nodes_by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind in sorted(schemas):
        schema = schemas[kind]
        if not isinstance(schema, NodeSchemaAPI) or kind.startswith(EXCLUDED_PREFIXES):
            continue
        read_default = _has_read_only_default(schema)
        nodes = client.all(kind=kind, branch=branch, include_metadata=True, populate_store=False)
        if nodes:
            nodes_by_kind[kind] = [_node_record(node, read_default=read_default) for node in nodes]
    return nodes_by_kind


def test_list_nodes_that_infrahub_creates_itself() -> None:
    """Read every non-`Core`, non-`Internal` node of a new branch and record how to recognize it."""
    address, token = _env_or_skip()
    client = _make_client(address, token)
    branch = f"builtin-probe-{uuid.uuid4().hex[:8]}"
    client.branch.create(branch_name=branch, sync_with_git=False)
    try:
        version = _infrahub_version(client)
        probed_kinds = sorted(
            kind
            for kind, schema in client.schema.all(branch=branch, refresh=True).items()
            if isinstance(schema, NodeSchemaAPI) and not kind.startswith(EXCLUDED_PREFIXES)
        )
        nodes_by_kind = _probe(client, branch)
        # A control: a node that the API token creates, to show that its metadata differs
        # from that of the nodes Infrahub creates itself. It is created after the listing,
        # on the probe's branch only.
        control = client.create(kind=CONTROL_KIND, branch=branch, data={"name": f"probe-control-{branch}"})
        control.save()
        control_node = client.get(kind=CONTROL_KIND, id=control.id, branch=branch, include_metadata=True)
        control_record = {"kind": CONTROL_KIND, **_node_record(control_node, read_default=False)}
    finally:
        client.branch.delete(branch)

    result = {
        "infrahub_version": version,
        "excluded_kind_prefixes": list(EXCLUDED_PREFIXES),
        "probed_kinds": probed_kinds,
        "nodes_by_kind": nodes_by_kind,
        "control_node_created_through_api": control_record,
    }
    output = os.environ.get(OUTPUT_VARIABLE)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert probed_kinds, "The destination served no node kind outside `Core` and `Internal`."
    for kind, records in nodes_by_kind.items():
        for record in records:
            assert record["id"], f"A {kind} node came back without an id."
    assert control_record["created_by"], "The node the API token created came back without a creator."
