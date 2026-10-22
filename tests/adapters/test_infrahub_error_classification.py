"""A missing permission is never recorded as a refused token (FR-008, SC-006).

One case per row of the run-errors contract. The SDK's own request path builds the
`AuthenticationError` from an `httpx.Response`, so the exception chain is the one a run sees:
the status is on `__cause__.response.status_code`, and the request on that cause carries the
token in its headers.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError
from infrahub_sdk.types import HTTPMethod

from infrahub_sync.infrahub_errors import InfrahubErrorClassification, classify_infrahub_error
from infrahub_sync.potenda import _operational_failure_summary  # noqa: PLC2701 -- the run's recorded cause

TOKEN = "18795e9c-b6e7-4e2b-a0e9-token-canary"  # noqa: S105 -- a canary, not a credential
ADDRESS = "http://infrahub.invalid"
ONE_PERMISSION = "You do not have the following permission: global:edit_default_branch:allow_all"
SEVERAL_PERMISSIONS = (
    "You do not have one of the following permissions: "
    "object:*:Core:Account:view:allow_all | object:*:Core:Account:view:allow_default"
)
OTHER_MESSAGE = "You are not allowed to change data in the default branch"


def _client(status: int, body: dict[str, Any]) -> InfrahubClientSync:
    """A client whose every request is answered with `status` and `body`."""

    def requester(
        url: str, method: HTTPMethod, headers: dict[str, Any], timeout: int, payload: dict | None = None
    ) -> httpx.Response:
        del timeout, payload
        request = httpx.Request(method.value, url, headers=headers)
        return httpx.Response(status, json=body, request=request)

    return InfrahubClientSync(
        address=ADDRESS, config=Config(address=ADDRESS, api_token=TOKEN, sync_requester=requester)
    )


def _raised(status: int, body: dict[str, Any]) -> Exception:
    """Return the exception the SDK raises for one GraphQL request answered with `status` and `body`."""
    with pytest.raises((AuthenticationError, GraphQLError)) as caught:
        _client(status, body).execute_graphql(query="query { InfrahubInfo { version } }")
    return caught.value


def _denied(*messages: str) -> dict[str, Any]:
    return {"errors": [{"message": message} for message in messages]}


def _assert_recorded_without_the_token(exc: Exception, classification: InfrahubErrorClassification) -> None:
    """Neither the classification nor the run's recorded summary holds the token or the request."""
    for recorded in (classification.describe(), _operational_failure_summary(exc), repr(classification)):
        assert TOKEN not in recorded
        assert "InfrahubInfo" not in recorded, "the GraphQL query must not be quoted"


def test_the_sdk_chain_carries_the_token_so_the_token_check_is_not_vacuous() -> None:
    """Precondition: the request the cause holds carries the token."""
    exc = _raised(401, _denied("Invalid token"))
    assert isinstance(exc.__cause__, httpx.HTTPStatusError)
    assert TOKEN in str(exc.__cause__.request.headers)


def test_http_401_is_a_refused_token() -> None:
    exc = _raised(401, _denied("Invalid token"))

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification == InfrahubErrorClassification(
        problem="refused_token", error_type="AuthenticationError", http_status=401
    )
    assert "refused the token" in classification.describe()
    _assert_recorded_without_the_token(exc, classification)


def test_http_403_with_one_permission_is_a_missing_permission_quoting_it() -> None:
    exc = _raised(403, _denied(ONE_PERMISSION))

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification.problem == "missing_permission"
    assert classification.http_status == 403
    assert classification.permissions == ("global:edit_default_branch:allow_all",)
    assert "`global:edit_default_branch:allow_all`" in classification.describe()
    assert "token" not in classification.describe()
    _assert_recorded_without_the_token(exc, classification)


def test_http_403_with_several_permissions_names_each_one() -> None:
    exc = _raised(403, _denied(SEVERAL_PERMISSIONS))

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification.problem == "missing_permission"
    assert classification.permissions == (
        "object:*:Core:Account:view:allow_all",
        "object:*:Core:Account:view:allow_default",
    )
    assert "one of the permissions" in classification.describe()
    _assert_recorded_without_the_token(exc, classification)


def test_http_403_with_another_message_is_a_missing_permission_quoting_the_message() -> None:
    exc = _raised(403, _denied(OTHER_MESSAGE))

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification.problem == "missing_permission"
    assert classification.permissions == ()
    assert classification.server_message == OTHER_MESSAGE
    assert OTHER_MESSAGE in classification.describe()
    assert "refused the token" not in classification.describe()
    _assert_recorded_without_the_token(exc, classification)


def test_a_graphql_error_starting_you_do_not_have_is_a_missing_permission() -> None:
    exc = _raised(200, {"data": None, **_denied(ONE_PERMISSION)})
    assert isinstance(exc, GraphQLError)

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification.problem == "missing_permission"
    assert classification.http_status is None
    assert classification.permissions == ("global:edit_default_branch:allow_all",)
    _assert_recorded_without_the_token(exc, classification)


def test_another_graphql_error_is_not_classified() -> None:
    exc = _raised(200, {"data": None, **_denied("Object not found")})

    assert classify_infrahub_error(exc) is None


def test_an_authentication_error_with_no_http_cause_is_a_refused_token() -> None:
    exc = AuthenticationError("Authentication failed")

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification == InfrahubErrorClassification(problem="refused_token", error_type="AuthenticationError")
    _assert_recorded_without_the_token(exc, classification)


@pytest.mark.parametrize(
    ("status", "problem"),
    [pytest.param(401, "refused_token", id="401"), pytest.param(403, "missing_permission", id="403")],
)
def test_a_rest_status_error_is_classified_by_its_status(status: int, problem: str) -> None:
    """The schema read raises `httpx.HTTPStatusError` itself; a 403 is still a missing permission."""
    request = httpx.Request("GET", f"{ADDRESS}/api/schema", headers={"X-INFRAHUB-KEY": TOKEN})
    response = httpx.Response(status, json=_denied(ONE_PERMISSION), request=request)
    exc = httpx.HTTPStatusError("refused", request=request, response=response)

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification.problem == problem
    assert classification.http_status == status
    assert TOKEN not in classification.describe()


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        pytest.param(401, _denied("Invalid token"), "refused the token", id="401"),
        pytest.param(403, _denied(ONE_PERMISSION), "`global:edit_default_branch:allow_all`", id="403-one"),
        pytest.param(403, _denied(OTHER_MESSAGE), OTHER_MESSAGE, id="403-other"),
        pytest.param(200, {"data": None, **_denied(ONE_PERMISSION)}, "a missing permission", id="graphql-denial"),
    ],
)
def test_the_apply_records_the_classified_cause(status: int, body: dict[str, Any], expected: str) -> None:
    """The run's recorded cause for a failed write is the classification, not a generic category."""
    exc = _raised(status, body)

    summary = _operational_failure_summary(exc)

    assert expected in summary
    assert type(exc).__name__ in summary
    if status != 401:
        assert "refused the token" not in summary


def test_a_quoted_server_message_drops_control_characters_and_is_capped() -> None:
    exc = _raised(403, _denied("Denied\x07\n" + "x" * 1000))

    classification = classify_infrahub_error(exc)

    assert classification is not None
    assert classification.server_message is not None
    assert "\x07" not in classification.server_message
    assert "\n" not in classification.server_message
    assert len(classification.server_message) <= 300
