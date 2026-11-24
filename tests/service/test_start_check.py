"""The Sync API and the worker refuse to start before the bootstrap of their version has run (spec 018 FR-007b).

The check reads the bootstrap record on a plain connection. These cases use SQLite for the
record itself, the real PostgreSQL driver for a database that cannot be reached, and the two
real entry points with the check injected or refusing.
"""

from __future__ import annotations

import sqlite3
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("psycopg")

from infrahub_sync.product_store import (
    DBAPIConnection,
    ProductProjection,
    ProductStoreProviderError,
)
from infrahub_sync.product_store.store import FileArtifactStore, SQLiteRunStore
from infrahub_sync.service import serve, worker
from infrahub_sync.service.settings import ADMIN_ACCOUNT_PASSWORD_ENV
from infrahub_sync.service.start_check import START_REFUSED_MESSAGE, StartRefusedError, check_bootstrap_record
from infrahub_sync.service.storage import DATABASE_URL_ENV

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

VERSION = "3.0.0a5"
POOL = "infrahub-sync"
_COMPLETED = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
PASSWORD_CANARY = "start-check-password-canary"  # noqa: S105 - a canary, not a credential.
# Port 1 on loopback refuses at once, so the real driver fails without a timeout.
UNREACHABLE_URL = f"postgresql://sync:{PASSWORD_CANARY}@127.0.0.1:1/sync?connect_timeout=2"


def _database(tmp_path: Path) -> Path:
    return tmp_path / "records.sqlite3"


def _connect(tmp_path: Path) -> Callable[[Mapping[str, str]], DBAPIConnection]:
    def connect(_values: Mapping[str, str]) -> DBAPIConnection:
        return cast("DBAPIConnection", sqlite3.connect(_database(tmp_path)))

    return connect


def _bootstrapped(tmp_path: Path, *, version: str = VERSION, pool: str = POOL) -> None:
    projection = ProductProjection(SQLiteRunStore(_database(tmp_path)), FileArtifactStore(tmp_path / "objects"))
    projection.write_bootstrap_record(version, pool, completed_at=_COMPLETED)


def _check(tmp_path: Path, pool: str = POOL) -> None:
    check_bootstrap_record(pool, environ={}, connect=_connect(tmp_path), sync_version=lambda: VERSION)


def test_a_matching_record_lets_the_process_start(tmp_path: Path) -> None:
    _bootstrapped(tmp_path)

    _check(tmp_path)


def test_a_missing_record_refuses_and_names_what_was_expected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ProductProjection(SQLiteRunStore(_database(tmp_path)), FileArtifactStore(tmp_path / "objects"))

    with caplog.at_level("ERROR"), pytest.raises(StartRefusedError, match=START_REFUSED_MESSAGE):
        _check(tmp_path)

    assert f"no bootstrap record found; expected Sync version {VERSION} and work pool {POOL}" in caplog.text


def test_a_missing_table_refuses_and_creates_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR"), pytest.raises(StartRefusedError):
        _check(tmp_path)

    assert "no bootstrap record found" in caplog.text
    connection = sqlite3.connect(_database(tmp_path))
    try:
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone() == (0,)
    finally:
        connection.close()


def test_a_record_of_another_version_refuses_and_names_both(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _bootstrapped(tmp_path, version="3.0.0a4")

    with caplog.at_level("ERROR"), pytest.raises(StartRefusedError):
        _check(tmp_path)

    assert (
        f"the bootstrap record names Sync version 3.0.0a4 and work pool {POOL}; "
        f"expected Sync version {VERSION} and work pool {POOL}"
    ) in caplog.text


def test_a_record_of_another_work_pool_refuses_and_names_both(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _bootstrapped(tmp_path, pool="other-pool")

    with caplog.at_level("ERROR"), pytest.raises(StartRefusedError):
        _check(tmp_path)

    assert (
        f"the bootstrap record names Sync version {VERSION} and work pool other-pool; "
        f"expected Sync version {VERSION} and work pool {POOL}"
    ) in caplog.text


def test_a_database_that_cannot_be_reached_refuses_without_the_connection_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real driver, against a port that refuses: no URL, password or driver text crosses."""
    with caplog.at_level("DEBUG"), pytest.raises(StartRefusedError) as refused:
        check_bootstrap_record(POOL, environ={DATABASE_URL_ENV: UNREACHABLE_URL}, sync_version=lambda: VERSION)

    assert f"the product database ({DATABASE_URL_ENV}) cannot be reached" in caplog.text
    assert refused.value.__cause__ is None
    assert refused.value.__suppress_context__
    rendered = "".join(traceback.format_exception(refused.value))
    for text in (caplog.text, str(refused.value), rendered):
        assert PASSWORD_CANARY not in text
        assert "127.0.0.1:1" not in text


def test_a_database_failure_during_the_read_refuses(caplog: pytest.LogCaptureFixture) -> None:
    class _Refusing:
        def cursor(self) -> _Refusing:
            return self

        @staticmethod
        def execute(*_arguments: Any) -> NoReturn:  # noqa: ANN401 - DB-API parameters.
            raise ProductStoreProviderError(sqlstate="57P01")

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    with caplog.at_level("ERROR"), pytest.raises(StartRefusedError):
        check_bootstrap_record(
            POOL, environ={}, connect=lambda _values: cast("DBAPIConnection", _Refusing()), sync_version=lambda: VERSION
        )

    assert "the bootstrap record cannot be read" in caplog.text


@pytest.mark.parametrize("value", [None, "", "not a connection string ="], ids=["unset", "empty", "invalid"])
def test_a_missing_or_invalid_database_setting_refuses_by_name(
    value: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    environ = {} if value is None else {DATABASE_URL_ENV: value}

    with caplog.at_level("ERROR"), pytest.raises(StartRefusedError):
        check_bootstrap_record(POOL, environ=environ, sync_version=lambda: VERSION)

    assert DATABASE_URL_ENV in caplog.text
    if value:
        assert value not in caplog.text


def test_an_unknown_installed_version_refuses(tmp_path: Path) -> None:
    _bootstrapped(tmp_path)

    def unknown() -> NoReturn:
        message = "service package metadata is unavailable"
        raise RuntimeError(message)

    with pytest.raises(StartRefusedError):
        check_bootstrap_record(POOL, environ={}, connect=_connect(tmp_path), sync_version=unknown)


# ---------------------------------------------------------------------------
# The Sync API
# ---------------------------------------------------------------------------


def _refuse(_work_pool: str) -> NoReturn:
    raise StartRefusedError


def test_the_api_factory_refuses_before_the_product_schema_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """`uvicorn --factory infrahub_sync.service.serve:build_app` runs the check without `main()`."""
    monkeypatch.setattr(serve, "install_custom_ca_bundle", lambda: None)
    # The variables of the `admin` account are read first; a valid password lets the check run.
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, "administrator-password-0001")

    with pytest.raises(StartRefusedError):
        serve.build_app(
            projection_factory=lambda: pytest.fail("the product schema step ran before the start check"),
            start_check=_refuse,
        )


def test_the_api_checks_the_work_pool_it_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "install_custom_ca_bundle", lambda: None)
    monkeypatch.setenv("INFRAHUB_SYNC_SERVICE_WORK_POOL", "team-pool")
    checked: list[str] = []
    reconciled: list[str] = []

    class _Resolver:
        secret_values: tuple[str, ...] = ()

    class _Reconciler:
        def __init__(self, *arguments: object, **_options: object) -> None:
            reconciled.append(cast("str", arguments[-1]))

    monkeypatch.setattr(serve, "RunLivenessReconciler", _Reconciler)
    serve.build_app(
        projection_factory=object,
        resolver_factory=_Resolver,
        run_service_factory=lambda *_args, **_kwargs: object(),
        configuration_routes_factory=lambda **_kwargs: object(),
        app_factory=lambda *_args, **_kwargs: object(),
        start_check=checked.append,
    )

    assert checked == reconciled == ["team-pool"]


def test_the_api_main_exits_non_zero_on_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused_build(**_factories: object) -> NoReturn:
        raise StartRefusedError

    monkeypatch.setattr(serve, "build_app", refused_build)
    monkeypatch.setattr(serve.uvicorn, "run", lambda *_args, **_kwargs: pytest.fail("the server started"))

    with pytest.raises(SystemExit) as stopped:
        serve.main()

    assert stopped.value.code == START_REFUSED_MESSAGE


def test_the_api_default_check_is_the_bootstrap_record_check() -> None:
    import inspect

    assert inspect.signature(serve.build_app).parameters["start_check"].default is check_bootstrap_record


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


def test_the_worker_exits_non_zero_before_it_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "install_custom_ca_bundle", lambda: None)
    monkeypatch.setattr(worker, "ServiceProcessWorker", lambda **_settings: pytest.fail("the worker was built"))

    assert worker.main(["--pool", POOL], start_check=_refuse) == 1


def test_the_worker_checks_the_pool_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[str] = []

    class _StopError(Exception):
        """Ends `main` once the worker would be built."""

    def build(**_settings: object) -> NoReturn:
        raise _StopError

    monkeypatch.setattr(worker, "install_custom_ca_bundle", lambda: None)
    monkeypatch.setattr(worker, "ServiceProcessWorker", build)

    with pytest.raises(_StopError):
        worker.main(["--pool", "team-pool"], start_check=checked.append)

    assert checked == ["team-pool"]


def test_the_worker_default_check_reads_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset, `main` runs the real check: no database setting refuses the start."""
    monkeypatch.setattr(worker, "install_custom_ca_bundle", lambda: None)
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    monkeypatch.setattr(worker, "ServiceProcessWorker", lambda **_settings: pytest.fail("the worker was built"))

    assert worker.main(["--pool", POOL]) == 1
