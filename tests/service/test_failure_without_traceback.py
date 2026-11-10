"""A known failure ends the flow run `Failed` with one ERROR line and no traceback (FR-003).

`service_sync_run` runs under the Prefect test harness, with the stage replaced by one that
raises. For an error class whose recorded evidence has a cause, Sync writes one ERROR line
`<cause> Next step: <next_action>` and returns a `Failed` state with the same text, so no
exception passes through Prefect's engine. An unknown error class still raises the sanitized
error (plan.md, "Failure exit for known error classes").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")

from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.runtime_schema.errors import DestinationBranchNotFoundError
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

_WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
_PREFIX = "infrahub_sync.service.flow | "
_SYNC_LINE = "infrahub_sync"
_EXPECTED = (
    "Branch `demo-missing` does not exist in Infrahub. "
    "Next step: Create it, or start the run with `--branch <existing branch>`."
)


@pytest.fixture(scope="module")
def prefect_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Run flows against a throwaway Prefect backend, with Prefect's files in a temporary home."""
    home = tmp_path_factory.mktemp("prefect-home")
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("PREFECT_HOME", str(home))
        patcher.setenv("PREFECT_LOCAL_STORAGE_PATH", str(home / "storage"))
        with prefect_test_harness():
            yield


class _Records(logging.Handler):
    """Keep every record the flow run logger writes."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def run_records() -> Iterator[list[logging.LogRecord]]:
    """The records of `prefect.flow_runs`, the logger `get_run_logger` returns in a flow run."""
    handler = _Records()
    run_logger = logging.getLogger("prefect.flow_runs")
    run_logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        run_logger.removeHandler(handler)


def _claimed_run(tmp_path: Path, run_id: str) -> tuple[ProductProjection, str]:
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
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
    append_execution(projection, run_id, PrefectExecutionLink(flow_run_id=flow_run_id, purpose="plan", attempt=1))
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return projection, flow_run_id


def _stage_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_id: str, error: Exception
) -> tuple[ProductProjection, list[dict[str, Any]]]:
    """Run the flow on a claimed run whose stage raises `error`; record each published artifact."""
    projection, flow_run_id = _claimed_run(tmp_path, run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (None, projection))
    monkeypatch.setattr(
        service_flow, "_claim_current_execution", lambda _projection, _run_id: (flow_run_id, _WORKER_ID)
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(service_flow, "_execute_stage", fail)
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(service_flow, "create_markdown_artifact", lambda **kwargs: created.append(kwargs))
    return projection, created


def _sync_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [record for record in records if record.getMessage().startswith(_SYNC_LINE)]


@pytest.mark.usefixtures("prefect_harness")
def test_a_missing_branch_writes_one_error_line_and_ends_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_records: list[logging.LogRecord]
) -> None:
    projection, created = _stage_raises(
        monkeypatch, tmp_path, "run-nobranch", DestinationBranchNotFoundError(branch="demo-missing")
    )

    state = service_sync_run("run-nobranch", "plan", "config-001", 1, "a" * 64, return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload

    assert state.is_failed()
    assert state.message == _EXPECTED
    sync = _sync_records(run_records)
    errors = [record for record in sync if record.levelno == logging.ERROR]
    assert [record.getMessage() for record in errors] == [_PREFIX + _EXPECTED]
    assert all("Traceback" not in record.getMessage() and record.exc_info is None for record in sync)
    # The last log line and the run summary artifact are still written on this exit.
    assert sync[-1].getMessage().startswith(_PREFIX + "service run run-nobranch stage=plan outcome=failed")
    assert len(created) == 1
    stored = projection.lookup_run("run-nobranch").value
    assert stored is not None
    assert stored.outcome == "failed"
    assert stored.results["plan_failure"]["cause"] == "Branch `demo-missing` does not exist in Infrahub."


@pytest.mark.usefixtures("prefect_harness")
def test_an_unknown_error_class_still_raises_the_sanitized_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_records: list[logging.LogRecord]
) -> None:
    _stage_raises(monkeypatch, tmp_path, "run-unknown", RuntimeError("stage failed"))

    state = service_sync_run("run-unknown", "plan", "config-001", 1, "a" * 64, return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload

    assert state.is_failed()
    with pytest.raises(RuntimeError, match="stage failed"):
        state.result()
    sync = _sync_records(run_records)
    assert all("Traceback" not in record.getMessage() for record in sync)
