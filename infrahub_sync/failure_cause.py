"""The structured cause of a failed or stopped run (spec 009 FR-014 to FR-020, ADR 0021).

`build_failure_cause` is the one place where a run failure becomes text that leaves the worker.
It classifies the failure by the exception types in the cause chain and reads only named,
typed attributes: Infrahub's error code, kind, attribute and message from a `GraphQLError`,
the kind, record ID and field of a typed Sync error, and the side and adapter name of a
`SideLoadError`. It never formats an exception (`str(exc)`, `repr(exc)`, `exc.args`), an
address, a header or a traceback, so no credential can reach a cause, whatever its length or
characters. Each field is limited in length; `summary` and `next_action` come from fixed
templates.

An exception class outside this module's imports declares its failure type with a class-level
`cause_failure_type`, for example `RegisteredPlanVerificationError` in the service flow.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

import httpx
import pydantic
import requests
from infrahub_sdk.exceptions import (
    AuthenticationError,
    GraphQLError,
    ServerNotReachableError,
    ServerNotResponsiveError,
)

from infrahub_sync import RecordLoadError, SourceRecordReference
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.load_errors import SideLoadError, TransformError
from infrahub_sync.plan.errors import (
    LeftOutConfirmationError,
    LeftOutPeerInOperationError,
    PlanFormatApplyUnsupportedError,
    PlanSchemaChangedError,
    PlanVerificationError,
    ReviewedPayloadFieldMissingError,
    UnkeyedCreateRefusedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

try:
    from pynetbox.core.query import RequestError as _NetboxRequestError

    _NETBOX_REQUEST_ERRORS: tuple[type[Exception], ...] = (_NetboxRequestError,)
except ImportError:  # pynetbox is an optional dependency
    _NETBOX_REQUEST_ERRORS = ()

FailureStage = Literal["plan", "verify", "apply", "sync"]
FailureType = Literal[
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
]

NAME_LIMIT = 128
MESSAGE_LIMIT = 1024
# The chain is walked to this depth at most; a real chain is a handful of links.
_CHAIN_LIMIT = 32
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_REJECTED_TOKEN_STATUSES = frozenset({401, 403})
_WRITE_STAGES = frozenset({"apply", "sync"})
_DECLARED_TYPES = frozenset({"verification_failed", "apply_refused", "credential_missing"})

NEXT_ACTIONS: dict[str, str] = {
    "destination_rejected_write": "Fix the cause, then create and review a new plan.",
    "system_unreachable": "Check that the system is running and that the worker can reach it, then run the stage again.",
    "system_timeout": "Check the load on the system and its response time, then run the stage again.",
    "token_rejected": "Check the token that the worker uses for this system, then run the stage again.",
    "tls_certificate_rejected": (
        "Add the CA certificate that signed the system's certificate to the directory that "
        "`INFRAHUB_SYNC_CA_CERTIFICATES` names, restart the services, then run the stage again."
    ),
    "record_load_failed": "Fix the record in the source or the mapping of its field, then create a new plan.",
    "transform_failed": "Fix the transform of the field or the record in the source, then create a new plan.",
    "credential_missing": "Set the credential that the configuration references in the worker, then run the stage again.",
    "verification_failed": "Create and review a new plan, then apply that plan.",
    "apply_refused": "Fix the reason the apply was refused, then apply the plan again. The destination was not changed.",
    "left_out_review_required": (
        "Review the left-out records with `infrahub-sync runs left-out`, then apply the plan with "
        "`infrahub-sync apply --confirm-left-out` and the plan's left-out count."
    ),
    "internal_error": "Run the stage again. If it fails again, report the error class to OpsMill.",
}

_UNREACHABLE = (requests.ConnectionError, httpx.ConnectError, ServerNotReachableError)
_TIMEOUT = (requests.Timeout, httpx.TimeoutException, ServerNotResponsiveError)
_VERIFICATION = (PlanVerificationError, PlanSchemaChangedError)
_REFUSED = (
    LeftOutConfirmationError,
    LeftOutPeerInOperationError,
    PlanFormatApplyUnsupportedError,
    ReviewedPayloadFieldMissingError,
    UnkeyedCreateRefusedError,
)


class FailureSystem(pydantic.BaseModel):
    """The system a failure came from: its role in the sync and its adapter name."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    role: Literal["source", "destination"]
    adapter: str = pydantic.Field(max_length=NAME_LIMIT)


class FailedOperation(pydantic.BaseModel):
    """The plan operation an apply stopped at, read from the saved plan by its ID."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    action: str
    kind: str
    identity: dict[str, Any] | None = None


class ApplyProgress(pydantic.BaseModel):
    """How far a stopped apply got (data-model.md "Apply progress", FR-020)."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    operations_total: int = pydantic.Field(ge=0)
    operations_written: int = pydantic.Field(ge=0)
    failed_operation: FailedOperation | None = None
    failed_operation_may_have_written: bool | None = None


class RunFailureCause(pydantic.BaseModel):
    """The cause of a failed or stopped run (data-model.md "Run failure cause").

    No field holds the text of an exception, an address, a header or a traceback.
    """

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    stage: FailureStage
    failure_type: FailureType
    system: FailureSystem | None = None
    error_code: str | None = pydantic.Field(default=None, max_length=NAME_LIMIT)
    node_kind: str | None = pydantic.Field(default=None, max_length=NAME_LIMIT)
    attribute: str | None = pydantic.Field(default=None, max_length=NAME_LIMIT)
    record: SourceRecordReference | None = None
    field: str | None = pydantic.Field(default=None, max_length=NAME_LIMIT)
    infrahub_message: str | None = pydantic.Field(default=None, max_length=MESSAGE_LIMIT)
    summary: str
    error_class: str | None = pydantic.Field(default=None, max_length=NAME_LIMIT)
    apply_progress: ApplyProgress | None = None
    next_action: str


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """Every exception in the cause and context chain, outermost first, each once.

    A suppressed context is walked too: only its type is read, and an HTTP library can hide
    the TLS error that decided the failure there.
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(seen) < _CHAIN_LIMIT:
        link = pending.pop(0)
        if id(link) in seen:
            continue
        seen.add(id(link))
        yield link
        pending.extend(item for item in (link.__cause__, link.__context__) if item is not None)


def _name(value: object) -> str | None:
    """A name field: a string cut to its limit, or None for any other value."""
    return value[:NAME_LIMIT] if isinstance(value, str) and value else None


def _http_status(link: BaseException) -> int | None:
    response: object = None
    if isinstance(link, (requests.HTTPError, httpx.HTTPStatusError)):
        response = link.response
    elif _NETBOX_REQUEST_ERRORS and isinstance(link, _NETBOX_REQUEST_ERRORS):
        response = getattr(link, "req", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _declares(link: BaseException, failure_type: str) -> bool:
    return getattr(type(link), "cause_failure_type", None) == failure_type and failure_type in _DECLARED_TYPES


def _rules(stage: str) -> list[tuple[FailureType, Callable[[BaseException], bool]]]:
    """The failure types in the order they are tried; the first type found in the chain wins."""
    return [
        ("tls_certificate_rejected", lambda link: isinstance(link, ssl.SSLCertVerificationError)),
        (
            "token_rejected",
            lambda link: isinstance(link, AuthenticationError) or _http_status(link) in _REJECTED_TOKEN_STATUSES,
        ),
        ("system_timeout", lambda link: isinstance(link, _TIMEOUT)),
        ("system_unreachable", lambda link: isinstance(link, _UNREACHABLE)),
        ("destination_rejected_write", lambda link: stage in _WRITE_STAGES and isinstance(link, GraphQLError)),
        ("record_load_failed", lambda link: isinstance(link, RecordLoadError)),
        ("transform_failed", lambda link: isinstance(link, TransformError)),
        (
            "credential_missing",
            lambda link: isinstance(link, CredentialConfigurationError) or _declares(link, "credential_missing"),
        ),
        ("verification_failed", lambda link: isinstance(link, _VERIFICATION) or _declares(link, "verification_failed")),
        ("apply_refused", lambda link: isinstance(link, _REFUSED) or _declares(link, "apply_refused")),
    ]


def _classify(stage: str, chain: list[BaseException]) -> tuple[FailureType, BaseException | None]:
    for failure_type, matches in _rules(stage):
        for link in chain:
            if matches(link):
                return failure_type, link
    return "internal_error", None


def _system(chain: list[BaseException]) -> FailureSystem | None:
    for link in chain:
        if isinstance(link, SideLoadError) and link.side in {"source", "destination"}:
            adapter = _name(link.adapter)
            return None if adapter is None else FailureSystem(role=link.side, adapter=adapter)
    return None


def _infrahub_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _CONTROL_CHARACTERS.sub("", value)[:MESSAGE_LIMIT] or None


def _rejection_fields(link: BaseException) -> dict[str, Any]:
    """Infrahub's code, kind, attribute and message, from the first error it returned."""
    errors = getattr(link, "errors", None)
    first = errors[0] if isinstance(errors, list) and errors else None
    if not isinstance(first, Mapping):
        return {}
    extensions = first.get("extensions")
    extensions = extensions if isinstance(extensions, Mapping) else {}
    return {
        "error_code": _name(extensions.get("code")),
        "node_kind": _name(extensions.get("node_kind")),
        "attribute": _name(extensions.get("attribute")),
        "infrahub_message": _infrahub_message(first.get("message")),
    }


def _record_fields(link: RecordLoadError | TransformError) -> dict[str, Any]:
    kind = _name(link.kind)
    source_id = _name(link.source_id)
    record = None if kind is None else SourceRecordReference(kind=kind, source_id=source_id)
    return {"node_kind": kind, "record": record, "field": _name(link.field)}


def _system_phrase(system: FailureSystem | None) -> str:
    return "a system" if system is None else f"the {system.role} system ({system.adapter})"


# Fixed sentences; the templated failure types are built by `_summary` below.
_FIXED_SUMMARIES: dict[str, str] = {
    "credential_missing": "A credential that the configuration references is missing or not valid",
    "verification_failed": "The saved plan did not pass verification",
    "apply_refused": "The apply was refused before any write",
    "left_out_review_required": "The plan leaves out records, so the sync stopped before writing",
}


def _capitalized(text: str) -> str:
    return f"{text[0].upper()}{text[1:]}"


def _rejection_summary(fields: Mapping[str, Any]) -> str:
    kind = f" to {fields['node_kind']}" if fields.get("node_kind") else ""
    code = f" with error code {fields['error_code']}" if fields.get("error_code") else ""
    return f"The destination rejected a write{kind}{code}"


def _record_summary(failure_type: str, fields: Mapping[str, Any]) -> str:
    record = fields.get("record")
    named = "a record" if record is None else f"the {record.kind} record {record.source_id or 'with no ID'}"
    if failure_type == "transform_failed":
        return f"The transform of field {fields.get('field') or 'unknown'} failed for {named}"
    field = f" (field {fields['field']})" if fields.get("field") else ""
    return f"{_capitalized(named)} could not be loaded{field}"


def _summary(failure_type: FailureType, stage: str, fields: Mapping[str, Any]) -> str:
    """One sentence from the fixed template of `failure_type` over the cause's fields."""
    system = _system_phrase(fields.get("system"))
    templated: dict[str, Callable[[], str]] = {
        "destination_rejected_write": lambda: _rejection_summary(fields),
        "system_unreachable": lambda: f"The connection to {system} failed",
        "system_timeout": lambda: f"{_capitalized(system)} did not respond in time",
        "token_rejected": lambda: f"{_capitalized(system)} rejected the token",
        "tls_certificate_rejected": lambda: f"The TLS certificate of {system} is not trusted",
        "record_load_failed": lambda: _record_summary(failure_type, fields),
        "transform_failed": lambda: _record_summary(failure_type, fields),
    }
    if failure_type in templated:
        return templated[failure_type]()
    if failure_type in _FIXED_SUMMARIES:
        return _FIXED_SUMMARIES[failure_type]
    return f"The {stage} stage failed with an internal error ({fields.get('error_class')})"


def build_failure_cause(
    stage: FailureStage,
    exc: BaseException,
    record: SourceRecordReference | None = None,
) -> RunFailureCause:
    """Build the cause of a run that failed in `stage` with `exc`, from named fields only.

    `record` names the source record the caller knows the failure belongs to, when the chain
    does not name one. `apply_progress` stays null here; T082 adds the apply record and the
    saved plan as inputs and fills it for a stopped apply.
    """
    chain = list(_chain(exc))
    failure_type, link = _classify(stage, chain)
    fields: dict[str, Any] = {"system": _system(chain), "error_class": _name(type(exc).__name__), "record": record}
    if failure_type == "destination_rejected_write" and link is not None:
        fields.update(_rejection_fields(link))
    elif isinstance(link, (RecordLoadError, TransformError)):
        fields.update(_record_fields(link))
        fields["record"] = fields["record"] or record
    return RunFailureCause(
        stage=stage,
        failure_type=failure_type,
        summary=_summary(failure_type, stage, fields),
        next_action=NEXT_ACTIONS[failure_type],
        **fields,
    )
