"""Tests of `MsgraphClient` against a mocked Microsoft Graph: paging, query parameters, retries and errors."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import TYPE_CHECKING, Any

import httpx
import pytest

pytest.importorskip("msgraph")

from azure.core.exceptions import AzureError, ServiceRequestError, ServiceResponseError  # ty: ignore[unresolved-import]

from infrahub_sync.adapters import msgraphsync
from infrahub_sync.adapters.msgraphsync import (
    FALLBACK_RETRY_SECONDS,
    HIDDEN_MEMBERSHIP_PERMISSION,
    MAX_BACKOFF_SECONDS,
    MAX_RETRY_WAIT_SECONDS,
    Collection,
    MsgraphAuthenticationError,
    MsgraphClient,
    MsgraphPermissionError,
    MsgraphRequestError,
    MsgraphSettings,
)
from tests.adapters.msgraphsync_fakes import (
    CLIENT_SECRET,
    MSGRAPH_ENV,
    TOKEN,
    FakeCredential,
    GraphRouter,
    SleepRecorder,
    graph_id,
    group_record,
    user_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

USERS = Collection(name="users", resource="users")
US_GRAPH_URL = "https://graph.microsoft.us/v1.0"


@pytest.fixture(name="sleeps")
def record_sleeps(monkeypatch: pytest.MonkeyPatch) -> SleepRecorder:
    """Record retry waits instead of sleeping."""
    recorder = SleepRecorder()
    monkeypatch.setattr(msgraphsync, "_sleep", recorder)
    return recorder


@pytest.fixture(autouse=True)
def maximum_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make retry waits deterministic at their upper bound."""
    monkeypatch.setattr(msgraphsync.random, "uniform", lambda _low, high: high)


def make_settings(**raw: object) -> MsgraphSettings:
    return MsgraphSettings.from_settings(raw, environ=MSGRAPH_ENV)


def list_collection(
    router: GraphRouter,
    collection: Collection,
    select: Sequence[str] = ("id", "displayName"),
    settings: Mapping[str, Any] | None = None,
    credential: FakeCredential | None = None,
) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with MsgraphClient(
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


@pytest.mark.parametrize(
    "error_cls", [httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.PoolTimeout]
)
@pytest.mark.parametrize("member", [False, True])
def test_transport_error_then_success(
    sleeps: SleepRecorder, error_cls: type[httpx.RequestError], *, member: bool
) -> None:
    router = GraphRouter()
    path = GraphRouter.members_path(graph_id(11)) if member else "/users"
    records = sample_records("users", 1)
    router.add_request_error(path, error_cls)
    router.add_records(path, records)

    async def read() -> None:
        async with MsgraphClient(make_settings(), transport=router.transport, credential=FakeCredential()) as graph:
            if member:
                assert await graph.user_member_ids(graph_id(11), transitive=False) == [records[0]["id"]]
            else:
                assert await graph.list_collection(USERS, ["id"]) == records
            assert graph.retries == 1
            assert graph.requests == 2
            assert graph.member_requests == (2 if member else 0)

    asyncio.run(read())
    assert len(sleeps.waits) == 1
    requests = router.requests_for(path)
    assert requests[0].url == requests[1].url


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


def test_retry_after_is_honored_in_full_up_to_the_limit(sleeps: SleepRecorder) -> None:
    router = GraphRouter()
    router.add_error("/users", 429, headers={"Retry-After": str(int(MAX_RETRY_WAIT_SECONDS))})

    list_collection(router, USERS)

    assert sleeps.waits == [MAX_RETRY_WAIT_SECONDS]


def test_retry_after_above_the_limit_stops_without_retrying_early(sleeps: SleepRecorder) -> None:
    wait = int(MAX_RETRY_WAIT_SECONDS) * 3
    router = GraphRouter()
    router.add_error("/users", 429, headers={"Retry-After": str(wait)})

    with pytest.raises(MsgraphRequestError) as excinfo:
        list_collection(router, USERS)

    assert excinfo.value.status == 429
    assert str(wait) in str(excinfo.value)
    assert "/users" in str(excinfo.value)
    assert sleeps.waits == []
    assert len(router.requests_for("/users")) == 1


def test_retry_without_header_backs_off_exponentially(sleeps: SleepRecorder) -> None:
    router = GraphRouter()
    for _ in range(5):
        router.add_error("/users", 503)

    list_collection(router, USERS)

    assert sleeps.waits == [5.0, 10.0, 20.0, 40.0, MAX_BACKOFF_SECONDS]
    assert len(router.requests_for("/users")) == 6


@pytest.mark.parametrize("status", [502, 504])
def test_retry_on_gateway_errors(sleeps: SleepRecorder, status: int) -> None:
    router = GraphRouter()
    router.add_error("/users", status)

    list_collection(router, USERS)

    assert sleeps.waits == [FALLBACK_RETRY_SECONDS]
    assert len(router.requests_for("/users")) == 2


@pytest.mark.parametrize("max_retries", [0, 2])
def test_retry_429_on_every_attempt_raises_after_max_retries(sleeps: SleepRecorder, max_retries: int) -> None:
    router = GraphRouter()
    for _ in range(max_retries + 1):
        router.add_error("/users", 429, headers={"Retry-After": "1"})

    with pytest.raises(MsgraphRequestError) as excinfo:
        list_collection(router, USERS, settings={"max_retries": max_retries})

    assert type(excinfo.value) is MsgraphRequestError
    assert excinfo.value.status == 429
    assert "429" in str(excinfo.value)
    assert "/users" in str(excinfo.value)
    assert len(router.requests_for("/users")) == max_retries + 1
    assert sleeps.waits == [1.0] * max_retries


@pytest.mark.parametrize("status", [401, 403])
def test_permission_status_raises_permission_error_naming_the_permissions(sleeps: SleepRecorder, status: int) -> None:
    router = GraphRouter()
    router.add_error("/users", status, code="Authorization_RequestDenied")

    with pytest.raises(MsgraphPermissionError) as excinfo:
        list_collection(router, USERS)

    message = str(excinfo.value)
    for permission in ("User.Read.All", "Group.Read.All", "GroupMember.Read.All"):
        assert permission in message
    assert str(status) in message
    assert excinfo.value.status == status
    assert excinfo.value.code == "Authorization_RequestDenied"
    assert len(router.requests_for("/users")) == 1
    assert sleeps.waits == []
    assert HIDDEN_MEMBERSHIP_PERMISSION not in message


def test_member_read_refused_names_the_hidden_membership_permission(sleeps: SleepRecorder) -> None:
    group_path = GraphRouter.members_path(graph_id(11))
    router = GraphRouter()
    router.add_error(group_path, 403, code="Authorization_RequestDenied")

    with pytest.raises(MsgraphPermissionError) as excinfo:
        read_members(router, graph_id(11))

    assert HIDDEN_MEMBERSHIP_PERMISSION in str(excinfo.value)
    assert sleeps.waits == []


@pytest.mark.parametrize("max_retries", [0, 2, 5])
def test_timeout_raises_graph_error_naming_the_path(sleeps: SleepRecorder, max_retries: int) -> None:
    router = GraphRouter()
    for _ in range(max_retries + 1):
        router.add_timeout("/users")

    with pytest.raises(MsgraphRequestError) as excinfo:
        list_collection(router, USERS, settings={"timeout": 7, "max_retries": max_retries})

    message = str(excinfo.value)
    assert excinfo.value.path is not None
    assert excinfo.value.path.split("?", 1)[0].endswith("/users")
    assert excinfo.value.path in message
    assert "7 seconds" in message
    assert "timeout" in message
    assert f"after {max_retries} retries" in message
    assert len(router.requests_for("/users")) == max_retries + 1
    assert len(sleeps.waits) == max_retries


@pytest.mark.parametrize("transport_last", [False, True])
def test_status_and_transport_errors_share_retry_budget(sleeps: SleepRecorder, *, transport_last: bool) -> None:
    router = GraphRouter()
    if transport_last:
        router.add_error("/users", 503)
        router.add_timeout("/users")
    else:
        router.add_timeout("/users")
        router.add_error("/users", 503)

    async def read() -> None:
        async with MsgraphClient(
            make_settings(max_retries=1), transport=router.transport, credential=FakeCredential()
        ) as graph:
            with pytest.raises(MsgraphRequestError, match="after 1 retries"):
                await graph.list_collection(USERS, ["id"])
            assert graph.retries == 1
            assert graph.requests == 2

    asyncio.run(read())
    assert sleeps.waits == [FALLBACK_RETRY_SECONDS]


def test_certificate_verification_error_is_not_retried(sleeps: SleepRecorder) -> None:
    router = GraphRouter()
    certificate_error = ssl.SSLCertVerificationError("untrusted certificate")
    wrapped = OSError("TLS handshake failed")
    wrapped.__cause__ = certificate_error
    router.add_request_error("/users", httpx.ConnectError, cause=wrapped)

    with pytest.raises(MsgraphRequestError, match="ConnectError after 0 retries"):
        list_collection(router, USERS)

    assert len(router.requests) == 1
    assert sleeps.waits == []


def test_retry_jitter_has_one_second_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(msgraphsync.random, "uniform", lambda low, _high: low)
    assert msgraphsync._retry_wait(None, 1) == pytest.approx(1.0)


AUTH_GUIDANCE = {
    "client_secret": ["MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET"],
    "default": ["Azure CLI login (az login)", "DefaultAzureCredential"],
}


@pytest.mark.parametrize("auth", ["client_secret", "default"])
def test_credential_failure_raises_authentication_error_naming_the_auth_mode(auth: str) -> None:
    router = GraphRouter()

    with pytest.raises(MsgraphAuthenticationError) as excinfo:
        list_collection(router, USERS, settings={"auth": auth}, credential=FakeCredential(fail=True))

    message = str(excinfo.value)
    assert f"auth '{auth}'" in message
    for guidance in AUTH_GUIDANCE[auth]:
        assert guidance in message
    assert CLIENT_SECRET not in message
    assert CLIENT_SECRET not in repr(excinfo.value)
    assert router.requests == []


@pytest.mark.parametrize("error_cls", [ServiceRequestError, ServiceResponseError])
@pytest.mark.parametrize("auth", ["client_secret", "default"])
def test_unreachable_token_endpoint_raises_authentication_error(auth: str, error_cls: type[AzureError]) -> None:
    router = GraphRouter()
    credential = FakeCredential(error=error_cls("fake: connection refused by the token endpoint"))

    with pytest.raises(MsgraphAuthenticationError) as excinfo:
        list_collection(router, USERS, settings={"auth": auth}, credential=credential)

    message = str(excinfo.value)
    assert f"auth '{auth}'" in message
    assert "token endpoint could not be reached or refused the request" in message
    assert error_cls.__name__ in message
    for guidance in AUTH_GUIDANCE[auth]:
        assert guidance in message
    assert CLIENT_SECRET not in message
    assert isinstance(excinfo.value.__cause__, error_cls)
    assert router.requests == []


@pytest.mark.parametrize(
    "error_cls", [httpx.DecodingError, httpx.TooManyRedirects, httpx.UnsupportedProtocol, httpx.InvalidURL]
)
def test_request_error_after_the_response_raises_graph_error_naming_the_path(
    sleeps: SleepRecorder, error_cls: type[httpx.RequestError | httpx.InvalidURL]
) -> None:
    router = GraphRouter()
    router.add_request_error("/users", error_cls)

    with pytest.raises(MsgraphRequestError) as excinfo:
        list_collection(router, USERS)

    assert type(excinfo.value) is MsgraphRequestError
    assert excinfo.value.path is not None
    assert excinfo.value.path.split("?", 1)[0].endswith("/users")
    assert excinfo.value.path in str(excinfo.value)
    assert error_cls.__name__ in str(excinfo.value)
    assert sleeps.waits == []


def read_members(router: GraphRouter, group_id: str) -> list[str]:
    async def read() -> list[str]:
        async with MsgraphClient(make_settings(), transport=router.transport, credential=FakeCredential()) as graph:
            return await graph.user_member_ids(group_id, transitive=False)

    return asyncio.run(read())


GROUP_ID = graph_id(11)
MEMBERS_PATH = GraphRouter.members_path(GROUP_ID)


def read_users(router: GraphRouter) -> list[Any]:
    return list_collection(router, USERS)


def read_group_members(router: GraphRouter) -> list[Any]:
    return read_members(router, GROUP_ID)


@pytest.mark.parametrize(
    ("path", "read"),
    [
        pytest.param("/users", read_users, id="collection-read"),
        pytest.param(MEMBERS_PATH, read_group_members, id="member-read"),
    ],
)
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(b"{}", "without a 'value' list", id="object-without-value"),
        pytest.param(b'{"value": null}', "without a 'value' list", id="value-null"),
        pytest.param(b"", "empty body", id="empty-body"),
    ],
)
def test_page_without_value_list_raises_instead_of_reading_zero_records(
    path: str, read: Callable[[GraphRouter], list[Any]], body: bytes, expected: str
) -> None:
    router = GraphRouter()
    router.add_body(path, body)

    with pytest.raises(MsgraphRequestError) as excinfo:
        read(router)

    assert type(excinfo.value) is MsgraphRequestError
    assert expected in str(excinfo.value)
    assert excinfo.value.path is not None
    assert excinfo.value.path.split("?", 1)[0].endswith(path)
    assert excinfo.value.path in str(excinfo.value)


@pytest.mark.parametrize(
    ("path", "read"),
    [
        pytest.param("/users", read_users, id="collection-read"),
        pytest.param(MEMBERS_PATH, read_group_members, id="member-read"),
    ],
)
def test_page_with_empty_value_list_reads_zero_records(path: str, read: Callable[[GraphRouter], list[Any]]) -> None:
    router = GraphRouter()
    router.add_body(path, b'{"value": []}')

    assert read(router) == []
    assert len(router.requests_for(path)) == 1


def test_graph_url_of_another_cloud_sets_the_token_scope_and_the_request_host() -> None:
    router = GraphRouter(base_url=US_GRAPH_URL)
    router.add_records("/users", sample_records("users", 1))
    credential = FakeCredential()

    list_collection(router, USERS, settings={"graph_url": US_GRAPH_URL}, credential=credential)

    assert credential.scopes == [("https://graph.microsoft.us/.default",)]
    request = router.requests_for("/users")[0]
    assert request.url.host == "graph.microsoft.us"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_next_link_to_another_host_is_sent_without_the_token() -> None:
    first, second = sample_records("users", 2)
    other_next_link = "https://other.example.com/v1.0/users?$skiptoken=1"
    router = GraphRouter()
    router.add_body("/users", json.dumps({"value": [first], "@odata.nextLink": other_next_link}).encode())
    router.add_body("/users", json.dumps({"value": [second]}).encode())
    credential = FakeCredential()

    result = list_collection(router, USERS, credential=credential)

    assert result == [first, second]
    graph_request, other_request = router.requests
    assert graph_request.url.host == "graph.microsoft.com"
    assert graph_request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert other_request.url.host == "other.example.com"
    assert "Authorization" not in other_request.headers
    assert len(credential.scopes) == 1


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
    return {"settings": {"max_retries": 0}}


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

    with caplog.at_level(logging.DEBUG), pytest.raises(MsgraphRequestError) as excinfo:
        list_collection(router, USERS, **options)

    # The token did travel in the Authorization header whenever a request was sent, so its absence below is meaningful.
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in router.requests)
    assert TOKEN not in str(excinfo.value)
    assert TOKEN not in repr(excinfo.value)
    assert TOKEN not in (excinfo.value.path or "")
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("membership", ["direct", "transitive"])
@pytest.mark.parametrize(
    ("query_filter", "advanced_query"),
    [
        (None, False),
        (None, True),
        ("startswith(userPrincipalName,'room')", False),
        ("startswith(userPrincipalName,'room')", True),
    ],
)
def test_member_of_reads_user_cast_with_paging_and_query_options(
    membership: str, query_filter: str | None, *, advanced_query: bool
) -> None:
    # Graph requires $count and ConsistencyLevel: eventual for an OData cast, so every member_of read sends them.
    router = GraphRouter()
    path = router.members_path(graph_id(11), transitive=membership == "transitive") + "/microsoft.graph.user"
    records = [user_record(1, "room1@example.com"), user_record(2, "room2@example.com")]
    router.add_pages(path, [[records[0]], [records[1]]])
    collection = Collection(
        name="room_accounts",
        resource="users",
        member_of=graph_id(11),
        filter=query_filter,
        advanced_query=advanced_query,
    )

    result = list_collection(
        router, collection, select=["id", "userPrincipalName"], settings={"membership": membership, "page_size": 25}
    )

    assert result == records
    assert router.paths() == [path, path]
    for request in router.requests:
        assert request.url.params["$select"] == "id,userPrincipalName"
        assert request.url.params["$top"] == "25"
        assert request.url.params.get("$filter") == query_filter
        assert request.url.params.get("$count") == "true"
        assert request.headers.get("ConsistencyLevel") == "eventual"


@pytest.mark.parametrize("page", [0, 1])
def test_missing_member_of_group_stops_the_collection_read(page: int) -> None:
    router = GraphRouter()
    path = router.members_path(graph_id(11)) + "/microsoft.graph.user"
    if page:
        router.add_body(
            path,
            json.dumps(
                {
                    "value": [user_record(1, "room1@example.com")],
                    "@odata.nextLink": f"{router.base_url}{path}?$skiptoken=1",
                }
            ).encode(),
        )
    router.add_error(path, 404, code="Request_ResourceNotFound", message="Group does not exist.")

    with pytest.raises(MsgraphRequestError) as excinfo:
        list_collection(router, Collection(name="room_accounts", resource="users", member_of=graph_id(11)))

    assert excinfo.value.status == 404
    assert excinfo.value.code == "Request_ResourceNotFound"
    assert "room_accounts" in str(excinfo.value)
    assert graph_id(11) in str(excinfo.value)
    assert len(router.requests) == page + 1


def test_member_of_permission_error_explains_member_permissions() -> None:
    router = GraphRouter()
    path = router.members_path(graph_id(11)) + "/microsoft.graph.user"
    router.add_error(path, 403, code="Authorization_RequestDenied")

    with pytest.raises(MsgraphPermissionError) as excinfo:
        list_collection(router, Collection(name="room_accounts", resource="users", member_of=graph_id(11)))

    assert "GroupMember.Read.All" in str(excinfo.value)
    assert HIDDEN_MEMBERSHIP_PERMISSION in str(excinfo.value)
