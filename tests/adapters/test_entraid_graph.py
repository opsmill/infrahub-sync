"""Tests of `EntraidGraphClient` against a mocked Microsoft Graph: paging and query parameters."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("msgraph")

from infrahub_sync.adapters.entraid import Collection, EntraidGraphClient, EntraidSettings
from tests.adapters.entraid_fakes import ENTRAID_ENV, FakeCredential, GraphRouter, group_record, user_record

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def make_settings(**raw: object) -> EntraidSettings:
    return EntraidSettings.from_settings(raw, environ=ENTRAID_ENV)


def list_collection(
    router: GraphRouter,
    collection: Collection,
    select: Sequence[str] = ("id", "displayName"),
    settings: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with EntraidGraphClient(
            make_settings(**(settings or {})), transport=router.transport, credential=FakeCredential()
        ) as graph:
            return await graph.list_collection(collection, select)

    return asyncio.run(read())


def sample_records(resource: str, count: int) -> list[dict[str, Any]]:
    if resource == "users":
        return [user_record(number, f"user{number}@example.com") for number in range(1, count + 1)]
    return [group_record(number, f"group-{number}") for number in range(1, count + 1)]


@pytest.mark.parametrize("resource", ["users", "groups"])
def test_paging_three_pages_returns_every_record_in_order(resource: str) -> None:
    records = sample_records(resource, 5)
    router = GraphRouter()
    router.add_pages(f"/{resource}", [records[:2], records[2:4], records[4:]])

    result = list_collection(router, Collection(name=resource, resource=resource))

    assert result == records
    assert len(router.requests_for(f"/{resource}")) == 3


def test_paging_follows_next_link_of_empty_page() -> None:
    records = sample_records("users", 2)
    router = GraphRouter()
    router.add_pages("/users", [[], records])

    result = list_collection(router, Collection(name="users", resource="users"))

    assert result == records
    assert len(router.requests_for("/users")) == 2


def test_empty_result_returns_empty_list() -> None:
    router = GraphRouter()
    router.add_pages("/groups", [[]])

    result = list_collection(router, Collection(name="groups", resource="groups"))

    assert result == []
    assert len(router.requests_for("/groups")) == 1


def test_first_request_carries_select_and_top_page_size() -> None:
    router = GraphRouter()
    router.add_records("/users", sample_records("users", 1))

    list_collection(
        router,
        Collection(name="users", resource="users"),
        select=["id", "displayName", "userPrincipalName"],
        settings={"page_size": 25},
    )

    params = router.requests_for("/users")[0].url.params
    assert params["$select"].split(",") == ["id", "displayName", "userPrincipalName"]
    assert params["$top"] == "25"


@pytest.mark.parametrize(
    ("resource", "query_filter"),
    [
        ("users", "accountEnabled eq true"),
        ("groups", "securityEnabled eq true and NOT groupTypes/any(c:c eq 'Unified')"),
    ],
)
def test_filter_sent_unchanged_without_consistency_level_or_count(resource: str, query_filter: str) -> None:
    router = GraphRouter()
    router.add_records(f"/{resource}", sample_records(resource, 1))

    list_collection(router, Collection(name="filtered", resource=resource, filter=query_filter))

    request = router.requests_for(f"/{resource}")[0]
    assert request.url.params["$filter"] == query_filter
    assert "$count" not in request.url.params
    assert "ConsistencyLevel" not in request.headers


def test_advanced_query_sends_count_and_consistency_level_on_every_page() -> None:
    query_filter = "securityEnabled eq true and NOT groupTypes/any(c:c eq 'Unified')"
    records = sample_records("groups", 3)
    router = GraphRouter()
    router.add_pages("/groups", [records[:1], records[1:2], records[2:]])

    result = list_collection(
        router, Collection(name="security_groups", resource="groups", filter=query_filter, advanced_query=True)
    )

    assert result == records
    requests = router.requests_for("/groups")
    assert len(requests) == 3
    for request in requests:
        assert request.url.params["$count"] == "true"
        assert request.url.params["$filter"] == query_filter
        assert request.headers["ConsistencyLevel"] == "eventual"
