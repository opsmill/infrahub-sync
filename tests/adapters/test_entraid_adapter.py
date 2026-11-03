"""Tests of `EntraidAdapter` against a mocked Microsoft Graph: loading, `$select`, membership and read-only behavior."""

from __future__ import annotations

import logging
from typing import TypeVar

import pytest

pytest.importorskip("msgraph")

from infrahub_sync.adapters.entraid import EntraidAdapter, EntraidConfigError, EntraidModel
from infrahub_sync.cache.cursors import CursorTier
from tests.adapters.entraid_fakes import (
    GraphRouter,
    IdentityEmployee,
    IdentityGroup,
    IdentityServiceAccount,
    graph_id,
    group_record,
    make_adapter,
    make_config,
    member_record,
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
ACCOUNTS_COLLECTION = {"resource": "users", "filter": "accountEnabled eq true"}
ACCOUNTS_SETTINGS = {"collections": {"accounts": ACCOUNTS_COLLECTION}}

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


def accounts_entry(name: str, upn_regex: str | None = None, **extra: object) -> dict[str, object]:
    """A `schema_mapping` entry of `name` that reads the `accounts` collection, optionally filtered on the UPN."""
    entry: dict[str, object] = {
        "name": name,
        "mapping": "accounts",
        "identifiers": ["entra_id"],
        "fields": [{"name": "entra_id", "mapping": "id"}, {"name": "name", "mapping": "userPrincipalName"}],
        **extra,
    }
    if upn_regex is not None:
        entry["filters"] = [{"field": "userPrincipalName", "operation": "regex", "value": upn_regex}]
    return entry


def test_shared_collection_splits_records_between_kinds_with_one_read() -> None:
    router = GraphRouter()
    router.add_pages(
        "/users",
        [
            [user_record(1, "alice@example.com"), user_record(2, "svc-backup@example.com")],
            [user_record(3, "bob@example.com"), user_record(4, "svc-monitoring@example.com")],
        ],
    )
    config = make_config(
        [
            accounts_entry("IdentityEmployee", "^(?!svc-)"),
            accounts_entry("IdentityServiceAccount", "^svc-"),
        ],
        settings=ACCOUNTS_SETTINGS,
    )
    adapter = make_adapter(config, router)

    adapter.load()

    assert set(loaded(adapter, IdentityEmployee)) == {graph_id(1), graph_id(3)}
    assert set(loaded(adapter, IdentityServiceAccount)) == {graph_id(2), graph_id(4)}
    requests = router.requests_for("/users")
    assert len(requests) == 2
    assert ["$skiptoken" in request.url.params for request in requests] == [False, True]
    assert requests[0].url.params["$filter"] == "accountEnabled eq true"


def test_shared_collection_transform_does_not_leak_into_other_kind() -> None:
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "Alice@Example.com")])
    lower_upn = {"field": "userPrincipalName", "expression": "{{ userPrincipalName | lower }}"}
    config = make_config(
        [
            accounts_entry("IdentityEmployee", transforms=[lower_upn]),
            accounts_entry("IdentityServiceAccount"),
        ],
        settings=ACCOUNTS_SETTINGS,
        order=["IdentityEmployee", "IdentityServiceAccount"],
    )
    adapter = make_adapter(config, router)

    adapter.load()

    assert loaded(adapter, IdentityEmployee)[graph_id(1)].name == "alice@example.com"
    assert loaded(adapter, IdentityServiceAccount)[graph_id(1)].name == "Alice@Example.com"
    assert len(router.requests_for("/users")) == 1


def test_unknown_collection_is_refused_at_construction_listing_valid_names() -> None:
    router = GraphRouter()
    contractors = {**accounts_entry("IdentityServiceAccount"), "mapping": "contractors"}
    config = make_config([accounts_entry("IdentityEmployee"), contractors], settings=ACCOUNTS_SETTINGS)

    with pytest.raises(EntraidConfigError) as excinfo:
        make_adapter(config, router)

    message = str(excinfo.value)
    assert "contractors" in message
    assert "IdentityServiceAccount" in message
    assert "Valid names: accounts, users, groups" in message
    assert router.requests == []


@pytest.mark.parametrize("reserved", ["users", "groups"])
def test_collection_with_resource_name_is_refused(reserved: str) -> None:
    router = GraphRouter()
    settings = {"collections": {reserved: {"resource": reserved, "filter": "displayName eq 'example'"}}}
    entry = {**USERS_ENTRY, "mapping": reserved}

    with pytest.raises(EntraidConfigError, match="reserved"):
        make_adapter(make_config([entry], settings=settings), router)

    assert router.requests == []


def test_collection_not_named_by_any_entry_is_not_read() -> None:
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com")])
    settings = {
        "collections": {
            "accounts": ACCOUNTS_COLLECTION,
            "security_groups": {"resource": "groups", "filter": "securityEnabled eq true", "advanced_query": True},
        }
    }
    adapter = make_adapter(make_config([accounts_entry("IdentityEmployee")], settings=settings), router)

    adapter.load()

    assert list(adapter.request_plan) == ["accounts"]
    assert router.paths() == ["/users"]
    assert set(loaded(adapter, IdentityEmployee)) == {graph_id(1)}


ENTRAID_LOGGER = "infrahub_sync.adapters.entraid"
GROUP_ODATA_TYPE = "#microsoft.graph.group"
DEVICE_ODATA_TYPE = "#microsoft.graph.device"
MEMBERS_FIELD = {"name": "members", "mapping": "members", "reference": "IdentityEmployee"}
GROUPS_WITH_MEMBERS_ENTRY = {**GROUPS_ENTRY, "fields": [*GROUP_FIELDS, MEMBERS_FIELD]}


def entraid_records(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    """The records of the adapter's logger at exactly `level`."""
    return [record for record in caplog.records if record.name == ENTRAID_LOGGER and record.levelno == level]


def member_paths(router: GraphRouter) -> list[str]:
    """The paths of the member requests, in order."""
    return [path for path in router.paths() if path.endswith(("/members", "/transitiveMembers"))]


@pytest.mark.parametrize("membership", ["direct", "transitive"])
def test_membership_mode_reads_its_endpoint_and_keeps_only_users(membership: str) -> None:
    transitive = membership == "transitive"
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com"), user_record(2, "bob@example.com")])
    router.add_records("/groups", [group_record(11, "network-engineers"), group_record(12, "network-admins")])
    router.add_members(
        graph_id(11),
        [
            member_record(graph_id(2)),
            member_record(graph_id(12), GROUP_ODATA_TYPE),
            member_record(graph_id(31), DEVICE_ODATA_TYPE),
            member_record(graph_id(1)),
        ],
        transitive=transitive,
    )
    router.add_members(graph_id(12), [member_record(graph_id(1))], transitive=transitive)
    config = make_config([USERS_ENTRY, GROUPS_WITH_MEMBERS_ENTRY], settings={"membership": membership})
    adapter = make_adapter(config, router)

    adapter.load()

    expected_paths = [router.members_path(graph_id(number), transitive=transitive) for number in (11, 12)]
    assert sorted(member_paths(router)) == expected_paths
    assert all(router.requests_for(path)[0].url.params["$select"] == "id" for path in expected_paths)
    groups = loaded(adapter, IdentityGroup)
    assert groups[graph_id(11)].members == [graph_id(1), graph_id(2)]
    assert groups[graph_id(12)].members == [graph_id(1)]


def test_user_reached_twice_appears_once() -> None:
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com"), user_record(2, "bob@example.com")])
    router.add_records("/groups", [group_record(11, "network-engineers")])
    # Transitive members list a user once per nested group that contains it; the pages split the duplicates.
    router.add_pages(
        router.members_path(graph_id(11), transitive=True),
        [
            [member_record(graph_id(1)), member_record(graph_id(2))],
            [member_record(graph_id(1))],
        ],
    )
    config = make_config([USERS_ENTRY, GROUPS_WITH_MEMBERS_ENTRY], settings={"membership": "transitive"})
    adapter = make_adapter(config, router)

    adapter.load()

    assert loaded(adapter, IdentityGroup)[graph_id(11)].members == [graph_id(1), graph_id(2)]


def test_members_not_loaded_are_dropped_with_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    router = GraphRouter()
    router.add_records(
        "/users",
        [
            user_record(1, "alice@example.com"),
            user_record(2, "svc-backup@example.com"),
            user_record(3, "ext-contractor@example.com"),
        ],
    )
    router.add_records("/groups", [group_record(11, "network-engineers"), group_record(12, "contractors")])
    # User 2 loads into another users kind; user 3 matches neither kind's filter, so no kind loads it.
    router.add_members(
        graph_id(11), [member_record(graph_id(1)), member_record(graph_id(2)), member_record(graph_id(3))]
    )
    router.add_members(graph_id(12), [member_record(graph_id(3))])
    config = make_config(
        [
            accounts_entry("IdentityEmployee", "^(?!svc-|ext-)"),
            accounts_entry("IdentityServiceAccount", "^svc-"),
            GROUPS_WITH_MEMBERS_ENTRY,
        ],
        settings=ACCOUNTS_SETTINGS,
    )
    adapter = make_adapter(config, router)
    caplog.set_level(logging.WARNING, logger=ENTRAID_LOGGER)

    adapter.load()

    groups = loaded(adapter, IdentityGroup)
    assert groups[graph_id(11)].members == [graph_id(1)]
    assert groups[graph_id(12)].members == []
    assert set(loaded(adapter, IdentityServiceAccount)) == {graph_id(2)}
    warnings = entraid_records(caplog, logging.WARNING)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "IdentityGroup" in message
    assert "members" in message
    assert "dropped=3" in message


def test_no_member_requests_when_no_entry_maps_members() -> None:
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com")])
    router.add_records("/groups", [group_record(11, "network-engineers")])
    router.add_members(graph_id(11), [member_record(graph_id(1))])
    adapter = make_adapter(make_config([USERS_ENTRY, GROUPS_ENTRY]), router)

    adapter.load()

    assert not adapter.request_plan["groups"].needs_members
    assert member_paths(router) == []
    assert sorted(router.paths()) == ["/groups", "/users"]


def test_summary_log_counts_member_requests(caplog: pytest.LogCaptureFixture) -> None:
    router = GraphRouter()
    router.add_records("/users", [user_record(1, "alice@example.com")])
    router.add_records("/groups", [group_record(11, "network-engineers"), group_record(12, "network-admins")])
    router.add_pages(router.members_path(graph_id(11)), [[member_record(graph_id(1))], []])
    router.add_members(graph_id(12), [member_record(graph_id(1))])
    adapter = make_adapter(make_config([USERS_ENTRY, GROUPS_WITH_MEMBERS_ENTRY]), router)
    caplog.set_level(logging.INFO, logger=ENTRAID_LOGGER)

    adapter.load()

    summaries = [
        record.getMessage()
        for record in entraid_records(caplog, logging.INFO)
        if "graph reads finished" in record.getMessage()
    ]
    assert len(summaries) == 1
    assert "member_requests=3" in summaries[0]
    assert "requests=5" in summaries[0]


def test_members_mapping_without_reference_is_refused_naming_the_field() -> None:
    router = GraphRouter()
    member_users = {"name": "member_users", "mapping": "members"}
    entry = {**GROUPS_ENTRY, "fields": [*GROUP_FIELDS, member_users]}

    with pytest.raises(EntraidConfigError, match="reference") as excinfo:
        make_adapter(make_config([USERS_ENTRY, entry]), router)

    assert "'member_users'" in str(excinfo.value)
    assert "IdentityGroup" in str(excinfo.value)
    assert router.requests == []
