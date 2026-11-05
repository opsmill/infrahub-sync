"""The last log line of a service stage names the stage's outcome and the operator's next step.

`service_sync_run` writes that line once, at whichever of its three exits the stage takes:
a stored success, a recorded failure, or a failure the worker could not record. The line is
written after the outcome is stored, so it never announces an outcome the store does not hold.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")

from infrahub_sync.plan.errors import PlanArtifactError
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import RecordedFailure, final_log_line, result_next_action, service_sync_run
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

_WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
_SHOW = "next step: `infrahub-sync runs show {run_id}`"


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


def _info_lines(calls: list[tuple[Any, ...]]) -> list[str]:
    return [call[1] for call in calls if call[0] == "info"]


def test_success_line_points_at_runs_show_after_the_commit(
    monkeypatch: pytest.MonkeyPatch, flow_calls: list[tuple[Any, ...]]
) -> None:
    _stage_returns(monkeypatch, {"outcome": "planned"})

    service_sync_run.fn("run-ok", "plan", "config-001", 1, "a" * 64)

    line = "service run run-ok stage=plan outcome=planned; next step: `infrahub-sync runs show run-ok`"
    assert _info_lines(flow_calls)[-1] == line
    assert flow_calls.index(("commit",)) < flow_calls.index(("info", line))


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
    assert flow_calls.index(("record",)) < flow_calls.index(("info", line))


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
