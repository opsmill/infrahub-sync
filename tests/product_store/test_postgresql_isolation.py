"""The integration parameters' blast radius: a test may only drop a schema it generated.

``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names a server, not a mandate over its contents. A
misconfigured developer or CI DSN must therefore cost at most one stray empty schema, so
these cases hold the isolation helper — and every test module under ``tests/`` — to naming
only generated identifiers when it destroys anything.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from typing_extensions import Self

from tests.product_store.postgresql_isolation import (
    SCHEMA_NAME_PREFIX,
    IsolatedSchema,
    generated_schema_name,
    schema_scoped_dsn,
)

_TESTS_ROOT = Path(__file__).resolve().parents[1]
# A schema drop is only bounded when its target is computed, so the allowed form is an
# f-string placeholder. Any literal target names a schema the tests did not create.
_SCHEMA_DROP = re.compile(r"DROP\s+SCHEMA\s+(?:IF\s+EXISTS\s+)?(?P<target>[^\s;]+)", re.IGNORECASE)
_FIXED_NAME = f"{SCHEMA_NAME_PREFIX}fixed"


def _expected_drop(name: str) -> str:
    """Build the drop this helper should issue, interpolated so the guard above accepts it."""
    return f'DROP SCHEMA IF EXISTS "{name}" CASCADE'


def _expected_create(name: str) -> str:
    """Build the create this helper should issue."""
    return f'CREATE SCHEMA "{name}"'


class _RecordingConnection:
    """A connection that records statement text instead of reaching a server."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> bool:
        return False

    def execute(self, statement: str, parameters: Any = None) -> None:  # noqa: ANN401, ARG002
        self.statements.append(statement)

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture(name="recorded")
def recorded_schema_fixture() -> tuple[IsolatedSchema, _RecordingConnection]:
    """Pair one isolated schema with the connection that records what it issues."""
    connection = _RecordingConnection()
    schema = IsolatedSchema(name=_FIXED_NAME, dsn="dsn", connect=lambda _dsn: connection)
    return schema, connection


def test_no_test_source_drops_a_schema_it_did_not_generate() -> None:
    """The repository-wide guard: no test may name a pre-existing schema in a drop."""
    unbounded: list[str] = []
    found = 0
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        for match in _SCHEMA_DROP.finditer(path.read_text(encoding="utf-8")):
            found += 1
            if "{" not in match.group("target"):
                unbounded.append(f"{path.relative_to(_TESTS_ROOT)}: {match.group(0)}")

    assert found, "the guard scanned no schema drop at all, so it proves nothing"
    assert not unbounded, f"these schema drops name an identifier the tests did not generate: {unbounded}"


def test_dropping_an_isolated_schema_names_only_that_schema(
    recorded: tuple[IsolatedSchema, _RecordingConnection],
) -> None:
    """Teardown destroys the generated schema and never reaches a sibling schema."""
    schema, connection = recorded

    schema.drop()

    assert connection.statements == [_expected_drop(_FIXED_NAME)]
    assert connection.commits == 1


def test_resetting_an_isolated_schema_names_only_that_schema(
    recorded: tuple[IsolatedSchema, _RecordingConnection],
) -> None:
    """Returning the schema to empty must not widen the drop's target either."""
    schema, connection = recorded

    schema.reset()

    assert connection.statements == [_expected_drop(_FIXED_NAME), _expected_create(_FIXED_NAME)]


def test_the_scoped_dsn_resolves_unqualified_names_to_the_generated_schema_alone() -> None:
    """Without ``public`` on the search path, no case can read or write a pre-existing table."""
    pytest.importorskip("psycopg")

    scoped = schema_scoped_dsn("host=127.0.0.1 port=55432 user=probe password=probe dbname=probe", "gen_schema")

    assert "options=-csearch_path=gen_schema" in scoped
    assert "search_path=public" not in scoped


def test_the_scoped_dsn_accepts_a_uri_endpoint() -> None:
    """A URI DSN is as ordinary as a keyword one, and must be pinned just the same."""
    pytest.importorskip("psycopg")

    scoped = schema_scoped_dsn("postgresql://probe:probe@127.0.0.1:55432/probe", "gen_schema")

    assert "options=-csearch_path=gen_schema" in scoped


def test_each_generated_schema_name_is_namespaced_and_unique() -> None:
    """Concurrent sessions against one server must not collide on a schema name."""
    names = {generated_schema_name() for _ in range(50)}

    assert len(names) == 50
    assert all(name.startswith(SCHEMA_NAME_PREFIX) for name in names)
    assert all(re.fullmatch(r"[a-z0-9_]+", name) for name in names)
