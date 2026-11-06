"""Synchronous Sync HTTP client boundary tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from typing_extensions import override

from infrahub_sync.client import (
    APIError,
    ApplyRunRequest,
    CancelRunRequest,
    ClientInputError,
    ClientTimeoutError,
    CompatibilityError,
    ConfigMutationRequest,
    ConfigsAPIError,
    ConfigurationPage,
    ConfigurationVersionPage,
    CreateRunRequest,
    EventPage,
    EventResource,
    PrincipalResource,
    ProtocolError,
    RunLogLine,
    RunLogPage,
    RunPage,
    SyncClient,
    TransportError,
    VerifyRunRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

TOKEN = "x"  # noqa: S105 - one-character boundary canary.
NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc).isoformat()


def _version(**extra: object) -> dict[str, object]:
    return {"server_version": "3.0.0", "api_versions": ["v3-unstable"], "stability": "unstable", **extra}


def _run(run_id: str = "run-1") -> dict[str, object]:
    return {
        "run": {
            "run_id": run_id,
            "operation": "plan",
            "configuration_reference": "cfg@1",
            "config_id": "cfg",
            "registry_version": 1,
            "package_checksum": "a" * 64,
            "actor": "operator",
            "audit_links": [],
            "started_at": NOW,
            "finished_at": None,
            "phase": "accepted",
            "outcome": None,
            "summary": {},
            "results": {},
            "artifact_refs": [],
            "prefect_executions": [{"flow_run_id": "flow-1", "purpose": "plan", "attempt": 1}],
        },
        "orchestration": [
            {
                "flow_run_id": "flow-1",
                "purpose": "plan",
                "attempt": 1,
                "state": "pending",
                "detail_available": True,
                "unavailable_reason": None,
                "submitted_at": NOW,
                "claimed_at": None,
                "stalled_at": None,
                "cancellation_requested_at": None,
                "cancellation_recovery_deadline_at": None,
                "cancellation_acknowledged_at": None,
                "terminal_at": None,
                "terminal_state": None,
                "terminal_outcome": None,
            }
        ],
    }


def _plan_response() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "checksum": "a" * 64,
        "checksum_ok": True,
        "verification_notes": [],
        "summary": {
            "by_action": {"create": 1},
            "by_kind": {"Device": 1},
            "total": 1,
            "delete_operations_computed": True,
            "deletes_not_executed": 0,
        },
        "operations": [
            {
                "operation_id": "op-001",
                "action": "create",
                "kind": "Device",
                "identity": {"name": "edge-01"},
                "tier": 0,
                "payload": {"name": "edge-01"},
                "relationships": [],
            }
        ],
    }


def _config_version() -> dict[str, object]:
    return {
        "config_id": "cfg",
        "registry_version": 1,
        "package_checksum": "a" * 64,
        "declared_content": {"format_version": 1},
        "created_at": NOW,
    }


@pytest.mark.parametrize(
    ("url", "token", "timeout", "argument"),
    [
        ("relative", TOKEN, 1, "service_url"),
        ("ftp://example.test", TOKEN, 1, "service_url"),
        ("https://user@example.test", TOKEN, 1, "service_url"),
        ("https://example.test?token=secret", TOKEN, 1, "service_url"),
        ("https://example.test", "", 1, "token"),
        ("https://example.test", TOKEN, 0, "timeout"),
        ("https://example.test", TOKEN, float("inf"), "timeout"),
        ("https://example.test", TOKEN, True, "timeout"),
    ],
)
def test_constructor_closes_connection_inputs(url: str, token: str, timeout: object, argument: str) -> None:
    with pytest.raises(ClientInputError) as raised:
        SyncClient(url, token, timeout=timeout)  # ty: ignore[invalid-argument-type]
    assert raised.value.argument == argument
    assert TOKEN not in repr(raised.value)


def test_compatibility_precedes_auth_and_is_cached() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json=_version(future="readable"))
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    assert client.list_configs().items == ()
    assert client.list_configs().items == ()

    assert [request.url.path for request in requests] == ["/version", "/configs", "/configs"]
    assert "Authorization" not in requests[0].headers
    assert requests[1].headers["Authorization"] == f"Bearer {TOKEN}"
    assert client.get_version().model_extra == {"future": "readable"}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"error": {"code": "down"}}),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"server_version": "3", "api_versions": [], "stability": "unstable"}),
        httpx.Response(200, json={"server_version": "3", "api_versions": ["v4"], "stability": "stable"}),
    ],
)
def test_compatibility_refuses_before_protected_request(response: httpx.Response) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(CompatibilityError):
        client.list_configs()
    assert [request.url.path for request in requests] == ["/version"]
    assert "Authorization" not in requests[0].headers


def test_compatibility_error_preserves_only_safely_parsed_discovery_fields() -> None:
    response = httpx.Response(
        200,
        json={"server_version": "3.1", "api_versions": ["v4"], "stability": {"wrong": True}},
    )
    client = SyncClient(
        "https://example.test",
        TOKEN,
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(CompatibilityError) as raised:
        client.list_configs()

    assert raised.value.server_version == "3.1"
    assert raised.value.api_versions == ("v4",)


def test_all_route_methods_send_the_frozen_contract() -> None:
    requests: list[httpx.Request] = []
    version = _config_version()
    config = {"config_id": "cfg", "created_at": NOW}
    responses: dict[tuple[str, str], tuple[int, object]] = {
        ("GET", "/status"): (
            200,
            {"service": "ready", "worker": {"state": "unavailable", "detail_available": False, "observed_at": None}},
        ),
        ("POST", "/configs"): (201, {"configuration": config, "version": version}),
        ("POST", "/configs/cfg/versions"): (200, {"version": version, "created": False}),
        ("GET", "/configs"): (200, {"items": [config], "next_cursor": None}),
        ("GET", "/configs/cfg"): (200, config),
        ("GET", "/configs/cfg/versions"): (200, {"items": [version], "next_cursor": None}),
        ("GET", "/configs/cfg/versions/1"): (200, version),
        ("POST", "/configs/cfg/versions/1/validate?offset=2&limit=3"): (
            200,
            {
                **version,
                "destination_schema_fingerprint": None,
                "findings": [],
                "offset": 2,
                "limit": 3,
                "total_findings": 0,
                "next_offset": None,
            },
        ),
        ("POST", "/runs"): (202, _run()),
        ("GET", "/runs/run-1"): (200, _run()),
        ("GET", "/runs/run-1/plan"): (
            200,
            _plan_response(),
        ),
        ("GET", "/runs/run-1/results"): (200, {"run_id": "run-1", "results": {}}),
        ("GET", "/runs/run-1/artifacts"): (200, {"run_id": "run-1", "artifacts": []}),
        ("POST", "/runs/run-1/verify"): (202, _run()),
        ("POST", "/runs/run-1/apply"): (202, _run()),
        ("POST", "/runs/run-1/cancel"): (202, _run()),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        key = (request.method, request.url.raw_path.decode())
        status, body = responses[key]
        return httpx.Response(status, json=body)

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    mutation = ConfigMutationRequest(package={"format_version": 1}, reason="test")
    client.get_status()
    client.register_config(mutation, "key-1")
    client.create_config_version("cfg", mutation, "key-2")
    client.list_configs()
    client.get_config("cfg")
    client.list_config_versions("cfg")
    client.get_config_version("cfg", 1)
    client.validate_config("cfg", 1, offset=2, limit=3)
    client.create_run(CreateRunRequest(config_id="cfg", registry_version=1, reason="test"), "key-3")
    client.get_run("run-1")
    client.get_plan("run-1")
    client.get_results("run-1")
    client.list_artifacts("run-1")
    client.verify_run("run-1", VerifyRunRequest(reason="test"), "key-4")
    client.apply_run("run-1", ApplyRunRequest(expected_checksum="a" * 64, confirm_writes=True, reason="test"), "key-5")
    client.cancel_run("run-1", CancelRunRequest(reason="test"), "key-6")

    protected = [request for request in requests if request.url.path not in {"/version", "/status"}]
    assert all(request.headers["Authorization"] == f"Bearer {TOKEN}" for request in protected)
    assert [request.headers.get("Idempotency-Key") for request in requests if request.method == "POST"] == [
        "key-1",
        "key-2",
        None,
        "key-3",
        "key-4",
        "key-5",
        "key-6",
    ]
    assert json.loads(requests[9].content) == {
        "operation": "plan",
        "config_id": "cfg",
        "registry_version": 1,
        "branch": None,
        "confirm_writes": False,
        "reason": "test",
    }
    body_paths = {
        "/configs",
        "/configs/cfg/versions",
        "/configs/cfg/versions/1/validate?offset=2&limit=3",
        "/runs/run-1/verify",
        "/runs/run-1/apply",
        "/runs/run-1/cancel",
    }
    mutation_bodies = {
        request.url.raw_path.decode(): json.loads(request.content) if request.content else None
        for request in requests
        if request.method == "POST" and request.url.raw_path.decode() in body_paths
    }
    assert mutation_bodies == {
        "/configs": {"package": {"format_version": 1}, "reason": "test"},
        "/configs/cfg/versions": {"package": {"format_version": 1}, "reason": "test"},
        "/configs/cfg/versions/1/validate?offset=2&limit=3": None,
        "/runs/run-1/verify": {"reason": "test"},
        "/runs/run-1/apply": {
            "expected_checksum": "a" * 64,
            "confirm_writes": True,
            "branch": None,
            "reason": "test",
        },
        "/runs/run-1/cancel": {"reason": "test"},
    }


def test_get_results_parses_schema_drift_apply_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(
            200,
            json={
                "run_id": "run-1",
                "results": {
                    "apply_failure": {
                        "stage": "apply",
                        "outcome": "failed",
                        "error_type": "PlanSchemaChangedError",
                    }
                },
            },
        )

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))

    failure = cast("dict[str, object]", client.get_results("run-1").results["apply_failure"])

    assert failure["stage"] == "apply"
    assert failure["error_type"] == "PlanSchemaChangedError"


def test_register_config_403_retains_machine_fields_and_discards_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "configs-forbidden",
                    "message": "server-secret",
                    "status": 403,
                    "family": "authorization",
                    "reason": "administrator-required",
                    "mutation_id": "mutation-1",
                }
            },
        )

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    request = ConfigMutationRequest(package={"format_version": 1}, reason="test")

    with pytest.raises(ConfigsAPIError) as raised:
        client.register_config(request, "key-1")

    assert raised.value.status == 403
    assert raised.value.code == "configs-forbidden"
    assert raised.value.family == "authorization"
    assert raised.value.reason == "administrator-required"
    assert raised.value.mutation_id == "mutation-1"
    assert "server-secret" not in str(raised.value)
    assert "server-secret" not in repr(raised.value)
    assert TOKEN not in repr(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(307, headers={"Location": "https://other.test/configs"}), ProtocolError),
        (httpx.Response(200, content=b"not-json"), ProtocolError),
        (httpx.Response(200, json={"wrong": True}), ProtocolError),
        (httpx.Response(418, json={"error": {"code": "teapot", "message": "secret", "status": 418}}), ProtocolError),
        (
            httpx.Response(
                403,
                json={
                    "error": {
                        "code": "forbidden",
                        "message": "secret",
                        "status": 403,
                        "run_id": None,
                        "mutation_id": "m-1",
                    }
                },
            ),
            APIError,
        ),
        (
            httpx.Response(
                400,
                json={
                    "error": {
                        "code": "configs-request",
                        "message": "secret",
                        "status": 400,
                        "family": "request",
                        "reason": "invalid",
                    }
                },
            ),
            ConfigsAPIError,
        ),
    ],
)
def test_response_failures_are_typed_and_secret_safe(response: httpx.Response, error_type: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return response

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(error_type) as raised:
        client.list_configs()
    assert "secret" not in str(raised.value)
    assert TOKEN not in repr(raised.value)
    assert raised.value.__cause__ is None


def test_status_accepts_only_its_declared_success_status() -> None:
    response = httpx.Response(
        503,
        json={
            "error": {
                "code": "unavailable",
                "message": "secret",
                "status": 503,
                "run_id": None,
                "mutation_id": None,
            }
        },
    )
    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(lambda _request: response))

    with pytest.raises(ProtocolError):
        client.get_status()


def test_json_response_over_16_mib_is_a_protocol_error() -> None:
    oversized_json = b"[]" + (b" " * (16 * 1024 * 1024))
    assert json.loads(oversized_json) == []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, content=oversized_json, headers={"Content-Type": "application/json"})

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))

    with pytest.raises(ProtocolError) as raised:
        client.list_configs()
    assert raised.value.operation == "list_configs"
    assert raised.value.status == 200


def test_partial_execution_terminal_fields_are_a_protocol_error() -> None:
    response = _run()
    orchestration = cast("list[dict[str, object]]", response["orchestration"])
    execution = orchestration[0]
    execution["terminal_at"] = NOW
    execution["terminal_state"] = "failed"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=response)

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))

    with pytest.raises(ProtocolError) as raised:
        client.get_run("run-1")
    assert raised.value.operation == "get_run"
    assert raised.value.status == 200


def test_malformed_plan_summary_is_a_protocol_error() -> None:
    response = _plan_response()
    summary = cast("dict[str, object]", response["summary"])
    del summary["total"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=response)

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))

    with pytest.raises(ProtocolError) as raised:
        client.get_plan("run-1")
    assert raised.value.operation == "get_plan"
    assert raised.value.status == 200


def test_malformed_plan_operation_is_a_protocol_error() -> None:
    response = _plan_response()
    operations = cast("list[dict[str, object]]", response["operations"])
    del operations[0]["kind"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=response)

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))

    with pytest.raises(ProtocolError) as raised:
        client.get_plan("run-1")
    assert raised.value.operation == "get_plan"
    assert raised.value.status == 200


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [(httpx.ReadTimeout("secret"), ClientTimeoutError), (httpx.ConnectError("secret"), TransportError)],
)
def test_httpx_failures_do_not_cross_the_boundary(failure: Exception, error_type: type[Exception]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise failure

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(error_type) as raised:
        client.get_version()
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_close_failure_does_not_cross_the_boundary() -> None:
    class ClosingTransport(httpx.BaseTransport):
        @override
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_version(), request=request)

        @override
        def close(self) -> None:
            failure_message = "secret"
            raise RuntimeError(failure_message)

    client = SyncClient("https://example.test", TOKEN, transport=ClosingTransport())

    with pytest.raises(TransportError) as raised:
        client.close()
    assert raised.value.operation == "close"
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("digest_header", [None, "bad", "sha-256=" + "0" * 64])
def test_artifact_requires_a_matching_digest(digest_header: str | None) -> None:
    data = b"artifact"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        headers = {"Content-Type": "application/octet-stream"}
        if digest_header is not None:
            headers["Digest"] = digest_header
        return httpx.Response(200, content=data, headers=headers)

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(ProtocolError):
        client.get_artifact("run-1", "artifact-1")

    matching = f"sha-256={sha256(data).hexdigest()}"
    client = SyncClient(
        "https://example.test",
        TOKEN,
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json=_version())
                if request.url.path == "/version"
                else httpx.Response(
                    200, content=data, headers={"Content-Type": "application/octet-stream", "Digest": matching}
                )
            )
        ),
    )
    assert client.get_artifact("run-1", "artifact-1").data == data


def _public_run(run_id: str) -> dict[str, object]:
    run = dict(cast("dict[str, object]", _run(run_id)["run"]))
    run["destination_branch"] = "main"
    return run


def _list_client(pages: dict[str | None, object], requests: list[httpx.Request]) -> SyncClient:
    """Answer each list request with the page keyed by its ``cursor`` query parameter."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        requests.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    return SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))


def test_list_runs_sends_filters_limit_and_cursor_and_parses_the_page() -> None:
    requests: list[httpx.Request] = []
    client = _list_client({"c1": {"items": [_public_run("run-2")], "next_cursor": "c2"}}, requests)

    page = client.list_runs(config_id="cfg", registry_version=3, limit=10, cursor="c1")

    assert isinstance(page, RunPage)
    assert [run.run_id for run in page.items] == ["run-2"]
    assert page.items[0].destination_branch == "main"
    assert page.next_cursor == "c2"
    assert requests[0].url.path == "/runs"
    assert dict(requests[0].url.params) == {
        "config_id": "cfg",
        "registry_version": "3",
        "limit": "10",
        "cursor": "c1",
    }
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_list_runs_without_arguments_sends_no_query() -> None:
    requests: list[httpx.Request] = []
    client = _list_client({None: {"items": [], "next_cursor": None}}, requests)

    assert client.list_runs() == RunPage(items=(), next_cursor=None)
    assert requests[0].url.query == b""


def test_iter_runs_follows_next_cursor_until_null() -> None:
    requests: list[httpx.Request] = []
    pages: dict[str | None, object] = {
        None: {"items": [_public_run("run-3")], "next_cursor": "c1"},
        "c1": {"items": [_public_run("run-2")], "next_cursor": "c2"},
        "c2": {"items": [_public_run("run-1")], "next_cursor": None},
    }
    client = _list_client(pages, requests)

    runs = list(client.iter_runs(config_id="cfg", limit=1))

    assert [run.run_id for run in runs] == ["run-3", "run-2", "run-1"]
    assert [request.url.params.get("cursor") for request in requests] == [None, "c1", "c2"]
    assert all(request.url.params["config_id"] == "cfg" for request in requests)
    assert all(request.url.params["limit"] == "1" for request in requests)


def test_iteration_refuses_a_cursor_that_does_not_advance() -> None:
    requests: list[httpx.Request] = []
    pages: dict[str | None, object] = {
        None: {"items": [], "next_cursor": "c1"},
        "c1": {"items": [], "next_cursor": "c1"},
    }
    client = _list_client(pages, requests)

    with pytest.raises(ProtocolError) as raised:
        list(client.iter_runs())
    assert raised.value.operation == "list_runs"


@pytest.mark.parametrize(
    ("call", "argument"),
    [
        (lambda client: client.list_runs(config_id="bad id"), "config_id"),
        (lambda client: client.list_runs(config_id="cfg", registry_version=0), "registry_version"),
        (lambda client: client.list_runs(limit=0), "limit"),
        (lambda client: client.list_runs(limit=201), "limit"),
        (lambda client: client.list_configs(limit=201), "limit"),
        (lambda client: client.list_config_versions("cfg", limit=201), "limit"),
        (lambda client: client.list_runs(cursor=""), "cursor"),
        (lambda client: client.list_configs(name=""), "name"),
        (lambda client: client.list_config_versions("cfg", limit=True), "limit"),
    ],
)
def test_list_arguments_are_refused_before_any_request(call: Callable[[SyncClient], object], argument: str) -> None:
    requests: list[httpx.Request] = []
    client = _list_client({}, requests)

    with pytest.raises(ClientInputError) as raised:
        call(client)
    assert raised.value.argument == argument
    assert requests == []


def test_list_runs_bare_array_is_a_protocol_error() -> None:
    requests: list[httpx.Request] = []
    client = _list_client({None: [_public_run("run-1")]}, requests)

    with pytest.raises(ProtocolError) as raised:
        client.list_runs()
    assert raised.value.operation == "list_runs"


def test_me_parses_the_principal() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json={"actor": "operator", "administrator": True})

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))

    assert client.me() == PrincipalResource(actor="operator", administrator=True)
    assert [request.url.path for request in requests] == ["/version", "/me"]
    assert requests[1].headers["Authorization"] == f"Bearer {TOKEN}"


def test_list_configs_sends_name_limit_and_cursor_and_parses_the_page() -> None:
    requests: list[httpx.Request] = []
    config = {"config_id": "cfg", "created_at": NOW, "name": "from-netbox", "latest_version": 2}
    client = _list_client({"c1": {"items": [config], "next_cursor": None}}, requests)

    page = client.list_configs(name="from-netbox", limit=5, cursor="c1")

    assert isinstance(page, ConfigurationPage)
    assert page.items[0].name == "from-netbox"
    assert page.items[0].latest_version == 2
    assert page.next_cursor is None
    assert requests[0].url.path == "/configs"
    assert dict(requests[0].url.params) == {"name": "from-netbox", "limit": "5", "cursor": "c1"}


def test_iter_configs_follows_next_cursor_with_the_name_filter() -> None:
    requests: list[httpx.Request] = []
    pages: dict[str | None, object] = {
        None: {"items": [{"config_id": "a", "created_at": NOW, "name": "n"}], "next_cursor": "c1"},
        "c1": {"items": [{"config_id": "b", "created_at": NOW, "name": "n"}], "next_cursor": None},
    }
    client = _list_client(pages, requests)

    assert [config.config_id for config in client.iter_configs(name="n")] == ["a", "b"]
    assert all(request.url.params["name"] == "n" for request in requests)


def test_list_config_versions_sends_limit_and_cursor_and_parses_the_page() -> None:
    requests: list[httpx.Request] = []
    version = {
        "config_id": "cfg",
        "registry_version": 2,
        "package_checksum": "a" * 64,
        "created_at": NOW,
        "latest_run": {"run_id": "run-1", "phase": "planned", "outcome": None, "started_at": NOW},
        "latest_applied_run": None,
    }
    client = _list_client({"c1": {"items": [version], "next_cursor": "c2"}}, requests)

    page = client.list_config_versions("cfg", limit=1, cursor="c1")

    assert isinstance(page, ConfigurationVersionPage)
    assert page.items[0].latest_run is not None
    assert page.items[0].latest_run.run_id == "run-1"
    assert page.next_cursor == "c2"
    assert requests[0].url.path == "/configs/cfg/versions"
    assert dict(requests[0].url.params) == {"limit": "1", "cursor": "c1"}


def test_iter_config_versions_follows_next_cursor_until_null() -> None:
    requests: list[httpx.Request] = []

    def version(number: int) -> dict[str, object]:
        return {"config_id": "cfg", "registry_version": number, "package_checksum": "a" * 64, "created_at": NOW}

    pages: dict[str | None, object] = {
        None: {"items": [version(2)], "next_cursor": "c1"},
        "c1": {"items": [version(1)], "next_cursor": None},
    }
    client = _list_client(pages, requests)

    assert [item.registry_version for item in client.iter_config_versions("cfg")] == [2, 1]
    assert [request.url.params.get("cursor") for request in requests] == [None, "c1"]


def _event_item(event_id: str) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "infrahub_sync.run.planned",
        "occurred_at": NOW,
        "actor": "alice",
        "operation": "plan",
        "outcome": "succeeded",
        "reason": None,
        "primary_resource": {"kind": "run", "id": "run-1"},
        "related": [{"kind": "account", "id": "alice"}, {"kind": "branch", "id": "main"}],
    }


def _events_client(pages: dict[str, object], requests: list[httpx.Request]) -> SyncClient:
    """Answer each event-list request with the page keyed by its ``offset`` query parameter."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        requests.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("offset", "0")])

    return SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))


def test_list_events_sends_the_contract_query_and_parses_the_page() -> None:
    requests: list[httpx.Request] = []
    page_body = {"items": [_event_item("e-1")], "limit": 10, "offset": 5, "has_more": True}
    client = _events_client({"5": page_body}, requests)
    since = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    until = datetime(2026, 11, 6, 11, tzinfo=timezone.utc)

    page = client.list_events(
        run_id="run-1",
        config_id="cfg",
        event_types=("infrahub_sync.run.accepted", "infrahub_sync.run.planned"),
        related=(("account", "alice"), ("branch", "main")),
        since=since,
        until=until,
        limit=10,
        offset=5,
    )

    assert isinstance(page, EventPage)
    assert (page.limit, page.offset, page.has_more) == (10, 5, True)
    (event,) = page.items
    assert isinstance(event, EventResource)
    assert (event.event_id, event.event_type, event.occurred_at) == (
        "e-1",
        "infrahub_sync.run.planned",
        datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    assert event.primary_resource is not None
    assert (event.primary_resource.kind, event.primary_resource.id) == ("run", "run-1")
    assert [(item.kind, item.id) for item in event.related] == [("account", "alice"), ("branch", "main")]
    assert requests[0].url.path == "/events"
    assert requests[0].url.params.multi_items() == [
        ("run_id", "run-1"),
        ("config_id", "cfg"),
        ("event_type", "infrahub_sync.run.accepted"),
        ("event_type", "infrahub_sync.run.planned"),
        ("related", "account:alice"),
        ("related", "branch:main"),
        ("since", since.isoformat()),
        ("until", until.isoformat()),
        ("limit", "10"),
        ("offset", "5"),
    ]
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_list_events_without_arguments_sends_no_query() -> None:
    requests: list[httpx.Request] = []
    client = _events_client({"0": {"items": [], "limit": 50, "offset": 0, "has_more": False}}, requests)

    assert client.list_events() == EventPage(items=(), limit=50, offset=0, has_more=False)
    assert requests[0].url.query == b""


def test_iter_events_follows_offset_until_has_more_is_false() -> None:
    requests: list[httpx.Request] = []
    pages: dict[str, object] = {
        "0": {"items": [_event_item("e-1"), _event_item("e-2")], "limit": 2, "offset": 0, "has_more": True},
        "2": {"items": [_event_item("e-3"), _event_item("e-4")], "limit": 2, "offset": 2, "has_more": True},
        "4": {"items": [_event_item("e-5")], "limit": 2, "offset": 4, "has_more": False},
    }
    client = _events_client(pages, requests)

    events = list(client.iter_events(run_id="run-1", limit=2))

    assert [event.event_id for event in events] == ["e-1", "e-2", "e-3", "e-4", "e-5"]
    assert [request.url.params.get("offset") for request in requests] == [None, "2", "4"]
    assert all(request.url.params["run_id"] == "run-1" for request in requests)
    assert all(request.url.params["limit"] == "2" for request in requests)


def test_iter_events_refuses_a_page_that_does_not_advance() -> None:
    requests: list[httpx.Request] = []
    client = _events_client({"0": {"items": [], "limit": 50, "offset": 0, "has_more": True}}, requests)

    with pytest.raises(ProtocolError) as raised:
        list(client.iter_events())
    assert raised.value.operation == "list_events"


@pytest.mark.parametrize(
    ("call", "argument"),
    [
        (lambda client: client.list_events(run_id="bad id"), "run_id"),
        (lambda client: client.list_events(config_id="-bad"), "config_id"),
        (lambda client: client.list_events(event_types=("",)), "event_types"),
        (lambda client: client.list_events(related=(("account", ""),)), "related"),
        (lambda client: client.list_events(since=datetime(2026, 11, 6)), "since"),  # noqa: DTZ001 - deliberately naive.
        (lambda client: client.list_events(until=datetime(2026, 11, 6)), "until"),  # noqa: DTZ001 - deliberately naive.
        (lambda client: client.list_events(limit=0), "limit"),
        (lambda client: client.list_events(limit=201), "limit"),
        (lambda client: client.list_events(offset=-1), "offset"),
        (lambda client: client.list_events(offset=True), "offset"),
    ],
)
def test_event_list_arguments_are_refused_before_any_request(
    call: Callable[[SyncClient], object], argument: str
) -> None:
    requests: list[httpx.Request] = []
    client = _events_client({}, requests)

    with pytest.raises(ClientInputError) as raised:
        call(client)
    assert raised.value.argument == argument
    assert requests == []


def _log_page(**extra: object) -> dict[str, object]:
    return {
        "run_id": "run-1",
        "items": [
            {
                "stage": "plan",
                "attempt": 1,
                "timestamp": NOW,
                "level": "ERROR",
                "source": "infrahub_sync.service.flow",
                "message": "Branch `demo` does not exist in Infrahub.",
            }
        ],
        "limit": 50,
        "offset": 3,
        "has_more": False,
        "run_phase": "plan-failed",
        **extra,
    }


def _logs_client(response: httpx.Response, requests: list[httpx.Request]) -> SyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        requests.append(request)
        return response

    return SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))


def test_read_run_logs_sends_the_contract_query_and_parses_the_page() -> None:
    requests: list[httpx.Request] = []
    client = _logs_client(httpx.Response(200, json=_log_page()), requests)

    page = client.read_run_logs("run-1", stage="plan", limit=50, offset=3)

    assert isinstance(page, RunLogPage)
    assert (page.run_id, page.limit, page.offset, page.has_more, page.run_phase) == (
        "run-1",
        50,
        3,
        False,
        "plan-failed",
    )
    (line,) = page.items
    assert isinstance(line, RunLogLine)
    assert (line.stage, line.attempt, line.level, line.source) == ("plan", 1, "ERROR", "infrahub_sync.service.flow")
    assert line.timestamp == datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    assert requests[0].url.path == "/runs/run-1/logs"
    assert requests[0].url.params.multi_items() == [("stage", "plan"), ("limit", "50"), ("offset", "3")]


def test_read_run_logs_without_arguments_sends_no_query() -> None:
    requests: list[httpx.Request] = []
    client = _logs_client(httpx.Response(200, json=_log_page(items=[], offset=0, limit=200)), requests)

    page = client.read_run_logs("run-1")

    assert page.items == ()
    assert requests[0].url.query == b""


@pytest.mark.parametrize(("status", "code"), [(410, "log-expired"), (503, "log-unavailable"), (404, "run-not-found")])
def test_read_run_logs_maps_refusals_to_api_errors(status: int, code: str) -> None:
    body = {
        "error": {
            "code": code,
            "message": "the log of this run has expired",
            "status": status,
            "run_id": "run-1",
            "mutation_id": None,
        }
    }
    client = _logs_client(httpx.Response(status, json=body), [])

    with pytest.raises(APIError) as raised:
        client.read_run_logs("run-1")

    assert (raised.value.status, raised.value.code, raised.value.run_id) == (status, code, "run-1")


@pytest.mark.parametrize(
    ("call", "argument"),
    [
        (lambda client: client.read_run_logs("bad id"), "run_id"),
        (lambda client: client.read_run_logs("run-1", stage="deploy"), "stage"),
        (lambda client: client.read_run_logs("run-1", limit=0), "limit"),
        (lambda client: client.read_run_logs("run-1", limit=1001), "limit"),
        (lambda client: client.read_run_logs("run-1", offset=-1), "offset"),
        (lambda client: client.read_run_logs("run-1", offset=True), "offset"),
    ],
)
def test_read_run_logs_arguments_are_refused_before_any_request(
    call: Callable[[SyncClient], object], argument: str
) -> None:
    requests: list[httpx.Request] = []
    client = _logs_client(httpx.Response(200, json=_log_page()), requests)

    with pytest.raises(ClientInputError) as raised:
        call(client)
    assert raised.value.argument == argument
    assert requests == []
