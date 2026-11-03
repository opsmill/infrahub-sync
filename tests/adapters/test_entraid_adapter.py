"""Tests of `EntraidAdapter` against a mocked Microsoft Graph: loading, `$select` and read-only behavior."""

from __future__ import annotations

from typing import TypeVar

import pytest

pytest.importorskip("msgraph")

from infrahub_sync.adapters.entraid import EntraidAdapter, EntraidConfigError, EntraidModel
from infrahub_sync.cache.cursors import CursorTier
from tests.adapters.entraid_fakes import (
    GraphRouter,
    IdentityEmployee,
    IdentityGroup,
    graph_id,
    group_record,
    make_adapter,
    make_config,
    set_entraid_env,
    user_record,
)

EMPLOYEE_FIELDS = [
    {"name": "entra_id", "mapping": "id"},
    {"name": "name", "mapping": "userPrincipalName"},
    {"name": "display_name", "mapping": "displayName"},
    {"name": "enabled", "mapping": "accountEnabled"},
]
GROUP_FIELDS = [
    {"name": "entra_id", "mapping": "id"},
    {"name": "name", "mapping": "displayName"},
    {"name": "description", "mapping": "description"},
]
USERS_ENTRY = {"name": "IdentityEmployee", "mapping": "users", "identifiers": ["entra_id"], "fields": EMPLOYEE_FIELDS}
GROUPS_ENTRY = {"name": "IdentityGroup", "mapping": "groups", "identifiers": ["entra_id"], "fields": GROUP_FIELDS}

ModelT = TypeVar("ModelT", bound=EntraidModel)


@pytest.fixture(autouse=True)
def entraid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    set_entraid_env(monkeypatch)


def loaded(adapter: EntraidAdapter, model: type[ModelT]) -> dict[str | None, ModelT]:
    """The loaded objects of a kind, by `local_id`."""
    return {obj.local_id: obj for obj in adapter.get_all(model) if isinstance(obj, model)}


def test_load_users_and_groups_from_bare_mappings() -> None:
    router = GraphRouter()
    router.add_records(
        "/users",
        [
            user_record(1, "alice@example.com", accountEnabled=True),
            user_record(2, "bob@example.com", accountEnabled=False),
        ],
    )
    router.add_records("/groups", [group_record(11, "network-engineers", description="Network team")])
    adapter = make_adapter(make_config([USERS_ENTRY, GROUPS_ENTRY]), router)

    adapter.load()

    users = loaded(adapter, IdentityEmployee)
    assert set(users) == {graph_id(1), graph_id(2)}
    alice = users[graph_id(1)]
    assert (alice.entra_id, alice.name, alice.display_name, alice.enabled) == (
        graph_id(1),
        "alice@example.com",
        "alice",
        True,
    )
    assert users[graph_id(2)].enabled is False
    groups = loaded(adapter, IdentityGroup)
    assert set(groups) == {graph_id(11)}
    group = groups[graph_id(11)]
    assert (group.entra_id, group.name, group.description) == (graph_id(11), "network-engineers", "Network team")


def test_load_reads_each_collection_once() -> None:
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com")])
    router.add_records("/groups", [group_record(11, "network-engineers")])
    adapter = make_adapter(make_config([USERS_ENTRY, GROUPS_ENTRY]), router)

    adapter.load()

    assert sorted(router.paths()) == ["/groups", "/users"]


def test_select_built_from_fields_filters_id_and_settings_without_transform_keys() -> None:
    entry = {
        "name": "IdentityEmployee",
        "mapping": "users",
        "identifiers": ["entra_id"],
        "fields": [
            {"name": "entra_id", "mapping": "id"},
            {"name": "name", "mapping": "name"},
            {"name": "display_name", "mapping": "displayName"},
            {"name": "email", "mapping": "onPremisesExtensionAttributes.extensionAttribute1"},
        ],
        "filters": [{"field": "userPrincipalName", "operation": "regex", "value": "^(?!svc-)"}],
        "transforms": [{"field": "name", "expression": "{{ userPrincipalName | lower }}"}],
    }
    router = GraphRouter()
    router.add_records(
        "/users",
        [
            user_record(
                1,
                "Alice@example.com",
                department="Network",
                onPremisesExtensionAttributes={"extensionAttribute1": "alice.mail@example.com"},
            )
        ],
    )
    adapter = make_adapter(make_config([entry], settings={"select": {"users": ["department", "displayName"]}}), router)

    adapter.load()

    select = router.requests_for("/users")[0].url.params["$select"].split(",")
    assert select == ["id", "displayName", "onPremisesExtensionAttributes", "userPrincipalName", "department"]
    user = loaded(adapter, IdentityEmployee)[graph_id(1)]
    assert (user.name, user.email) == ("alice@example.com", "alice.mail@example.com")


def test_empty_tenant_loads_zero_objects() -> None:
    router = GraphRouter()
    router.add_pages("/users", [[]])
    router.add_pages("/groups", [[]])
    adapter = make_adapter(make_config([USERS_ENTRY, GROUPS_ENTRY]), router)

    adapter.load()

    assert adapter.count() == 0
    assert sorted(router.paths()) == ["/groups", "/users"]


def test_destination_target_is_refused() -> None:
    router = GraphRouter()

    with pytest.raises(EntraidConfigError, match="source"):
        make_adapter(make_config([USERS_ENTRY]), router, target="destination")

    assert router.requests == []


def test_load_list_property_mapped_to_single_attribute_asks_for_transform() -> None:
    entry = {
        "name": "IdentityEmployee",
        "mapping": "users",
        "identifiers": ["entra_id"],
        "fields": [{"name": "entra_id", "mapping": "id"}, {"name": "email", "mapping": "businessPhones"}],
    }
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com", businessPhones=["+1 555 0100"])])
    adapter = make_adapter(make_config([entry]), router)

    with pytest.raises(EntraidConfigError, match="transform") as excinfo:
        adapter.load()

    assert "email" in str(excinfo.value)


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_model_is_read_only(operation: str) -> None:
    adapter = make_adapter(make_config([USERS_ENTRY]), GraphRouter())
    employee = IdentityEmployee(entra_id=graph_id(1))
    calls = {
        "create": lambda: IdentityEmployee.create(adapter=adapter, ids={"entra_id": graph_id(1)}, attrs={}),
        "update": lambda: employee.update(attrs={"name": "alice@example.com"}),
        "delete": employee.delete,
    }

    with pytest.raises(NotImplementedError, match="read-only"):
        calls[operation]()


def test_cursor_tier_is_none() -> None:
    adapter = make_adapter(make_config([USERS_ENTRY]), GraphRouter())

    assert adapter.cursor_tier_for("IdentityEmployee") is CursorTier.NONE
