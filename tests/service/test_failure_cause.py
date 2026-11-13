"""The cause a failed run records.

- Spec 017 FR-001: a destination schema read failure records a cause and a next action the
  reader acts on.
- Spec 019 T046: each failure type records its `failure_type` and the fields of FR-014.
"""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import requests
from infrahub_sdk.exceptions import GraphQLError, ServerNotReachableError, ServerNotResponsiveError

from infrahub_sync import RecordLoadError
from infrahub_sync.client.models import (
    FAILURE_TYPES,
    MAX_FAILURE_CAUSE_TEXT,
    MAX_FAILURE_NAME_TEXT,
    StageFailureResource,
)
from infrahub_sync.infrahub_errors import MAX_REJECTED_WRITE_MESSAGE
from infrahub_sync.plan.errors import OperationApplyFailedError
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.potenda import SideLoadError
from infrahub_sync.runtime_schema import DestinationSchemaUnavailableError
from infrahub_sync.runtime_schema.errors import DestinationBranchNotFoundError
from infrahub_sync.service.failure_cause import FAILURE_RECORD_KEYS, failure_record, operator_cause
from tests.service.failure_cases import APPLIED_OPERATION, CASES, FAILED_OPERATION, NEUTRAL_TEXT, SOURCE, FailureCase

if TYPE_CHECKING:
    from collections.abc import Callable

_REASONS = ("timeout", "unreachable", "unauthorized", "forbidden", "credentials", "unconfigured", "rejected")


def test_a_missing_branch_records_the_branch_and_the_fix() -> None:
    recorded = operator_cause(DestinationBranchNotFoundError(branch="demo-test"), [])

    assert recorded["cause"] == "Branch `demo-test` does not exist in Infrahub."
    assert recorded["next_action"] == "Create it, or start the run with `--branch <existing branch>`."


@pytest.mark.parametrize("reason", _REASONS)
def test_every_schema_read_reason_records_a_cause_and_a_next_action(reason: str) -> None:
    recorded = operator_cause(DestinationSchemaUnavailableError(reason=reason, branch="demo-test"), [])

    assert "`demo-test`" in recorded["cause"]
    assert recorded["next_action"]
    assert "prefect" not in f"{recorded['cause']} {recorded['next_action']}".lower()


@pytest.mark.parametrize("reason", _REASONS)
def test_the_reasons_do_not_share_one_cause(reason: str) -> None:
    others = {
        operator_cause(DestinationSchemaUnavailableError(reason=other, branch="b"), [])["cause"]
        for other in _REASONS
        if other != reason
    }

    assert operator_cause(DestinationSchemaUnavailableError(reason=reason, branch="b"), [])["cause"] not in others


def test_a_schema_read_failure_wrapped_by_a_stage_is_still_described() -> None:
    # A plan wraps a side's load failure in a `ValueError` raised `from` the original.
    outer = ValueError("destination side could not be loaded")
    outer.__cause__ = DestinationBranchNotFoundError(branch="demo-test")

    recorded = operator_cause(outer, [])

    assert recorded["cause"] == "Branch `demo-test` does not exist in Infrahub."


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(DestinationBranchNotFoundError(branch="demo-s3cr3t-value"), id="branch-missing"),
        pytest.param(DestinationSchemaUnavailableError(reason="timeout", branch="demo-s3cr3t-value"), id="timeout"),
        pytest.param(
            DestinationSchemaUnavailableError(
                reason="forbidden", branch="main", detail="missing permission `s3cr3t-value`"
            ),
            id="forbidden-detail",
        ),
    ],
)
def test_the_cause_and_next_action_are_redacted(error: DestinationSchemaUnavailableError) -> None:
    recorded = operator_cause(error, ["s3cr3t-value"])

    assert "s3cr3t-value" not in recorded["cause"]
    assert "s3cr3t-value" not in recorded["next_action"]


@pytest.mark.parametrize("reason", [*_REASONS, "nobranch"])
def test_the_cause_and_next_action_are_capped(reason: str) -> None:
    branch = "b" * (MAX_FAILURE_CAUSE_TEXT * 2)
    error = (
        DestinationBranchNotFoundError(branch=branch)
        if reason == "nobranch"
        else DestinationSchemaUnavailableError(reason=reason, branch=branch)
    )

    recorded = operator_cause(error, [])

    assert len(recorded["cause"]) <= MAX_FAILURE_CAUSE_TEXT
    assert len(recorded["next_action"]) <= MAX_FAILURE_CAUSE_TEXT


def test_an_unknown_reason_still_records_a_cause_and_a_next_action() -> None:
    recorded = operator_cause(DestinationSchemaUnavailableError(reason="novel", branch="demo-test"), [])

    assert "`demo-test`" in recorded["cause"]
    assert recorded["next_action"]


# Spec 019 T046 (FR-014 to FR-018, data-model.md "Stage failure record additions"): the record of
# each failure type. The record is built by `_failure_evidence`, the function the worker writes
# `results.<stage>_failure` with; spec 019 T053 and T054 make it call `failure_record`.


@pytest.fixture
def failure_evidence() -> Callable[..., dict[str, Any]]:
    """`_failure_evidence` of the service worker; skipped where Prefect is not installed."""
    pytest.importorskip("prefect")
    from infrahub_sync.service import flow

    return flow._failure_evidence


def _evidence(failure_evidence: Callable[..., dict[str, Any]], case: FailureCase) -> dict[str, Any]:
    exc = case.build(NEUTRAL_TEXT)
    return failure_evidence(case.stage, exc, outcome="failed", record=None, secrets=())


@pytest.mark.parametrize("case", [pytest.param(case, id=case.case_id) for case in CASES])
def test_each_failure_type_records_its_type_and_fields(
    failure_evidence: Callable[..., dict[str, Any]], case: FailureCase
) -> None:
    evidence = _evidence(failure_evidence, case)

    assert evidence["failure_type"] == case.failure_type
    assert evidence["failure_type"] in FAILURE_TYPES
    for name, expected in case.fields.items():
        if name == "record":
            assert {key: evidence["record"][key] for key in expected} == expected
        else:
            assert evidence[name] == expected, name


@pytest.mark.parametrize("case", [pytest.param(case, id=case.case_id) for case in CASES])
def test_each_failure_type_keeps_the_fields_it_records_today(
    failure_evidence: Callable[..., dict[str, Any]], case: FailureCase
) -> None:
    """The keys of spec 017 keep their meaning next to the new fields (FR-014)."""
    exc = case.build(NEUTRAL_TEXT)
    evidence = failure_evidence(case.stage, exc, outcome="failed", record=None, secrets=())

    assert evidence["failure_type"] == case.failure_type
    assert evidence["stage"] == case.stage
    assert evidence["error_type"] == type(exc).__name__
    assert evidence["cause_type"] == (None if exc.__cause__ is None else type(exc.__cause__).__name__)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, id=case.case_id)
        for case in CASES
        if case.case_id in {"destination-peer-missing", "destination-peer-ambiguous", "rejected-write-with-code"}
    ],
)
def test_a_stopped_apply_keeps_its_failed_operation(
    failure_evidence: Callable[..., dict[str, Any]], case: FailureCase
) -> None:
    """FR-018: a destination peer refusal names the operation; the apply record keys stay."""
    evidence = _evidence(failure_evidence, case)

    assert evidence["failure_type"] == case.failure_type
    assert evidence["failed_operation"] == FAILED_OPERATION
    assert evidence["applied_operations"] == [APPLIED_OPERATION]


@pytest.mark.parametrize("case", [pytest.param(case, id=case.case_id) for case in CASES])
def test_every_record_carries_every_new_key(failure_evidence: Callable[..., dict[str, Any]], case: FailureCase) -> None:
    """data-model.md: `failure_type` is always present; the other fields are `None` when not named."""
    evidence = _evidence(failure_evidence, case)

    assert set(FAILURE_RECORD_KEYS) <= set(evidence)
    assert StageFailureResource.model_validate(evidence).failure_type == case.failure_type


def test_a_stopped_apply_records_the_plan_size_and_the_failed_operation(
    failure_evidence: Callable[..., dict[str, Any]],
) -> None:
    """FR-014: the operation count and the failed operation's action, kind and identity, from the plan."""
    record = ApplyRecord(
        applied_operations=(APPLIED_OPERATION,),
        failed_operation=FAILED_OPERATION,
        operations_total=2291,
        failed_operation_detail={
            "action": "update",
            "kind": "DcimDevice",
            "identity": {"location": "r1", "name": "x" * 500},
        },
    )
    exc = OperationApplyFailedError("failed", apply_record=record)

    evidence = failure_evidence("apply", exc, outcome="ambiguous", record=None, secrets=())

    assert evidence["operations_total"] == 2291
    detail = evidence["failed_operation_detail"]
    assert (detail["action"], detail["kind"], detail["identity"]["location"]) == ("update", "DcimDevice", "r1")
    assert len(detail["identity"]["name"]) <= MAX_FAILURE_NAME_TEXT


def test_a_stopped_resume_records_the_operations_the_whole_run_wrote(
    failure_evidence: Callable[..., dict[str, Any]],
) -> None:
    """Spec 022 research R6: the written count covers every execution of the run, not only this one."""
    record = ApplyRecord(
        applied_operations=(APPLIED_OPERATION,),
        failed_operation=FAILED_OPERATION,
        operations_total=10_000,
        operations_written=6_101,
    )

    evidence = failure_evidence(
        "resume", OperationApplyFailedError("failed", apply_record=record), outcome="ambiguous", record=None, secrets=()
    )

    assert (evidence["operations_written"], evidence["operations_total"]) == (6_101, 10_000)
    assert StageFailureResource.model_validate(evidence).operations_written == 6_101


def test_a_failure_record_that_cannot_be_built_is_an_internal_error(
    failure_evidence: Callable[..., dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker never loses the whole record because the structured cause could not be built."""
    from infrahub_sync.service import flow

    def broken(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AttributeError

    monkeypatch.setattr(flow, "failure_record", broken)

    evidence = failure_evidence("plan", RuntimeError("x"), outcome="failed", record=None, secrets=())

    assert evidence["failure_type"] == "internal_error"
    assert evidence["error_type"] == "RuntimeError"


def _from(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


def _source(inner: BaseException, adapter: str = "ipfabricsync") -> BaseException:
    return _from(SideLoadError("read failed", role="source", adapter=adapter), inner)


def _with_context(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__context__ = inner
    return outer


def _httpx_status(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://source.example.test/api")
    return httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))


_INFRAHUB = {"role": "destination", "adapter": "infrahub"}
_IPFABRIC = {"role": "source", "adapter": "ipfabricsync"}


@pytest.mark.parametrize(
    ("stage", "exc", "failure_type", "system"),
    [
        pytest.param(
            "plan",
            _source(requests.ConnectTimeout("t"), "netbox"),
            "system_timeout",
            SOURCE,
            id="requests-connect-timeout",
        ),
        pytest.param("plan", _source(httpx.ReadTimeout("t")), "system_timeout", _IPFABRIC, id="httpx-timeout"),
        pytest.param("plan", _source(httpx.ConnectError("c")), "system_unreachable", _IPFABRIC, id="httpx-connect"),
        pytest.param("plan", _source(_httpx_status(401)), "token_rejected", _IPFABRIC, id="httpx-401-from-a-source"),
        pytest.param(
            "plan",
            _source(_with_context(requests.ConnectionError("c"), ssl.SSLCertVerificationError("v")), "netbox"),
            "tls_certificate_rejected",
            SOURCE,
            id="certificate-error-as-context",
        ),
        pytest.param(
            "sync",
            _from(
                OperationApplyFailedError("failed", apply_record=ApplyRecord(failed_operation="op_1")),
                ServerNotResponsiveError(url="https://infrahub.example.test", timeout=5),
            ),
            "system_timeout",
            _INFRAHUB,
            id="sync-apply-timeout",
        ),
        pytest.param(
            "verify",
            ServerNotReachableError(address="https://infrahub.example.test"),
            "system_unreachable",
            _INFRAHUB,
            id="unwrapped-sdk-error",
        ),
    ],
)
def test_the_classification_table(stage: str, exc: BaseException, failure_type: str, system: dict[str, str]) -> None:
    """T052: the exception type and the HTTP status code decide the failure type and the system."""
    recorded = failure_record(stage, exc)

    assert (recorded["failure_type"], recorded["system"]) == (failure_type, system)


def _record_load(inner: BaseException) -> BaseException:
    return _source(_from(RecordLoadError(kind="DcimInterface", source_id="12"), inner), "netbox")


@pytest.mark.parametrize(
    ("inner", "failure_type"),
    [
        pytest.param(requests.ReadTimeout("t"), "system_timeout", id="timeout"),
        pytest.param(httpx.ConnectError("c"), "system_unreachable", id="connection"),
        pytest.param(_httpx_status(401), "token_rejected", id="token"),
    ],
)
def test_a_transport_failure_while_a_record_is_converted_names_the_system_and_the_record(
    inner: BaseException, failure_type: str
) -> None:
    """FR-017: a source that stops answering while record 12 is converted is a transport failure, not the record's."""
    recorded = failure_record("plan", _record_load(inner))

    assert (recorded["failure_type"], recorded["system"]) == (failure_type, SOURCE)
    assert recorded["record"] == {"kind": "DcimInterface", "source_id": "12"}


def test_a_record_load_failure_without_a_transport_cause_stays_a_record_failure() -> None:
    """A conversion error of record 12 that no transport error caused is still `record_load_failed`."""
    recorded = failure_record("plan", _record_load(RuntimeError("conversion failed")))

    assert recorded["failure_type"] == "record_load_failed"
    assert recorded["record"] == {"kind": "DcimInterface", "source_id": "12"}


def test_infrahubs_message_goes_through_the_masking_pass() -> None:
    rejected = GraphQLError(errors=[{"message": "value s3cr3t-value is not allowed"}])
    exc = _from(OperationApplyFailedError("failed", apply_record=ApplyRecord(failed_operation="op_1")), rejected)

    recorded = failure_record("apply", exc, secrets=["s3cr3t-value"])

    assert recorded["failure_type"] == "destination_rejected_write"
    assert "s3cr3t-value" not in recorded["infrahub_message"]


@pytest.mark.parametrize("offset", [0, 10, MAX_REJECTED_WRITE_MESSAGE - 20, MAX_REJECTED_WRITE_MESSAGE - 5])
def test_a_secret_in_infrahubs_rejection_message_is_masked_before_the_message_is_cut(offset: int) -> None:
    """A secret in Infrahub's own message is masked, also when it crosses the length limit (FR-019)."""
    secret = "s3cr3t-T0KEN-value-0123456789abcdef"  # noqa: S105 - a test credential
    message = "x" * offset + secret + " y" * MAX_REJECTED_WRITE_MESSAGE
    rejected = GraphQLError(errors=[{"message": message, "extensions": {"code": "X"}}], query="", variables={})
    error = OperationApplyFailedError("apply failed", apply_record=ApplyRecord())
    error.__cause__ = rejected

    record = failure_record("apply", error, secrets=[secret])

    assert record["failure_type"] == "destination_rejected_write"
    recorded = record["infrahub_message"]
    assert recorded is not None
    assert len(recorded) <= MAX_REJECTED_WRITE_MESSAGE
    assert secret[:4] not in recorded


@pytest.mark.parametrize(
    ("resumable", "ending"),
    [
        pytest.param(False, "then plan again.", id="not-resumable"),
        pytest.param(
            True,
            "then run `infrahub-sync verify` on this run again, then `infrahub-sync runs resume`.",
            id="resumable",
        ),
    ],
)
def test_an_unreachable_destination_names_the_next_step_of_the_run(resumable: bool, ending: str) -> None:  # noqa: FBT001
    """Spec 022: a run that keeps its saved plan continues with `verify` and `runs resume`."""
    recorded = failure_record(
        "verify", ServerNotReachableError(address="https://infrahub.example.test"), resumable=resumable
    )

    assert recorded["next_action"].startswith("Check that infrahub answers at the address the configuration names")
    assert recorded["next_action"].endswith(ending)


def test_a_record_load_failure_keeps_plan_again_on_a_resumable_run() -> None:
    """A fix in the source data needs a new plan: a resume writes the saved plan's values."""
    exc = _record_load(ValueError("bad value"))

    recorded = failure_record("resume", exc, resumable=True)

    assert recorded["failure_type"] == "record_load_failed"
    assert recorded["next_action"].endswith("then plan again.")
