"""Synchronous Sync HTTP client boundary tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import cast

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
    CreateRunRequest,
    ProtocolError,
    SyncClient,
    TransportError,
    VerifyRunRequest,
)

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
        return httpx.Response(200, json=[])

    client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    assert client.list_configs() == ()
    assert client.list_configs() == ()

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
        ("GET", "/configs"): (200, [config]),
        ("GET", "/configs/cfg"): (200, config),
        ("GET", "/configs/cfg/versions"): (200, [version]),
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
            {
                "run_id": "run-1",
                "checksum": "a" * 64,
                "checksum_ok": True,
                "verification_notes": [],
                "summary": {},
                "operations": [],
            },
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
