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
from infrahub_sync.failure_cause import (
    APPLY_STAGE_NEXT_ACTIONS,
    NEXT_ACTIONS,
    RunFailureCause,
    build_failure_cause,
    next_action,
)
from infrahub_sync.plan.errors import (
    LeftOutConfirmationError,
    OperationApplyFailedError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanReportDigestMismatchError,
)
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.runtime_schema.errors import DestinationSchemaUnavailableError
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

PEER_OPERATION = "op_3531c0d83d698fd1"
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
        # Review #6: the summary names the innermost class, not the `SideLoadError` around it.
        _expected(
            "internal_error",
            "The plan stage failed with an internal error (HTTPError)",
            system=NETBOX,
            error_class="HTTPError",
        ),
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
        "apply",
        lambda: _chain(
            OperationApplyFailedError(LEAK, apply_record=ApplyRecord(applied_operations=())),
            PeerNotFoundError(LEAK, peer_kind="LocationSite", referring_operation_id=PEER_OPERATION),
        ),
        _expected(
            "destination_peer_missing",
            f"The LocationSite peer of operation {PEER_OPERATION} is missing in Infrahub: it was deleted or "
            "renamed after the plan",
            system=INFRAHUB,
            node_kind="LocationSite",
            error_class="OperationApplyFailedError",
        ),
        id="destination_peer_missing",
    ),
    pytest.param(
        "apply",
        lambda: PeerAmbiguousError(LEAK, peer_kind="LocationSite", referring_operation_id=PEER_OPERATION),
        _expected(
            "destination_peer_ambiguous",
            f"More than one LocationSite object in Infrahub matches the peer of operation {PEER_OPERATION}",
            system=INFRAHUB,
            node_kind="LocationSite",
            error_class="PeerAmbiguousError",
        ),
        id="destination_peer_ambiguous",
    ),
    pytest.param(
        "apply",
        lambda: PeerNotFoundError(LEAK, next_action="Re-plan with a direct attribute in the identity."),
        _expected(
            "internal_error",
            "The apply stage failed with an internal error (PeerNotFoundError)",
            error_class="PeerNotFoundError",
        ),
        id="peer-with-no-filter-is-internal_error",
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
        "next_action": next_action(expected["failure_type"], stage),
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
    # Review #6: the innermost class, not the `SideLoadError` that wraps it.
    assert cause.error_class == "_UnreadableError"


def test_an_internal_error_names_the_innermost_class_not_its_wrappers() -> None:
    """Review #6: the engine wraps a load failure twice; the class that failed is the innermost one."""
    side = SideLoadError(LEAK, side="source", adapter="netbox")
    side.__cause__ = KeyError(LEAK)
    outer = ValueError(f"An error occurred while loading the sync: {LEAK}")
    outer.__cause__ = side

    cause = build_failure_cause("plan", outer)

    assert cause.failure_type == "internal_error"
    assert cause.error_class == "KeyError"
    assert cause.summary == "The plan stage failed with an internal error (KeyError)"
    assert LEAK not in cause.model_dump_json()


def test_an_internal_error_raised_alone_names_its_own_class() -> None:
    assert build_failure_cause("plan", ZeroDivisionError(LEAK)).error_class == "ZeroDivisionError"


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
        "destination_peer_missing",
        "destination_peer_ambiguous",
        "internal_error",
    }
    assert all(NEXT_ACTIONS.values())


def test_a_refused_apply_leads_to_a_new_plan_not_a_second_apply() -> None:
    # A refusal in the worker comes after the apply was admitted, so a second apply of the
    # same run gets `new-plan-required` (contracts/sync-api.md apply check 4).
    action = NEXT_ACTIONS["apply_refused"]

    assert "create and review a new plan" in action
    assert "apply the plan again" not in action
    assert "The destination was not changed." in action


@pytest.mark.parametrize("failure_type", sorted(NEXT_ACTIONS))
def test_no_next_action_of_the_apply_stage_retries_the_same_run(failure_type: str) -> None:
    action = next_action(failure_type, "apply")  # ty: ignore[invalid-argument-type]

    assert "run the stage again" not in action.lower()
    assert "apply the plan again" not in action.lower()


@pytest.mark.parametrize("failure_type", sorted(APPLY_STAGE_NEXT_ACTIONS))
def test_a_retryable_failure_of_the_apply_stage_leads_to_a_new_plan(failure_type: str) -> None:
    assert "new plan" in next_action(failure_type, "apply")  # ty: ignore[invalid-argument-type]
    assert "run the stage again" in NEXT_ACTIONS[failure_type].lower()
    assert next_action(failure_type, "plan") == NEXT_ACTIONS[failure_type]  # ty: ignore[invalid-argument-type]


def test_a_destination_schema_read_names_the_destination_system() -> None:
    # The worker reads the destination schema before any adapter loads, so no `SideLoadError`
    # wraps a token the destination rejects there; the read's own error names the system.
    read = DestinationSchemaUnavailableError(LEAK, reason="unauthorized", adapter="infrahub")
    read.__context__ = AuthenticationError(LEAK)

    cause = build_failure_cause("plan", read)

    assert cause.failure_type == "token_rejected"
    assert cause.system is not None
    assert cause.system.model_dump() == {"role": "destination", "adapter": "infrahub"}
    assert cause.summary == "The destination system (infrahub) rejected the token"
    assert LEAK not in cause.model_dump_json()


def test_the_stored_sync_stop_failure_is_a_valid_cause() -> None:
    class _Report:
        left_out = 2

    class _Manifest:
        report = _Report()
        plan_checksum = "c" * 64

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
    # Review #10: review the plan, then apply this run with the confirmation; never "run the stage again".
    assert "infrahub-sync runs plan run-1" in cause.next_action
    assert "infrahub-sync runs left-out run-1" in cause.next_action
    assert f"infrahub-sync apply run-1 --expected-checksum {'c' * 64} --confirm-left-out" in cause.next_action
    assert "stage again" not in cause.next_action
