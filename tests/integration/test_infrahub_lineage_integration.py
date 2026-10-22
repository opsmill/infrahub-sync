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

They cover quickstart scenario 1 (SC-001, SC-002, SC-007) and the missing-permission half of
scenario 5 (SC-006). The owner half of scenario 5 belongs to the owner tests, which later
scenarios add to this module and which reuse `account_factory` and `lineage_branch`.

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
from typing import TYPE_CHECKING, Any

import pytest
import requests

from infrahub_sync import SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.infrahub_errors import classify_infrahub_error
from infrahub_sync.plan.errors import ImportAccountMismatchError, OperationApplyFailedError
from infrahub_sync.potenda import Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from infrahub_sdk import InfrahubClientSync
    from infrahub_sdk.node import InfrahubNodeSync

pytestmark = pytest.mark.integration

TAG_KIND = "TestLineageTag"
DEVICE_KIND = "TestLineageDevice"
TAGS_RELATIONSHIP = "tags"

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
            ],
            "relationships": [
                {"name": "tags", "peer": TAG_KIND, "cardinality": "many", "kind": "Generic", "optional": True}
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


def import_adapter(lineage: LineageBranch, account: ImportAccount, monkeypatch: pytest.MonkeyPatch) -> InfrahubAdapter:
    """The Infrahub destination adapter built the way a run builds it, with the account's token.

    A direct run takes the token from `INFRAHUB_API_TOKEN` before the settings, so the
    environment variable is set to the account's token for the construction only. The
    construction reads the server version and the schema with that token.
    """
    config = SyncConfig(
        name="lineage-integration",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub", settings={"url": lineage.address}),
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


def save_plan(tmp_path: Path, planning: InfrahubAdapter) -> SavedPlan:
    """Write the plan a run with `planning` records: two tag creates, then a device create that refers to both.

    The manifest records the lineage `planning` resolved, as the plan writer does
    (`destination_lineage_record`), so an apply compares its account with this one.
    """
    record = planning.lineage_record
    assert record is not None, "Precondition: the planning adapter must have resolved its lineage."
    suffix = uuid.uuid4().hex[:8]
    run_id = f"{datetime.now(tz=timezone.utc):%Y%m%dT%H%M}-{suffix}"
    tag_names = (f"lineage-tag-{suffix}-0", f"lineage-tag-{suffix}-1")
    device_name = f"lineage-device-{suffix}"
    operations = [operation_record(kind=TAG_KIND, identity={"name": name}) for name in tag_names]
    operations.append(
        operation_record(
            kind=DEVICE_KIND,
            identity={"name": device_name},
            payload={"name": device_name, "serial": f"serial-{suffix}"},
            relationships=[
                {
                    "field": TAGS_RELATIONSHIP,
                    "peer_kind": TAG_KIND,
                    "cardinality": "many",
                    "peers": [{"name": name} for name in tag_names],
                }
            ],
            tier=1,
        )
    )
    run_dir = tmp_path / "lineage-integration" / run_id
    run_dir.mkdir(parents=True)
    write_artifact(run_dir, operations, run_id=run_id, source_snapshot=[], lineage=record.model_dump(mode="json"))
    return SavedPlan(run_dir=run_dir, run_id=run_id, tag_names=tag_names, device_name=device_name)


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
