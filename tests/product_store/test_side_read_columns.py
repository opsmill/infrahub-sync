"""The stored read state of each side of a stage execution (spec 018, research R3, critique R2)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, get_args

import pytest

from infrahub_sync.potenda.read_progress import ReadMode
from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection
from infrahub_sync.product_store.store import (
    _SIDE_READ_MODES,  # noqa: PLC2701 - the store's copy of the engine read modes
    FileArtifactStore,
    PostgreSQLRunStore,
    S3ArtifactStore,
    SQLiteRunStore,
)
from tests.product_store.test_contract import _append_execution, _connect, _FakeS3, _run

if TYPE_CHECKING:
    from pathlib import Path

_FLOW_RUN_ID = "flow-plan-1"
_SUCCEEDED = {
    "state": "succeeded",
    "started_at": "2026-11-09T10:00:01+00:00",
    "ended_at": "2026-11-09T10:00:13+00:00",
    "read_mode": "full",
    "nodes_by_kind": {"DcimDevice": 3, "LocationSite": 2},
}
_READING = {"state": "reading", "started_at": "2026-11-09T10:00:01+00:00"}


_LOGGER_NAME = "infrahub_sync.product_store.store"
# Each profile names the file its records live in: the emulated PostgreSQL store keeps them in
# a SQLite file behind a PostgreSQL-dialect adapter, so a test can still change a row directly.
_PROFILES = ("local", "postgresql-emulated")


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


def _projection_with_execution(tmp_path: Path, profile: str) -> tuple[ProductProjection, Path]:
    projection = _projection(tmp_path, profile)
    run = _run()
    projection.create_run(run)
    _append_execution(projection, run.run_id, PrefectExecutionLink(flow_run_id=_FLOW_RUN_ID, purpose="plan", attempt=1))
    return projection, _database(tmp_path, profile)


def _link(projection: ProductProjection) -> PrefectExecutionLink:
    run = projection.lookup_run("run-001").value
    assert run is not None
    return run.prefect_executions[0]


def test_a_store_created_before_the_feature_gains_both_columns_and_reads_old_rows_as_none(
    tmp_path: Path, profile: str
) -> None:
    _original, database = _projection_with_execution(tmp_path, profile)
    # Return the store to its shape before this feature.
    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(prefect_executions)")}
        for column in ("source_read", "destination_read"):
            if column in columns:
                connection.execute(f"ALTER TABLE prefect_executions DROP COLUMN {column}")
        connection.commit()
    finally:
        connection.close()

    restarted = _projection(tmp_path, profile)

    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(prefect_executions)")}
    finally:
        connection.close()
    assert {"source_read", "destination_read"} <= columns
    link = _link(restarted)
    assert link.source_read is None
    assert link.destination_read is None


def test_recording_one_side_leaves_the_other_side_unchanged(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"destination": _READING}) is True
    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": _SUCCEEDED}) is True

    link = _link(projection)
    assert link.source_read == _SUCCEEDED
    assert link.destination_read == _READING


def test_both_sides_are_recorded_in_one_write(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": _SUCCEEDED, "destination": _READING})

    link = _link(projection)
    assert link.source_read == _SUCCEEDED
    assert link.destination_read == _READING


def test_a_write_with_no_side_is_refused(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    with pytest.raises(ValueError, match="side"):
        projection.record_side_reads("run-001", _FLOW_RUN_ID, {})


def test_a_write_to_a_terminal_execution_changes_nothing(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": _READING})
    assert projection.abandon_execution("run-001", _FLOW_RUN_ID, terminal_at=datetime.now(timezone.utc))

    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": _SUCCEEDED}) is False

    link = _link(projection)
    assert link.source_read == _READING
    assert link.destination_read is None


@pytest.mark.parametrize("side", ["A", "source_read", "source; DROP TABLE prefect_executions", ""])
def test_an_unknown_side_is_refused_before_any_sql_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, side: str
) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    records = projection._records

    def no_connection() -> None:
        pytest.fail("a connection was opened for an unknown side")

    monkeypatch.setattr(records, "_connect", no_connection)

    with pytest.raises(ValueError, match="side"):
        projection.record_side_reads("run-001", _FLOW_RUN_ID, {side: _SUCCEEDED})  # ty: ignore[invalid-argument-type]


def test_both_sides_written_from_two_threads_at_once_are_both_stored(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def write(side: str, document: dict[str, object]) -> None:
        try:
            barrier.wait()
            for _ in range(20):
                projection.record_side_reads("run-001", _FLOW_RUN_ID, {side: document})  # ty: ignore[invalid-argument-type]
        except BaseException as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=("source", _SUCCEEDED)),
        threading.Thread(target=write, args=("destination", _READING)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    link = _link(projection)
    assert link.source_read == _SUCCEEDED
    assert link.destination_read == _READING


@pytest.mark.parametrize("stored", ["{not json", "[1,2]"], ids=["not-json", "not-an-object"])
def test_a_stored_read_that_is_not_a_json_object_reads_as_none_and_logs_one_warning(
    tmp_path: Path, profile: str, stored: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A damaged read column must not stop the run from being read, by the API or by the worker."""
    projection, database = _projection_with_execution(tmp_path, profile)
    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"destination": _SUCCEEDED}) is True
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE prefect_executions SET source_read = ?", (stored,))
        connection.commit()
    finally:
        connection.close()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        link = _link(projection)

    assert link.source_read is None
    assert link.destination_read == _SUCCEEDED
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "run-001" in message
    assert "source_read" in message
    assert stored not in message
    assert "not json" not in message
    assert "1,2" not in message


# A value that must never reach the run record or an error message.
_CANARY = "db.internal.example:5432 password=ZZ-WRITE-CANARY"
_FAILED = {
    "state": "failed",
    "started_at": "2026-11-09T10:00:01+00:00",
    "ended_at": "2026-11-09T10:00:02+00:00",
    "error_class": "ConnectionError",
}


@pytest.mark.parametrize(
    "document",
    [
        pytest.param({**_READING, "message": _CANARY}, id="extra-field"),
        pytest.param({**_READING, _CANARY: "x"}, id="extra-field-named-by-the-value"),
        pytest.param({**_READING, "state": _CANARY}, id="unknown-state"),
        pytest.param({"started_at": "2026-11-09T10:00:01+00:00"}, id="no-state"),
        pytest.param({**_SUCCEEDED, "read_mode": _CANARY}, id="unknown-read-mode"),
        pytest.param({**_READING, "started_at": _CANARY}, id="timestamp-that-does-not-parse"),
        pytest.param({**_READING, "started_at": 1731146401}, id="timestamp-that-is-not-text"),
        pytest.param({**_SUCCEEDED, "nodes_by_kind": {"DcimDevice": _CANARY}}, id="count-that-is-text"),
        pytest.param({**_SUCCEEDED, "nodes_by_kind": {"DcimDevice": -1}}, id="negative-count"),
        pytest.param({**_SUCCEEDED, "nodes_by_kind": {"DcimDevice": True}}, id="count-that-is-a-bool"),
        pytest.param({**_SUCCEEDED, "nodes_by_kind": {"DcimDevice": 1.5}}, id="count-that-is-a-float"),
        pytest.param({**_SUCCEEDED, "nodes_by_kind": None}, id="counts-that-are-none"),
        pytest.param({**_FAILED, "error_class": _CANARY}, id="error-class-holding-a-message"),
        pytest.param({**_FAILED, "error_class": "E" * 201}, id="error-class-too-long"),
    ],
)
def test_a_read_document_with_a_field_or_value_that_is_not_allowed_is_refused_before_any_sql_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, document: dict[str, object]
) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"destination": _READING}) is True
    records = projection._records

    def no_connection() -> None:
        pytest.fail("a connection was opened for a refused document")

    with monkeypatch.context() as patch:
        patch.setattr(records, "_connect", no_connection)
        with pytest.raises(ValueError, match="source read document") as refused:
            projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": document, "destination": _SUCCEEDED})

    assert _CANARY not in str(refused.value)
    link = _link(projection)
    assert link.source_read is None
    assert link.destination_read == _READING


@pytest.mark.parametrize(
    "document",
    [
        pytest.param({"state": "waiting"}, id="waiting"),
        pytest.param(_READING, id="reading"),
        pytest.param(_SUCCEEDED, id="succeeded"),
        pytest.param({**_SUCCEEDED, "read_mode": "incremental", "nodes_by_kind": {}}, id="incremental-no-nodes"),
        pytest.param(_FAILED, id="failed"),
        pytest.param({key: value for key, value in _FAILED.items() if key != "started_at"}, id="failed-before-start"),
    ],
)
def test_each_read_document_the_worker_writes_is_stored(
    tmp_path: Path, profile: str, document: dict[str, object]
) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": document}) is True

    assert _link(projection).source_read == document


def test_the_read_modes_the_store_allows_are_the_engine_read_modes() -> None:
    assert set(get_args(ReadMode)) == _SIDE_READ_MODES
