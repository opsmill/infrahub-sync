"""Configurations, versions, approvals, and run mirrors in Infrahub."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.platform.records import (
    ConfigurationDocumentError,
    InfrahubConfigurations,
    RunMirror,
    parse_document,
)
from infrahub_sync.product_store.configs import ConfigsValidationError
from infrahub_sync.product_store.models import ProductRun

if TYPE_CHECKING:
    from collections.abc import Iterator

DECLARED: dict[str, Any] = {
    "format_version": 1,
    "configuration": {
        "name": "netbox-demo",
        "source": {"name": "netbox", "settings": {"url": "https://netbox.example", "token": {"$credential": "token"}}},
        "destination": {
            "name": "infrahub",
            "settings": {"url": "https://infrahub.example", "token": {"$credential": "token"}},
        },
        "order": [],
        "schema_mapping": [],
        "diffsync_flags": [],
        "incremental": None,
    },
    "credentials": {"token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_TOKEN"}},
}


class _Attribute:
    def __init__(self, value: object) -> None:
        self.value = value


class _Node:
    _ids = count(1)

    def __init__(self, store: _Store, kind: str, data: dict[str, Any]) -> None:
        self.id = f"node-{next(self._ids)}"
        self.kind = kind
        self._store = store
        self.saved = 0
        for name, value in data.items():
            setattr(self, name, _Attribute(value))

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - attributes are set from the node's data
        # An SDK node carries every attribute of its kind, set or not.
        if name.startswith("_"):
            raise AttributeError(name)
        attribute = _Attribute(None)
        setattr(self, name, attribute)
        return attribute

    def delete(self) -> None:
        self._store.nodes.remove(self)

    def save(self) -> None:
        if self._store.fail_saves:
            msg = "infrahub refused the write"
            raise RuntimeError(msg)
        self.saved += 1
        if self not in self._store.nodes:
            self._store.nodes.append(self)


class _Store:
    """An in-memory stand-in for the SDK client, filtering the way Infrahub does."""

    def __init__(self) -> None:
        self.nodes: list[_Node] = []
        self.branches: set[str | None] = set()
        self.fail_saves = False

    def _matches(self, node: _Node, filters: dict[str, Any]) -> bool:
        for key, wanted in filters.items():
            parts = key.split("__")
            if parts[0] == "configuration" and parts[1:] == ["name", "value"]:
                peer = next((n for n in self.nodes if n.id == node.configuration.value), None)
                if peer is None or peer.name.value != wanted:
                    return False
            elif getattr(node, parts[0]).value != wanted:
                return False
        return True

    def filters(self, kind: str, branch: str | None = None, **filters: Any) -> list[_Node]:  # noqa: ANN401
        self.branches.add(branch)
        return [node for node in self.nodes if node.kind == kind and self._matches(node, filters)]

    def create(self, kind: str, data: dict | None = None, branch: str | None = None, **_kwargs: Any) -> _Node:  # noqa: ANN401
        self.branches.add(branch)
        return _Node(self, kind, dict(data or {}))

    def configuration(self, name: str, document: str) -> _Node:
        node = _Node(self, "SyncConfiguration", {"name": name, "description": None, "document": document})
        node.save()
        return node


@pytest.fixture
def store() -> _Store:
    return _Store()


def _configurations(store: _Store, locks: list[str] | None = None) -> InfrahubConfigurations:
    @contextmanager
    def lock(config_id: str) -> Iterator[None]:
        if locks is not None:
            locks.append(config_id)
        yield

    return InfrahubConfigurations(client=store, branch="trunk", version_lock=lock)


def _document(**changes: Any) -> str:  # noqa: ANN401
    content = json.loads(json.dumps(DECLARED))
    content["configuration"].update(changes)
    return yaml.safe_dump(content)


def test_a_first_run_records_version_one_with_the_package_checksum(store: _Store) -> None:
    store.configuration("netbox-demo", _document())
    locks: list[str] = []

    version = _configurations(store, locks).current_version("netbox-demo").value

    assert version is not None
    assert version.registry_version == 1
    assert version.package_checksum == ConfigurationPackage.model_validate(DECLARED).checksum()
    assert version.declared_content == DECLARED
    assert locks == ["netbox-demo"], "the version was not recorded under the configuration's lock"
    assert store.branches == {"trunk"}, "a record was read or written off the default branch"


def test_unchanged_content_reuses_its_version(store: _Store) -> None:
    store.configuration("netbox-demo", _document())
    configurations = _configurations(store)
    first = configurations.current_version("netbox-demo").value

    again = configurations.current_version("netbox-demo").value

    assert again == first
    assert len(store.filters(kind="SyncConfigurationVersion")) == 1


def test_formatting_changes_alone_do_not_make_a_new_version(store: _Store) -> None:
    configuration = store.configuration("netbox-demo", _document())
    configurations = _configurations(store)
    configurations.current_version("netbox-demo")

    configuration.document.value = json.dumps(DECLARED, indent=4)

    version = configurations.current_version("netbox-demo").value
    assert version is not None
    assert version.registry_version == 1


def test_changed_content_records_the_next_number_and_keeps_the_earlier_one(store: _Store) -> None:
    configuration = store.configuration("netbox-demo", _document())
    configurations = _configurations(store)
    configurations.current_version("netbox-demo")

    configuration.document.value = _document(order=["InfraDevice"])
    second = configurations.current_version("netbox-demo").value
    first = configurations.lookup_configuration_version("netbox-demo", 1).value

    assert second is not None
    assert second.registry_version == 2
    assert first is not None
    assert first.declared_content == DECLARED
    assert [record.registry_version for record in configurations.list_configuration_versions("netbox-demo")] == [1, 2]


def test_content_never_run_gets_no_version(store: _Store) -> None:
    configuration = store.configuration("netbox-demo", _document())
    configurations = _configurations(store)
    configurations.current_version("netbox-demo")

    configuration.document.value = _document(order=["A"])
    configuration.document.value = _document(order=["B"])
    latest = configurations.current_version("netbox-demo").value

    assert latest is not None
    assert latest.registry_version == 2
    assert latest.declared_content["configuration"]["order"] == ["B"]


def test_invalid_content_is_refused_with_findings_and_gets_no_version(store: _Store) -> None:
    content = json.loads(json.dumps(DECLARED))
    content["configuration"]["source"]["settings"]["token"] = "a-literal-credential-value"  # noqa: S105 - the defect under test
    store.configuration("netbox-demo", yaml.safe_dump(content))

    with pytest.raises(ConfigsValidationError) as caught:
        _configurations(store).current_version("netbox-demo")

    assert caught.value.findings
    assert store.filters(kind="SyncConfigurationVersion") == []


def test_a_document_that_is_not_a_mapping_is_refused(store: _Store) -> None:
    store.configuration("netbox-demo", "- just\n- a list\n")

    with pytest.raises(ConfigurationDocumentError):
        _configurations(store).current_version("netbox-demo")


def test_an_unknown_configuration_is_not_found(store: _Store) -> None:
    configurations = _configurations(store)

    assert configurations.current_version("missing").reason == "configuration-not-found"
    assert configurations.lookup_configuration("missing").reason == "configuration-not-found"
    assert configurations.lookup_configuration_version("missing", 1).reason == "configuration-version-not-found"


def test_the_document_on_another_branch_is_read_for_validation(store: _Store) -> None:
    store.configuration("netbox-demo", _document())

    content = _configurations(store).declared_content("netbox-demo", branch="my-change")

    assert content == DECLARED
    assert "my-change" in store.branches


def test_configurations_are_listed_with_their_first_version_date(store: _Store) -> None:
    store.configuration("netbox-demo", _document())
    configurations = _configurations(store)
    version = configurations.current_version("netbox-demo").value

    listed = configurations.list_configurations()

    assert [summary.config_id for summary in listed] == ["netbox-demo"]
    assert version is not None
    assert listed[0].created_at == version.created_at


def _run(**changes: Any) -> ProductRun:  # noqa: ANN401
    values: dict[str, Any] = {
        "run_id": "run-0001",
        "operation": "plan",
        "configuration_reference": "netbox-demo@1",
        "config_id": "netbox-demo",
        "registry_version": 1,
        "package_checksum": "b" * 64,
        "actor": "alice",
        "started_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
        "phase": "accepted",
        **changes,
    }
    return ProductRun(**values)


def test_a_run_is_mirrored_and_linked_to_its_configuration_and_version(store: _Store) -> None:
    configuration = store.configuration("netbox-demo", _document())
    _configurations(store).current_version("netbox-demo")
    mirror = RunMirror(client=store, branch="trunk")

    mirror.mirror(_run(), {"target_branch": "main"})

    (node,) = store.filters(kind="SyncRun")
    assert node.run_id.value == "run-0001"
    assert node.requested_by.value == "alice"
    assert node.target_branch.value == "main"
    assert node.configuration.value == configuration.id
    assert node.version.value == store.filters(kind="SyncConfigurationVersion")[0].id


def test_a_later_state_updates_the_same_mirror(store: _Store) -> None:
    mirror = RunMirror(client=store, branch="trunk")
    mirror.mirror(_run())

    mirror.mirror(_run(phase="finished", outcome="succeeded"))

    (node,) = store.filters(kind="SyncRun")
    assert (node.phase.value, node.outcome.value) == ("finished", "succeeded")
    # The fake hands back live nodes, so the values alone would show without a save.
    assert node.saved == 2, "the update was never saved to Infrahub"


def test_a_failed_mirror_write_never_fails_the_run(store: _Store, caplog: pytest.LogCaptureFixture) -> None:
    store.fail_saves = True

    RunMirror(client=store, branch="trunk").mirror(_run())

    assert "was not mirrored" in caplog.text


def test_an_approval_names_the_approver_and_the_checksum(store: _Store) -> None:
    mirror = RunMirror(client=store, branch="trunk")
    mirror.mirror(_run(operation="apply"))
    run = store.filters(kind="SyncRun")[0]

    mirror.approve("run-0001", checksum="a" * 64, approved_by="bob", reason="reviewed")

    (approval,) = store.filters(kind="SyncApproval")
    assert approval.approved_by.value == "bob"
    assert approval.approved_checksum.value == "a" * 64
    assert approval.run.value == run.id


def test_a_configuration_no_run_has_used_is_undated_and_listed_last(store: _Store) -> None:
    store.configuration("unused", _document())
    store.configuration("netbox-demo", _document())
    configurations = _configurations(store)
    configurations.current_version("netbox-demo")

    unused = configurations.lookup_configuration("unused").value
    listed = configurations.list_configurations()

    assert unused is not None
    assert unused.created_at is None
    assert [summary.config_id for summary in listed] == ["netbox-demo", "unused"]


def test_each_apply_of_a_run_records_its_own_approval(store: _Store) -> None:
    mirror = RunMirror(client=store, branch="trunk")
    mirror.mirror(_run(operation="apply"))

    mirror.approve("run-0001", checksum="a" * 64, approved_by="bob", reason="first apply")
    mirror.approve("run-0001", checksum="a" * 64, approved_by="carol", reason="retried apply")

    assert [approval.approved_by.value for approval in store.filters(kind="SyncApproval")] == ["bob", "carol"]


def test_a_mirror_writes_the_runs_state_as_it_is_when_written(store: _Store) -> None:
    mirror = RunMirror(client=store, branch="trunk")

    mirror.mirror(_run(), current=lambda: _run(phase="finished", outcome="succeeded"))

    (node,) = store.filters(kind="SyncRun")
    assert (node.phase.value, node.outcome.value) == ("finished", "succeeded")


def test_a_document_whose_aliases_expand_past_the_limit_is_refused(store: _Store) -> None:
    levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"]
    levels += [f"a{n}: &a{n} [{', '.join([f'*a{n - 1}'] * 9)}]" for n in range(1, 9)]
    store.configuration("netbox-demo", "\n".join(levels) + "\n")

    with pytest.raises(ConfigurationDocumentError, match="through YAML aliases"):
        _configurations(store).current_version("netbox-demo")


def test_an_oversized_document_is_refused(store: _Store) -> None:
    store.configuration("netbox-demo", "x: " + "y" * 1_048_577 + "\n")

    with pytest.raises(ConfigurationDocumentError, match="larger than"):
        _configurations(store).current_version("netbox-demo")


def test_the_netbox_example_with_its_aliases_is_admitted() -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "netbox_to_infrahub" / "package.yml"

    assert parse_document(example.read_text(encoding="utf-8"))["configuration"]["name"] == "from-netbox"


def test_a_document_nested_too_deeply_is_refused() -> None:
    with pytest.raises(ConfigurationDocumentError, match="nested too deeply"):
        parse_document("[" * 100_000 + "]" * 100_000)
