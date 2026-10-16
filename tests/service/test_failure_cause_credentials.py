"""No credential and no error text reaches a failure cause (spec 009 T068, SC-005, SC-007).

For every failure type, the exception carries the two fixture credentials of T001 in its text,
in an address with a user name and password, in a request header and in a traceback. The
stored cause is checked on its raw bytes: neither value, in any encoding JSON gives it, and no
part of that text appears. The check does not depend on value replacement.
"""

from __future__ import annotations

import json
import ssl
import traceback
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import requests
from infrahub_sdk.exceptions import (
    AuthenticationError,
    GraphQLError,
    ServerNotReachableError,
    ServerNotResponsiveError,
)

from infrahub_sync import RecordLoadError, SideLoadError, TransformError
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.failure_cause import build_failure_cause
from infrahub_sync.plan.errors import LeftOutConfirmationError, PlanReportDigestMismatchError
from tests.plan.problem_fixtures import SHORT_CREDENTIAL, SPECIAL_CREDENTIAL

if TYPE_CHECKING:
    from collections.abc import Callable

ADDRESS = f"https://admin:{SHORT_CREDENTIAL}@netbox.example.com:8443/api/dcim/devices/?token={SPECIAL_CREDENTIAL}"
HEADER = f"Authorization: Token {SPECIAL_CREDENTIAL}"


def _connect() -> None:
    msg = f"connect {ADDRESS} with {HEADER}"
    raise RuntimeError(msg)


def _traceback_text() -> str:
    try:
        _connect()
    except RuntimeError:
        return traceback.format_exc()
    return ""


TEXT = f"request to {ADDRESS} failed; {HEADER}; {_traceback_text()}"

# Parts of the error text that must not appear, besides the credential values themselves.
TEXT_PARTS = ("admin", "netbox.example.com", "8443", "Authorization", "Token", "Traceback", "request to", "connect ")


def _forbidden() -> list[bytes]:
    values: list[str] = []
    for value in (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL):
        values.extend((value, json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=True)[1:-1]))
    values.extend(TEXT_PARTS)
    return [value.encode() for value in dict.fromkeys(values)]


def _side(inner: BaseException, role: str = "source", adapter: str = "netbox") -> SideLoadError:
    error = SideLoadError(f"An error occurred while loading {adapter}: {TEXT}", side=role, adapter=adapter)  # ty: ignore[invalid-argument-type]
    error.__cause__ = inner
    return error


def _chain(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


def _requests_status(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.url = ADDRESS
    response.headers["Authorization"] = f"Token {SPECIAL_CREDENTIAL}"
    return requests.HTTPError(TEXT, response=response)


def _httpx_status(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", ADDRESS, headers={"Authorization": f"Token {SPECIAL_CREDENTIAL}"})
    return httpx.HTTPStatusError(TEXT, request=request, response=httpx.Response(status, request=request))


def _netbox_status(status: int) -> Exception:
    query = pytest.importorskip("pynetbox.core.query")
    response = requests.Response()
    response.status_code = status
    response.reason = TEXT
    response.url = ADDRESS
    response._content = json.dumps({"detail": TEXT}).encode()
    response.request = requests.Request(
        "POST", ADDRESS, headers={"Authorization": f"Token {SPECIAL_CREDENTIAL}"}, data={"text": TEXT}
    ).prepare()
    return query.RequestError(response)


def _transform_error() -> BaseException:
    error = TransformError(f"Failed to transform 'name' with '{TEXT}'", field="name", source_id="42")
    error.kind = "DcimDevice"
    return _chain(error, RuntimeError(TEXT))


# One case per failure type that `build_failure_cause` returns.
CASES: list[Any] = [
    pytest.param(
        "apply",
        lambda: GraphQLError(
            errors=[{"message": "Value is not valid for name", "extensions": {"code": "VALIDATION_ERROR"}}],
            query=f"mutation {{ login(token: {json.dumps(SPECIAL_CREDENTIAL)}) }} # {TEXT}",
            variables={"password": SHORT_CREDENTIAL, "text": TEXT},
        ),
        "destination_rejected_write",
        id="destination_rejected_write",
    ),
    pytest.param(
        "plan", lambda: _side(requests.ConnectionError(TEXT)), "system_unreachable", id="unreachable-requests"
    ),
    pytest.param("plan", lambda: _side(httpx.ConnectError(TEXT)), "system_unreachable", id="unreachable-httpx"),
    pytest.param(
        "plan",
        lambda: _side(ServerNotReachableError(address=ADDRESS, message=TEXT)),
        "system_unreachable",
        id="unreachable-infrahub-sdk",
    ),
    pytest.param("plan", lambda: _side(requests.Timeout(TEXT)), "system_timeout", id="timeout-requests"),
    pytest.param("plan", lambda: _side(httpx.ReadTimeout(TEXT)), "system_timeout", id="timeout-httpx"),
    pytest.param(
        "plan",
        lambda: _side(ServerNotResponsiveError(url=ADDRESS, timeout=5, message=TEXT)),
        "system_timeout",
        id="timeout-infrahub-sdk",
    ),
    pytest.param("plan", lambda: _side(_requests_status(401)), "token_rejected", id="token-requests"),
    pytest.param("plan", lambda: _side(_httpx_status(403)), "token_rejected", id="token-httpx"),
    pytest.param("plan", lambda: _side(_netbox_status(403)), "token_rejected", id="token-pynetbox"),
    pytest.param("plan", lambda: _side(AuthenticationError(TEXT)), "token_rejected", id="token-infrahub-sdk"),
    pytest.param(
        "plan",
        lambda: _side(ssl.SSLCertVerificationError(TEXT)),
        "tls_certificate_rejected",
        id="tls_certificate_rejected",
    ),
    pytest.param(
        "plan",
        lambda: _side(_chain(RecordLoadError(kind="DcimDevice", source_id="42", field="name"), RuntimeError(TEXT))),
        "record_load_failed",
        id="record_load_failed",
    ),
    pytest.param("plan", lambda: _side(_transform_error()), "transform_failed", id="transform_failed"),
    pytest.param("plan", lambda: CredentialConfigurationError(TEXT), "credential_missing", id="credential_missing"),
    pytest.param("apply", lambda: PlanReportDigestMismatchError(TEXT), "verification_failed", id="verification_failed"),
    pytest.param(
        "apply", lambda: LeftOutConfirmationError(TEXT, expected_left_out_count=1), "apply_refused", id="apply_refused"
    ),
    pytest.param("plan", lambda: _side(RuntimeError(TEXT)), "internal_error", id="internal_error"),
]


@pytest.mark.parametrize(("stage", "factory", "failure_type"), CASES)
def test_no_credential_and_no_error_text_reaches_the_cause(
    stage: str, factory: Callable[[], BaseException], failure_type: str
) -> None:
    exc = factory()
    # The fixture text is really in the exception, so its absence below is the builder's doing.
    assert SHORT_CREDENTIAL in "".join(traceback.format_exception(exc))

    cause = build_failure_cause(stage, exc)  # ty: ignore[invalid-argument-type]

    assert cause.failure_type == failure_type
    stored = [cause.model_dump_json().encode(), json.dumps(cause.model_dump(mode="json"), ensure_ascii=False).encode()]
    for raw in stored:
        for forbidden in _forbidden():
            assert forbidden not in raw, f"{forbidden!r} reached the {failure_type} cause"
