"""The stored progress marker of a write execution (spec 022, research R2)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from infrahub_sync.product_store import ApplyProgress, PrefectExecutionLink, ProductProjection
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, S3ArtifactStore, SQLiteRunStore
from tests.product_store.test_contract import _append_execution, _connect, _FakeS3, _run

if TYPE_CHECKING:
    from pathlib import Path

# The column does not depend on the execution's purpose; a `plan` link needs no write admission.
_FLOW_RUN_ID = "flow-apply-1"
_LOGGER_NAME = "infrahub_sync.product_store.store"
# The emulated PostgreSQL store keeps its records in a SQLite file behind a PostgreSQL-dialect
# adapter, so a test can still change a row directly.
_PROFILES = ("local", "postgresql-emulated")
_MARKER = ApplyProgress(
    position=41,
    operation_id="op_0123456789abcdef",
    total=120,
    start_after=None,
    saved_at=datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc),
)


def _database(tmp_path: Path, profile: str) -> Path:
    return tmp_path / ("records.sqlite3" if profile == "local" else "postgres-emulator.sqlite3")


def _projection(tmp_path: Path, profile: str) -> ProductProjection:
    if profile == "local":
        return ProductProjection(SQLiteRunStore(_database(tmp_path, profile)), FileArtifactStore(tmp_path / "objects"))
    return ProductProjection(
        PostgreSQLRunStore(_connect(_database(tmp_path, profile))),
        S3ArtifactStore(_FakeS3(), bucket="product-artifacts", prefix="contract"),
    )


@pytest.fixture(params=_PROFILES)
def profile(request: pytest.FixtureRequest) -> str:
    return request.param


def _projection_with_execution(tmp_path: Path, profile: str) -> ProductProjection:
    projection = _projection(tmp_path, profile)
    run = _run()
    projection.create_run(run)
    _append_execution(projection, run.run_id, PrefectExecutionLink(flow_run_id=_FLOW_RUN_ID, purpose="plan", attempt=1))
    return projection


def _link(projection: ProductProjection) -> PrefectExecutionLink:
    run = projection.lookup_run("run-001").value
    assert run is not None
    return run.prefect_executions[0]


def _columns(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(prefect_executions)")}
    finally:
        connection.close()


def test_a_store_created_before_the_feature_gains_the_column_once_and_reads_old_rows_as_none(
    tmp_path: Path, profile: str
) -> None:
    _projection_with_execution(tmp_path, profile)
    database = _database(tmp_path, profile)
    # Return the store to its shape before this feature.
    connection = sqlite3.connect(database)
    try:
        connection.execute("ALTER TABLE prefect_executions DROP COLUMN apply_progress")
        connection.commit()
    finally:
        connection.close()
    assert "apply_progress" not in _columns(database)

    _projection(tmp_path, profile)
    restarted = _projection(tmp_path, profile)

    assert "apply_progress" in _columns(database)
    assert _link(restarted).apply_progress is None


def test_a_marker_round_trips(tmp_path: Path, profile: str) -> None:
    projection = _projection_with_execution(tmp_path, profile)

    assert projection.record_apply_progress("run-001", _FLOW_RUN_ID, _MARKER) is True

    assert _link(projection).apply_progress == _MARKER
    assert _link(_projection(tmp_path, profile)).apply_progress == _MARKER


def test_a_later_marker_replaces_the_earlier_one(tmp_path: Path, profile: str) -> None:
    projection = _projection_with_execution(tmp_path, profile)
    later = _MARKER.model_copy(update={"position": 99, "operation_id": "op_fedcba9876543210"})

    assert projection.record_apply_progress("run-001", _FLOW_RUN_ID, _MARKER) is True
    assert projection.record_apply_progress("run-001", _FLOW_RUN_ID, later) is True

    assert _link(projection).apply_progress == later


def test_a_write_after_the_execution_is_terminal_changes_nothing(tmp_path: Path, profile: str) -> None:
    projection = _projection_with_execution(tmp_path, profile)
    assert projection.record_apply_progress("run-001", _FLOW_RUN_ID, _MARKER) is True
    assert projection.abandon_execution("run-001", _FLOW_RUN_ID, terminal_at=datetime.now(timezone.utc))

    late = _MARKER.model_copy(update={"position": 99})
    assert projection.record_apply_progress("run-001", _FLOW_RUN_ID, late) is False

    assert _link(projection).apply_progress == _MARKER


def test_a_write_to_an_unknown_execution_changes_nothing(tmp_path: Path, profile: str) -> None:
    projection = _projection_with_execution(tmp_path, profile)

    assert projection.record_apply_progress("run-001", "flow-unknown", _MARKER) is False

    assert _link(projection).apply_progress is None


@pytest.mark.parametrize("stored", ["not json", "[1, 2]", '{"position": 1}'])
def test_a_stored_marker_that_does_not_parse_reads_as_none_and_logs_one_warning(
    tmp_path: Path, profile: str, stored: str, caplog: pytest.LogCaptureFixture
) -> None:
    projection = _projection_with_execution(tmp_path, profile)
    connection = sqlite3.connect(_database(tmp_path, profile))
    try:
        connection.execute("UPDATE prefect_executions SET apply_progress = ?", (stored,))
        connection.commit()
    finally:
        connection.close()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        link = _link(projection)

    assert link.apply_progress is None
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "run-001" in message
    assert stored not in message


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"position": 120}, id="position-outside-the-plan"),
        pytest.param({"position": -1}, id="negative-position"),
        pytest.param({"operation_id": "not an operation"}, id="operation-id-that-is-not-one"),
        pytest.param({"start_after": 41}, id="position-not-after-the-resume-start"),
        pytest.param({"saved_at": datetime(2026, 11, 12, 9, 30)}, id="time-without-a-timezone"),  # noqa: DTZ001
        pytest.param({"message": "password=ZZ-CANARY"}, id="extra-field"),
    ],
)
def test_a_marker_with_a_value_that_is_not_allowed_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ApplyProgress.model_validate({**_MARKER.model_dump(), **fields})
