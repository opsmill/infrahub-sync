"""Generate and exercise the shipped Webex project entirely against fixtures."""

from __future__ import annotations

# ruff: noqa: ANN401 - generated model/SDK doubles have dynamic attributes
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
import requests
import yaml
from diffsync import Adapter
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
        self, project: SyncConfig, module: ModuleType, records: dict[str, list[dict]] | None = None
    ) -> None:
        # Bypass server initialization while exercising real destination behavior.
        Adapter.__init__(self)  # pylint: disable=non-parent-init-called
        self.config = project
        self.schema = {}
        self.schema.update(schema())
        self.source_node = self.owner_node = None
        type(self).top_level = project.compute_order()
        self.nodes: dict[str, list[Any]] = {kind: [] for kind in self.top_level}
        self.index: dict[tuple[str, str], Any] = {}
        self.writes: list[tuple[str, dict]] = []
        for kind in self.top_level:
            setattr(self, kind, getattr(module, kind))
        for kind, items in (records or {}).items():
            for item in items:
                self._node(kind, item)
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
        for relationship in self.schema[kind].relationships:
            setattr(node, relationship.name, SimpleNamespace(id=data.get(relationship.name)))
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


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBEX_TOKEN", "invented-access-value")


def source(project: SyncConfig, module: ModuleType, devices: list[dict] | None = None) -> WebexAdapter:
    """Load invented inventory using the shipped project and generated models."""
    adapter = WebexAdapter("source", project.source, project)
    type(adapter).top_level = project.compute_order()
    for kind in adapter.top_level:
        setattr(adapter, kind, getattr(module, kind))
    pages = {
        "/v1/locations": [fixture("locations-page-1.json"), fixture("locations-page-2.json")],
        "/v1/workspaces": [fixture("workspaces.json")],
        "/v1/devices": [{"items": fixture("devices.json")["items"] if devices is None else devices}],
    }
    adapter.client.pages: Any = Mock(side_effect=lambda path, _params: pages[path])
    adapter.load()
    return adapter


@pytest.mark.parametrize("devices_only", [True, False])
def test_25_devices_create_in_empty_destination_and_converge(tmp_path: Path, *, devices_only: bool) -> None:
    project = config("webex_workspaces_to_infrahub")
    if devices_only:
        project.order = ["WebexDevice"]
        project.schema_mapping = [item for item in project.schema_mapping if item.name == "WebexDevice"]
        project.schema_mapping[0].fields = [
            field for field in project.schema_mapping[0].fields if field.name != "workspace"
        ]
        assert project.source.settings is not None
        project.source.settings["resources"] = {"WebexDevice": {"page_size": 100}}
    src = source(project, models(tmp_path, project, "source"))
    module = models(tmp_path, project, "destination")
    dest = Destination(project, module)
    dest.load()
    diff = dest.diff_from(src)
    assert len(diff.dict()["WebexDevice"]) == 25
    assert diff.summary()["create"] == (25 if devices_only else 25 + 4 + 3)
    assert diff.summary()["update"] == diff.summary()["delete"] == 0
    dest.sync_from(src, diff=diff)
    assert len(dest.get_all("WebexDevice")) == len(dest.nodes["WebexDevice"]) == 25
    created = {data["webex_id"]: data for kind, data in dest.writes if kind == "WebexDevice"}
    for record in fixture("devices.json")["items"]:
        assert created[record["id"]]["name"] == record["displayName"]
        assert created[record["id"]]["serial_number"] == record["serial"]
        assert created[record["id"]]["model"] == record["product"]
        assert created[record["id"]]["software"] == record["software"]
        assert created[record["id"]]["ip_address"] == record["ip"]
        assert created[record["id"]]["connection_status"] == record["connectionStatus"]
    assert not dest.diff_from(src).has_diffs()
    # Reload from persisted SDK fixture nodes rather than relying only on DiffSync's cache.
    persisted = {}
    for kind, nodes in dest.nodes.items():
        persisted[kind] = [
            {
                "id": node.id,
                **{attr: getattr(node, attr).value for attr in dest.schema[kind].attribute_names},
                **{rel: getattr(node, rel).id for rel in dest.schema[kind].relationship_names},
            }
            for node in nodes
        ]
    reloaded = Destination(project, module, persisted)
    reloaded.load()
    assert not reloaded.diff_from(src).has_diffs()


def test_devices_reference_workspaces_created_after_locations(tmp_path: Path) -> None:
    project = config("webex_workspaces_to_infrahub")
    src = source(project, models(tmp_path, project, "source"))
    dest = Destination(project, models(tmp_path, project, "destination"))
    dest.load()
    dest.sync_from(src)
    writes = [(kind, data) for kind, data in dest.writes if "webex_id" in data]
    kinds = [kind for kind, _ in writes]
    assert max(i for i, kind in enumerate(kinds) if kind == "WebexLocation") < min(
        i for i, kind in enumerate(kinds) if kind == "WebexWorkspace"
    )
    assert max(i for i, kind in enumerate(kinds) if kind == "WebexWorkspace") < min(
        i for i, kind in enumerate(kinds) if kind == "WebexDevice"
    )
    workspaces = {node.webex_id.value: node for node in dest.nodes["WebexWorkspace"]}
    locations = {node.webex_id.value: node for node in dest.nodes["WebexLocation"]}
    for record in fixture("devices.json")["items"]:
        device = src.get("WebexDevice", record["id"])
        assert device.get_attrs()["workspace"] == record["workspaceId"]
        payload = next(data for kind, data in writes if kind == "WebexDevice" and data["webex_id"] == record["id"])
        assert payload["workspace"] == workspaces[record["workspaceId"]].id
    for node in workspaces.values():
        assert node.location.id in {location.id for location in locations.values()}


def test_removing_one_device_marks_only_its_destination_node_for_deletion(tmp_path: Path) -> None:
    """Prove the one-device delete diff; persisted Infrahub deletion is a core limitation."""
    project = config("webex_workspaces_to_infrahub")
    source_module = models(tmp_path, project, "source")
    src = source(project, source_module)
    dest = Destination(project, models(tmp_path, project, "destination"))
    dest.load()
    dest.sync_from(src)
    before = {kind: {obj.get_unique_id() for obj in dest.get_all(kind)} for kind in dest.top_level}
    unchanged = dest.diff_from(src)
    assert not unchanged.has_diffs()
    dest.sync_from(src, diff=unchanged)
    remaining = [record for record in fixture("devices.json")["items"] if record["id"] != "device-07"]
    src = source(project, source_module, remaining)
    diff = dest.diff_from(src)
    assert diff.summary()["delete"] == 1
    assert diff.summary()["create"] == diff.summary()["update"] == 0
    assert set(diff.dict()["WebexDevice"]) == {"device-07"}
    dest.sync_from(src, diff=diff)
    after = {kind: {obj.get_unique_id() for obj in dest.get_all(kind)} for kind in dest.top_level}
    assert after["WebexDevice"] == before["WebexDevice"] - {"device-07"}
    assert after["WebexWorkspace"] == before["WebexWorkspace"]
    assert after["WebexLocation"] == before["WebexLocation"]
    assert not dest.diff_from(src).has_diffs()
    assert len(dest.nodes["WebexDevice"]) == 25
    assert any(node.webex_id.value == "device-07" for node in dest.nodes["WebexDevice"])


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


def test_two_projects_isolate_two_organizations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    organizations = fixture("organizations.json")
    requested = []

    def read(_method: str, url: str, **kwargs: Any) -> requests.Response:
        org_id = kwargs["params"]["orgId"]
        path = urlsplit(url).path
        requested.append((org_id, path))
        result = requests.Response()
        result.status_code = 200
        result._content = json.dumps(organizations[org_id][path]).encode()
        return result

    monkeypatch.setattr(requests, "request", read)
    for org_id in organizations:
        project = config("webex_workspaces_to_infrahub")
        project.name = org_id
        monkeypatch.setenv("WEBEX_FIXTURE_ORG", org_id)
        project.source.settings = {**(project.source.settings or {}), "org_id": {"env": "WEBEX_FIXTURE_ORG"}}
        src = WebexAdapter("source", project.source, project)
        type(src).top_level = project.compute_order()
        module = models(tmp_path, project, "source")
        for kind in src.top_level:
            setattr(src, kind, getattr(module, kind))
        src.load()
        dest = Destination(project, models(tmp_path, project, "destination"))
        dest.load()
        diff = dest.diff_from(src)
        assert diff.summary()["create"] == 3
        for kind, suffix in [("WebexLocation", "location"), ("WebexWorkspace", "workspace"), ("WebexDevice", "device")]:
            assert set(diff.dict()[kind]) == {f"{org_id}-{suffix}"}
        assert src.get("WebexWorkspace", f"{org_id}-workspace").get_attrs()["location"] == f"{org_id}-location"
        assert src.get("WebexDevice", f"{org_id}-device").get_attrs()["workspace"] == f"{org_id}-workspace"
    assert requested == [
        (org, endpoint) for org in organizations for endpoint in ["/v1/locations", "/v1/workspaces", "/v1/devices"]
    ]
