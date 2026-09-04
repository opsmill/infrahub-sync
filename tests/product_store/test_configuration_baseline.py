"""The configuration-scoped safety baseline, committed with a successful write verdict.

The baseline is the input to the next run's rowcount guardrail and full-extract cadence,
so a run that did not succeed must not move it. SQLite covers the transaction shape; the
same cases run against a real PostgreSQL server when ``PRODUCT_STORE_TEST_POSTGRESQL_DSN``
names one, because the durable authority is a PostgreSQL table.

WARNING: the PostgreSQL parameter's session fixture runs ``DROP SCHEMA public CASCADE``
against whatever database ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` points at, which destroys
every table in that database's ``public`` schema. Point that variable only at a disposable,
single-purpose database — never at a shared or persistent development database.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from infrahub_sync.product_store import (
    BaselineWriteback,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_DSN_ENVIRONMENT_NAME = "PRODUCT_STORE_TEST_POSTGRESQL_DSN"
_SESSION = uuid4().hex[:10]
_WORKER_ID = "2b7c9d41-6e0a-4f18-8c53-1d4e7a9b0c62"
_STARTED_AT = datetime(2026, 9, 3, 9, tzinfo=timezone.utc)
_COUNTS = {"device": 120, "interface": 4400}


def _postgresql_dsn_or_skip() -> str:
    """Return a reachable disposable DSN, or skip before any server is contacted."""
    dsn = os.environ.get(_DSN_ENVIRONMENT_NAME)
    if not dsn:
        pytest.skip(f"the baseline contract's PostgreSQL parameter requires {_DSN_ENVIRONMENT_NAME}")
    psycopg = pytest.importorskip("psycopg")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as probe:
            probe.execute("SELECT 1")
    except psycopg.Error:
        pytest.skip(f"{_DSN_ENVIRONMENT_NAME} is not reachable")
    return dsn


@pytest.fixture(name="_postgresql_schema", scope="session")
def postgresql_schema_fixture() -> str:
    """Recreate the disposable database's schema once, then return its DSN."""
    dsn = _postgresql_dsn_or_skip()
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    with psycopg.connect(dsn) as admin:
        admin.execute("DROP SCHEMA public CASCADE")
        admin.execute("CREATE SCHEMA public")
        admin.commit()
    return dsn


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    dsn = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    return f"{name}-{_SESSION}"


def _config(name: str) -> str:
    return f"{name}-{_SESSION}"


def _run(run_id: str, *, config_id: str) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="sync",
        configuration_reference=f"{config_id}@1",
        config_id=config_id,
        registry_version=1,
        package_checksum="a" * 64,
        actor="operator@example.com",
        started_at=_STARTED_AT,
        phase="accepted",
    )


def _receipt(receipt_id: str, *, run_id: str) -> MutationReceipt:
    return MutationReceipt(
        receipt_id=f"{receipt_id}.{run_id}",
        actor="operator@example.com",
        key_digest=sha256(f"{receipt_id}:{run_id}".encode()).hexdigest(),
        operation="sync",
        target_run_id=run_id,
        request_fingerprint=sha256(f"sync:{run_id}".encode()).hexdigest(),
        reason="baseline contract",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{receipt_id}:{run_id}".encode()).hexdigest(),
        created_at=_STARTED_AT,
        updated_at=_STARTED_AT,
    )


def _claimed(projection: ProductProjection, run_id: str, *, config_id: str, flow_run_id: str) -> None:
    """Create a run whose one admitted sync execution is claimed and awaiting its verdict."""
    projection.create_run(_run(run_id, config_id=config_id))
    receipt, _created = projection.reserve_mutation(_receipt("m-sync", run_id=run_id), admit_write=True)
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id=flow_run_id,
            purpose="sync",
            attempt=1,
            submitted_at=datetime.now(timezone.utc),
        ),
        receipt_id=receipt.receipt_id,
    )
    projection.complete_mutation(
        receipt.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id=flow_run_id,
    )
    projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)


def _finish(
    *,
    outcome: str = "succeeded",
    baseline: BaselineWriteback | None = None,
) -> ExecutionFinishWriteback:
    return ExecutionFinishWriteback(
        phase="finished",
        outcome=outcome,
        finished_at=datetime.now(timezone.utc),
        summary={"total": 2},
        results={"status": "synced"},
        baseline=baseline,
    )


def _commit(
    projection: ProductProjection,
    run_id: str,
    flow_run_id: str,
    *,
    terminal: tuple[str, str] = ("completed", "succeeded"),
    writeback: Any,  # noqa: ANN401 - the store's discriminated writeback union.
) -> bool:
    terminal_state, terminal_outcome = terminal
    return projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=datetime.now(timezone.utc),
        terminal_state=terminal_state,
        terminal_outcome=terminal_outcome,
        writeback=writeback,
    )


def test_a_successful_write_verdict_records_the_baseline(projection: ProductProjection) -> None:
    run_id, config_id = _rid("baseline-success"), _config("cfg-success")
    _claimed(projection, run_id, config_id=config_id, flow_run_id="flow-success")

    committed = _commit(
        projection,
        run_id,
        "flow-success",
        writeback=_finish(baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=True)),
    )

    assert committed
    stored = projection.lookup_configuration_baseline(config_id)
    assert stored.value is not None
    assert stored.value.source_row_counts == _COUNTS
    assert stored.value.runs_since_full_extract == 0


@pytest.mark.parametrize(
    ("terminal_state", "terminal_outcome"),
    [
        pytest.param("failed", "failed", id="failed"),
        pytest.param("interrupted", "ambiguous", id="ambiguous"),
    ],
)
def test_a_non_successful_terminal_does_not_record_a_baseline(
    projection: ProductProjection, terminal_state: str, terminal_outcome: str
) -> None:
    """A run that may not have written must not become the next run's safety reference."""
    run_id = _rid(f"baseline-{terminal_outcome}")
    config_id = _config(f"cfg-{terminal_outcome}")
    _claimed(projection, run_id, config_id=config_id, flow_run_id=f"flow-{terminal_outcome}")

    committed = _commit(
        projection,
        run_id,
        f"flow-{terminal_outcome}",
        terminal=(terminal_state, terminal_outcome),
        writeback=_finish(
            outcome=terminal_outcome,
            baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=True),
        ),
    )

    assert committed
    assert projection.lookup_configuration_baseline(config_id).value is None


def test_a_non_successful_terminal_leaves_an_earlier_baseline_untouched(
    projection: ProductProjection,
) -> None:
    """Not clearing is as important as not writing: the old reference stays authoritative."""
    config_id = _config("cfg-retained")
    first = _rid("baseline-retained-first")
    _claimed(projection, first, config_id=config_id, flow_run_id="flow-retained-first")
    _commit(
        projection,
        first,
        "flow-retained-first",
        writeback=_finish(baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=True)),
    )
    second = _rid("baseline-retained-second")
    _claimed(projection, second, config_id=config_id, flow_run_id="flow-retained-second")

    _commit(
        projection,
        second,
        "flow-retained-second",
        terminal=("interrupted", "ambiguous"),
        writeback=_finish(
            outcome="ambiguous",
            baseline=BaselineWriteback(source_row_counts={"device": 1}, full_extract=True),
        ),
    )

    stored = projection.lookup_configuration_baseline(config_id)
    assert stored.value is not None
    assert stored.value.source_row_counts == _COUNTS


def test_a_merge_writeback_never_records_a_baseline(projection: ProductProjection) -> None:
    """A verify verdict succeeds without writing, so it carries no safety reference."""
    run_id, config_id = _rid("baseline-merge"), _config("cfg-merge")
    _claimed(projection, run_id, config_id=config_id, flow_run_id="flow-merge")

    _commit(
        projection,
        run_id,
        "flow-merge",
        writeback=ExecutionMergeWriteback(results={"verification": {"outcome": "verified"}}),
    )

    assert projection.lookup_configuration_baseline(config_id).value is None


def test_a_successful_verdict_without_a_baseline_records_nothing(projection: ProductProjection) -> None:
    run_id, config_id = _rid("baseline-absent"), _config("cfg-absent")
    _claimed(projection, run_id, config_id=config_id, flow_run_id="flow-absent")

    _commit(
        projection,
        run_id,
        "flow-absent",
        writeback=_finish(),
    )

    assert projection.lookup_configuration_baseline(config_id).value is None


def test_an_incremental_run_advances_the_full_extract_cadence(projection: ProductProjection) -> None:
    config_id = _config("cfg-cadence")
    for index, full_extract in enumerate((True, False, False)):
        run_id = _rid(f"baseline-cadence-{index}")
        flow_run_id = f"flow-cadence-{index}"
        _claimed(projection, run_id, config_id=config_id, flow_run_id=flow_run_id)
        _commit(
            projection,
            run_id,
            flow_run_id,
            writeback=_finish(baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=full_extract)),
        )

    stored = projection.lookup_configuration_baseline(config_id)
    assert stored.value is not None
    assert stored.value.runs_since_full_extract == 2


def test_a_full_extract_resets_the_cadence(projection: ProductProjection) -> None:
    config_id = _config("cfg-reset")
    for index, full_extract in enumerate((True, False, True)):
        run_id = _rid(f"baseline-reset-{index}")
        flow_run_id = f"flow-reset-{index}"
        _claimed(projection, run_id, config_id=config_id, flow_run_id=flow_run_id)
        _commit(
            projection,
            run_id,
            flow_run_id,
            writeback=_finish(baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=full_extract)),
        )

    stored = projection.lookup_configuration_baseline(config_id)
    assert stored.value is not None
    assert stored.value.runs_since_full_extract == 0


def test_baselines_are_scoped_to_one_configuration(projection: ProductProjection) -> None:
    """Two configurations sync independently; one's row counts never guard the other."""
    for suffix, counts in (("a", {"device": 10}), ("b", {"device": 900})):
        run_id = _rid(f"baseline-scope-{suffix}")
        flow_run_id = f"flow-scope-{suffix}"
        _claimed(projection, run_id, config_id=_config(f"cfg-scope-{suffix}"), flow_run_id=flow_run_id)
        _commit(
            projection,
            run_id,
            flow_run_id,
            writeback=_finish(baseline=BaselineWriteback(source_row_counts=counts, full_extract=True)),
        )

    first = projection.lookup_configuration_baseline(_config("cfg-scope-a"))
    second = projection.lookup_configuration_baseline(_config("cfg-scope-b"))
    assert first.value is not None
    assert second.value is not None
    assert first.value.source_row_counts == {"device": 10}
    assert second.value.source_row_counts == {"device": 900}


def test_a_lost_verdict_race_writes_no_baseline(projection: ProductProjection) -> None:
    """The baseline shares the verdict's transaction, so a refused verdict changes nothing."""
    run_id, config_id = _rid("baseline-race"), _config("cfg-race")
    _claimed(projection, run_id, config_id=config_id, flow_run_id="flow-race")
    _commit(
        projection,
        run_id,
        "flow-race",
        writeback=_finish(baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=True)),
    )

    committed = _commit(
        projection,
        run_id,
        "flow-race",
        writeback=_finish(baseline=BaselineWriteback(source_row_counts={"device": 3}, full_extract=False)),
    )

    assert not committed
    stored = projection.lookup_configuration_baseline(config_id)
    assert stored.value is not None
    assert stored.value.source_row_counts == _COUNTS
    assert stored.value.runs_since_full_extract == 0


def test_an_unbound_run_records_no_baseline(projection: ProductProjection) -> None:
    """A run without a registered configuration has nothing to scope a baseline to."""
    run_id = _rid("baseline-unbound")
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="sync",
            configuration_reference="sha256:unbound",
            actor="operator@example.com",
            started_at=_STARTED_AT,
            phase="accepted",
        )
    )
    receipt, _created = projection.reserve_mutation(_receipt("m-sync", run_id=run_id), admit_write=True)
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id="flow-unbound",
            purpose="sync",
            attempt=1,
            submitted_at=datetime.now(timezone.utc),
        ),
        receipt_id=receipt.receipt_id,
    )
    projection.complete_mutation(
        receipt.receipt_id,
        response_status=202,
        response_body={"accepted": True},
        flow_run_id="flow-unbound",
    )
    projection.claim_execution(run_id, "flow-unbound", worker_id=_WORKER_ID)

    committed = _commit(
        projection,
        run_id,
        "flow-unbound",
        writeback=_finish(baseline=BaselineWriteback(source_row_counts=_COUNTS, full_extract=True)),
    )

    assert committed
