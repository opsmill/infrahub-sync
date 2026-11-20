"""The "registered without validation" mark on a configuration version (spec 025, User Story 4).

The mark is a row in ``configuration_versions_unvalidated``, written in the transaction that
creates the version. No row reads as ``false``. The cases run against SQLite and, when
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names a server, against PostgreSQL in a generated schema
that only these tests create and drop (see ``tests/product_store/postgresql_isolation``).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.product_store import ProductProjection
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.configuration.validation_packages import package, package_data
from tests.product_store import postgresql_isolation
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from infrahub_sync.configuration.models import ConfigurationPackage

_MARK_TABLE = "configuration_versions_unvalidated"

_postgresql_schema = postgresql_schema_fixture("the unvalidated mark's PostgreSQL parameter")


def _postgresql_store(dsn: str) -> PostgreSQLRunStore:
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    # pylint: disable-next=import-outside-toplevel
    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(dsn)

    return PostgreSQLRunStore(connect)


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    schema = request.getfixturevalue("_postgresql_schema")
    yield ProductProjection(_postgresql_store(schema.dsn), artifacts)


def _second_package() -> ConfigurationPackage:
    """Return a valid package whose checksum differs from the default one."""
    data = package_data()
    data["omissions"] = [{"kind": "InfraDevice"}]
    return package(data)


def _marks(projection: ProductProjection, config_id: str) -> dict[str, dict[int, bool]]:
    """Return the mark of every version of ``config_id`` from lookup, version list and version page."""
    versions = projection.list_configuration_versions(config_id)
    looked_up: dict[int, bool] = {}
    for version in versions:
        found = projection.lookup_configuration_version(config_id, version.registry_version).value
        assert found is not None
        looked_up[version.registry_version] = found.registered_without_validation
    page = projection.list_configuration_version_page(config_id)
    return {
        "lookup": looked_up,
        "list": {version.registry_version: version.registered_without_validation for version in versions},
        "page": {item.registry_version: item.registered_without_validation for item in page.items},
    }


def test_a_marked_first_version_reads_back_marked_everywhere(projection: ProductProjection) -> None:
    created = projection.create_configuration(package(), registered_without_validation=True)

    assert created.registered_without_validation is True
    assert _marks(projection, created.config_id) == {"lookup": {1: True}, "list": {1: True}, "page": {1: True}}


def test_a_version_registered_without_the_mark_reads_false(projection: ProductProjection) -> None:
    created = projection.create_configuration(package())
    added, was_created = projection.add_configuration_version(
        created.config_id, _second_package(), registered_without_validation=False
    )

    assert was_created
    assert created.registered_without_validation is False
    assert added.registered_without_validation is False
    assert _marks(projection, created.config_id) == {
        "lookup": {1: False, 2: False},
        "list": {1: False, 2: False},
        "page": {1: False, 2: False},
    }


def test_a_marked_added_version_is_marked_and_its_predecessor_is_not(projection: ProductProjection) -> None:
    created = projection.create_configuration(package())
    added, was_created = projection.add_configuration_version(
        created.config_id, _second_package(), registered_without_validation=True
    )

    assert was_created
    assert added.registered_without_validation is True
    assert _marks(projection, created.config_id) == {
        "lookup": {1: False, 2: True},
        "list": {1: False, 2: True},
        "page": {1: False, 2: True},
    }


@pytest.mark.parametrize("existing_mark", [False, True], ids=["unmarked-existing", "marked-existing"])
def test_an_identical_package_does_not_change_the_existing_mark(
    projection: ProductProjection, *, existing_mark: bool
) -> None:
    created = projection.create_configuration(package())
    first, _ = projection.add_configuration_version(
        created.config_id, _second_package(), registered_without_validation=existing_mark
    )

    again, was_created = projection.add_configuration_version(
        created.config_id, _second_package(), registered_without_validation=not existing_mark
    )

    assert not was_created
    assert again.registry_version == first.registry_version
    assert again.registered_without_validation is existing_mark
    assert _marks(projection, created.config_id)["lookup"] == {1: False, 2: existing_mark}


def _sqlite_tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_sqlite_store_created_before_the_mark_gains_the_table_at_start_up(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    artifacts = FileArtifactStore(tmp_path / "objects")
    created = ProductProjection(SQLiteRunStore(database), artifacts).create_configuration(package())
    with sqlite3.connect(database) as connection:
        connection.execute(f"DROP TABLE {_MARK_TABLE}")
    assert _MARK_TABLE not in _sqlite_tables(database)

    reopened = ProductProjection(SQLiteRunStore(database), artifacts)

    assert _MARK_TABLE in _sqlite_tables(database)
    assert _marks(reopened, created.config_id) == {"lookup": {1: False}, "list": {1: False}, "page": {1: False}}


@pytest.mark.integration
def test_a_postgresql_store_created_before_the_mark_gains_the_table_at_start_up(tmp_path: Path) -> None:
    """Its own generated schema: dropping the table in the shared one would break sibling cases."""
    schema = postgresql_isolation.isolated_schema(
        postgresql_isolation.dsn_or_skip("the unvalidated mark's PostgreSQL start-up case")
    )
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    def tables() -> set[str]:
        with psycopg.connect(schema.dsn) as connection:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
                )
            }

    artifacts = FileArtifactStore(tmp_path / "objects")
    schema.create()
    try:
        created = ProductProjection(_postgresql_store(schema.dsn), artifacts).create_configuration(package())
        with psycopg.connect(schema.dsn) as connection:
            connection.execute(f"DROP TABLE {_MARK_TABLE}")
            connection.commit()
        assert _MARK_TABLE not in tables()

        reopened = ProductProjection(_postgresql_store(schema.dsn), artifacts)

        assert _MARK_TABLE in tables()
        assert _marks(reopened, created.config_id) == {"lookup": {1: False}, "list": {1: False}, "page": {1: False}}
    finally:
        schema.drop()


def test_a_failed_mark_insert_leaves_no_version(tmp_path: Path) -> None:
    """The mark row is written in the version's transaction, so a failed mark stores no version."""
    database = tmp_path / "records.sqlite3"
    projection = ProductProjection(SQLiteRunStore(database), FileArtifactStore(tmp_path / "objects"))
    created = projection.create_configuration(package())
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"CREATE TRIGGER refuse_mark BEFORE INSERT ON {_MARK_TABLE} BEGIN SELECT RAISE(ABORT, 'mark refused'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="mark refused"):
        projection.add_configuration_version(created.config_id, _second_package(), registered_without_validation=True)

    assert [version.registry_version for version in projection.list_configuration_versions(created.config_id)] == [1]
    assert _marks(projection, created.config_id)["lookup"] == {1: False}
