"""The `worker_environments` table: credential variable names each worker published (spec 019 FR-035).

SQLite covers every case. The same cases run against a real PostgreSQL server when
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names one (``-m integration``), confined to a generated
schema; see ``tests/product_store/postgresql_isolation``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pydantic
import pytest

from infrahub_sync.product_store import ProductProjection
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NOW = datetime(2026, 11, 11, 9, tzinfo=timezone.utc)
_NETBOX = "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"
_INFRAHUB = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"

_postgresql_schema = postgresql_schema_fixture("the worker environment PostgreSQL parameter")


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    schema = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


class _Workers:
    """Worker names namespaced to one test case.

    The PostgreSQL schema lives for the whole session, so a case reads only the rows it wrote.
    """

    def __init__(self) -> None:
        self.suffix = uuid4().hex[:10]

    def name(self, name: str) -> str:
        return f"{name}-{self.suffix}"

    def mine(self, projection: ProductProjection, *, observed_after: datetime) -> dict[str, tuple[str, ...]]:
        return {
            environment.worker_id: environment.credential_names
            for environment in projection.live_worker_environments(observed_after=observed_after)
            if environment.worker_id.endswith(self.suffix)
        }


@pytest.fixture
def workers() -> _Workers:
    """A fresh worker-name namespace for each test case."""
    return _Workers()


def test_a_publication_replaces_the_names_the_worker_published_before(
    projection: ProductProjection, workers: _Workers
) -> None:
    projection.publish_worker_environment(workers.name("a"), [_NETBOX, _INFRAHUB], observed_at=_NOW)
    projection.publish_worker_environment(workers.name("a"), [_INFRAHUB], observed_at=_NOW + timedelta(seconds=10))

    assert workers.mine(projection, observed_after=_NOW - timedelta(hours=1)) == {workers.name("a"): (_INFRAHUB,)}


def test_only_workers_that_published_after_the_threshold_are_live(
    projection: ProductProjection, workers: _Workers
) -> None:
    projection.publish_worker_environment(workers.name("stale"), [_NETBOX], observed_at=_NOW - timedelta(minutes=5))
    projection.publish_worker_environment(workers.name("live"), [_INFRAHUB], observed_at=_NOW)

    assert workers.mine(projection, observed_after=_NOW - timedelta(seconds=30)) == {workers.name("live"): (_INFRAHUB,)}


@pytest.mark.parametrize("name", ["INFRAHUB_SYNC_DATABASE_URL", "postgresql://sync:secret@db/sync", ""])
def test_anything_but_a_credential_variable_name_is_refused(
    projection: ProductProjection, workers: _Workers, name: str
) -> None:
    with pytest.raises(pydantic.ValidationError):
        projection.publish_worker_environment(workers.name("refused"), [name], observed_at=_NOW)

    assert workers.mine(projection, observed_after=_NOW - timedelta(hours=1)) == {}
