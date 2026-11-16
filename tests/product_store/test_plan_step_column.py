"""The stored plan step document of a stage execution (spec 019, research R11, FR-019, FR-021)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection
from infrahub_sync.product_store.store import (
    FileArtifactStore,
    PostgreSQLRunStore,
    S3ArtifactStore,
    SQLiteRunStore,
)
from tests.product_store.test_contract import _append_execution, _connect, _FakeS3, _run

if TYPE_CHECKING:
    from pathlib import Path

_FLOW_RUN_ID = "flow-plan-1"
_STARTED = "2026-11-16T10:00:12.104000+00:00"
_ENDED = "2026-11-16T10:00:14.500000+00:00"
_EMPTY: dict[str, object] = {"parts": {}}
_COMPARING = {"running": "comparing", "parts": {"comparing": {"started_at": _STARTED}}}
_SAVED = {
    "parts": {
        "comparing": {"started_at": _STARTED, "ended_at": _ENDED},
        "checking": {"started_at": _ENDED, "ended_at": _ENDED},
        "saving": {"started_at": _ENDED, "ended_at": _ENDED},
    }
}

_LOGGER_NAME = "infrahub_sync.product_store.store"
# The emulated PostgreSQL store keeps its records in a SQLite file behind a PostgreSQL-dialect
# adapter, so the PostgreSQL SQL runs and a test can still change a row directly.
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


def _columns(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(prefect_executions)")}
    finally:
        connection.close()


def test_a_store_created_before_the_feature_gains_the_column_and_reads_old_rows_as_none(
    tmp_path: Path, profile: str
) -> None:
    _original, database = _projection_with_execution(tmp_path, profile)
    # Return the store to its shape before this feature.
    connection = sqlite3.connect(database)
    try:
        connection.execute("ALTER TABLE prefect_executions DROP COLUMN plan_step")
        connection.commit()
    finally:
        connection.close()
    assert "plan_step" not in _columns(database)

    restarted = _projection(tmp_path, profile)

    assert "plan_step" in _columns(database)
    assert _link(restarted).plan_step is None


def test_a_new_execution_has_no_document(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    assert _link(projection).plan_step is None


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(_EMPTY, id="no-part"),
        pytest.param(_COMPARING, id="comparing-runs"),
        pytest.param(_SAVED, id="saved"),
        pytest.param(
            {"parts": {"preparing": {"started_at": _STARTED, "ended_at": _ENDED}}}, id="preparing-for-a-later-release"
        ),
    ],
)
def test_each_document_the_worker_writes_is_stored(tmp_path: Path, profile: str, document: dict[str, object]) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, document) is True

    assert _link(projection).plan_step == document


def test_a_later_write_replaces_the_document(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, _COMPARING) is True

    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, _SAVED) is True

    assert _link(projection).plan_step == _SAVED


def test_the_document_leaves_the_read_columns_unchanged(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    reading = {"state": "reading", "started_at": _STARTED}
    assert projection.record_side_reads("run-001", _FLOW_RUN_ID, {"source": reading}) is True

    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, _COMPARING) is True

    link = _link(projection)
    assert link.source_read == reading
    assert link.destination_read is None
    assert link.plan_step == _COMPARING


def test_a_write_after_the_verdict_changes_nothing(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, _COMPARING) is True
    assert projection.abandon_execution("run-001", _FLOW_RUN_ID, terminal_at=datetime.now(timezone.utc))

    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, _SAVED) is False

    assert _link(projection).plan_step == _COMPARING


def test_a_write_to_an_unknown_execution_changes_nothing(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    assert projection.record_plan_step("run-001", "flow-unknown", _COMPARING) is False

    assert _link(projection).plan_step is None


@pytest.mark.parametrize("stored", ["{not json", "[1,2]"], ids=["not-json", "not-an-object"])
def test_a_stored_document_that_is_not_a_json_object_reads_as_none_and_logs_one_warning(
    tmp_path: Path, profile: str, stored: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A damaged plan step column must not stop the run from being read."""
    projection, database = _projection_with_execution(tmp_path, profile)
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE prefect_executions SET plan_step = ?", (stored,))
        connection.commit()
    finally:
        connection.close()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        link = _link(projection)

    assert link.plan_step is None
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "run-001" in message
    assert "plan_step" in message
    assert "not json" not in message
    assert "1,2" not in message


# A value that must never reach the run record or an error message.
_CANARY = "db.internal.example:5432 password=ZZ-PLAN-STEP-CANARY"


@pytest.mark.parametrize(
    "document",
    [
        pytest.param({**_COMPARING, "message": _CANARY}, id="extra-key"),
        pytest.param({**_COMPARING, _CANARY: "x"}, id="extra-key-named-by-the-value"),
        pytest.param({"running": "comparing"}, id="no-parts"),
        pytest.param({"parts": [_CANARY]}, id="parts-that-are-not-a-mapping"),
        pytest.param({"parts": {"reading": {"started_at": _STARTED}}}, id="read-part-stored-twice"),
        pytest.param({"parts": {"waiting": {"started_at": _STARTED}}}, id="waiting-is-derived"),
        pytest.param({"parts": {_CANARY: {"started_at": _STARTED}}}, id="unknown-part"),
        pytest.param({"parts": {"comparing": _CANARY}}, id="part-that-is-not-a-mapping"),
        pytest.param({"parts": {"comparing": {}}}, id="part-without-a-start"),
        pytest.param({"parts": {"comparing": {"ended_at": _ENDED}}}, id="part-with-only-an-end"),
        pytest.param({"parts": {"comparing": {"started_at": _STARTED, "error": _CANARY}}}, id="part-with-a-message"),
        pytest.param({"parts": {"comparing": {"started_at": _CANARY}}}, id="start-that-does-not-parse"),
        pytest.param({"parts": {"comparing": {"started_at": 1731751212}}}, id="start-that-is-not-text"),
        pytest.param({"parts": {"comparing": {"started_at": "2026-11-16T10:00:12"}}}, id="start-without-a-time-zone"),
        pytest.param(
            {"parts": {"comparing": {"started_at": _STARTED, "ended_at": _CANARY}}}, id="end-that-does-not-parse"
        ),
        pytest.param(
            {"parts": {"comparing": {"started_at": _STARTED, "ended_at": None}}}, id="end-that-is-none-not-absent"
        ),
        pytest.param({**_COMPARING, "running": _CANARY}, id="unknown-running-part"),
        pytest.param({**_COMPARING, "running": None}, id="running-that-is-none-not-absent"),
        pytest.param({"running": "checking", "parts": _COMPARING["parts"]}, id="running-part-without-an-entry"),
        pytest.param({"running": "comparing", "parts": _SAVED["parts"]}, id="running-part-that-ended"),
    ],
)
def test_a_document_with_a_key_or_value_that_is_not_allowed_is_refused_before_any_sql_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, document: dict[str, object]
) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)
    assert projection.record_plan_step("run-001", _FLOW_RUN_ID, _COMPARING) is True
    records = projection._records

    def no_connection() -> None:
        pytest.fail("a connection was opened for a refused document")

    with monkeypatch.context() as patch:
        patch.setattr(records, "_connect", no_connection)
        with pytest.raises(ValueError, match="plan step document") as refused:
            projection.record_plan_step("run-001", _FLOW_RUN_ID, document)

    assert _CANARY not in str(refused.value)
    assert _link(projection).plan_step == _COMPARING


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path: Path, profile: str) -> None:
    projection, _database = _projection_with_execution(tmp_path, profile)

    with pytest.raises(ValueError, match="plan step document"):
        projection.record_plan_step("run-001", _FLOW_RUN_ID, [_CANARY])  # ty: ignore[invalid-argument-type]

    assert _link(projection).plan_step is None
