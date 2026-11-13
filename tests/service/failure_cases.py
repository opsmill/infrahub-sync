"""One exception per failure type of a failed run (spec 020 User Story 3, T046 and T047).

Not a test module: it holds the exceptions that the failure record tests share. No assertion
lives here.

Each case builds the exception a stage raises today, with `text` placed in the error's own text:
its message, a request address, a request header, a GraphQL query and its variables, the extra
data of a GraphQL error, or an inner error in the cause chain. T046 passes neutral text and
checks the fields of the failure record. T047 passes text that holds two credentials and checks
that neither value reaches any output.

Infrahub's own `errors[].message` and the permission text of a 403 never hold `text`: FR-019
allows the failure record to quote them.

The destination peer errors are raised by the real `PeerResolver` of an apply, against the
recording client of `tests/adapters/test_infrahub_planned_write.py`.
"""

from __future__ import annotations

import json
import ssl
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
import requests
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError, ServerNotReachableError, ServerNotResponsiveError

from infrahub_sync import RecordLoadError, TransformError
from infrahub_sync.adapters.infrahub import PeerResolver
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.plan.errors import (
    LeftOutConfirmationRequiredError,
    NamedLineageNodeNotFoundError,
    OperationApplyFailedError,
    PlanVerificationError,
)
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.potenda import SideLoadError
from infrahub_sync.runtime_schema.errors import DestinationSchemaUnavailableError
from tests.adapters.test_infrahub_planned_write import SITE_KIND, RecordingClient, make_adapter, server_operation

if TYPE_CHECKING:
    from collections.abc import Callable

Stage = Literal["plan", "verify", "apply", "sync"]

# A referenced credential of five characters, below the masking pass's minimum length of six
# (`MIN_SECRET_LENGTH`), so masking cannot hide it.
SHORT_CREDENTIAL = "k3yQz"
# A referenced credential that holds the characters JSON escapes (`"` and `\`) and a comma. It
# starts with a run of characters that no form escapes, so a leak in any escaped form (JSON,
# Python's `repr`, a double escape) still shows that run.
SPECIAL_CREDENTIAL = 'Wq7Zm"ss\\wo,rd'
SPECIAL_CREDENTIAL_RUN = "Wq7Zm"
SHORT_CREDENTIAL_NAME = "INFRAHUB_SYNC_CREDENTIAL_SHORT_TOKEN"
SPECIAL_CREDENTIAL_NAME = "INFRAHUB_SYNC_CREDENTIAL_SPECIAL_PASSWORD"
CREDENTIALS = (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL)

# An address with a user name and password, and a request header.
ADDRESS = f"https://admin:{SHORT_CREDENTIAL}@netbox.example.test:8443/api/dcim/devices/?token={SPECIAL_CREDENTIAL}"
HEADER = f"Authorization: Token {SPECIAL_CREDENTIAL}"

# The destination and the operation of every apply case.
DESTINATION = {"role": "destination", "adapter": "infrahub"}
SOURCE = {"role": "source", "adapter": "netbox"}
APPLIED_OPERATION = "op_0a1b2c3d4e5f6a7b"
FAILED_OPERATION = "op_518aada155d790ad"

# Infrahub's own messages: the failure record may quote them, so they never hold credentials.
INFRAHUB_MESSAGE = "planned is not a valid value for status"
PERMISSION = "object:*:DcimDevice:create:allow_all"


def _connect() -> None:
    msg = f"connect {ADDRESS} with {HEADER}"
    raise RuntimeError(msg)


def _traceback_text() -> str:
    try:
        _connect()
    except RuntimeError:
        return traceback.format_exc()
    return ""


# Error text that holds both credentials: an address, a request header and a traceback.
ERROR_TEXT = f"request to {ADDRESS} failed; {HEADER}; {_traceback_text()}"
# Neutral error text, for the tests that check the fields of the record.
NEUTRAL_TEXT = "the request failed"

# Parts of `ERROR_TEXT` that must not appear in any output, besides the credential values.
ERROR_TEXT_PARTS = (
    "netbox.example.test",
    "Authorization: Token",
    "Traceback (most recent call last)",
    "request to https",
    "connect https",
)


def forbidden_values() -> list[str]:
    """Each credential raw and as JSON writes it inside a string, then the parts of the error text."""
    values: list[str] = []
    for value in CREDENTIALS:
        values.extend((value, json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1]))
    values.append(SPECIAL_CREDENTIAL_RUN)
    values.extend(ERROR_TEXT_PARTS)
    return list(dict.fromkeys(values))


@dataclass(frozen=True)
class FailureCase:
    """One failure: the stage it fails, the error it raises and what its record holds.

    `fields` are the record's spec 020 fields that this failure sets (data-model.md "Stage
    failure record additions"); `record` is compared key by key, so an added `identity` does
    not fail it.
    """

    case_id: str
    stage: Stage
    build: Callable[[str], Exception]
    failure_type: str
    fields: dict[str, Any] = field(default_factory=dict)


def _side(role: Literal["source", "destination"], adapter: str, inner: BaseException, text: str) -> SideLoadError:
    """The plan's wrapper of one side's read failure, raised `from` `inner`."""
    error = SideLoadError(f"An error occurred while loading {adapter}: {text}", role=role, adapter=adapter)
    error.__cause__ = inner
    return error


def _chained(outer: Exception, inner: BaseException) -> Exception:
    outer.__cause__ = inner
    return outer


def _apply_failed(inner: Exception, text: str) -> OperationApplyFailedError:
    """The engine's wrapper of a failed operation, as `Potenda.apply_plan` raises it."""
    record = ApplyRecord(
        applied_operations=(APPLIED_OPERATION,),
        failed_operation=FAILED_OPERATION,
        failed_operation_wrote=getattr(inner, "wrote", None),
    )
    msg = f"Applying operation {FAILED_OPERATION!r} to the destination failed with {text}."
    return _chained(OperationApplyFailedError(msg, apply_record=record), inner)  # ty: ignore[invalid-return-type]


def _rejected_write(text: str, *, with_code: bool) -> Exception:
    error: dict[str, Any] = {"message": INFRAHUB_MESSAGE, "path": ["DcimDeviceUpsert"]}
    if with_code:
        error["extensions"] = {
            "code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
            "data": {"node_kind": "DcimDevice", "field_name": "status", "detail": text},
        }
    query = f"mutation {{ DcimDeviceUpsert(data: {{token: {json.dumps(text)}}}) {{ ok }} }}"
    rejected = GraphQLError(errors=[error], query=query, variables={"token": SHORT_CREDENTIAL, "text": text})
    return _apply_failed(rejected, text)


def _requests_status(status: int, text: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.url = ADDRESS
    response.reason = text
    response._content = json.dumps({"detail": text}).encode()
    response.request = requests.Request(
        "GET", ADDRESS, headers={"Authorization": f"Token {SPECIAL_CREDENTIAL}"}
    ).prepare()
    return requests.HTTPError(text, response=response)


def _netbox_status(status: int, text: str) -> Exception:
    """The `RequestError` that pynetbox raises for a refused request."""
    import pytest

    query = pytest.importorskip("pynetbox.core.query")
    response = _requests_status(status, text).response
    assert response is not None
    return query.RequestError(response)


def _infrahub_status(status: int, text: str) -> AuthenticationError:
    """The SDK's `AuthenticationError`, raised `from` the HTTP error of a refused request."""
    request = httpx.Request(
        "POST", ADDRESS, headers={"Authorization": f"Token {SPECIAL_CREDENTIAL}"}, content=text.encode()
    )
    body = {"errors": [{"message": f"You do not have the following permission: {PERMISSION}"}]} if status == 403 else {}
    response = httpx.Response(status, request=request, json=body)
    return _chained(  # ty: ignore[invalid-return-type]
        AuthenticationError(text), httpx.HTTPStatusError(text, request=request, response=response)
    )


def _tls_refused(text: str) -> Exception:
    return _chained(requests.exceptions.SSLError(text), ssl.SSLCertVerificationError(text))


def _peer_refusal(text: str, *, matches: int) -> Exception:
    """The refusal the apply's peer resolver raises for the site a server create refers to."""
    client = RecordingClient()
    client.filter_results = [[object() for _ in range(matches)]]  # ty: ignore[invalid-assignment]
    adapter = make_adapter(client)
    try:
        adapter.apply_planned_operation(operation=server_operation("server-a"), peers=PeerResolver(adapter))
    except Exception as refusal:  # noqa: BLE001 - the case keeps whichever refusal the resolver raised.
        refusal.args = (f"{refusal} {text}",)
        return _apply_failed(refusal, text)
    msg = "The peer resolver did not refuse the operation."
    raise AssertionError(msg)


def _transform_failed(text: str) -> Exception:
    error = TransformError(f"Failed to transform 'name' with '{{{{ {text} }}}}'", field="name", position=0)
    return _side("source", "netbox", _chained(error, RuntimeError(text)), text)


# Every failure type of T046, with the fields its record holds.
CASES: tuple[FailureCase, ...] = (
    FailureCase(
        "rejected-write-with-code",
        "apply",
        lambda text: _rejected_write(text, with_code=True),
        "destination_rejected_write",
        {
            "system": DESTINATION,
            "error_code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
            "node_kind": "DcimDevice",
            "attribute": "status",
            "infrahub_message": INFRAHUB_MESSAGE,
        },
    ),
    FailureCase(
        "rejected-write-without-code",
        "apply",
        lambda text: _rejected_write(text, with_code=False),
        "destination_rejected_write",
        {"system": DESTINATION, "error_code": None, "infrahub_message": INFRAHUB_MESSAGE},
    ),
    FailureCase(
        "source-unreachable",
        "plan",
        lambda text: _side("source", "netbox", requests.ConnectionError(text), text),
        "system_unreachable",
        {"system": SOURCE},
    ),
    FailureCase(
        "destination-unreachable",
        "plan",
        lambda text: _side("destination", "infrahub", ServerNotReachableError(address=ADDRESS, message=text), text),
        "system_unreachable",
        {"system": DESTINATION},
    ),
    FailureCase(
        "source-timeout",
        "plan",
        lambda text: _side("source", "netbox", requests.ReadTimeout(text), text),
        "system_timeout",
        {"system": SOURCE},
    ),
    FailureCase(
        "destination-timeout",
        "plan",
        lambda text: _side(
            "destination", "infrahub", ServerNotResponsiveError(url=ADDRESS, timeout=5, message=text), text
        ),
        "system_timeout",
        {"system": DESTINATION},
    ),
    FailureCase(
        "source-token-refused",
        "plan",
        lambda text: _side("source", "netbox", _requests_status(401, text), text),
        "token_rejected",
        {"system": SOURCE},
    ),
    FailureCase(
        "source-token-refused-by-netbox",
        "plan",
        lambda text: _side("source", "netbox", _netbox_status(401, text), text),
        "token_rejected",
        {"system": SOURCE},
    ),
    FailureCase(
        "source-permission-missing-in-netbox",
        "plan",
        lambda text: _side("source", "netbox", _netbox_status(403, text), text),
        "permission_missing",
        {"system": SOURCE},
    ),
    FailureCase(
        "destination-token-refused",
        "plan",
        lambda text: _side("destination", "infrahub", _infrahub_status(401, text), text),
        "token_rejected",
        {"system": DESTINATION},
    ),
    FailureCase(
        "permission-missing",
        "plan",
        lambda text: _side("destination", "infrahub", _infrahub_status(403, text), text),
        "permission_missing",
        {"system": DESTINATION},
    ),
    FailureCase(
        "tls-certificate-refused",
        "plan",
        lambda text: _side("source", "netbox", _tls_refused(text), text),
        "tls_certificate_rejected",
        {"system": SOURCE},
    ),
    FailureCase(
        "lineage-refused",
        "plan",
        lambda text: _chained(
            NamedLineageNodeNotFoundError(setting="source", kind="CoreAccount", name="netbox"), RuntimeError(text)
        ),
        "lineage_refused",
    ),
    FailureCase(
        "destination-schema-unavailable",
        "plan",
        lambda text: _chained(
            DestinationSchemaUnavailableError(reason="timeout", branch="main"),
            ServerNotResponsiveError(url=ADDRESS, message=text),
        ),
        "destination_schema_unavailable",
    ),
    FailureCase(
        "destination-peer-missing",
        "apply",
        lambda text: _peer_refusal(text, matches=0),
        "destination_peer_missing",
        {"system": DESTINATION, "node_kind": SITE_KIND},
    ),
    FailureCase(
        "destination-peer-ambiguous",
        "apply",
        lambda text: _peer_refusal(text, matches=2),
        "destination_peer_ambiguous",
        {"system": DESTINATION, "node_kind": SITE_KIND},
    ),
    FailureCase(
        "record-load-failed",
        "plan",
        lambda text: _side(
            "source",
            "netbox",
            _chained(RecordLoadError(kind="DcimDevice", source_id="42", field="name"), RuntimeError(text)),
            text,
        ),
        "record_load_failed",
        {"system": SOURCE, "record": {"kind": "DcimDevice", "source_id": "42"}, "field": "name"},
    ),
    FailureCase("transform-failed", "plan", _transform_failed, "transform_failed", {"system": SOURCE, "field": "name"}),
    FailureCase(
        "credential-missing",
        "plan",
        lambda text: CredentialConfigurationError(f"environment credential '{text}' is missing or empty"),
        "credential_missing",
    ),
    FailureCase("verification-failed", "apply", PlanVerificationError, "verification_failed"),
    FailureCase(
        "apply-refused",
        "apply",
        lambda text: _chained(LeftOutConfirmationRequiredError(2, run_id="run-failure-case"), RuntimeError(text)),
        "apply_refused",
    ),
    FailureCase("unknown-class", "plan", RuntimeError, "internal_error"),
    FailureCase(
        "unknown-class-in-a-side-read",
        "plan",
        lambda text: _side("source", "netbox", RuntimeError(text), text),
        "internal_error",
        {"system": SOURCE},
    ),
)

__all__ = [
    "ADDRESS",
    "CASES",
    "CREDENTIALS",
    "ERROR_TEXT",
    "FAILED_OPERATION",
    "NEUTRAL_TEXT",
    "SHORT_CREDENTIAL",
    "SHORT_CREDENTIAL_NAME",
    "SPECIAL_CREDENTIAL",
    "SPECIAL_CREDENTIAL_NAME",
    "SPECIAL_CREDENTIAL_RUN",
    "FailureCase",
    "forbidden_values",
]
