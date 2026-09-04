"""Bounded PostgreSQL isolation for the product store's opt-in integration parameters.

The store's PostgreSQL profile needs a real server, so these cases take a DSN from
``PRODUCT_STORE_TEST_POSTGRESQL_DSN``. A DSN cannot say which of a database's schemas are
disposable, so a test must never assume any of them are: no statement issued from here
touches a schema the tests did not create. Each caller gets a uniquely named generated
schema, plus a DSN whose sessions resolve unqualified names to that schema alone, and
only that schema is dropped afterwards.

``public`` is deliberately absent from the scoped ``search_path``, so a mistargeted DSN
costs one stray empty schema instead of the named database's contents.

The store's own PostgreSQL statements are already schema-agnostic — its introspection asks
``current_schema()`` rather than naming ``public`` — so this isolation needs no change to
production behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DSN_ENVIRONMENT_NAME = "PRODUCT_STORE_TEST_POSTGRESQL_DSN"
SCHEMA_NAME_PREFIX = "infrahub_sync_test_"


def _psycopg_connect(dsn: str) -> Any:  # noqa: ANN401 - the driver's own connection type.
    """Open one administrative connection, importing the optional driver on demand."""
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    return psycopg.connect(dsn)


@dataclass(frozen=True)
class IsolatedSchema:
    """One generated schema, and the DSN whose sessions resolve unqualified names to it.

    Every statement issued here names ``name`` explicitly, and ``name`` is generated in
    this process, so no method can reach a schema the tests did not create.
    """

    name: str
    dsn: str
    connect: Callable[[str], Any] = _psycopg_connect

    def create(self) -> None:
        """Create this schema on the server."""
        self._execute(f'CREATE SCHEMA "{self.name}"')

    def drop(self) -> None:
        """Drop this schema and everything the tests put in it, and nothing else."""
        self._execute(f'DROP SCHEMA IF EXISTS "{self.name}" CASCADE')

    def reset(self) -> None:
        """Return this schema to empty without naming any other schema."""
        self.drop()
        self.create()

    def _execute(self, *statements: str) -> None:
        with self.connect(self.dsn) as admin:
            for statement in statements:
                admin.execute(statement)
            admin.commit()


def generated_schema_name() -> str:
    """Return a schema identifier owned by this process alone."""
    return f"{SCHEMA_NAME_PREFIX}{uuid4().hex}"


def schema_scoped_dsn(dsn: str, schema: str) -> str:
    """Return ``dsn`` with its sessions' ``search_path`` pinned to ``schema`` alone.

    ``public`` is left out of the resulting search path, so an unqualified statement cannot
    read or write a table that was already in the named database. An ``options`` keyword
    already present in ``dsn`` is replaced.
    """
    # pylint: disable-next=import-outside-toplevel,import-error
    from psycopg.conninfo import (  # ty: ignore[unresolved-import] - TODO: optional service dependency
        make_conninfo,
    )

    return make_conninfo(dsn, options=f"-csearch_path={schema}")


def isolated_schema(dsn: str) -> IsolatedSchema:
    """Describe a fresh generated schema for ``dsn``, without contacting the server."""
    name = generated_schema_name()
    return IsolatedSchema(name=name, dsn=schema_scoped_dsn(dsn, name))


def dsn_or_skip(requirement: str) -> str:
    """Return a reachable DSN for ``requirement``, or skip before any server is contacted."""
    dsn = os.environ.get(DSN_ENVIRONMENT_NAME)
    if not dsn:
        pytest.skip(f"{requirement} requires {DSN_ENVIRONMENT_NAME}")
    psycopg = pytest.importorskip("psycopg")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as probe:
            probe.execute("SELECT 1")
    except psycopg.Error:
        pytest.skip(f"{DSN_ENVIRONMENT_NAME} is not reachable")
    return dsn


def isolated_schema_fixture(requirement: str) -> Iterator[IsolatedSchema]:
    """Session-fixture body: one generated schema, created now and dropped at teardown."""
    schema = isolated_schema(dsn_or_skip(requirement))
    schema.create()
    try:
        yield schema
    finally:
        schema.drop()
