"""A database of an earlier build keeps its ``worker_environments`` table (spec 027, FR-022, research R7).

Workers no longer publish the names of their credential variables, and the product store no
longer creates, reads or writes that table. The store drops no table: a database that an
earlier build wrote opens as before, and the table stays in it, unused.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from infrahub_sync.product_store import local_product_projection
from tests.configuration.validation_packages import package

if TYPE_CHECKING:
    from pathlib import Path

_EARLIER_TABLE = (
    "CREATE TABLE worker_environments ("
    "worker_id TEXT PRIMARY KEY, credential_names TEXT NOT NULL, observed_at TEXT NOT NULL)"
)
_EARLIER_ROW = ("worker-of-an-earlier-build", '["NAME_OF_A_VARIABLE"]', "2026-11-01T00:00:00+00:00")


def _database(root: Path) -> Path:
    databases = sorted(root.rglob("*.sqlite3"))
    assert len(databases) == 1, databases
    return databases[0]


def test_a_new_database_has_no_worker_environments_table(tmp_path: Path) -> None:
    local_product_projection(tmp_path).create_configuration(package())

    with sqlite3.connect(_database(tmp_path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert "worker_environments" not in tables
    assert "configuration_versions" in tables


def test_a_database_of_an_earlier_build_opens_and_keeps_the_table_and_its_rows(tmp_path: Path) -> None:
    first = local_product_projection(tmp_path)
    registered = first.create_configuration(package())
    with sqlite3.connect(_database(tmp_path)) as connection:
        connection.execute(_EARLIER_TABLE)
        connection.execute("INSERT INTO worker_environments VALUES (?, ?, ?)", _EARLIER_ROW)

    reopened = local_product_projection(tmp_path)
    again = reopened.lookup_configuration_version(registered.config_id, registered.registry_version).value

    assert again == registered
    with sqlite3.connect(_database(tmp_path)) as connection:
        rows = connection.execute("SELECT worker_id, credential_names, observed_at FROM worker_environments").fetchall()
    assert rows == [_EARLIER_ROW]
