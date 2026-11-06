"""Generate and exercise the shipped Webex project entirely against fixtures."""

from __future__ import annotations

# ruff: noqa: ANN401 - generated model/SDK doubles have dynamic attributes
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import yaml
from diffsync import Adapter
from diffsync.enum import DiffSyncFlags
from infrahub_sdk.schema.main import NodeSchemaAPI
from typer.testing import CliRunner

from infrahub_sync import SyncConfig, cli
from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.adapters.webex import WebexAdapter
from infrahub_sync.cli import app
from infrahub_sync.generator import render_template

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/webex"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def config(project: str) -> SyncConfig:
    return SyncConfig.model_validate(yaml.safe_load((ROOT / "examples" / project / "config.yml").read_text()))


def schema() -> dict[str, NodeSchemaAPI]:
    return {node.kind: node for node in (NodeSchemaAPI(**item) for item in fixture("schema.json")["nodes"])}


def models(tmp_path: Path, project: SyncConfig, side: str) -> ModuleType:
    output = tmp_path / f"{side}_models.py"
    render_template(
        Path("diffsync_models.j2"),
        tmp_path,
        Path(output.name),
        {
            "schema": schema(),
            "adapter": getattr(project, side),
            "config": project,
        },
    )
    spec = importlib.util.spec_from_file_location(f"webex_{tmp_path.name}_{side}", output)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Destination(InfrahubAdapter):
    """Use real destination loading and write conversion with an in-memory SDK stand-in."""

    def __init__(  # pylint: disable=super-init-not-called
        self, project: SyncConfig, module: ModuleType, sites: list[dict], rooms: list[dict] | None = None
    ) -> None:
        # Bypass server initialization while exercising real destination behavior.
        Adapter.__init__(self)  # pylint: disable=non-parent-init-called
        self.config = project
        self.schema = {}
        self.schema.update(schema())
        self.source_node = self.owner_node = None
        type(self).top_level = project.compute_order()
        self.nodes: dict[str, list[Any]] = {"LocationSite": [], "LocationMeetingRoom": []}
        self.index: dict[tuple[str, str], Any] = {}
        self.writes: list[tuple[str, dict]] = []
        for kind in self.top_level:
            setattr(self, kind, getattr(module, kind))
        for item in sites:
            self._node("LocationSite", item)
        for item in rooms or []:
            self._node("LocationMeetingRoom", item)
        self.client = SimpleNamespace(
            all=lambda *, kind, **_kwargs: self.nodes[kind],
            get=lambda *, id, kind, **_kwargs: next(node for node in self.nodes[kind] if node.id == id),  # noqa: A006 - SDK signature
            store=SimpleNamespace(get=self._get, set=self._set),
            schema=SimpleNamespace(
                get=lambda *, kind: self.schema[kind], generate_payload_create=lambda **kwargs: kwargs["data"]
            ),
            create=self._create,
        )

    def _node(self, kind: str, data: dict) -> Any:
        node = SimpleNamespace(id=data["id"], get_kind=lambda: kind)
        for attr in self.schema[kind].attributes:
            setattr(node, attr.name, SimpleNamespace(value=data.get(attr.name)))
        if kind == "LocationMeetingRoom":
            node.parent = SimpleNamespace(id=data.get("parent"))
        node.save = lambda **_kwargs: self.writes.append(
            (kind, {key: getattr(node, key).value for key in self.schema[kind].attribute_names})
        )
        self.nodes[kind].append(node)
        self.index[kind, node.id] = node
        return node

    def _get(self, key: str, kind: str, **_kwargs: Any) -> Any:
        return self.index.get((kind, key))

    def _set(self, *, key: str, node: Any) -> None:
        self.index[node.get_kind(), key] = node

    def _create(self, *, kind: str, data: dict) -> Any:
        self.writes.append((kind, data))
        return self._node(kind, {"id": f"created-{len(self.nodes[kind])}", **data})


@pytest.fixture(name="source")
def _source_fixture(monkeypatch: pytest.MonkeyPatch) -> Callable[[SyncConfig, ModuleType], WebexAdapter]:
    monkeypatch.setenv("WEBEX_TOKEN", "invented-access-value")

    def build(project: SyncConfig, module: ModuleType) -> WebexAdapter:
        adapter = WebexAdapter("source", project.source, project)
        type(adapter).top_level = project.compute_order()
        for kind in adapter.top_level:
            setattr(adapter, kind, getattr(module, kind))
        pages = {
            "/v1/locations": [fixture("locations-page-1.json"), fixture("locations-page-2.json")],
            "/v1/workspaces": [fixture("workspaces.json")],
        }
        adapter.client.pages: Any = Mock(side_effect=lambda path, _params: pages[path])
        adapter.load()
        return adapter

    return build


def flags(project: SyncConfig) -> DiffSyncFlags:
    result = DiffSyncFlags.NONE
    for flag in project.diffsync_flags or []:
        assert isinstance(flag, DiffSyncFlags)
        result |= flag
    return result


def test_workspaces_create_rooms_and_keep_sites_and_old_names(tmp_path: Path, source) -> None:
    project = config("webex_workspaces_to_infrahub")
    src = source(project, models(tmp_path, project, "source"))
    dest = Destination(
        project,
        models(tmp_path, project, "destination"),
        [
            {"id": "site-1", "webex_location_id": "location-1"},
            {"id": "site-2", "webex_location_id": "location-2"},
        ],
        [{"id": "room-old", "name": "DE/Berlin/HQ1/OLD.01", "room_number": "OLD.01", "parent": "site-1"}],
    )
    dest.load()
    assert len(src.get_all("LocationSite")) == 3
    assert src.get("LocationMeetingRoom", "DE/Berlin/HQ1/A1.01").parent == "location-1"
    diff = dest.diff_from(src, flags=flags(project))
    assert diff.summary()["create"] == 2
    assert diff.summary()["update"] == diff.summary()["delete"] == 0
    dest.sync_from(src, flags=flags(project), diff=diff)
    created = [data for kind, data in dest.writes if "parent" in data]
    assert {data["parent"] for data in created} == {"site-1", "site-2"}
    assert {data["room_number"] for data in created} == {"A1.01", "B2.02"}
    assert all(kind == "LocationMeetingRoom" for kind, _data in dest.writes)
    assert len(dest.nodes["LocationSite"]) == 2
    assert len(dest.nodes["LocationMeetingRoom"]) == 3
    assert not dest.diff_from(src, flags=flags(project)).has_diffs()


def test_cli_generate_uses_fixture_destination_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = "webex_workspaces_to_infrahub"
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "invented-infrahub-value")
    monkeypatch.setattr(
        cli, "InfrahubClientSync", lambda **_kwargs: SimpleNamespace(schema=SimpleNamespace(all=schema))
    )
    project_file = tmp_path / "config.yml"
    project_file.write_text((ROOT / "examples" / project_dir / "config.yml").read_text())
    result = CliRunner().invoke(app, ["generate", "--config-file", str(project_file)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "webex/sync_models.py").is_file()
    assert (tmp_path / "infrahub/sync_adapter.py").is_file()
