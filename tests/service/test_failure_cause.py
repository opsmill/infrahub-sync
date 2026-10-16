"""`build_failure_cause` classifies a failure by exception type only (spec 009 T067, research R13).

One case per row of research R13. Every exception holds `LEAK` in its text; the cause is built
from named fields and fixed templates, so `LEAK` never reaches it. Each case also runs once
with the mutation "use `str(exc)` for `summary`" and checks that the assertions catch it.
"""

from __future__ import annotations

import ssl
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
from infrahub_sync.failure_cause import NEXT_ACTIONS, RunFailureCause, build_failure_cause
from infrahub_sync.plan.errors import LeftOutConfirmationError, PlanReportDigestMismatchError
from infrahub_sync.service.flow import RegisteredPlanVerificationError
from infrahub_sync.service.left_out_stop import left_out_stop

if TYPE_CHECKING:
    from collections.abc import Callable

LEAK = "LEAKED-ERROR-TEXT"
READ_TEXT = "the builder read the text or the arguments of the exception"

CAUSE_FIELDS = {
    "stage",
    "failure_type",
    "system",
    "error_code",
    "node_kind",
    "attribute",
    "record",
    "field",
    "infrahub_message",
    "summary",
    "error_class",
    "apply_progress",
    "next_action",
}

NETBOX = {"role": "source", "adapter": "netbox"}
INFRAHUB = {"role": "destination", "adapter": "infrahub"}


def _side(inner: BaseException, role: str = "source", adapter: str = "netbox") -> SideLoadError:
    error = SideLoadError(f"An error occurred while loading {adapter}: {LEAK}", side=role, adapter=adapter)  # ty: ignore[invalid-argument-type]
    error.__cause__ = inner
    return error


def _wrapped(inner: BaseException) -> RuntimeError:
    error = RuntimeError(f"apply failed: {LEAK}")
    error.__cause__ = inner
    return error


def _suppressed(outer: BaseException, inner: BaseException) -> BaseException:
    """Chain the way httpcore does: the inner error is the suppressed context."""
    outer.__context__ = inner
    outer.__suppress_context__ = True
    return outer


def _requests_status(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.url = f"https://user:{LEAK}@netbox.example.com/api/"
    return requests.HTTPError(LEAK, response=response)


def _httpx_status(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://user:{LEAK}@infrahub.example.com/graphql")
    return httpx.HTTPStatusError(LEAK, request=request, response=httpx.Response(status, request=request))


def _netbox_status(status: int) -> Exception:
    query = pytest.importorskip("pynetbox.core.query")
    response = requests.Response()
    response.status_code = status
    response.reason = LEAK
    response.url = f"https://user:{LEAK}@netbox.example.com/api/"
    response._content = b"not json"
    response.request = requests.Request("GET", response.url, data={"text": LEAK}).prepare()
    return query.RequestError(response)


def _graphql(errors: list[dict[str, Any]]) -> GraphQLError:
    return GraphQLError(errors=errors, query=LEAK, variables={"text": LEAK})


def _extensions(code: str, data: object) -> dict[str, Any]:
    """The `extensions` that Infrahub's error formatter writes: code, HTTP status and `data`."""
    return {"code": code, "http_status": 422, "data": data}


def _expected(failure_type: str, summary: str, **fields: object) -> dict[str, Any]:
    return {"failure_type": failure_type, "summary": summary, **fields}


# (stage, exception factory, expected fields). Fields left out of `expected` must be null.
CASES: list[Any] = [
    pytest.param(
        "apply",
        lambda: _wrapped(
            _graphql(
                [
                    {
                        "message": f"Value is not valid\x07 for name\n{chr(0x9B)}",
                        "extensions": _extensions(
                            "ATTRIBUTE_REQUIRED", {"node_kind": "DcimDevice", "field_name": "name"}
                        ),
                    },
                    {"message": LEAK, "extensions": {"code": LEAK}},
                ]
            )
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code ATTRIBUTE_REQUIRED",
            error_code="ATTRIBUTE_REQUIRED",
            node_kind="DcimDevice",
            attribute="name",
            infrahub_message="Value is not valid for name",
            error_class="RuntimeError",
        ),
        id="destination_rejected_write-attribute-required",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Value is not valid for status",
                    "extensions": _extensions(
                        "ATTRIBUTE_INVALID_TYPE",
                        {
                            "node_kind": "DcimDevice",
                            "field_name": "status",
                            "expected_type": LEAK,
                            "received_type": LEAK,
                            "constraint": LEAK,
                            "detail": LEAK,
                        },
                    ),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code ATTRIBUTE_INVALID_TYPE",
            error_code="ATTRIBUTE_INVALID_TYPE",
            node_kind="DcimDevice",
            attribute="status",
            infrahub_message="Value is not valid for status",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-attribute-invalid-type",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Value is too long for name",
                    "extensions": _extensions(
                        "ATTRIBUTE_CONSTRAINT_VIOLATION",
                        {"node_kind": "DcimDevice", "field_name": "name", "constraint": LEAK, "detail": LEAK},
                    ),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code ATTRIBUTE_CONSTRAINT_VIOLATION",
            error_code="ATTRIBUTE_CONSTRAINT_VIOLATION",
            node_kind="DcimDevice",
            attribute="name",
            infrahub_message="Value is too long for name",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-attribute-constraint-violation",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Violates uniqueness constraint",
                    "extensions": _extensions(
                        "UNIQUENESS_VIOLATION", {"node_kind": "DcimDevice", "fields": ["name", "site"]}
                    ),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code UNIQUENESS_VIOLATION",
            error_code="UNIQUENESS_VIOLATION",
            node_kind="DcimDevice",
            attribute="name, site",
            infrahub_message="Violates uniqueness constraint",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-uniqueness-violation",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Violates uniqueness constraint",
                    "extensions": _extensions(
                        "UNIQUENESS_VIOLATION", {"node_kind": "DcimDevice", "fields": ["name", 5]}
                    ),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code UNIQUENESS_VIOLATION",
            error_code="UNIQUENESS_VIOLATION",
            node_kind="DcimDevice",
            infrahub_message="Violates uniqueness constraint",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-uniqueness-violation-non-string-member",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Unable to find the node",
                    "extensions": _extensions("NODE_NOT_FOUND", {"node_kind": "DcimDevice", "identifier": LEAK}),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code NODE_NOT_FOUND",
            error_code="NODE_NOT_FOUND",
            node_kind="DcimDevice",
            infrahub_message="Unable to find the node",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-node-not-found",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Value is not valid for name",
                    "extensions": {"code": "VALIDATION_ERROR", "node_kind": "DcimDevice", "attribute": "name"},
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write with error code VALIDATION_ERROR",
            error_code="VALIDATION_ERROR",
            infrahub_message="Value is not valid for name",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-no-data-ignores-top-level-names",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Value is not valid for name",
                    "extensions": {"code": "ATTRIBUTE_REQUIRED", "http_status": 422, "data": ["DcimDevice", "name"]},
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write with error code ATTRIBUTE_REQUIRED",
            error_code="ATTRIBUTE_REQUIRED",
            infrahub_message="Value is not valid for name",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-data-not-a-dict",
    ),
    pytest.param(
        "sync",
        lambda: _graphql([{"message": "Something went wrong"}]),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write",
            infrahub_message="Something went wrong",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-without-code",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "m" * 2000,
                    "extensions": _extensions("C" * 200, {"node_kind": 5, "field_name": ["name"]}),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write with error code " + "C" * 128,
            error_code="C" * 128,
            infrahub_message="m" * 1024,
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-limits",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Value is not valid",
                    "extensions": _extensions("ATTRIBUTE_REQUIRED", {"node_kind": "K" * 200, "field_name": "f" * 200}),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            f"The destination rejected a write to {'K' * 128} with error code ATTRIBUTE_REQUIRED",
            error_code="ATTRIBUTE_REQUIRED",
            node_kind="K" * 128,
            attribute="f" * 128,
            infrahub_message="Value is not valid",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-data-limits",
    ),
    pytest.param(
        "apply",
        lambda: _graphql(
            [
                {
                    "message": "Violates uniqueness constraint",
                    "extensions": _extensions(
                        "UNIQUENESS_VIOLATION", {"node_kind": "DcimDevice", "fields": ["n" * 100] * 3}
                    ),
                }
            ]
        ),
        _expected(
            "destination_rejected_write",
            "The destination rejected a write to DcimDevice with error code UNIQUENESS_VIOLATION",
            error_code="UNIQUENESS_VIOLATION",
            node_kind="DcimDevice",
            attribute=", ".join(["n" * 100] * 3)[:128],
            infrahub_message="Violates uniqueness constraint",
            error_class="GraphQLError",
        ),
        id="destination_rejected_write-uniqueness-violation-limits",
    ),
    pytest.param(
        "plan",
        lambda: _graphql([{"message": LEAK, "extensions": {"code": "X"}}]),
        _expected(
            "internal_error", "The plan stage failed with an internal error (GraphQLError)", error_class="GraphQLError"
        ),
        id="graphql-outside-apply-is-internal_error",
    ),
    pytest.param(
        "plan",
        lambda: _side(requests.ConnectionError(LEAK)),
        _expected("system_unreachable", "The connection to the source system (netbox) failed", system=NETBOX),
        id="system_unreachable-requests",
    ),
    pytest.param(
        "plan",
        lambda: _side(httpx.ConnectError(LEAK), "destination", "infrahub"),
        _expected("system_unreachable", "The connection to the destination system (infrahub) failed", system=INFRAHUB),
        id="system_unreachable-httpx",
    ),
    pytest.param(
        "verify",
        lambda: ServerNotReachableError(address=f"https://user:{LEAK}@infrahub.example.com", message=LEAK),
        _expected("system_unreachable", "The connection to a system failed", error_class="ServerNotReachableError"),
        id="system_unreachable-infrahub-sdk",
    ),
    pytest.param(
        "plan",
        lambda: _side(requests.Timeout(LEAK)),
        _expected("system_timeout", "The source system (netbox) did not respond in time", system=NETBOX),
        id="system_timeout-requests",
    ),
    pytest.param(
        "plan",
        lambda: _side(requests.ConnectTimeout(LEAK)),
        _expected("system_timeout", "The source system (netbox) did not respond in time", system=NETBOX),
        id="system_timeout-requests-connect",
    ),
    pytest.param(
        "plan",
        lambda: _side(httpx.ReadTimeout(LEAK), "destination", "infrahub"),
        _expected("system_timeout", "The destination system (infrahub) did not respond in time", system=INFRAHUB),
        id="system_timeout-httpx",
    ),
    pytest.param(
        "plan",
        lambda: _side(ServerNotResponsiveError(url=LEAK, timeout=5, message=LEAK), "destination", "infrahub"),
        _expected("system_timeout", "The destination system (infrahub) did not respond in time", system=INFRAHUB),
        id="system_timeout-infrahub-sdk",
    ),
    pytest.param(
        "plan",
        lambda: _side(_requests_status(401)),
        _expected("token_rejected", "The source system (netbox) rejected the token", system=NETBOX),
        id="token_rejected-requests-401",
    ),
    pytest.param(
        "plan",
        lambda: _side(_requests_status(403)),
        _expected("token_rejected", "The source system (netbox) rejected the token", system=NETBOX),
        id="token_rejected-requests-403",
    ),
    pytest.param(
        "plan",
        lambda: _side(_httpx_status(401), "destination", "infrahub"),
        _expected("token_rejected", "The destination system (infrahub) rejected the token", system=INFRAHUB),
        id="token_rejected-httpx-401",
    ),
    pytest.param(
        "plan",
        lambda: _side(_httpx_status(403), "destination", "infrahub"),
        _expected("token_rejected", "The destination system (infrahub) rejected the token", system=INFRAHUB),
        id="token_rejected-httpx-403",
    ),
    pytest.param(
        "plan",
        lambda: _side(_netbox_status(401)),
        _expected("token_rejected", "The source system (netbox) rejected the token", system=NETBOX),
        id="token_rejected-pynetbox-401",
    ),
    pytest.param(
        "plan",
        lambda: _side(_netbox_status(403)),
        _expected("token_rejected", "The source system (netbox) rejected the token", system=NETBOX),
        id="token_rejected-pynetbox-403",
    ),
    pytest.param(
        "plan",
        lambda: _side(AuthenticationError(LEAK), "destination", "infrahub"),
        _expected("token_rejected", "The destination system (infrahub) rejected the token", system=INFRAHUB),
        id="token_rejected-infrahub-sdk",
    ),
    pytest.param(
        "plan",
        lambda: _side(_requests_status(500)),
        _expected("internal_error", "The plan stage failed with an internal error (SideLoadError)", system=NETBOX),
        id="http-500-is-internal_error",
    ),
    pytest.param(
        "plan",
        lambda: _side(ssl.SSLCertVerificationError(LEAK)),
        _expected(
            "tls_certificate_rejected",
            "The TLS certificate of the source system (netbox) is not trusted",
            system=NETBOX,
        ),
        id="tls_certificate_rejected",
    ),
    pytest.param(
        "plan",
        lambda: _side(
            _chain(
                ServerNotReachableError(address=LEAK, message=LEAK),
                _suppressed(httpx.ConnectError(LEAK), ssl.SSLCertVerificationError(LEAK)),
            ),
            "destination",
            "infrahub",
        ),
        _expected(
            "tls_certificate_rejected",
            "The TLS certificate of the destination system (infrahub) is not trusted",
            system=INFRAHUB,
        ),
        id="tls_certificate_rejected-through-httpx",
    ),
    pytest.param(
        "plan",
        lambda: _side(_suppressed(requests.exceptions.SSLError(LEAK), _wrapped(ssl.SSLCertVerificationError(LEAK)))),
        _expected(
            "tls_certificate_rejected",
            "The TLS certificate of the source system (netbox) is not trusted",
            system=NETBOX,
        ),
        id="tls_certificate_rejected-through-requests",
    ),
    pytest.param(
        "plan",
        lambda: _side(_chain(RecordLoadError(kind="DcimDevice", source_id="42", field="position"), RuntimeError(LEAK))),
        _expected(
            "record_load_failed",
            "The DcimDevice record 42 could not be loaded (field position)",
            system=NETBOX,
            node_kind="DcimDevice",
            record={"kind": "DcimDevice", "identity": None, "source_id": "42"},
            field="position",
        ),
        id="record_load_failed",
    ),
    pytest.param(
        "plan",
        lambda: _side(RecordLoadError(kind="DcimDevice", source_id="42", field=None)),
        _expected(
            "record_load_failed",
            "The DcimDevice record 42 could not be loaded",
            system=NETBOX,
            node_kind="DcimDevice",
            record={"kind": "DcimDevice", "identity": None, "source_id": "42"},
        ),
        id="record_load_failed-without-field",
    ),
    pytest.param(
        "plan",
        lambda: _side(_transform_error()),
        _expected(
            "transform_failed",
            "The transform of field name failed for the DcimDevice record 42",
            system=NETBOX,
            node_kind="DcimDevice",
            record={"kind": "DcimDevice", "identity": None, "source_id": "42"},
            field="name",
        ),
        id="transform_failed",
    ),
    pytest.param(
        "plan",
        lambda: CredentialConfigurationError(f"environment credential {LEAK!r} is missing or empty"),
        _expected(
            "credential_missing",
            "A credential that the configuration references is missing or not valid",
            error_class="CredentialConfigurationError",
        ),
        id="credential_missing",
    ),
    pytest.param(
        "apply",
        lambda: PlanReportDigestMismatchError(LEAK),
        _expected(
            "verification_failed",
            "The saved plan did not pass verification",
            error_class="PlanReportDigestMismatchError",
        ),
        id="verification_failed-plan-report",
    ),
    pytest.param(
        "apply",
        lambda: RegisteredPlanVerificationError(LEAK, "rebuild"),
        _expected(
            "verification_failed",
            "The saved plan did not pass verification",
            error_class="RegisteredPlanVerificationError",
        ),
        id="verification_failed-registered-plan",
    ),
    pytest.param(
        "apply",
        lambda: LeftOutConfirmationError(LEAK, expected_left_out_count=3),
        _expected("apply_refused", "The apply was refused before any write", error_class="LeftOutConfirmationError"),
        id="apply_refused",
    ),
    pytest.param(
        "plan",
        lambda: KeyError(LEAK),
        _expected("internal_error", "The plan stage failed with an internal error (KeyError)", error_class="KeyError"),
        id="internal_error",
    ),
]


def _chain(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


def _transform_error() -> TransformError:
    error = TransformError(f"Failed to transform 'name' with '{LEAK}'", field="name", source_id="42")
    error.kind = "DcimDevice"
    return _chain(error, RuntimeError(LEAK))  # ty: ignore[invalid-return-type]


def _check(cause: RunFailureCause, stage: str, expected: dict[str, Any]) -> None:
    dumped = cause.model_dump(mode="json")
    assert set(dumped) == CAUSE_FIELDS
    expected_fields = {
        "stage": stage,
        "system": None,
        "error_code": None,
        "node_kind": None,
        "attribute": None,
        "record": None,
        "field": None,
        "infrahub_message": None,
        "error_class": "SideLoadError",
        "apply_progress": None,
        "next_action": NEXT_ACTIONS[expected["failure_type"]],
        **expected,
    }
    assert dumped == expected_fields
    assert LEAK not in cause.model_dump_json()


@pytest.mark.parametrize(("stage", "factory", "expected"), CASES)
def test_build_failure_cause_classifies_by_exception_type(
    stage: str, factory: Callable[[], BaseException], expected: dict[str, Any]
) -> None:
    exc = factory()

    cause = build_failure_cause(stage, exc)  # ty: ignore[invalid-argument-type]

    _check(cause, stage, expected)


@pytest.mark.parametrize(("stage", "factory", "expected"), CASES)
def test_the_check_fails_when_the_summary_is_the_exception_text(
    stage: str, factory: Callable[[], BaseException], expected: dict[str, Any]
) -> None:
    exc = factory()
    cause = build_failure_cause(stage, exc)  # ty: ignore[invalid-argument-type]
    mutated = cause.model_copy(update={"summary": str(exc)})

    with pytest.raises(AssertionError):
        _check(mutated, stage, expected)


class _UnreadableError(Exception):
    """An error whose text cannot be read: the builder must not try."""

    def __str__(self) -> str:
        raise AssertionError(READ_TEXT)

    def __repr__(self) -> str:
        raise AssertionError(READ_TEXT)

    @property
    def args(self) -> tuple[Any, ...]:
        raise AssertionError(READ_TEXT)


def test_the_builder_never_reads_the_exception_text_or_arguments() -> None:
    exc = _side(_UnreadableError())

    cause = build_failure_cause("plan", exc)

    assert cause.failure_type == "internal_error"
    assert cause.error_class == "SideLoadError"


def test_the_error_class_is_limited_in_length() -> None:
    long_class = type("E" * 300, (Exception,), {})

    cause = build_failure_cause("plan", long_class())

    assert cause.error_class == "E" * 128


def test_every_failure_type_has_a_next_action() -> None:
    assert set(NEXT_ACTIONS) == {
        "destination_rejected_write",
        "system_unreachable",
        "system_timeout",
        "token_rejected",
        "tls_certificate_rejected",
        "record_load_failed",
        "transform_failed",
        "credential_missing",
        "verification_failed",
        "apply_refused",
        "left_out_review_required",
        "internal_error",
    }
    assert all(NEXT_ACTIONS.values())


def test_the_stored_sync_stop_failure_is_a_valid_cause() -> None:
    class _Report:
        left_out = 2

    class _Manifest:
        report = _Report()

    class _Saved:
        manifest = _Manifest()

        @staticmethod
        def summary() -> Any:  # noqa: ANN401
            class _Summary:
                @staticmethod
                def model_dump(mode: str) -> dict[str, Any]:  # noqa: ARG004
                    return {}

            return _Summary()

    stopped = left_out_stop("run-1", _Saved(), "sync-name")  # ty: ignore[invalid-argument-type]
    assert stopped is not None
    result, _writeback = stopped

    cause = RunFailureCause.model_validate(result["failure"])

    assert cause.failure_type == "left_out_review_required"
    assert cause.error_class is None
