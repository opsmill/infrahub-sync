"""The cause a failed run records for the operator, beside the error's class name.

The service worker records a failure's class name and the class of its immediate cause, and,
since spec 019, a structured cause (ADR 0021): a failure type from a fixed list, the system
involved, Infrahub's error code, node kind, attribute name and message for a rejected write, the
kind, record and field a typed Sync error names, and text from fixed templates filled with
those values. `failure_record` builds those fields; `operator_cause` builds the cause and next
action of spec 017.

Every value comes from a class name, an HTTP status code, a typed attribute this repository
defines, or one of the four values Infrahub returns with a rejected write
(`infrahub_sync.infrahub_errors.rejected_write`). No value is built from the text of an
exception, from an address, a request header or a traceback, so no credential can enter the
record (spec 019 FR-019, FR-029). Every value is stripped of control characters and capped, and
the cause, the next action and Infrahub's message are still redacted.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import requests
from infrahub_sdk.exceptions import Error as InfrahubSDKError
from infrahub_sdk.exceptions import GraphQLError, ServerNotReachableError, ServerNotResponsiveError

from infrahub_sync import RecordLoadError, TransformError
from infrahub_sync.client.models import MAX_FAILURE_CAUSE_TEXT, MAX_FAILURE_NAME_TEXT
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.execution import redact
from infrahub_sync.infrahub_errors import (
    MAX_REJECTED_WRITE_MESSAGE,
    MISSING_PERMISSION_STATUS,
    REFUSED_TOKEN_STATUS,
    cause_chain,
    classify_infrahub_error_chain,
    quotable_text,
    rejected_write,
)
from infrahub_sync.plan.errors import (
    LINEAGE_REFUSALS,
    LeftOutConfirmationRequiredError,
    OperationApplyFailedError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanArtifactError,
    PlanFormatApplyUnsupportedError,
    PlanSchemaChangedError,
    PlanVerificationError,
)
from infrahub_sync.potenda import SideLoadError
from infrahub_sync.runtime_schema.errors import DestinationSchemaUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from infrahub_sync.client.models import FailureType
    from infrahub_sync.plan.models import ApplyRecord

# The adapter an apply writes through. A V3 apply writes only to Infrahub (ADR 0014).
APPLY_DESTINATION_ADAPTER = "infrahub"

# The keys `failure_record` adds to `<stage>_failure` (spec 019 data-model.md). Each one is set
# on every record the worker writes, `None` when the failure does not name it.
FAILURE_RECORD_KEYS = (
    "failure_type",
    "system",
    "error_code",
    "node_kind",
    "attribute",
    "infrahub_message",
    "record",
    "field",
)


def _recordable(text: str, secrets: Sequence[str]) -> str:
    """Return text fit to record and print: redacted, without control characters, capped."""
    return quotable_text(redact(text, secrets), limit=MAX_FAILURE_CAUSE_TEXT)


def _name(value: object) -> str | None:
    """A typed name (a kind, a field, an ID, an adapter) fit to record, else `None`."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    return quotable_text(value, limit=MAX_FAILURE_NAME_TEXT) or None


def operator_cause(exc: BaseException, secrets: Sequence[str]) -> dict[str, Any]:
    """The cause and next action an operator acts on, from the cause chain.

    A plan wraps each side's load failure in a `SideLoadError` raised `from` the original, and
    the lineage resolution that starts a plan, and an apply, raise the original, so the walk
    covers both stages.

    - A lineage refusal (`LINEAGE_REFUSALS`) records its `message` as `cause` and its
      `next_action`. This repository writes those messages, and they
      name settings, kinds, node names, account names and ids only.
    - A destination schema read failure (`DestinationSchemaUnavailableError`, and its subclass
      `DestinationBranchNotFoundError` for a missing branch) records its `message` as `cause`
      and its `next_action` the same way. Both come from one fixed template per reason, filled
      with the branch and, for a refused token or a missing permission, the cause
      `InfrahubErrorClassification.describe` returns.
    - Otherwise, the first link that carries a non-empty `next_action` this repository writes
      (a `PlanArtifactError`) records that
      `next_action`, and never its message.

    The search for a next action follows only the explicit `raise ... from` links, never an
    error that was being handled when another was raised. The Infrahub classification below
    walks the whole chain.
    - A refused token or a missing permission records `infrahub_error` and its description as
      `cause`. The description quotes only the permission Infrahub names, or a
      capped server message, never the request or the token.

    Every text is redacted, stripped of control characters and capped, like every value a
    run records. Any other message records nothing here: its text can be a destination's
    response.
    """
    # The explicit `raise ... from` links only: an unrelated error that was being handled when
    # `exc` was raised (its `__context__`) must not lend `exc` its next action.
    links = tuple(cause_chain(exc, follow_context=False))
    for link in links:
        if isinstance(link, (*LINEAGE_REFUSALS, DestinationSchemaUnavailableError)):
            return {
                "cause": _recordable(link.message, secrets),
                "next_action": _recordable(link.next_action, secrets),
            }
    recorded: dict[str, Any] = {}
    next_action = next(
        (link.next_action for link in links if isinstance(link, PlanArtifactError) and link.next_action), ""
    )
    if next_action:
        recorded["next_action"] = _recordable(next_action, secrets)
    classification = classify_infrahub_error_chain(exc)
    if classification is not None:
        recorded["infrahub_error"] = {
            "problem": classification.problem,
            "http_status": classification.http_status,
            "permissions": list(classification.permissions),
        }
        recorded["cause"] = _recordable(classification.describe(), secrets)
    return recorded


# The classification table for connection, timeout and token failures (spec 019 plan.md, open
# risk R12). It reads the exception type and the HTTP status code attribute only, never text.
#
# | Library and adapters | Exception type | Failure type |
# | --- | --- | --- |
# | Python `ssl` (any adapter) | `ssl.SSLCertVerificationError` | `tls_certificate_rejected` |
# | `requests` (netbox, nautobot, aci, prometheus, peeringmanager, genericrestapi) | `requests.exceptions.SSLError` | `tls_certificate_rejected` |
# | `requests` | `requests.Timeout` (`ReadTimeout`, `ConnectTimeout`) | `system_timeout` |
# | `requests` | `requests.ConnectionError` | `system_unreachable` |
# | `requests` | `requests.HTTPError` with `response.status_code` 401 or 403 | `token_rejected`, `permission_missing` |
# | pynetbox and pynautobot (netbox, nautobot) | `RequestError` with `req.status_code` 401 or 403 | `token_rejected`, `permission_missing` |
# | `httpx` (infrahub, ipfabricsync, slurpitsync) | `httpx.TimeoutException` | `system_timeout` |
# | `httpx` | `httpx.TransportError` | `system_unreachable` |
# | `httpx` | `httpx.HTTPStatusError` with `response.status_code` 401 or 403 | `token_rejected`, `permission_missing` |
# | infrahub-sdk (infrahub) | `ServerNotResponsiveError` | `system_timeout` |
# | infrahub-sdk (infrahub) | `ServerNotReachableError` | `system_unreachable` |
# | infrahub-sdk (infrahub) | `AuthenticationError`, a GraphQL permission denial | `token_rejected`, `permission_missing` |
# | Python (any adapter) | `TimeoutError` | `system_timeout` |
# | Python (any adapter) | `ConnectionError` | `system_unreachable` |
#
# The certificate checks come first: `requests.exceptions.SSLError` is a `ConnectionError`. The
# timeout checks come before the connection checks: `requests.ConnectTimeout` is both.
_TLS_ERRORS: tuple[type[BaseException], ...] = (ssl.SSLCertVerificationError, requests.exceptions.SSLError)
_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
    requests.Timeout,
    httpx.TimeoutException,
    ServerNotResponsiveError,
    TimeoutError,
)
_UNREACHABLE_ERRORS: tuple[type[BaseException], ...] = (
    requests.ConnectionError,
    httpx.TransportError,
    ServerNotReachableError,
    ConnectionError,
)
# The modules whose `RequestError` carries the refused response as `req` (pynetbox, pynautobot).
_REQUEST_ERROR_MODULES = ("pynetbox.", "pynautobot.")


def _http_status(link: BaseException) -> int | None:
    """The HTTP status code of a refused request, from the exception's typed response attribute."""
    response: object = None
    if isinstance(link, (requests.HTTPError, httpx.HTTPStatusError)):
        response = link.response
    elif type(link).__name__ == "RequestError" and type(link).__module__.startswith(_REQUEST_ERROR_MODULES):
        response = getattr(link, "req", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _transport_failure_type(links: Sequence[BaseException]) -> FailureType | None:
    """The failure type of a refused token or permission, a certificate, a timeout or a connection."""
    classification = classify_infrahub_error_chain(links[0])
    if classification is not None:
        return "token_rejected" if classification.problem == "refused_token" else "permission_missing"
    for link in links:
        status = _http_status(link)
        if status == REFUSED_TOKEN_STATUS:
            return "token_rejected"
        if status == MISSING_PERMISSION_STATUS:
            return "permission_missing"
    for errors, failure_type in (
        (_TLS_ERRORS, "tls_certificate_rejected"),
        (_TIMEOUT_ERRORS, "system_timeout"),
        (_UNREACHABLE_ERRORS, "system_unreachable"),
    ):
        if any(isinstance(link, errors) for link in links):
            return failure_type
    return None


@dataclass(frozen=True, slots=True)
class _Classified:
    """The failure type, and the link of the chain that decided it."""

    failure_type: FailureType
    link: BaseException | None = None


# The typed Sync errors, in the order they decide a failure type. Each one is found on the
# explicit `raise ... from` links only.
_TYPED_ERRORS: tuple[tuple[tuple[type[BaseException], ...], FailureType], ...] = (
    (LINEAGE_REFUSALS, "lineage_refused"),
    ((DestinationSchemaUnavailableError,), "destination_schema_unavailable"),
    ((CredentialConfigurationError,), "credential_missing"),
    ((LeftOutConfirmationRequiredError, PlanFormatApplyUnsupportedError), "apply_refused"),
    ((PlanVerificationError, PlanSchemaChangedError), "verification_failed"),
    ((PeerNotFoundError,), "destination_peer_missing"),
    ((PeerAmbiguousError,), "destination_peer_ambiguous"),
    ((RecordLoadError,), "record_load_failed"),
    ((TransformError,), "transform_failed"),
)


def _classify(exc: BaseException) -> _Classified:
    """Map the cause chain to one failure type through the fixed table, else `internal_error`."""
    explicit = tuple(cause_chain(exc, follow_context=False))
    for errors, failure_type in _TYPED_ERRORS:
        link = next((link for link in explicit if isinstance(link, errors)), None)
        if link is not None:
            if failure_type == "record_load_failed":
                # The read loop raises every unrecorded failure of a record as `RecordLoadError`,
                # including a source that stopped answering while the record was converted (a
                # peer kind read, for example). That failure names the system, not the record
                # (FR-017), so the transport classification wins; the record is still named.
                transport = _transport_failure_type(tuple(cause_chain(exc)))
                if transport is not None:
                    return _Classified(transport, link)
            return _Classified(failure_type, link)
    # A certificate error can be set as the `__context__` of the transport error a library
    # raises, so the transport checks walk the whole chain.
    transport = _transport_failure_type(tuple(cause_chain(exc)))
    if transport is not None:
        return _Classified(transport)
    if any(isinstance(link, OperationApplyFailedError) for link in explicit) and any(
        isinstance(link, GraphQLError) for link in cause_chain(exc)
    ):
        return _Classified("destination_rejected_write")
    return _Classified("internal_error")


# The failure types whose failure involves the destination even when no side read failed.
_DESTINATION_TYPES: frozenset[str] = frozenset(
    {
        "destination_rejected_write",
        "destination_peer_missing",
        "destination_peer_ambiguous",
        "destination_schema_unavailable",
        "lineage_refused",
    }
)


def _system(exc: BaseException, failure_type: FailureType, *, destination_adapter: str) -> dict[str, str] | None:
    """The role and adapter of the one system involved, or `None` when no one system is.

    A side read names its side (`SideLoadError`). Otherwise the destination is the system when
    the failure type is one of the destination's, when an apply operation failed, or when the
    chain holds an infrahub-sdk error: only the destination is read through the SDK outside a
    side read (the lineage resolution, the schema read, an apply).
    """
    explicit = tuple(cause_chain(exc, follow_context=False))
    side = next((link for link in explicit if isinstance(link, SideLoadError)), None)
    if side is not None:
        adapter = _name(side.adapter)
        return {"role": side.role, "adapter": adapter} if adapter else None
    if (
        failure_type in _DESTINATION_TYPES
        or any(isinstance(link, OperationApplyFailedError) for link in explicit)
        or any(isinstance(link, InfrahubSDKError) for link in cause_chain(exc))
        or classify_infrahub_error_chain(exc) is not None
    ):
        return {"role": "destination", "adapter": destination_adapter}
    return None


def _where(system: dict[str, str] | None) -> str:
    """The system as a cause names it: "The source (netbox)", or "The system" when none is known."""
    if system is None:
        return "A system"
    return f"The {system['role']} ({system['adapter']})"


_Template = tuple[str | None, str | None]


def _rejected_write_template(fields: dict[str, Any], _where: str, _adapter: str) -> _Template:
    cause = "Infrahub rejected a write"
    if fields["node_kind"]:
        cause += f" to {fields['node_kind']}"
    if fields["attribute"]:
        cause += f" attribute {fields['attribute']}"
    if fields["error_code"]:
        cause += f" ({fields['error_code']})"
    return cause, None


def _record_load_template(fields: dict[str, Any], _where: str, _adapter: str) -> _Template:
    record = fields["record"] or {}
    cause = f"A {record.get('kind')} record (source ID {record.get('source_id')}) could not be loaded"
    if fields["field"]:
        cause += f": field {fields['field']}"
    return cause, f"Correct that record in the source, {_PLAN_AGAIN}"


_PLAN_AGAIN = "then plan again."
_REACH = "Check that {adapter} answers at the address the configuration names, " + _PLAN_AGAIN

# One fixed template per failure type: the cause and the next action, filled with the recorded
# fields, the system ("The source (netbox)") and the adapter name. `internal_error` has none: an
# error class with no template records only its class name. A type whose error carries its own
# next action (`PlanArtifactError`) leaves the next action to `operator_cause`.
_TEMPLATES: dict[str, Callable[[dict[str, Any], str, str], _Template]] = {
    "destination_rejected_write": _rejected_write_template,
    "system_unreachable": lambda _f, where, adapter: (
        f"{where} could not be reached",
        _REACH.format(adapter=adapter),
    ),
    "system_timeout": lambda _f, where, adapter: (f"{where} did not answer in time", _REACH.format(adapter=adapter)),
    "token_rejected": lambda _f, where, adapter: (
        f"{where} rejected the token",
        f"Check the credential that the configuration references for {adapter}, {_PLAN_AGAIN}",
    ),
    "permission_missing": lambda _f, where, adapter: (
        f"{where} refused the request: the account lacks a permission",
        f"Give the account that the configuration references for {adapter} the permission it needs, {_PLAN_AGAIN}",
    ),
    "tls_certificate_rejected": lambda _f, where, adapter: (
        f"{where} presented a TLS certificate that was not accepted",
        f"Check the certificate of {adapter}, or the certificate authorities the Sync worker trusts, {_PLAN_AGAIN}",
    ),
    "record_load_failed": _record_load_template,
    "transform_failed": lambda fields, where, _adapter: (
        f"A transform of field {fields['field']} failed for a record of {where[0].lower()}{where[1:]}",
        f"Correct the transform of field {fields['field']} in the configuration, or the source value it reads, "
        f"{_PLAN_AGAIN}",
    ),
    "credential_missing": lambda _f, _where, _adapter: (
        "A credential that the configuration references is missing or not usable",
        f"Set the credential that the configuration references, {_PLAN_AGAIN}",
    ),
    "destination_peer_missing": lambda fields, _where, _adapter: (
        f"No {fields['node_kind']} node at the destination matches a peer that the operation refers to",
        None,
    ),
    "destination_peer_ambiguous": lambda fields, _where, _adapter: (
        f"More than one {fields['node_kind']} node at the destination matches a peer that the operation refers to",
        None,
    ),
}


def _templates(fields: dict[str, Any]) -> _Template:
    """The cause and the next action of one failure type, from fixed templates over recorded fields."""
    template = _TEMPLATES.get(fields["failure_type"])
    if template is None:
        return None, None
    system = fields["system"]
    return template(fields, _where(system), system["adapter"] if system else "the system")


def _record_reference(link: BaseException | None) -> dict[str, Any] | None:
    """The source record a `RecordLoadError` names: its kind and source ID."""
    if not isinstance(link, RecordLoadError):
        return None
    kind = _name(link.kind)
    if kind is None:
        return None
    return {"kind": kind, "source_id": _name(link.source_id)}


def _node_kind(classified: _Classified, written: Any) -> str | None:
    link = classified.link
    if isinstance(link, (PeerNotFoundError, PeerAmbiguousError)):
        return _name(getattr(link, "peer_kind", None))
    if isinstance(link, RecordLoadError):
        return _name(link.kind)
    if written is not None:
        return written.node_kind
    return None


def failure_record(
    stage: str,
    exc: BaseException,
    *,
    secrets: Sequence[str] = (),
    destination_adapter: str = APPLY_DESTINATION_ADAPTER,
) -> dict[str, Any]:
    """The spec 019 fields of a `<stage>_failure` record, and a template cause and next action.

    Returns every key of `FAILURE_RECORD_KEYS`, `None` where the failure does not name it, and
    `cause` and `next_action` when a template exists for the failure type. The caller keeps the
    `cause` and `next_action` that `operator_cause` returns where it returns one, so the keys
    spec 017 records keep their values (FR-014).

    The failure type comes from the fixed table of research R12. The values come from typed
    attributes, HTTP status codes and Infrahub's own description of a rejected write, never from
    the text of an exception (FR-019). The function does not raise: any value of an unexpected
    shape is recorded as `None`. The failure type does not depend on `stage`: the caller records
    the stage, and the same error means the same failure in every stage.
    """
    del stage
    classified = _classify(exc)
    failure_type = classified.failure_type
    written = rejected_write(exc) if failure_type == "destination_rejected_write" else None
    link = classified.link
    field_name = _name(getattr(link, "field", None)) if isinstance(link, (RecordLoadError, TransformError)) else None
    fields: dict[str, Any] = {
        "failure_type": failure_type,
        "system": _system(
            exc, failure_type, destination_adapter=_name(destination_adapter) or APPLY_DESTINATION_ADAPTER
        ),
        "error_code": written.error_code if written else None,
        "node_kind": _node_kind(classified, written),
        "attribute": written.attribute if written else None,
        "infrahub_message": (
            quotable_text(
                redact(written.message, secrets), limit=min(MAX_REJECTED_WRITE_MESSAGE, MAX_FAILURE_CAUSE_TEXT)
            )
            or None
        )
        if written and written.message
        else None,
        "record": _record_reference(link),
        "field": field_name,
    }
    cause, next_action = _templates(fields)
    recorded = dict(fields)
    if cause:
        recorded["cause"] = _recordable(cause, secrets)
    if next_action:
        recorded["next_action"] = _recordable(next_action, secrets)
    return recorded


def _identity_value(value: object, depth: int = 0) -> object:
    """An identity value fit to record: a scalar, a capped string, or a small nested mapping or list."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return quotable_text(value, limit=MAX_FAILURE_NAME_TEXT)
    if depth < _MAX_IDENTITY_DEPTH and isinstance(value, dict):
        return {str(key)[:MAX_FAILURE_NAME_TEXT]: _identity_value(item, depth + 1) for key, item in value.items()}
    if depth < _MAX_IDENTITY_DEPTH and isinstance(value, (list, tuple)):
        return [_identity_value(item, depth + 1) for item in value]
    return None


_MAX_IDENTITY_DEPTH = 3


def apply_progress(record: ApplyRecord | None) -> dict[str, Any]:
    """The plan's operation count, the run's written count and the failed operation, when known.

    The engine reads them from the plan (`Potenda.apply_plan`). `operations_written` counts the
    creates and updates of every execution of the run, through the position a resume started
    after (spec 022 research R6). The identity is the source record identity the plan holds,
    which FR-019 allows; each string is capped.
    """
    if record is None:
        return {}
    progress: dict[str, Any] = {}
    total = record.operations_total
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        progress["operations_total"] = total
    written = record.operations_written
    if isinstance(written, int) and not isinstance(written, bool) and written >= 0:
        progress["operations_written"] = written
    detail = record.failed_operation_detail
    if record.failed_operation is not None and detail is not None:
        action, kind = _name(detail.get("action")), _name(detail.get("kind"))
        if action and kind:
            identity = detail.get("identity")
            progress["failed_operation_detail"] = {
                "action": action,
                "kind": kind,
                "identity": _identity_value(identity) if isinstance(identity, dict) else None,
            }
    return progress
