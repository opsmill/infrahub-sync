"""Import lineage against a live Infrahub: who wrote each value, with which permissions.

The import runs with the token of an account used only by this import. Each test creates that
account with the permission set documented in research R-7 of the import-lineage
specification, for a branch other than the default branch:

- `view`, `create` and `update` (decision `allow_other`) on each kind the import writes;
- `view` on the peer kind of each relationship it writes, here the same tag kind.

The tests then drive the import's own destination calls with that token:

- the adapter's construction, which reads the server version and the schema;
- the lineage resolution, which reads the account behind the token;
- the reads a plan makes of each kind;
- a saved-plan apply through `Potenda.apply_plan`, which resolves the account again, checks it
  against the account the plan recorded and writes through the planned-write surface.

They cover quickstart scenario 1 (SC-001, SC-002, SC-007), scenario 2 (the `owner` setting:
SC-003, SC-004, FR-011), scenario 3 (who last changed each value a plan updates, read with
the plan's destination read: SC-005, FR-007) and scenario 5 (a missing permission on a
written kind and on the owner's kind: SC-006). With an owner, the account also holds `view` on `CoreAccountGroup`,
the only permission the owner lookup needs, because it selects the group's name only.

What is written where:

- The throwaway schema and every node the import writes go to a branch the module creates and
  deletes, never to `main`.
- Accounts, roles, groups and object permissions are global: Infrahub keeps them on `main`.
  Each test deletes the ones it created at teardown. A permission that already existed is
  reused and left in place.

The destination needs `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN`, the token of an account that
can manage accounts and permissions (the initial admin). Anonymous access should be off, so that
a missing permission cannot be granted by the anonymous role. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<admin token> \\
    uv run pytest -m integration tests/integration/test_infrahub_lineage_integration.py
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import MagicMock

import pytest
import requests
from diffsync import Adapter
from infrahub_sdk.exceptions import AuthenticationError
from infrahub_sdk.node.attribute import Attribute
from pydantic import Field
from typer.testing import CliRunner

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig, SyncInstance
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel, NamedLineageNodeNotFoundError
from infrahub_sync.cache.cursors import CursorTier
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.cli import NOT_RECORDED, app
from infrahub_sync.configuration import CredentialConfigurationError, validate_package_credentials
from infrahub_sync.infrahub_errors import classify_infrahub_error
from infrahub_sync.plan.errors import ImportAccountMismatchError, OperationApplyFailedError
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.potenda import Potenda
from tests.configuration.validation_packages import package, package_data
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from infrahub_sdk import InfrahubClientSync
    from infrahub_sdk.node import InfrahubNodeSync

    from infrahub_sync.plan.models import PlannedOperation
    from infrahub_sync.plan.review import SavedPlan as SavedReview

pytestmark = pytest.mark.integration

TAG_KIND = "TestLineageTag"
DEVICE_KIND = "TestLineageDevice"
TAGS_RELATIONSHIP = "tags"
PRIMARY_TAG_RELATIONSHIP = "primary_tag"

_SCHEMA = {
    "version": "1.0",
    "nodes": [
        {
            "name": "LineageTag",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}],
        },
        {
            "name": "LineageDevice",
            "namespace": "Test",
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [
                {"name": "name", "kind": "Text", "unique": True},
                {"name": "serial", "kind": "Text", "optional": True},
                {"name": "description", "kind": "Text", "optional": True},
            ],
            "relationships": [
                {"name": "tags", "peer": TAG_KIND, "cardinality": "many", "kind": "Generic", "optional": True},
                # A second relationship to the same peer kind needs an identifier of its own.
                {
                    "name": PRIMARY_TAG_RELATIONSHIP,
                    "peer": TAG_KIND,
                    "cardinality": "one",
                    "kind": "Generic",
                    "optional": True,
                    "identifier": "testlineagedevice__primary_tag",
                },
            ],
        },
    ],
}

# `decision` values of an object permission (Infrahub `PermissionDecision`).
ALLOW_OTHER = 4


@dataclass(frozen=True, slots=True)
class ObjectPermission:
    """One object permission, as Infrahub renders it: `object:<namespace>:<name>:<action>:<decision>`."""

    namespace: str
    name: str
    action: str
    decision: int = ALLOW_OTHER

    @property
    def label(self) -> str:
        """The permission as Infrahub names it in a denial."""
        decision = {2: "allow_default", 4: "allow_other", 6: "allow_all"}[self.decision]
        return f"object:{self.namespace}:{self.name}:{self.action}:{decision}"


def _kind_permissions(namespace: str, name: str, actions: Sequence[str]) -> tuple[ObjectPermission, ...]:
    return tuple(ObjectPermission(namespace=namespace, name=name, action=action) for action in actions)


# The smallest permission set of research R-7, for a branch other than the default branch: the
# import writes both kinds and reads the tag kind as the peer kind of `tags`. No global
# permission is needed off the default branch, and none is needed for `get_version`,
# `get_user` or the schema read.
BRANCH_IMPORT_PERMISSIONS: tuple[ObjectPermission, ...] = (
    *_kind_permissions("Test", "LineageTag", ("view", "create", "update")),
    *_kind_permissions("Test", "LineageDevice", ("view", "create", "update")),
)
DEVICE_CREATE = ObjectPermission(namespace="Test", name="LineageDevice", action="create")
# The owner lookup reads the named group, selecting its name only (research R-7).
GROUP_VIEW = ObjectPermission(namespace="Core", name="AccountGroup", action="view")
OWNER_IMPORT_PERMISSIONS: tuple[ObjectPermission, ...] = (*BRANCH_IMPORT_PERMISSIONS, GROUP_VIEW)
GROUP_KIND = "CoreAccountGroup"


@dataclass(slots=True)
class ImportAccount:
    """An account made for one test, with the token the import uses and the role holding its permissions."""

    name: str
    id: str
    token: str = field(repr=False)
    role_id: str
    permission_ids: dict[ObjectPermission, str]


def _env_or_skip() -> tuple[str, str]:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        pytest.skip("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set")
    return address, token


def _make_client(address: str, token: str, branch: str | None = None) -> InfrahubClientSync:
    """A sync Infrahub client bound to `branch` when one is given, imported lazily."""
    from infrahub_sdk import Config, InfrahubClientSync

    settings: dict[str, Any] = {"address": address, "api_token": token, "timeout": 120}
    if branch is not None:
        settings["default_branch"] = branch
    return InfrahubClientSync(config=Config(**settings))


def _await_schema_kinds(client: InfrahubClientSync, branch: str, kinds: Sequence[str], timeout: float = 120.0) -> None:
    """Block until `branch` serves every kind in `kinds`; a schema load returns before they are queryable."""
    deadline = time.monotonic() + timeout
    while True:
        missing = set(kinds) - set(client.schema.all(branch=branch, refresh=True))
        if not missing:
            return
        if time.monotonic() >= deadline:
            msg = f"The destination did not serve {sorted(missing)} on {branch!r} within {timeout:.0f}s."
            raise AssertionError(msg)
        time.sleep(1.0)


@dataclass(frozen=True, slots=True)
class LineageBranch:
    """The module's branch, holding the throwaway schema, and the admin client used to set up and read back."""

    address: str
    admin_token: str = field(repr=False)
    branch: str
    admin: InfrahubClientSync


@pytest.fixture(scope="module")
def lineage_branch() -> Iterator[LineageBranch]:
    """A branch of this module's own with the throwaway schema; deleting it removes every node the tests wrote."""
    address, token = _env_or_skip()
    branch = f"lineage-{uuid.uuid4().hex[:8]}"
    lifecycle = _make_client(address, token)
    lifecycle.branch.create(branch_name=branch, sync_with_git=False)
    try:
        response = requests.post(
            f"{address}/api/schema/load?branch={branch}",
            headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
            json={"schemas": [_SCHEMA]},
            timeout=120,
        )
        response.raise_for_status()
        admin = _make_client(address, token, branch=branch)
        _await_schema_kinds(admin, branch, (TAG_KIND, DEVICE_KIND))
        yield LineageBranch(address=address, admin_token=token, branch=branch, admin=admin)
    finally:
        lifecycle.branch.delete(branch)


def _graphql(address: str, query: str, *, headers: dict[str, str]) -> dict[str, Any]:
    """Run one GraphQL query on `main` and return its `data`, failing the fixture on any error."""
    response = requests.post(f"{address}/graphql", json={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        messages = [error.get("message") for error in body["errors"]]
        msg = f"Fixture GraphQL call refused: {messages}"
        raise AssertionError(msg)
    return body["data"]


def _create_api_token(address: str, name: str, password: str) -> str:
    """Log in as the account and create an API token for it; the token is never printed."""
    login = requests.post(f"{address}/api/auth/login", json={"username": name, "password": password}, timeout=60)
    login.raise_for_status()
    access = login.json()["access_token"]
    data = _graphql(
        address,
        'mutation { InfrahubAccountTokenCreate(data: {name: "lineage-import"}) { ok object { token { value } } } }',
        headers={"Authorization": f"Bearer {access}"},
    )
    return data["InfrahubAccountTokenCreate"]["object"]["token"]["value"]


class AccountFactory:
    """Make import accounts holding an exact permission set, and remove one permission from an account.

    Every account is a member of a group of its own, whose only role holds the permissions.
    Permissions that do not exist yet are created and deleted at teardown.
    """

    def __init__(self, address: str, admin_token: str) -> None:
        self._address = address
        self._admin_token = admin_token
        self._admin = _make_client(address, admin_token)
        self._created: list[InfrahubNodeSync] = []
        self._created_permissions: list[InfrahubNodeSync] = []
        self._permission_ids: dict[ObjectPermission, str] = {}

    def __call__(self, permissions: Sequence[ObjectPermission]) -> ImportAccount:
        """Return a new account whose only permissions are `permissions`."""
        name = f"lineage-import-{uuid.uuid4().hex[:8]}"
        password = secrets.token_urlsafe(24)
        account = self._admin.create(
            kind="CoreAccount", data={"name": name, "password": password, "account_type": "User"}
        )
        account.save()
        self._created.append(account)
        permission_ids = {permission: self._permission_id(permission) for permission in permissions}
        role = self._admin.create(
            kind="CoreAccountRole", data={"name": f"{name}-role", "permissions": list(permission_ids.values())}
        )
        role.save()
        self._created.append(role)
        group = self._admin.create(
            kind="CoreAccountGroup", data={"name": f"{name}-group", "roles": [role.id], "members": [account.id]}
        )
        group.save()
        self._created.append(group)
        assert account.id is not None
        assert role.id is not None
        return ImportAccount(
            name=name,
            id=account.id,
            token=_create_api_token(self._address, name, password),
            role_id=role.id,
            permission_ids=permission_ids,
        )

    def remove_permission(self, account: ImportAccount, permission: ObjectPermission) -> None:
        """Take `permission` away from the account's role, leaving every other permission in place."""
        permission_id = account.permission_ids.pop(permission)
        _graphql(
            self._address,
            "mutation { RelationshipRemove(data: {"
            f'id: "{account.role_id}", name: "permissions", nodes: [{{id: "{permission_id}"}}]'
            "}) { ok } }",
            headers={"X-INFRAHUB-KEY": self._admin_token},
        )

    def _permission_id(self, permission: ObjectPermission) -> str:
        if permission in self._permission_ids:
            return self._permission_ids[permission]
        data = {
            "namespace": permission.namespace,
            "name": permission.name,
            "action": permission.action,
            "decision": permission.decision,
        }
        filters: dict[str, Any] = {f"{key}__value": value for key, value in data.items()}
        found = self._admin.filters(kind="CoreObjectPermission", **filters)
        if found:
            node = found[0]
        else:
            node = self._admin.create(kind="CoreObjectPermission", data=data)
            node.save()
            self._created_permissions.append(node)
        assert node.id is not None
        self._permission_ids[permission] = node.id
        return node.id

    def close(self) -> None:
        """Delete what this factory created: groups, roles and accounts, then the permissions."""
        for node in [*reversed(self._created), *self._created_permissions]:
            node.delete()


@pytest.fixture
def account_factory(lineage_branch: LineageBranch) -> Iterator[AccountFactory]:
    """An `AccountFactory` whose accounts, roles, groups and new permissions are deleted after the test."""
    factory = AccountFactory(lineage_branch.address, lineage_branch.admin_token)
    try:
        yield factory
    finally:
        factory.close()


def _attribute(node: InfrahubNodeSync, name: str) -> Attribute:
    """The attribute `name` of `node`, typed as an attribute rather than as any field of the node."""
    attribute = getattr(node, name)
    assert isinstance(attribute, Attribute), f"{node.get_kind()}.{name} is not an attribute."
    return attribute


@dataclass(slots=True)
class OwnerGroup:
    """A `CoreAccountGroup` a test names as owner, which the test can rename and restore."""

    node: InfrahubNodeSync
    name: str

    @property
    def id(self) -> str:
        """The id Infrahub gave the group."""
        assert self.node.id is not None
        return self.node.id

    def rename(self, name: str) -> None:
        """Give the group another name, as a person does in Infrahub."""
        _attribute(self.node, "name").value = name
        self.node.save()


@pytest.fixture
def owner_groups(lineage_branch: LineageBranch) -> Iterator[Callable[[], OwnerGroup]]:
    """Make `CoreAccountGroup` nodes with no member and no role; each is deleted after the test."""
    admin = _make_client(lineage_branch.address, lineage_branch.admin_token)
    created: list[OwnerGroup] = []

    def make() -> OwnerGroup:
        name = f"network-team-{uuid.uuid4().hex[:8]}"
        node = admin.create(kind=GROUP_KIND, data={"name": name})
        node.save()
        group = OwnerGroup(node=node, name=name)
        created.append(group)
        return group

    try:
        yield make
    finally:
        for group in reversed(created):
            group.node.delete()


def import_adapter(
    lineage: LineageBranch,
    account: ImportAccount,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner: OwnerGroup | None = None,
) -> InfrahubAdapter:
    """The Infrahub destination adapter built the way a run builds it, with the account's token.

    A direct run takes the token from `INFRAHUB_API_TOKEN` before the settings, so the
    environment variable is set to the account's token for the construction only. The
    construction reads the server version and the schema with that token. `owner` adds the
    setting `owner: {kind: CoreAccountGroup, name: <the group's name>}`.
    """
    settings: dict[str, Any] = {"url": lineage.address}
    if owner is not None:
        settings["owner"] = {"kind": GROUP_KIND, "name": owner.name}
    config = SyncConfig(
        name="lineage-integration",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub", settings=settings),
    )
    with monkeypatch.context() as patched:
        patched.setenv("INFRAHUB_ADDRESS", lineage.address)
        patched.setenv("INFRAHUB_API_TOKEN", account.token)
        return InfrahubAdapter(target="destination", adapter=config.destination, config=config, branch=lineage.branch)


def plan_reads(adapter: InfrahubAdapter) -> None:
    """The reads a plan makes of the destination: lineage, then each kind with its fields (SC-002)."""
    adapter.resolve_lineage()
    adapter.client.all(kind=TAG_KIND, include=["name"], populate_store=True)
    adapter.client.all(kind=DEVICE_KIND, include=["name", "serial", TAGS_RELATIONSHIP], populate_store=True)


@dataclass(frozen=True, slots=True)
class SavedPlan:
    """A saved plan on disk, with the names it creates."""

    run_dir: Path
    run_id: str
    tag_names: tuple[str, ...]
    device_name: str


def _tags_reference(tag_names: Sequence[str]) -> dict[str, Any]:
    return {
        "field": TAGS_RELATIONSHIP,
        "peer_kind": TAG_KIND,
        "cardinality": "many",
        "peers": [{"name": name} for name in tag_names],
    }


def _write_plan(
    tmp_path: Path, planning: InfrahubAdapter, operations: list[dict[str, Any]], *, suffix: str
) -> tuple[Path, str]:
    """Write `operations` as a saved plan whose manifest records the lineage `planning` resolved.

    The manifest records that lineage as the plan writer does (`destination_lineage_record`), so
    an apply compares its account with this one. Returns the run directory and the run id.
    """
    record = planning.lineage_record
    assert record is not None, "Precondition: the planning adapter must have resolved its lineage."
    run_id = f"{datetime.now(tz=timezone.utc):%Y%m%dT%H%M}-{suffix}"
    run_dir = tmp_path / "lineage-integration" / run_id
    run_dir.mkdir(parents=True)
    write_artifact(run_dir, operations, run_id=run_id, source_snapshot=[], lineage=record.model_dump(mode="json"))
    return run_dir, run_id


def save_plan(tmp_path: Path, planning: InfrahubAdapter, *, device_tags: int = 2) -> SavedPlan:
    """Write the plan a run with `planning` records: two tag creates, then a device create.

    The device refers to the first `device_tags` of the two tags.
    """
    suffix = uuid.uuid4().hex[:8]
    tag_names = (f"lineage-tag-{suffix}-0", f"lineage-tag-{suffix}-1")
    device_name = f"lineage-device-{suffix}"
    operations = [operation_record(kind=TAG_KIND, identity={"name": name}) for name in tag_names]
    operations.append(
        operation_record(
            kind=DEVICE_KIND,
            identity={"name": device_name},
            payload={"name": device_name, "serial": f"serial-{suffix}"},
            relationships=[_tags_reference(tag_names[:device_tags])],
            tier=1,
        )
    )
    run_dir, run_id = _write_plan(tmp_path, planning, operations, suffix=suffix)
    return SavedPlan(run_dir=run_dir, run_id=run_id, tag_names=tag_names, device_name=device_name)


def save_device_update_plan(
    tmp_path: Path, planning: InfrahubAdapter, created: SavedPlan, *, device_id: str, serial: str
) -> SavedPlan:
    """Write the plan of a second run: one update of the device, with a new serial and both tags."""
    suffix = uuid.uuid4().hex[:8]
    operations = [
        operation_record(
            action="update",
            kind=DEVICE_KIND,
            identity={"name": created.device_name},
            payload={"name": created.device_name, "serial": serial},
            relationships=[_tags_reference(created.tag_names)],
            tier=1,
            destination_id=device_id,
        )
    ]
    run_dir, run_id = _write_plan(tmp_path, planning, operations, suffix=suffix)
    return SavedPlan(run_dir=run_dir, run_id=run_id, tag_names=created.tag_names, device_name=created.device_name)


def apply_saved_plan(plan: SavedPlan, destination: InfrahubAdapter) -> None:
    """Apply the saved plan through the engine, as `infrahub-sync apply` does once the plan is approved."""
    engine = Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type] — apply reads no source
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type] — the configuration version is passed in
        top_level=[TAG_KIND, DEVICE_KIND],
        run_dir=plan.run_dir,
        run_id=plan.run_id,
    )
    engine.apply_plan(ownership=granted_ownership(), config_version=CONFIG_VERSION)


def _read_back(admin: InfrahubClientSync, kind: str, name: str, branch: str) -> InfrahubNodeSync:
    """The node as an admin reads it, with the properties of each value and the node metadata."""
    return admin.get(kind=kind, name__value=name, branch=branch, property=True, include_metadata=True)


def _existing_ids(admin: InfrahubClientSync, kind: str, names: Sequence[str], branch: str) -> list[str | None]:
    """The ids of the nodes of `kind` named in `names` that the destination holds."""
    return [node.id for node in admin.filters(kind=kind, name__values=list(names), branch=branch)]


def _value_lineage(attribute: object) -> dict[str, Any]:
    """The source id, owner id and protection of one attribute, as the SDK reads them with `property=True`."""
    source = getattr(attribute, "source", None)
    owner = getattr(attribute, "owner", None)
    return {
        "source": getattr(source, "id", None),
        "owner": getattr(owner, "id", None),
        "protected": bool(getattr(attribute, "is_protected", None)),
    }


# The SDK 1.23.2 node of a cardinality-many relationship read with `property=True` leaves each
# peer's `source` and `owner` unset, so the edge properties are read with a query of their own.
_EDGE_PROPERTIES_QUERY = """
query EdgeProperties($name: String!) {
  TestLineageDevice(name__value: $name) {
    edges { node { tags { edges { node { id } properties { source { id } owner { id } is_protected } } } } }
  }
}
"""


def _edge_lineage(admin: InfrahubClientSync, device_name: str, branch: str) -> dict[str, dict[str, Any]]:
    """The source id, owner id and protection of each `tags` edge of the device, by peer id.

    Infrahub returns `is_protected` as `null` on an edge written without the flag, which is
    read as not protected.
    """
    data = admin.execute_graphql(query=_EDGE_PROPERTIES_QUERY, variables={"name": device_name}, branch_name=branch)
    devices = data[DEVICE_KIND]["edges"]
    assert len(devices) == 1, f"Expected one device named {device_name!r}, read back {len(devices)}."
    lineage: dict[str, dict[str, Any]] = {}
    for edge in devices[0]["node"][TAGS_RELATIONSHIP]["edges"]:
        properties = edge["properties"]
        lineage[edge["node"]["id"]] = {
            "source": (properties.get("source") or {}).get("id"),
            "owner": (properties.get("owner") or {}).get("id"),
            "protected": bool(properties.get("is_protected")),
        }
    return lineage


def test_values_created_by_the_import_carry_the_import_account_as_source(
    lineage_branch: LineageBranch, account_factory: AccountFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Quickstart scenario 1 (SC-001, SC-002): a plan and an apply with only the documented permissions.

    Every attribute and every relationship edge the apply created has the import account as
    source, no owner and no protection, and every node it created has `created_by` set to the
    import account. The edge source was not read back by the permission probe of research R-7.
    """
    account = account_factory(BRANCH_IMPORT_PERMISSIONS)

    planning = import_adapter(lineage_branch, account, monkeypatch)
    plan_reads(planning)
    plan = save_plan(tmp_path, planning)

    apply_saved_plan(plan, import_adapter(lineage_branch, account, monkeypatch))

    admin, branch = lineage_branch.admin, lineage_branch.branch
    expected = {"source": account.id, "owner": None, "protected": False}
    observed: dict[str, dict[str, Any]] = {}
    for name in plan.tag_names:
        tag = _read_back(admin, TAG_KIND, name, branch)
        observed[f"{name}.name"] = _value_lineage(tag.name)
        metadata = tag.get_node_metadata()
        observed[f"{name} created_by"] = {"id": getattr(getattr(metadata, "created_by", None), "id", None)}
    device = _read_back(admin, DEVICE_KIND, plan.device_name, branch)
    observed[f"{plan.device_name}.name"] = _value_lineage(device.name)
    observed[f"{plan.device_name}.serial"] = _value_lineage(device.serial)
    metadata = device.get_node_metadata()
    observed[f"{plan.device_name} created_by"] = {"id": getattr(getattr(metadata, "created_by", None), "id", None)}
    edges = _edge_lineage(admin, plan.device_name, branch)
    assert len(edges) == len(plan.tag_names), f"Expected {len(plan.tag_names)} tags edges, read back {edges}."
    for peer_id, edge in edges.items():
        observed[f"{plan.device_name}.tags -> {peer_id}"] = edge

    wrong = {
        key: value
        for key, value in observed.items()
        if value != ({"id": account.id} if key.endswith("created_by") else expected)
    }
    assert not wrong, (
        f"Every value the import created must have the import account {account.name!r} ({account.id}) as "
        f"source, no owner and no protection, and every node must have it as `created_by`. Differs: {wrong}"
    )


def test_an_apply_with_another_accounts_token_is_refused_before_any_write(
    lineage_branch: LineageBranch, account_factory: AccountFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Quickstart scenario 1, step 4 (SC-007, FR-012): the cause names both accounts and nothing is written."""
    planner = account_factory(BRANCH_IMPORT_PERMISSIONS)
    other = account_factory(BRANCH_IMPORT_PERMISSIONS)

    planning = import_adapter(lineage_branch, planner, monkeypatch)
    plan_reads(planning)
    plan = save_plan(tmp_path, planning)

    with pytest.raises(ImportAccountMismatchError) as raised:
        apply_saved_plan(plan, import_adapter(lineage_branch, other, monkeypatch))

    message = str(raised.value)
    for named in (planner.name, planner.id, other.name, other.id):
        assert named in message, f"The refusal must name {named!r}; it said: {message}"
    admin, branch = lineage_branch.admin, lineage_branch.branch
    written = _existing_ids(admin, TAG_KIND, plan.tag_names, branch) + _existing_ids(
        admin, DEVICE_KIND, [plan.device_name], branch
    )
    assert written == [], f"The refused apply must write nothing; the destination holds {written}."


def test_a_missing_permission_is_recorded_as_a_missing_permission(
    lineage_branch: LineageBranch, account_factory: AccountFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Quickstart scenario 5 (SC-006, FR-008): one permission removed, and the cause names it.

    The account loses `create` on the device kind after the plan is saved. The apply writes the
    tags, then the device write is refused with HTTP 403 or a GraphQL permission denial. The
    run's cause is a missing permission that quotes the permission Infrahub names, and never a
    rejected token.
    """
    account = account_factory(BRANCH_IMPORT_PERMISSIONS)
    planning = import_adapter(lineage_branch, account, monkeypatch)
    plan_reads(planning)
    plan = save_plan(tmp_path, planning)

    account_factory.remove_permission(account, DEVICE_CREATE)

    with pytest.raises(OperationApplyFailedError) as raised:
        apply_saved_plan(plan, import_adapter(lineage_branch, account, monkeypatch))

    message = str(raised.value)
    classification = classify_infrahub_error(raised.value.__cause__) if raised.value.__cause__ else None
    assert classification is not None, f"The refusal's cause must be classified; the run said: {message}"
    assert classification.problem == "missing_permission", f"Classified as {classification}; the run said: {message}"
    assert "a missing permission" in message, f"The run must record a missing permission; it said: {message}"
    assert "authentication failure" not in message, f"A 403 must not read as a refused token; it said: {message}"
    assert any(DEVICE_KIND.removeprefix("Test") in permission for permission in classification.permissions), (
        f"The cause must quote the permission Infrahub names for the device kind; it quoted "
        f"{classification.permissions} and said: {message}"
    )


_SET_OWNER_BY_HAND = """
mutation SetOwnerByHand($device: String!, $serial: String!, $tag: String!, $owner: String!) {
  TestLineageDeviceUpdate(data: {
    id: $device, serial: {value: $serial, owner: $owner}, tags: [{id: $tag, _relation__owner: $owner}]
  }) { ok }
}
"""


def _set_owner_by_hand(lineage: LineageBranch, *, device: InfrahubNodeSync, tag_id: str, owner: OwnerGroup) -> None:
    """Make `owner` the owner of the device's serial and of its edge to `tag_id`, as a person does by hand.

    The device has only that edge at this point, so the list sent keeps its peers as they are.
    """
    lineage.admin.execute_graphql(
        query=_SET_OWNER_BY_HAND,
        variables={"device": device.id, "serial": _attribute(device, "serial").value, "tag": tag_id, "owner": owner.id},
        branch_name=lineage.branch,
    )


class SourceNotRead:
    """A source adapter that records each load; a plan stopped by lineage must record none."""

    top_level: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.loads: list[str] = []

    def load(self) -> None:
        """Record the load."""
        self.loads.append("load")


def plan_loads(destination: InfrahubAdapter, source: SourceNotRead) -> None:
    """Load both sides as a plan does: the engine resolves the destination's lineage first."""
    engine = Potenda(
        source=source,  # ty: ignore[invalid-argument-type] — the stand-in records the load the engine starts
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type] — no plan is written
        top_level=[TAG_KIND, DEVICE_KIND],
    )
    engine.load_both_sides()


def test_owner_is_set_on_created_values_and_on_added_edges_only(
    lineage_branch: LineageBranch,
    account_factory: AccountFactory,
    owner_groups: Callable[[], OwnerGroup],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Quickstart scenario 2, steps 1 to 4 (SC-004, FR-002, research R-6).

    A first apply creates two tags and a device that refers to the first tag: every value it
    creates has the named group as owner. A person then gives the device's serial and its edge
    to the first tag another owner. A second apply updates the serial and adds the second tag:
    the serial and the first edge keep the owner the person set, and only the added edge gets
    the named group.
    """
    account = account_factory(OWNER_IMPORT_PERMISSIONS)
    team = owner_groups()
    by_hand = owner_groups()
    admin, branch = lineage_branch.admin, lineage_branch.branch

    planning = import_adapter(lineage_branch, account, monkeypatch, owner=team)
    plan_reads(planning)
    created = save_plan(tmp_path, planning, device_tags=1)
    assert planning.lineage_record is not None
    assert planning.lineage_record.owner is not None
    assert (planning.lineage_record.owner.kind, planning.lineage_record.owner.name) == (GROUP_KIND, team.name)
    apply_saved_plan(created, import_adapter(lineage_branch, account, monkeypatch, owner=team))

    tags = {name: _read_back(admin, TAG_KIND, name, branch) for name in created.tag_names}
    device = _read_back(admin, DEVICE_KIND, created.device_name, branch)
    first_tag_id, second_tag_id = (tags[name].id for name in created.tag_names)
    assert first_tag_id is not None
    assert second_tag_id is not None
    after_create = {f"{name}.name": _value_lineage(tag.name)["owner"] for name, tag in tags.items()}
    after_create["device.name"] = _value_lineage(device.name)["owner"]
    after_create["device.serial"] = _value_lineage(device.serial)["owner"]
    after_create.update(
        {
            f"device.tags -> {peer}": edge["owner"]
            for peer, edge in _edge_lineage(admin, created.device_name, branch).items()
        }
    )
    assert after_create == {
        **{f"{name}.name": team.id for name in created.tag_names},
        "device.name": team.id,
        "device.serial": team.id,
        f"device.tags -> {first_tag_id}": team.id,
    }, f"Every value the first apply created must have the group {team.name!r} ({team.id}) as owner."

    _set_owner_by_hand(lineage_branch, device=device, tag_id=first_tag_id, owner=by_hand)

    planning = import_adapter(lineage_branch, account, monkeypatch, owner=team)
    plan_reads(planning)
    assert device.id is not None
    updated = save_device_update_plan(tmp_path, planning, created, device_id=device.id, serial="serial-updated")
    apply_saved_plan(updated, import_adapter(lineage_branch, account, monkeypatch, owner=team))

    device = _read_back(admin, DEVICE_KIND, created.device_name, branch)
    assert _attribute(device, "serial").value == "serial-updated", (
        "Precondition: the second apply must update the serial."
    )
    after_update = {
        "device.name": _value_lineage(device.name)["owner"],
        "device.serial": _value_lineage(device.serial)["owner"],
        **{
            f"device.tags -> {peer}": edge["owner"]
            for peer, edge in _edge_lineage(admin, created.device_name, branch).items()
        },
    }
    assert after_update == {
        "device.name": team.id,
        "device.serial": by_hand.id,
        f"device.tags -> {first_tag_id}": by_hand.id,
        f"device.tags -> {second_tag_id}": team.id,
    }, (
        f"The second apply must keep each value's owner and give only the added edge the group "
        f"{team.name!r} ({team.id}); the owner given by hand is {by_hand.name!r} ({by_hand.id})."
    )


def test_a_missing_owner_stops_the_plan_before_the_source_is_read_and_the_apply_before_any_write(
    lineage_branch: LineageBranch,
    account_factory: AccountFactory,
    owner_groups: Callable[[], OwnerGroup],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Quickstart scenario 2, steps 5 and 6 (SC-003, FR-004, FR-011): the same cause in plan and in apply.

    The group is renamed, so the name the package gives matches no group. The plan stops before
    the engine starts the source load. With the group restored, a plan is saved; the group is
    renamed again, and the apply stops before any write with the cause the plan recorded.
    """
    account = account_factory(OWNER_IMPORT_PERMISSIONS)
    team = owner_groups()
    named = team.name
    expected_cause = f"The setting `owner` names `{GROUP_KIND}` `{named}`, which does not exist."

    team.rename(f"{named}-renamed")
    source = SourceNotRead()
    with pytest.raises(NamedLineageNodeNotFoundError) as plan_stop:
        plan_loads(import_adapter(lineage_branch, account, monkeypatch, owner=OwnerGroup(team.node, named)), source)
    assert str(plan_stop.value) == expected_cause
    assert source.loads == [], "The plan must stop before the source system is read."

    team.rename(named)
    planning = import_adapter(lineage_branch, account, monkeypatch, owner=team)
    plan_reads(planning)
    plan = save_plan(tmp_path, planning)

    team.rename(f"{named}-renamed")
    with pytest.raises(NamedLineageNodeNotFoundError) as apply_stop:
        apply_saved_plan(plan, import_adapter(lineage_branch, account, monkeypatch, owner=OwnerGroup(team.node, named)))
    assert str(apply_stop.value) == str(plan_stop.value), "The apply must record the cause the plan records."

    admin, branch = lineage_branch.admin, lineage_branch.branch
    written = _existing_ids(admin, TAG_KIND, plan.tag_names, branch) + _existing_ids(
        admin, DEVICE_KIND, [plan.device_name], branch
    )
    assert written == [], f"The stopped apply must write nothing; the destination holds {written}."


def test_a_missing_permission_on_the_owner_kind_is_recorded_as_a_missing_permission(
    lineage_branch: LineageBranch,
    account_factory: AccountFactory,
    owner_groups: Callable[[], OwnerGroup],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quickstart scenario 5 with an owner (SC-006, FR-008): no `view` on `CoreAccountGroup`.

    The plan's owner lookup is refused with HTTP 403, which the SDK raises as an
    `AuthenticationError`, and not as a missing group. The cause is a missing permission that
    quotes the permission Infrahub names (on 1.10.6, `object:Core:AccountGroup:view:allow_other`),
    and never a rejected token.
    """
    account = account_factory(OWNER_IMPORT_PERMISSIONS)
    team = owner_groups()
    account_factory.remove_permission(account, GROUP_VIEW)

    planning = import_adapter(lineage_branch, account, monkeypatch, owner=team)
    with pytest.raises(AuthenticationError) as raised:
        planning.resolve_lineage()

    classification = classify_infrahub_error(raised.value)
    assert classification is not None, f"The refusal must be classified; the run said: {raised.value!r}"
    assert classification.problem == "missing_permission", f"Classified as {classification}"
    assert any("AccountGroup" in permission for permission in classification.permissions), (
        f"The cause must quote the permission Infrahub names for the group kind; it quoted {classification.permissions}"
    )


# --- Quickstart scenario 3: who last changed each value a plan updates -------------------------


class LineageTagModel(InfrahubModel):
    """The tag kind as a plan compares it: its name only."""

    _modelname = TAG_KIND
    _identifiers = ("name",)
    _attributes = ()

    name: str
    local_data: Any | None = None


class LineageDeviceModel(InfrahubModel):
    """The device kind as a plan compares it: two attributes, a one-edge and a many-edge."""

    _modelname = DEVICE_KIND
    _identifiers = ("name",)
    _attributes = ("description", PRIMARY_TAG_RELATIONSHIP, "serial", TAGS_RELATIONSHIP)

    name: str
    serial: str | None = None
    description: str | None = None
    primary_tag: str | None = None
    tags: list[str] | None = Field(default_factory=list)
    local_data: Any | None = None


class LineagePlanDestination(InfrahubAdapter):
    """The Infrahub destination with the two kinds' models, as a generated adapter declares them."""

    TestLineageTag = LineageTagModel
    TestLineageDevice = LineageDeviceModel


class LineagePlanSource(Adapter):
    """A source system held in memory: the records a plan compares the destination with."""

    TestLineageTag = LineageTagModel
    TestLineageDevice = LineageDeviceModel
    top_level: ClassVar[list[str]] = [TAG_KIND, DEVICE_KIND]

    def __init__(self, records: Sequence[InfrahubModel]) -> None:
        super().__init__()
        self._records = list(records)

    def load(self) -> None:
        """Add every record."""
        for record in self._records:
            self.add(record)

    def cursor_tier_for(self, resource: str) -> CursorTier:  # noqa: ARG002, PLR6301 — always a full load
        """No incremental load: the source is read in full."""
        return CursorTier.NONE


def _plan_config(lineage: LineageBranch, sync_name: str) -> SyncInstance:
    """The configuration of the scenario 3 plan: both kinds, every field of the device mapped."""
    device_fields = {
        "name": None,
        "serial": None,
        "description": None,
        TAGS_RELATIONSHIP: TAG_KIND,
        PRIMARY_TAG_RELATIONSHIP: TAG_KIND,
    }
    return SyncInstance(
        name=sync_name,
        directory="/nonexistent/lineage-integration",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub", settings={"url": lineage.address}),
        order=[TAG_KIND, DEVICE_KIND],
        schema_mapping=[
            SchemaMappingModel(
                name=TAG_KIND,
                mapping="tags",
                identifiers=["name"],
                fields=[SchemaMappingField(name="name", mapping="name")],
            ),
            SchemaMappingModel(
                name=DEVICE_KIND,
                mapping="devices",
                identifiers=["name"],
                fields=[
                    SchemaMappingField(name=name, mapping=name, reference=reference)
                    for name, reference in device_fields.items()
                ],
            ),
        ],
    )


def _run_plan(
    lineage: LineageBranch,
    account: ImportAccount,
    monkeypatch: pytest.MonkeyPatch,
    source: LineagePlanSource,
    sync_name: str,
) -> str:
    """Run a plan as `diff` does, with the account's token: load both sides, compare, write the plan.

    Returns the run id. The run directory is under the test's cache root, where
    `read_saved_plan` finds it by the synchronization's name and the run id.
    """
    config = _plan_config(lineage, sync_name)
    with monkeypatch.context() as patched:
        patched.setenv("INFRAHUB_ADDRESS", lineage.address)
        patched.setenv("INFRAHUB_API_TOKEN", account.token)
        destination = LineagePlanDestination(
            target="destination", adapter=config.destination, config=config, branch=lineage.branch
        )
    run_id = f"{datetime.now(tz=timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    run_dir = cache_root_for(sync_name) / run_id
    run_dir.mkdir(parents=True)
    engine = Potenda(
        source=source,
        destination=destination,
        config=config,
        top_level=[TAG_KIND, DEVICE_KIND],
        run_dir=run_dir,
        run_id=run_id,
        show_progress=False,
        concurrent_load=False,
    )
    engine.load_both_sides()
    engine.write_plan(engine.diff())
    return run_id


def _primary_tag_reference(tag_name: str) -> dict[str, Any]:
    return {
        "field": PRIMARY_TAG_RELATIONSHIP,
        "peer_kind": TAG_KIND,
        "cardinality": "one",
        "peers": [{"name": tag_name}],
    }


def _save_scenario_three_creates(tmp_path: Path, planning: InfrahubAdapter, suffix: str) -> SavedPlan:
    """The import's first plan: three tags; a device with a description and both relationships; a bare device.

    The first device refers to the first tag through `tags` and `primary_tag`. The second
    device has a serial and the first tag, and no description and no `primary_tag`.
    """
    tag_names = tuple(f"lineage-tag-{suffix}-{index}" for index in range(3))
    operations = [operation_record(kind=TAG_KIND, identity={"name": name}) for name in tag_names]
    for device, payload, relationships in (
        (
            f"lineage-device-{suffix}-edited",
            {"serial": f"serial-{suffix}-0", "description": "written by the import"},
            [_tags_reference(tag_names[:1]), _primary_tag_reference(tag_names[0])],
        ),
        (f"lineage-device-{suffix}-bare", {"serial": f"serial-{suffix}-1"}, [_tags_reference(tag_names[:1])]),
    ):
        operations.append(
            operation_record(
                kind=DEVICE_KIND,
                identity={"name": device},
                payload={"name": device, **payload},
                relationships=relationships,
                tier=1,
            )
        )
    run_dir, run_id = _write_plan(tmp_path, planning, operations, suffix=suffix)
    return SavedPlan(run_dir=run_dir, run_id=run_id, tag_names=tag_names, device_name=f"lineage-device-{suffix}")


_EDIT_BY_HAND = """
mutation EditByHand($device: String!, $description: String!, $primary: String!) {
  TestLineageDeviceUpdate(data: {id: $device, description: {value: $description}, primary_tag: {id: $primary}}) {
    ok
  }
}
"""

_ADD_TAG_BY_HAND = """
mutation AddTagByHand($device: String!, $tag: String!) {
  RelationshipAdd(data: {id: $device, name: "tags", nodes: [{id: $tag}]}) { ok }
}
"""

# The metadata Infrahub holds for the values the editor changed, read back as an admin.
_LAST_CHANGES_QUERY = """
query LastChanges($name: String!) {
  TestLineageDevice(name__value: $name) {
    edges { node {
      name { updated_by { id } updated_at }
      description { updated_by { id } updated_at }
      primary_tag { node { id } relationship_metadata { updated_by { id } updated_at } }
      tags { edges { node { id } relationship_metadata { updated_by { id } updated_at } } }
    } }
  }
}
"""


def _edit_by_hand(lineage: LineageBranch, editor: ImportAccount, *, device_id: str, tag_id: str) -> None:
    """As the editor: change the description, set `primary_tag` to the tag, and add the tag to `tags`."""
    client = _make_client(lineage.address, editor.token, branch=lineage.branch)
    client.execute_graphql(
        query=_EDIT_BY_HAND,
        variables={"device": device_id, "description": "changed by the editor", "primary": tag_id},
        branch_name=lineage.branch,
    )
    client.execute_graphql(
        query=_ADD_TAG_BY_HAND, variables={"device": device_id, "tag": tag_id}, branch_name=lineage.branch
    )


def _held_last_changes(lineage: LineageBranch, device_name: str, tag_id: str) -> dict[str, dict[str, Any]]:
    """`updated_by` and `updated_at` that Infrahub holds for the device's name and the editor's three values.

    For `tags`, the values of the edge to `tag_id`, the edge the editor added.
    """
    data = lineage.admin.execute_graphql(
        query=_LAST_CHANGES_QUERY, variables={"name": device_name}, branch_name=lineage.branch
    )
    devices = data[DEVICE_KIND]["edges"]
    assert len(devices) == 1, f"Expected one device named {device_name!r}, read back {len(devices)}."
    node = devices[0]["node"]
    edges = {edge["node"]["id"]: edge["relationship_metadata"] for edge in node[TAGS_RELATIONSHIP]["edges"]}

    def flat(metadata: dict[str, Any]) -> dict[str, Any]:
        return {"updated_by": (metadata.get("updated_by") or {}).get("id"), "updated_at": metadata.get("updated_at")}

    return {
        "name": flat(node["name"]),
        "description": flat(node["description"]),
        PRIMARY_TAG_RELATIONSHIP: flat(node[PRIMARY_TAG_RELATIONSHIP]["relationship_metadata"]),
        TAGS_RELATIONSHIP: flat(edges[tag_id]),
    }


def test_the_plan_shows_who_last_changed_each_value_it_updates(
    lineage_branch: LineageBranch,
    account_factory: AccountFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Quickstart scenario 3 (SC-005, FR-007, research R-2 and R-7).

    The import creates three tags and two devices. A second account then changes, on the first
    device, the description, the cardinality-one `primary_tag` and the cardinality-many `tags`
    (it adds an edge). A plan made with the import account's token, which holds no permission
    on `CoreAccount`, proposes:

    - an update of the first device's three values: each record names the second account and
      the time Infrahub holds for that value (for `tags`, the edge the second account added);
    - an update of the second device's `primary_tag`, which has no edge: Infrahub holds no
      `updated_by` and no `updated_at` for it, so the record has none and review shows
      "not recorded";
    - a create of a third device, which has no record.

    On Infrahub 1.10.6 an attribute never given a value still holds `updated_by` and
    `updated_at` from the create of its node, so an empty relationship is the value with
    nothing recorded.
    """
    account = account_factory(BRANCH_IMPORT_PERMISSIONS)
    editor = account_factory(BRANCH_IMPORT_PERMISSIONS)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    scene = _import_then_edit(lineage_branch, account, editor, monkeypatch, tmp_path)
    edited, bare, new = scene.edited, scene.bare, scene.new

    sync_name = f"lineage-last-changes-{scene.suffix}"
    run_id = _run_plan(lineage_branch, account, monkeypatch, _scenario_three_source(scene), sync_name)

    saved = read_saved_plan(sync_name=sync_name, run_id=run_id)
    assert saved.manifest.last_changes is not None, "The plan must record its last changes in the manifest."
    assert saved.manifest.last_changes.metadata_read is True, "On Infrahub 1.10.6 the plan must read metadata."
    by_device = _device_operations(saved, (edited, bare, new))
    assert {name: operation.action for name, operation in by_device.items()} == {
        edited: "update",
        bare: "update",
        new: "create",
    }
    observed, labels = _observed_last_changes(saved, by_device)
    assert observed == {
        f"{edited}.description": {"field_type": "attribute", **scene.held["description"]},
        f"{edited}.{PRIMARY_TAG_RELATIONSHIP}": {"field_type": "relationship", **scene.held[PRIMARY_TAG_RELATIONSHIP]},
        f"{edited}.{TAGS_RELATIONSHIP}": {"field_type": "relationship", **scene.held[TAGS_RELATIONSHIP]},
        f"{bare}.{PRIMARY_TAG_RELATIONSHIP}": {"field_type": "relationship", "updated_by": None, "updated_at": None},
    }, (
        f"Each updated field must name the editor {editor.name!r} ({editor.id}) and the time Infrahub holds; "
        f"the empty `primary_tag` must have no value; the create must have no record. Held: {scene.held}"
    )
    assert len(labels) == 1, f"The editor's records must carry one display label; they carry {labels}."
    (editor_label,) = labels
    assert editor_label, "The import account must read the editor's display label without a permission on accounts."

    shown = _runs_plan_detail(run_id, saved)
    blocks = _detail_blocks(shown, [operation.operation_id for operation in saved.operations()])

    def last_change_lines(device: str) -> list[str]:
        return [line for line in blocks[by_device[device].operation_id] if line.startswith("  last change:")]

    assert last_change_lines(edited) == [
        f"  last change: description (attribute): updated_by={editor_label}, "
        f"updated_at={scene.held['description']['updated_at']}",
        f"  last change: {PRIMARY_TAG_RELATIONSHIP} (relationship): updated_by={editor_label}, "
        f"updated_at={scene.held[PRIMARY_TAG_RELATIONSHIP]['updated_at']}",
        f"  last change: {TAGS_RELATIONSHIP} (relationship): updated_by={editor_label}, "
        f"updated_at={scene.held[TAGS_RELATIONSHIP]['updated_at']}",
    ], f"`runs plan --detail` showed:\n{shown}"
    assert last_change_lines(bare) == [
        f"  last change: {PRIMARY_TAG_RELATIONSHIP} (relationship): updated_by={NOT_RECORDED}, "
        f"updated_at={NOT_RECORDED}",
    ], f"`runs plan --detail` showed:\n{shown}"
    assert last_change_lines(new) == [], f"A create must show no last change; `runs plan --detail` showed:\n{shown}"


@dataclass(frozen=True, slots=True)
class EditedScene:
    """What scenario 3 holds before its plan: the names the import wrote and the values the editor changed."""

    suffix: str
    tag_names: tuple[str, ...]
    edited: str
    bare: str
    new: str
    held: dict[str, dict[str, Any]]


def _import_then_edit(
    lineage: LineageBranch,
    account: ImportAccount,
    editor: ImportAccount,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> EditedScene:
    """The import writes three tags and two devices; the editor then changes three values of the first device.

    Checks that Infrahub holds the import account for the device's name and the editor, at a
    later time, for each value the editor changed.
    """
    admin, branch = lineage.admin, lineage.branch
    suffix = uuid.uuid4().hex[:8]
    planning = import_adapter(lineage, account, monkeypatch)
    plan_reads(planning)
    created = _save_scenario_three_creates(tmp_path, planning, suffix)
    apply_saved_plan(created, import_adapter(lineage, account, monkeypatch))
    edited, bare, new = (f"{created.device_name}-{label}" for label in ("edited", "bare", "new"))
    tag_id_by_name = {
        _attribute(node, "name").value: node.id
        for node in admin.filters(kind=TAG_KIND, name__values=list(created.tag_names), branch=branch)
    }
    assert set(tag_id_by_name) == set(created.tag_names), f"Precondition: the import must create {tag_id_by_name}."
    device_id = _read_back(admin, DEVICE_KIND, edited, branch).id
    second_tag_id = tag_id_by_name[created.tag_names[1]]
    assert device_id is not None
    assert second_tag_id is not None

    _edit_by_hand(lineage, editor, device_id=device_id, tag_id=second_tag_id)
    held = _held_last_changes(lineage, edited, second_tag_id)
    assert held["name"]["updated_by"] == account.id, f"Precondition: the import wrote the name; Infrahub holds {held}."
    for value in ("description", PRIMARY_TAG_RELATIONSHIP, TAGS_RELATIONSHIP):
        assert held[value]["updated_by"] == editor.id, (
            f"Precondition: the editor changed {value}; Infrahub holds {held}."
        )
        assert held[value]["updated_at"] > held["name"]["updated_at"], f"Precondition failed for {value}: {held}"
    return EditedScene(suffix=suffix, tag_names=created.tag_names, edited=edited, bare=bare, new=new, held=held)


def _scenario_three_source(scene: EditedScene) -> LineagePlanSource:
    """The source system's records for scenario 3.

    The first device's description and both relationships differ from the destination, the
    second device gains a `primary_tag`, and a third device is new.
    """
    first, _, third = scene.tag_names
    return LineagePlanSource(
        [
            *(LineageTagModel(name=name) for name in scene.tag_names),
            LineageDeviceModel(
                name=scene.edited,
                serial=f"serial-{scene.suffix}-0",
                description="from the source system",
                primary_tag=third,
                tags=sorted([first, third]),
            ),
            LineageDeviceModel(name=scene.bare, serial=f"serial-{scene.suffix}-1", primary_tag=first, tags=[first]),
            LineageDeviceModel(name=scene.new, serial=f"serial-{scene.suffix}-2"),
        ]
    )


def _device_operations(saved: SavedReview, names: Sequence[str]) -> dict[str, PlannedOperation]:
    """The plan's operation on each named device, by device name."""
    operations = {(operation.kind, operation.identity["name"]): operation for operation in saved.operations()}
    return {name: operations[DEVICE_KIND, name] for name in names}


def _observed_last_changes(
    saved: SavedReview, by_device: dict[str, PlannedOperation]
) -> tuple[dict[str, dict[str, Any]], set[str | None]]:
    """The plan's last-change records by `<device>.<field>`, and the display labels of the accounts they name."""
    device_by_operation = {operation.operation_id: name for name, operation in by_device.items()}
    records = saved.last_changes or []
    observed = {
        f"{device_by_operation[record.operation_id]}.{record.field}": {
            "field_type": record.field_type,
            "updated_by": None if record.updated_by is None else record.updated_by.id,
            "updated_at": record.updated_at,
        }
        for record in records
    }
    labels = {record.updated_by.display_label for record in records if record.updated_by is not None}
    return observed, labels


def _runs_plan_detail(run_id: str, saved: SavedReview) -> str:
    """The output of `infrahub-sync runs plan <run id> --detail` for the plan review the Sync API publishes.

    The review document is the one the service builds from the stored plan; the client that
    would fetch it over HTTP returns it directly.
    """
    review = pytest.importorskip("infrahub_sync.service.flow")._review_document(run_id, saved)
    client = MagicMock()
    client.get_plan.return_value = review
    result = CliRunner().invoke(app, ["runs", "plan", run_id, "--detail"], obj={"client": client})
    assert result.exit_code == 0, result.output
    return result.output


def _detail_blocks(shown: str, operation_ids: Sequence[str]) -> dict[str, list[str]]:
    """The lines `runs plan --detail` prints for each operation, keyed by operation id.

    An operation's block starts at the line that begins with its id and ends before the next
    such line.
    """
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in shown.splitlines():
        starts = next((operation_id for operation_id in operation_ids if line.startswith(f"{operation_id} ")), None)
        if starts is not None:
            current = blocks.setdefault(starts, [])
        elif current is not None:
            current.append(line)
    return blocks


def test_registration_refuses_an_owner_given_as_a_bare_name() -> None:
    """Quickstart scenario 2, step 7 (FR-003): `owner: network-team` is refused when the package is registered."""
    data = package_data()
    data["configuration"]["destination"]["settings"]["owner"] = "network-team"

    with pytest.raises(CredentialConfigurationError) as raised:
        validate_package_credentials(package(data))

    message = str(raised.value)
    assert "/configuration/destination/settings/owner" in message
    assert "`owner` must be `{kind, name}`" in message
