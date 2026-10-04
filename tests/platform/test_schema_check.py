"""The startup check that the Sync schema extension is loaded."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from infrahub_sdk.exceptions import SchemaNotFoundError

from infrahub_sync.platform.schema_check import (
    REQUIRED_KINDS,
    SCHEMA_FILE,
    SyncSchemaMissingError,
    require_sync_schema,
    require_sync_schema_sync,
)

REPOSITORY = Path(__file__).resolve().parents[2]


def _shipped() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load((REPOSITORY / SCHEMA_FILE).read_text(encoding="utf-8"))
    return {f"{node['namespace']}{node['name']}": node for node in document["nodes"]}


def _as_loaded(node: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        attributes=[SimpleNamespace(name=attribute["name"]) for attribute in node.get("attributes", [])],
        relationships=[SimpleNamespace(name=relationship["name"]) for relationship in node.get("relationships", [])],
        inherit_from=node.get("inherit_from", []),
    )


class _Schema:
    def __init__(self, nodes: dict[str, SimpleNamespace]) -> None:
        self.nodes = nodes
        self.branches: list[str] = []

    def _get(self, kind: str, branch: str) -> SimpleNamespace:
        self.branches.append(branch)
        if kind not in self.nodes:
            raise SchemaNotFoundError(identifier=kind)
        return self.nodes[kind]

    async def get(self, *, kind: str, branch: str, refresh: bool) -> SimpleNamespace:
        assert refresh
        return self._get(kind, branch)


def _client(nodes: dict[str, SimpleNamespace]) -> Any:  # noqa: ANN401 - a stand-in SDK client
    return SimpleNamespace(schema=_Schema(nodes))


def test_every_required_kind_and_field_is_in_the_shipped_schema_file() -> None:
    """The check and the file the operator loads cannot drift apart."""
    shipped = _shipped()
    for required in REQUIRED_KINDS:
        assert required.kind in shipped, required.kind
        loaded = _as_loaded(shipped[required.kind])
        assert required.attributes <= {attribute.name for attribute in loaded.attributes}, required.kind
        assert required.relationships <= {relationship.name for relationship in loaded.relationships}, required.kind
        assert required.inherits <= set(loaded.inherit_from), required.kind


async def test_the_shipped_schema_passes_on_the_default_branch() -> None:
    client = _client({kind: _as_loaded(node) for kind, node in _shipped().items()})

    await require_sync_schema(client, "trunk")

    assert set(client.schema.branches) == {"trunk"}


async def test_a_missing_kind_field_and_generic_are_all_named() -> None:
    nodes = {kind: _as_loaded(node) for kind, node in _shipped().items()}
    del nodes["SyncApproval"]
    nodes["SyncRun"].attributes = [a for a in nodes["SyncRun"].attributes if a.name != "plan_checksum"]
    nodes["SyncConfiguration"].inherit_from = []

    with pytest.raises(SyncSchemaMissingError) as caught:
        await require_sync_schema(_client(nodes), "main")

    assert set(caught.value.missing) == {
        "SyncApproval",
        "SyncRun.plan_checksum",
        "SyncConfiguration inheriting CoreTaskTarget",
    }
    assert SCHEMA_FILE in str(caught.value)


def test_the_synchronous_check_refuses_an_empty_server() -> None:
    class _SyncSchema:
        def __init__(self) -> None:
            self.reads = _Schema({})

        def get(self, *, kind: str, branch: str, refresh: bool) -> SimpleNamespace:
            assert refresh
            return self.reads._get(kind, branch)

    client = cast("Any", SimpleNamespace(schema=_SyncSchema()))

    with pytest.raises(SyncSchemaMissingError) as caught:
        require_sync_schema_sync(client, "main")

    assert caught.value.missing == tuple(required.kind for required in REQUIRED_KINDS)
