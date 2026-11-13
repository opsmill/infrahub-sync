"""The last log line of a service stage names the stage's outcome and the operator's next step.

`service_sync_run` writes that line once, at whichever of its three exits the stage takes:
a stored success, a recorded failure, or a failure the worker could not record. On the first
two it is written after the outcome is stored; on the third it reports `failed` while the
store keeps the run nonterminal (plan.md, "Stage exits").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")

from infrahub_sdk.exceptions import ServerNotReachableError
from prefect.states import State

from infrahub_sync.plan.destination_check import DestinationReadError
from infrahub_sync.plan.errors import OperationApplyFailedError, PlanArtifactError
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.failure_cause import RESUMABLE_WRITE_NEXT_ACTION
from infrahub_sync.service.flow import RecordedFailure, final_log_line, result_next_action, service_sync_run
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

_WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
_SHOW = "next step: `infrahub-sync runs show {run_id}`"
_PREFIX = "infrahub_sync.service.flow | "


class _ActionableRefusalError(PlanArtifactError):
    """A refusal this repository writes a next action for."""

    next_action = "Do X."


class _RecordingLogger:
    """A run logger that records every call, in order, into a shared list."""

    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        self.calls = calls

    def info(self, message: str, *args: object) -> None:
        self.calls.append(("info", message % args if args else message))

    def log(self, level: int, message: str, *args: object) -> None:
        self.calls.append(("log", level, message % args if args else message))

    def warning(self, message: str, *args: object) -> None:
        self.calls.append(("log", logging.WARNING, message % args if args else message))


# --- final_log_line and result_next_action (T010) ---------------------------------------------


def test_final_log_line_without_next_action_points_at_runs_show() -> None:
    assert final_log_line("R", "plan", "planned", None) == (
        "service run R stage=plan outcome=planned; next step: `infrahub-sync runs show R`"
    )


def test_final_log_line_uses_the_next_action_as_given() -> None:
    assert final_log_line("R", "apply", "failed", "Re-run `plan`.") == (
        "service run R stage=apply outcome=failed; next step: Re-run `plan`."
    )


def test_result_next_action_reads_the_failure_next_action() -> None:
    assert result_next_action({"failure": {"next_action": "Re-run `plan`."}}) == "Re-run `plan`."


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"outcome": "planned"}, id="missing-failure"),
        pytest.param({"failure": "Re-run `plan`."}, id="failure-not-a-dict"),
        pytest.param({"failure": {"next_action": ""}}, id="empty-next-action"),
        pytest.param({"failure": {"next_action": 3}}, id="next-action-not-a-string"),
    ],
)
def test_result_next_action_is_none_without_a_usable_next_action(result: dict[str, Any]) -> None:
    assert result_next_action(result) is None


# --- _record_failure returns what it stored (T011) --------------------------------------------


def _claimed_run(tmp_path: Path, run_id: str, operation: Literal["plan", "apply"]) -> tuple[ProductProjection, str]:
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    flow_run_id = str(uuid5(NAMESPACE_URL, run_id))
    append_execution(projection, run_id, PrefectExecutionLink(flow_run_id=flow_run_id, purpose=operation, attempt=1))
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return projection, flow_run_id


def _record(
    projection: ProductProjection,
    claim: tuple[str, str],
    stage: Literal["plan", "apply"],
    exc: Exception,
    *,
    dispatch_started: bool,
) -> RecordedFailure | None:
    """Record `exc` for the claimed run `claim` (run id, flow run id), the way the flow does."""
    run_id, flow_run_id = claim
    return service_flow._record_failure(
        run_id,
        stage,
        exc,
        logging.getLogger("test-service"),
        [],
        projection,
        flow_run_id,
        _WORKER_ID,
        dispatch_started=dispatch_started,
        record=None,
    )


def test_record_failure_returns_the_stored_outcome_and_next_action(tmp_path: Path) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-failed", "plan")
    error = RuntimeError("plan failed")
    error.__cause__ = _ActionableRefusalError("the plan is stale")

    recorded = _record(projection, ("run-failed", flow_run_id), "plan", error, dispatch_started=False)

    assert recorded == RecordedFailure(outcome="failed", next_action="Do X.")
    stored = projection.lookup_run("run-failed").value
    assert stored is not None
    assert stored.outcome == "failed"


def test_record_failure_without_next_action_returns_none_for_it(tmp_path: Path) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-plain", "plan")

    recorded = _record(projection, ("run-plain", flow_run_id), "plan", RuntimeError("boom"), dispatch_started=False)

    assert recorded == RecordedFailure(outcome="failed", next_action=None)


def test_record_failure_returns_ambiguous_after_dispatch(tmp_path: Path) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-ambiguous", "apply")

    recorded = _record(projection, ("run-ambiguous", flow_run_id), "apply", RuntimeError("boom"), dispatch_started=True)

    assert recorded is not None
    assert recorded.outcome == "ambiguous"


def test_record_failure_returns_none_when_the_run_is_unknown(tmp_path: Path) -> None:
    projection = local_product_projection(tmp_path)

    assert _record(projection, ("run-missing", "flow"), "plan", RuntimeError("boom"), dispatch_started=False) is None


def test_record_failure_returns_none_when_the_commit_raises(tmp_path: Path) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-commit-fails", "plan")

    class _FailingCommit:
        @staticmethod
        def lookup_run(run_id: str) -> object:
            return projection.lookup_run(run_id)

        @staticmethod
        def commit_claimed_execution(*_args: object, **_kwargs: object) -> bool:
            msg = "store unavailable"
            raise OSError(msg)

    recorded = _record(
        cast("ProductProjection", _FailingCommit()),
        ("run-commit-fails", flow_run_id),
        "plan",
        RuntimeError("boom"),
        dispatch_started=False,
    )

    assert recorded is None


def test_record_failure_returns_none_and_warns_when_the_claim_is_lost(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A commit that answers False stored nothing, so the failure takes the unrecorded path."""
    projection, flow_run_id = _claimed_run(tmp_path, "run-claim-lost", "plan")

    class _LostClaim:
        @staticmethod
        def lookup_run(run_id: str) -> object:
            return projection.lookup_run(run_id)

        @staticmethod
        def lookup_mutation_for_execution(run_id: str, flow_run_id: str, purpose: str) -> object:
            return projection.lookup_mutation_for_execution(run_id, flow_run_id, purpose)

        @staticmethod
        def commit_claimed_execution(*_args: object, **_kwargs: object) -> bool:
            return False

    with caplog.at_level(logging.WARNING, logger="test-service"):
        recorded = _record(
            cast("ProductProjection", _LostClaim()),
            ("run-claim-lost", flow_run_id),
            "plan",
            RuntimeError("boom"),
            dispatch_started=False,
        )

    assert recorded is None
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["service failure evidence was not recorded: this worker no longer holds the execution"]
    assert all("prefect" not in message.lower() for message in warnings)


class _LookupFailsError(RuntimeError):
    """The receipt lookup's failure, named by class only."""


def _failing_lookup(*_args: object) -> object:
    msg = "receipt lookup secret-detail"
    raise _LookupFailsError(msg)


def test_record_failure_keeps_the_evidence_when_the_receipt_lookup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The event falls back to the run's actor; the failure evidence still commits."""
    projection, flow_run_id = _claimed_run(tmp_path, "run-lookup-fails", "plan")
    monkeypatch.setattr(projection, "lookup_mutation_for_execution", _failing_lookup)

    with caplog.at_level(logging.WARNING, logger="test-service"):
        recorded = _record(
            projection, ("run-lookup-fails", flow_run_id), "plan", RuntimeError("boom"), dispatch_started=False
        )

    assert recorded == RecordedFailure(outcome="failed", next_action=None)
    events = projection.audit_events("run-lookup-fails")
    assert [event.actor for event in events if event.event_type == "infrahub_sync.run.failed"] == ["owner"]
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["the request that started this execution could not be read (_LookupFailsError)"]


def test_commit_success_still_commits_when_the_receipt_lookup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A lookup error must not turn a stored success into a failure."""
    projection, flow_run_id = _claimed_run(tmp_path, "run-success-lookup", "plan")
    monkeypatch.setattr(projection, "lookup_mutation_for_execution", _failing_lookup)

    with caplog.at_level(logging.WARNING, logger="test-service"):
        service_flow._commit_success(
            projection,
            "run-success-lookup",
            flow_run_id,
            stage="plan",
            worker_id=_WORKER_ID,
            writeback=service_flow.ExecutionFinishWriteback(
                phase="planned",
                outcome="succeeded",
                finished_at=datetime.now(timezone.utc),
                summary={},
                results={},
            ),
            secrets=[],
            run_logger=logging.getLogger("test-service"),
        )

    assert service_flow._stored_success(projection, "run-success-lookup", flow_run_id)
    events = projection.audit_events("run-success-lookup")
    assert [event.actor for event in events if event.outcome == "succeeded"] == ["owner"]
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["the request that started this execution could not be read (_LookupFailsError)"]


# --- the line at each exit of service_sync_run (T012) -----------------------------------------


@pytest.fixture
def flow_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Patch the flow down to its exits and record the calls that matter, in order."""
    calls: list[tuple[Any, ...]] = []
    logger = _RecordingLogger(calls)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logger, False))
    monkeypatch.setattr(service_flow, "_runtime", lambda: (None, object()))
    monkeypatch.setattr(service_flow, "_claim_current_execution", lambda _projection, _run_id: ("flow", _WORKER_ID))

    def commit(*_args: object, **_kwargs: object) -> None:
        calls.append(("commit",))

    monkeypatch.setattr(service_flow, "_commit_success", commit)
    return calls


def _stage_returns(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> None:
    monkeypatch.setattr(service_flow, "_execute_stage", lambda *_args, **_kwargs: (result, object()))


def _stage_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        msg = "stage failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(service_flow, "_execute_stage", fail)


def _messages(calls: list[tuple[Any, ...]]) -> list[str]:
    """Every line written through the run logger, in order, as the logger rendered it."""
    return [call[-1] for call in calls if call[0] in {"info", "log"}]


def _info_lines(calls: list[tuple[Any, ...]]) -> list[str]:
    """The INFO lines, each checked for the Sync prefix and returned without it."""
    lines = [call[1] for call in calls if call[0] == "info"]
    assert all(line.startswith(_PREFIX) for line in lines), lines
    return [line.removeprefix(_PREFIX) for line in lines]


def test_success_line_points_at_runs_show_after_the_commit(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_returns(monkeypatch, {"outcome": "planned"})

    service_sync_run.fn("run-ok", "plan", "config-001", 1, "a" * 64)

    line = "service run run-ok stage=plan outcome=planned; next step: `infrahub-sync runs show run-ok`"
    assert _info_lines(flow_calls)[-1] == line
    assert flow_calls.index(("commit",)) < flow_calls.index(("info", _PREFIX + line))


def test_success_line_uses_the_result_failure_next_action(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_returns(monkeypatch, {"outcome": "failed", "failure": {"next_action": "Re-run `plan`."}})

    service_sync_run.fn("run-stale", "verify", "config-001", 1, "a" * 64)

    assert _info_lines(flow_calls)[-1].endswith("next step: Re-run `plan`.")
    assert flow_calls[-1][0] == "info"


def test_recorded_failure_line_uses_the_recorded_outcome_and_next_action(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_raises(monkeypatch)

    def record(*_args: object, **_kwargs: object) -> RecordedFailure:
        flow_calls.append(("record",))
        return RecordedFailure("failed", "Do X.")

    monkeypatch.setattr(service_flow, "_record_failure", record)

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-bad", "plan", "config-001", 1, "a" * 64)

    line = "service run run-bad stage=plan outcome=failed; next step: Do X."
    assert _info_lines(flow_calls) == [line]
    assert flow_calls.index(("record",)) < flow_calls.index(("info", _PREFIX + line))


def test_unrecorded_failure_line_reads_failed_and_points_at_runs_show(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_raises(monkeypatch)
    monkeypatch.setattr(service_flow, "_record_failure", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-lost", "apply", "config-001", 1, "a" * 64)

    assert _info_lines(flow_calls) == [
        "service run run-lost stage=apply outcome=failed; " + _SHOW.format(run_id="run-lost")
    ]


def test_unrecorded_read_stage_commit_failure_reads_failed(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    """A read stage whose success does not store records nothing, and still ends with the line."""
    _stage_returns(monkeypatch, {"outcome": "planned"})

    def commit_fails(*_args: object, **_kwargs: object) -> None:
        msg = "store unavailable"
        raise OSError(msg)

    def record_is_forbidden(*_args: object, **_kwargs: object) -> None:
        msg = "a read stage's failed success commit is not recorded"
        raise AssertionError(msg)

    monkeypatch.setattr(service_flow, "_commit_success", commit_fails)
    monkeypatch.setattr(service_flow, "_record_failure", record_is_forbidden)

    with pytest.raises(RuntimeError, match="store unavailable"):
        service_sync_run.fn("run-read", "plan", "config-001", 1, "a" * 64)

    assert _info_lines(flow_calls) == [
        "service run run-read stage=plan outcome=failed; " + _SHOW.format(run_id="run-read")
    ]


def test_the_line_is_redacted(monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]) -> None:
    _stage_returns(monkeypatch, {"outcome": "failed", "failure": {"next_action": "Rotate s3cr3t-token."}})
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda: ["s3cr3t-token"])

    service_sync_run.fn("run-secret", "plan", "config-001", 1, "a" * 64)

    assert "s3cr3t-token" not in _info_lines(flow_calls)[-1]


def test_the_recorded_failure_line_is_redacted_before_the_secrets_are_cleared(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    """The failure exit writes its line while the stage's secrets are still known."""
    _stage_raises(monkeypatch)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda: ["s3cr3t-token"])
    monkeypatch.setattr(
        service_flow, "_record_failure", lambda *_args, **_kwargs: RecordedFailure("failed", "Rotate s3cr3t-token.")
    )

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-secret-failure", "plan", "config-001", 1, "a" * 64)

    last_line = _info_lines(flow_calls)[-1]
    assert "s3cr3t-token" not in last_line
    assert last_line.endswith("next step: Rotate ***.")


def test_a_failing_last_line_still_raises_the_original_sanitized_failure(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    """Writing the line is best-effort: it never replaces the stage's failure or skips the cleanup."""
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda: ["s3cr3t-token"])
    sanitized_with: list[list[str]] = []
    sanitize = service_flow.sanitize_exception_chain

    def sanitize_and_keep(failure: BaseException, secrets: list[str]) -> BaseException:
        sanitized_with.append(secrets)
        return sanitize(failure, secrets)

    monkeypatch.setattr(service_flow, "sanitize_exception_chain", sanitize_and_keep)

    def fail(*_args: object, **_kwargs: object) -> None:
        msg = "stage failed with s3cr3t-token"
        raise RuntimeError(msg)

    monkeypatch.setattr(service_flow, "_execute_stage", fail)
    monkeypatch.setattr(service_flow, "_record_failure", lambda *_args, **_kwargs: RecordedFailure("failed", None))

    class _BrokenLogger(_RecordingLogger):
        def info(self, message: str, *args: object) -> None:
            del message, args
            self.calls.append(("info-failed",))
            msg = "log handler unavailable"
            raise OSError(msg)

    monkeypatch.setattr(service_flow, "_run_logger", lambda: (_BrokenLogger(flow_calls), False))

    with pytest.raises(RuntimeError, match="stage failed") as raised:
        service_sync_run.fn("run-broken-log", "plan", "config-001", 1, "a" * 64)

    assert "s3cr3t-token" not in str(raised.value)
    assert sanitized_with == [[]]  # the very list sanitized with was cleared afterwards


# --- the run summary artifact at each exit of service_sync_run (T018) -------------------------

_REDACTION_PROBE = "s3cr3t-token"


class _StoredRun:
    """A projection whose run record holds a secret value as a kind name."""

    @staticmethod
    def lookup_run(run_id: str) -> object:
        run = ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"by_kind": {_REDACTION_PROBE: 1}},
        )
        return type("Lookup", (), {"value": run})()


@pytest.fixture
def published(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> tuple[list[tuple[Any, ...]], list[dict[str, Any]]]:
    """Run the flow inside a Prefect context, with a secret, and record every published artifact."""
    created: list[dict[str, Any]] = []
    logger = _RecordingLogger(flow_calls)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logger, True))
    monkeypatch.setattr(service_flow, "_runtime", lambda: (None, _StoredRun()))
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda: [_REDACTION_PROBE])

    def create(**kwargs: object) -> None:
        flow_calls.append(("artifact",))
        created.append(kwargs)

    monkeypatch.setattr(service_flow, "create_markdown_artifact", create)
    return flow_calls, created


def _assert_published_after_the_last_line(calls: list[tuple[Any, ...]], created: list[dict[str, Any]]) -> None:
    assert len(created) == 1
    assert _REDACTION_PROBE not in created[0]["markdown"]
    last_line = max(index for index, call in enumerate(calls) if call[0] == "info")
    assert calls.index(("artifact",)) == last_line + 1


def test_success_publishes_the_artifact_after_the_last_line(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    calls, created = published
    _stage_returns(monkeypatch, {"outcome": "planned"})

    service_sync_run.fn("run-ok", "plan", "config-001", 1, "a" * 64)

    _assert_published_after_the_last_line(calls, created)
    assert "- Outcome: planned\n" in created[0]["markdown"]
    assert created[0]["key"] == "sync-run-run-ok-plan"


def test_recorded_failure_publishes_its_outcome_and_next_action(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    calls, created = published
    _stage_raises(monkeypatch)
    monkeypatch.setattr(
        service_flow, "_record_failure", lambda *_args, **_kwargs: RecordedFailure("ambiguous", "Do X.")
    )

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-bad", "apply", "config-001", 1, "a" * 64)

    _assert_published_after_the_last_line(calls, created)
    assert "- Outcome: ambiguous\n" in created[0]["markdown"]
    assert created[0]["markdown"].endswith("- Do X.\n")


def test_unrecorded_failure_publishes_outcome_failed(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    calls, created = published
    _stage_raises(monkeypatch)
    monkeypatch.setattr(service_flow, "_record_failure", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-lost", "apply", "config-001", 1, "a" * 64)

    _assert_published_after_the_last_line(calls, created)
    assert "- Outcome: failed\n" in created[0]["markdown"]


def test_outside_a_prefect_run_nothing_is_published(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_returns(monkeypatch, {"outcome": "planned"})
    monkeypatch.setattr(service_flow, "_runtime", lambda: (None, _StoredRun()))

    created: list[dict[str, Any]] = []
    monkeypatch.setattr(service_flow, "create_markdown_artifact", lambda **kwargs: created.append(kwargs))

    service_sync_run.fn("run-local", "plan", "config-001", 1, "a" * 64)

    assert created == []
    assert all(call[0] != "log" for call in flow_calls)


def test_a_refused_claim_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    calls, created = published

    def refuse(_projection: object, run_id: str) -> tuple[str, str]:
        raise service_flow.ExecutionClaimRefusedError(run_id)

    monkeypatch.setattr(service_flow, "_claim_current_execution", refuse)

    state = service_sync_run.fn("run-refused", "plan", "config-001", 1, "a" * 64)

    assert isinstance(state, State)
    assert state.is_failed()
    assert created == []
    assert ("artifact",) not in calls


def _publish_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def create(**_kwargs: object) -> None:
        msg = "artifact store unavailable"
        raise OSError(msg)

    monkeypatch.setattr(service_flow, "create_markdown_artifact", create)


def test_a_failed_publish_leaves_the_success_result_unchanged(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    """FR-004: an artifact that cannot be published does not change the stage's result."""
    calls, _created = published
    result = {"outcome": "planned"}
    _stage_returns(monkeypatch, result)
    _publish_raises(monkeypatch)

    assert service_sync_run.fn("run-ok", "plan", "config-001", 1, "a" * 64) is result
    assert any(call[0] == "log" and call[1] == logging.WARNING for call in calls)


def test_a_failed_publish_leaves_the_stage_failure_unchanged(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    """FR-004: on the failure exit, the stage's own sanitized failure is still the one raised."""
    calls, _created = published

    def fail(*_args: object, **_kwargs: object) -> None:
        msg = f"stage failed with {_REDACTION_PROBE}"
        raise RuntimeError(msg)

    monkeypatch.setattr(service_flow, "_execute_stage", fail)
    monkeypatch.setattr(service_flow, "_record_failure", lambda *_args, **_kwargs: RecordedFailure("failed", None))
    _publish_raises(monkeypatch)

    with pytest.raises(RuntimeError, match="stage failed") as raised:
        service_sync_run.fn("run-bad", "plan", "config-001", 1, "a" * 64)

    assert _REDACTION_PROBE not in str(raised.value)
    assert any(call[0] == "log" and call[1] == logging.WARNING for call in calls)


# --- every line the service flow writes directly carries the Sync prefix (T024, FR-006) -------


def test_the_success_line_carries_the_sync_prefix(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_returns(monkeypatch, {"outcome": "planned"})

    service_sync_run.fn("run-ok", "plan", "config-001", 1, "a" * 64)

    messages = _messages(flow_calls)
    assert messages
    assert all(message.startswith(_PREFIX) for message in messages), messages


def test_the_failure_line_carries_the_sync_prefix(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_raises(monkeypatch)
    monkeypatch.setattr(service_flow, "_record_failure", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-lost", "apply", "config-001", 1, "a" * 64)

    messages = _messages(flow_calls)
    assert messages
    assert all(message.startswith(_PREFIX) for message in messages), messages


def test_the_refused_claim_line_carries_the_sync_prefix(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    def refuse(_projection: object, run_id: str) -> tuple[str, str]:
        raise service_flow.ExecutionClaimRefusedError(run_id)

    monkeypatch.setattr(service_flow, "_claim_current_execution", refuse)

    state = service_sync_run.fn("run-refused", "plan", "config-001", 1, "a" * 64)

    assert isinstance(state, State)
    assert state.is_failed()
    errors = [call[2] for call in flow_calls if call[0] == "log" and call[1] == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].startswith(_PREFIX + "service worker execution claim was refused")


def test_the_unpersisted_evidence_warning_carries_the_sync_prefix(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]], tmp_path: Path
) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-unpersisted", "plan")

    class _FailingCommit:
        @staticmethod
        def lookup_run(run_id: str) -> object:
            return projection.lookup_run(run_id)

        @staticmethod
        def lookup_mutation_for_execution(run_id: str, flow_run_id: str, purpose: str) -> object:
            return projection.lookup_mutation_for_execution(run_id, flow_run_id, purpose)

        @staticmethod
        def commit_claimed_execution(*_args: object, **_kwargs: object) -> bool:
            msg = "store unavailable"
            raise OSError(msg)

    monkeypatch.setattr(service_flow, "_runtime", lambda: (None, _FailingCommit()))
    monkeypatch.setattr(
        service_flow, "_claim_current_execution", lambda _projection, _run_id: (flow_run_id, _WORKER_ID)
    )
    _stage_raises(monkeypatch)

    with pytest.raises(RuntimeError, match="stage failed"):
        service_sync_run.fn("run-unpersisted", "plan", "config-001", 1, "a" * 64)

    warnings = [call[2] for call in flow_calls if call[0] == "log" and call[1] == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].startswith(_PREFIX + "service failure evidence could not be persisted (")


def test_the_unpublished_artifact_warning_carries_the_sync_prefix(
    monkeypatch: pytest.MonkeyPatch, published: tuple[list[tuple[Any, ...]], list[dict[str, Any]]]
) -> None:
    calls, _created = published
    _stage_returns(monkeypatch, {"outcome": "planned"})
    _publish_raises(monkeypatch)

    service_sync_run.fn("run-ok", "plan", "config-001", 1, "a" * 64)

    warnings = [call[2] for call in calls if call[0] == "log" and call[1] == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].startswith(_PREFIX + "run summary artifact for run run-ok")


# --- the next step of a run that keeps its saved plan (spec 022) ------------------------------

_UNREACHABLE = ServerNotReachableError(address="https://infrahub.example.test")
_VERIFY_AND_RESUME = (
    "then run `infrahub-sync verify RUN_ID` again, then "
    "`infrahub-sync runs resume RUN_ID --expected-checksum CHECKSUM`."
)


def _interrupted_run(
    tmp_path: Path, run_id: str, purpose: Literal["verify", "resume"]
) -> tuple[ProductProjection, str]:
    """An interrupted apply run with one claimed execution of `purpose`."""
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="apply",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="apply-interrupted",
            reconciliation_required=True,
            summary={"sync_name": "inventory"},
        )
    )
    flow_run_id = str(uuid5(NAMESPACE_URL, run_id))
    append_execution(projection, run_id, PrefectExecutionLink(flow_run_id=flow_run_id, purpose=purpose, attempt=1))
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return projection, flow_run_id


def _record_stage(
    projection: ProductProjection,
    claim: tuple[str, str],
    stage: Literal["apply", "verify", "resume"],
    exc: Exception,
    *,
    dispatch_started: bool,
) -> RecordedFailure | None:
    run_id, flow_run_id = claim
    return service_flow._record_failure(
        run_id,
        stage,
        exc,
        logging.getLogger("test-service"),
        [],
        projection,
        flow_run_id,
        _WORKER_ID,
        dispatch_started=dispatch_started,
        record=None,
    )


def _caused_by(error: Exception, cause: Exception) -> Exception:
    error.__cause__ = cause
    return error


def test_a_write_that_stops_part_way_names_verify_and_resume_as_the_next_step(tmp_path: Path) -> None:
    """The engine's error names a new plan for every path; on a resumable run the worker names a resume."""
    projection, flow_run_id = _claimed_run(tmp_path, "run-write-stopped", "apply")
    stopped = _caused_by(
        OperationApplyFailedError("failed", apply_record=ApplyRecord(failed_operation="op_1")), _UNREACHABLE
    )

    recorded = _record_stage(projection, ("run-write-stopped", flow_run_id), "apply", stopped, dispatch_started=True)

    assert recorded is not None
    assert recorded.outcome == "ambiguous"
    assert recorded.next_action == RESUMABLE_WRITE_NEXT_ACTION


def test_a_write_refused_for_a_conflicting_node_keeps_its_own_next_action(tmp_path: Path) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-write-conflict", "apply")
    stopped = OperationApplyFailedError(
        "failed",
        apply_record=ApplyRecord(failed_operation="op_1"),
        next_action=OperationApplyFailedError.UNIQUENESS_NEXT_ACTION,
    )

    recorded = _record_stage(projection, ("run-write-conflict", flow_run_id), "apply", stopped, dispatch_started=True)

    assert recorded is not None
    assert recorded.next_action == OperationApplyFailedError.UNIQUENESS_NEXT_ACTION


def test_a_failed_verify_read_of_an_interrupted_run_names_verify_and_resume_as_the_next_step(tmp_path: Path) -> None:
    projection, flow_run_id = _interrupted_run(tmp_path, "run-verify-read", "verify")
    failed_read = _caused_by(DestinationReadError(), _UNREACHABLE)

    recorded = _record_stage(
        projection, ("run-verify-read", flow_run_id), "verify", failed_read, dispatch_started=False
    )

    assert recorded is not None
    assert recorded.next_action is not None
    assert recorded.next_action.endswith(_VERIFY_AND_RESUME)


def test_a_resume_that_fails_before_its_first_write_names_verify_and_resume_as_the_next_step(tmp_path: Path) -> None:
    """Not ambiguous, but the run was already interrupted and keeps its saved plan."""
    # The claimed execution is a `verify` one: appending a resume execution needs a resume
    # admission, and `_record_failure` reads only the run and the stage it is given.
    projection, flow_run_id = _interrupted_run(tmp_path, "run-resume-early", "verify")

    recorded = _record_stage(
        projection, ("run-resume-early", flow_run_id), "resume", _UNREACHABLE, dispatch_started=False
    )

    assert recorded is not None
    assert recorded.outcome == "failed"
    assert recorded.next_action is not None
    assert recorded.next_action.endswith(_VERIFY_AND_RESUME)


def test_a_failed_plan_keeps_plan_again_as_the_next_step(tmp_path: Path) -> None:
    projection, flow_run_id = _claimed_run(tmp_path, "run-plan-read", "plan")

    recorded = _record(projection, ("run-plan-read", flow_run_id), "plan", _UNREACHABLE, dispatch_started=False)

    assert recorded is not None
    assert recorded.next_action is not None
    assert recorded.next_action.endswith("then plan again.")
