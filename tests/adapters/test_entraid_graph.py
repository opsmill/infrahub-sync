"""Tests of `EntraidGraphClient` against a mocked Microsoft Graph: paging, query parameters, retries and errors."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("msgraph")

from infrahub_sync.adapters import entraid
from infrahub_sync.adapters.entraid import (
    FALLBACK_RETRY_SECONDS,
    MAX_RETRY_WAIT_SECONDS,
    Collection,
    EntraidAuthenticationError,
    EntraidGraphClient,
    EntraidGraphError,
    EntraidPermissionError,
    EntraidSettings,
)
from tests.adapters.entraid_fakes import (
    ENTRAID_ENV,
    TOKEN,
    FakeCredential,
    GraphRouter,
    SleepRecorder,
    group_record,
    user_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

USERS = Collection(name="users", resource="users")


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> SleepRecorder:
    """Record retry waits instead of sleeping."""
    recorder = SleepRecorder()
    monkeypatch.setattr(entraid, "_sleep", recorder)
    return recorder


def make_settings(**raw: object) -> EntraidSettings:
    return EntraidSettings.from_settings(raw, environ=ENTRAID_ENV)


def list_collection(
    router: GraphRouter,
    collection: Collection,
    select: Sequence[str] = ("id", "displayName"),
    settings: Mapping[str, Any] | None = None,
    credential: FakeCredential | None = None,
) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with EntraidGraphClient(
            make_settings(**(settings or {})), transport=router.transport, credential=credential or FakeCredential()
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


def test_retry_after_429_waits_the_header_value_then_succeeds(sleeps: SleepRecorder) -> None:
    records = sample_records("users", 2)
    router = GraphRouter()
    router.add_error("/users", 429, headers={"Retry-After": "2"})
    router.add_records("/users", records)

    result = list_collection(router, USERS)

    assert result == records
    assert sleeps.waits == [2.0]
    assert len(router.requests_for("/users")) == 2


def test_retry_after_503_without_header_waits_the_fallback(sleeps: SleepRecorder) -> None:
    router = GraphRouter()
    router.add_error("/users", 503)

    list_collection(router, USERS)

    assert sleeps.waits == [FALLBACK_RETRY_SECONDS]
    assert len(router.requests_for("/users")) == 2


def test_retry_after_above_the_cap_waits_the_cap(sleeps: SleepRecorder) -> None:
    router = GraphRouter()
    router.add_error("/users", 429, headers={"Retry-After": str(int(MAX_RETRY_WAIT_SECONDS) * 10)})

    list_collection(router, USERS)

    assert sleeps.waits == [MAX_RETRY_WAIT_SECONDS]


@pytest.mark.parametrize("max_retries", [0, 2])
def test_retry_429_on_every_attempt_raises_after_max_retries(sleeps: SleepRecorder, max_retries: int) -> None:
    router = GraphRouter()
    for _ in range(max_retries + 1):
        router.add_error("/users", 429, headers={"Retry-After": "1"})

    with pytest.raises(EntraidGraphError) as excinfo:
        list_collection(router, USERS, settings={"max_retries": max_retries})

    assert type(excinfo.value) is EntraidGraphError
    assert excinfo.value.status == 429
    assert "429" in str(excinfo.value)
    assert "/users" in str(excinfo.value)
    assert len(router.requests_for("/users")) == max_retries + 1
    assert sleeps.waits == [1.0] * max_retries


@pytest.mark.parametrize("status", [401, 403])
def test_permission_status_raises_permission_error_naming_the_permissions(sleeps: SleepRecorder, status: int) -> None:
    router = GraphRouter()
    router.add_error("/users", status, code="Authorization_RequestDenied")

    with pytest.raises(EntraidPermissionError) as excinfo:
        list_collection(router, USERS)

    message = str(excinfo.value)
    for permission in ("User.Read.All", "Group.Read.All", "GroupMember.Read.All"):
        assert permission in message
    assert str(status) in message
    assert excinfo.value.status == status
    assert excinfo.value.code == "Authorization_RequestDenied"
    assert len(router.requests_for("/users")) == 1
    assert sleeps.waits == []


def test_timeout_raises_graph_error_naming_the_path(sleeps: SleepRecorder) -> None:
    router = GraphRouter()
    router.add_timeout("/users")

    with pytest.raises(EntraidGraphError) as excinfo:
        list_collection(router, USERS, settings={"timeout": 7})

    message = str(excinfo.value)
    assert excinfo.value.path is not None
    assert excinfo.value.path.split("?", 1)[0].endswith("/users")
    assert excinfo.value.path in message
    assert "7 seconds" in message
    assert "timeout" in message
    assert len(router.requests_for("/users")) == 1
    assert sleeps.waits == []


@pytest.mark.parametrize("auth", ["client_secret", "default"])
def test_credential_failure_raises_authentication_error_naming_the_auth_mode(auth: str) -> None:
    router = GraphRouter()

    with pytest.raises(EntraidAuthenticationError) as excinfo:
        list_collection(router, USERS, settings={"auth": auth}, credential=FakeCredential(fail=True))

    assert f"auth '{auth}'" in str(excinfo.value)
    assert router.requests == []


def queue_throttling(router: GraphRouter) -> dict[str, Any]:
    for _ in range(2):
        router.add_error("/users", 429)
    return {"settings": {"max_retries": 1}}


def queue_permission_error(router: GraphRouter) -> dict[str, Any]:
    router.add_error("/users", 403)
    return {}


def queue_server_error(router: GraphRouter) -> dict[str, Any]:
    router.add_error("/users", 500)
    return {}


def queue_timeout(router: GraphRouter) -> dict[str, Any]:
    router.add_timeout("/users")
    return {}


def refuse_token(router: GraphRouter) -> dict[str, Any]:  # noqa: ARG001 - same signature as the other scenarios
    return {"credential": FakeCredential(fail=True)}


@pytest.mark.parametrize(
    "scenario", [queue_throttling, queue_permission_error, queue_server_error, queue_timeout, refuse_token]
)
def test_error_messages_and_logs_never_contain_the_token(
    sleeps: SleepRecorder,  # noqa: ARG001 - the fixture only replaces the retry wait
    caplog: pytest.LogCaptureFixture,
    scenario: Callable[[GraphRouter], dict[str, Any]],
) -> None:
    router = GraphRouter()
    options = scenario(router)

    with caplog.at_level(logging.DEBUG), pytest.raises(EntraidGraphError) as excinfo:
        list_collection(router, USERS, **options)

    # The token did travel in the Authorization header whenever a request was sent, so its absence below is meaningful.
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in router.requests)
    assert TOKEN not in str(excinfo.value)
    assert TOKEN not in repr(excinfo.value)
    assert TOKEN not in (excinfo.value.path or "")
    assert TOKEN not in caplog.text
