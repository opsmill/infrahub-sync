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

from tests.product_store import postgresql_isolation
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


def test_the_shared_fixture_body_creates_before_yielding_and_drops_at_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three wrapper modules share this body; it must create, yield, then drop in order."""

    class _FakeSchema:
        def __init__(self) -> None:
            """Start with no recorded calls."""
            self.calls: list[str] = []

        def create(self) -> None:
            """Record that the schema was created."""
            self.calls.append("create")

        def drop(self) -> None:
            """Record that the schema was dropped."""
            self.calls.append("drop")

    fake = _FakeSchema()
    monkeypatch.setattr(postgresql_isolation, "dsn_or_skip", lambda requirement: "dsn")  # noqa: ARG005
    monkeypatch.setattr(postgresql_isolation, "isolated_schema", lambda dsn: fake)  # noqa: ARG005

    generator = postgresql_isolation.isolated_schema_fixture("some requirement")
    yielded = next(generator)

    assert yielded is fake
    assert fake.calls == ["create"]

    with pytest.raises(StopIteration):
        next(generator)

    assert fake.calls == ["create", "drop"]


def test_each_generated_schema_name_is_namespaced_and_unique() -> None:
    """Concurrent sessions against one server must not collide on a schema name."""
    names = {generated_schema_name() for _ in range(50)}

    assert len(names) == 50
    assert all(name.startswith(SCHEMA_NAME_PREFIX) for name in names)
    assert all(re.fullmatch(r"[a-z0-9_]+", name) for name in names)


# The pre-run-discovery shape of the three tables the start-up migration extends.
_LEGACY_RUN_DISCOVERY_TABLES = (
    "CREATE TABLE product_runs (run_id TEXT PRIMARY KEY, operation TEXT NOT NULL, "
    "configuration_reference TEXT NOT NULL, actor TEXT, audit_links TEXT NOT NULL, started_at TEXT NOT NULL, "
    "finished_at TEXT, phase TEXT NOT NULL, outcome TEXT, summary TEXT NOT NULL, results TEXT NOT NULL, "
    "reconciliation_required BOOLEAN NOT NULL DEFAULT FALSE)",
    "CREATE TABLE configurations (config_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)",
    "CREATE TABLE configuration_versions (config_id TEXT NOT NULL, registry_version INTEGER NOT NULL, "
    "package_checksum TEXT NOT NULL, declared_content TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY (config_id, registry_version), FOREIGN KEY (config_id) REFERENCES configurations(config_id))",
    "INSERT INTO product_runs (run_id, operation, configuration_reference, audit_links, started_at, phase, "
    "summary, results) VALUES ('legacy-run-001', 'plan', 'sha256:legacy', '[]', "
    "'2026-01-01T00:00:00+00:00', 'planning', '{}', '{}')",
)
_RUN_DISCOVERY_COLUMNS = {
    "product_runs": {"config_id", "registry_version", "package_checksum", "destination_branch"},
    "configuration_versions": {"registered_by", "registration_reason"},
    "configurations": {"name"},
}
_RUN_DISCOVERY_INDEXES = {
    "product_runs_started": "(started_at, run_id)",
    "product_runs_config_started": "(config_id, started_at, run_id)",
    "product_runs_binding_started": "(config_id, registry_version, started_at, run_id)",
    "product_runs_binding_applied": "(config_id, registry_version, finished_at, run_id) WHERE (phase = 'applied'::text)",
    "configurations_name": "(name)",
    "configurations_created": "(created_at, config_id)",
}


@pytest.mark.integration
def test_postgresql_start_up_upgrades_a_database_without_the_run_discovery_schema_once() -> None:
    """A real server: legacy tables gain the columns and indexes, and a second start-up is a no-op."""
    schema = postgresql_isolation.isolated_schema(
        postgresql_isolation.dsn_or_skip("the run-discovery migration's PostgreSQL case")
    )
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    # pylint: disable-next=import-outside-toplevel
    from infrahub_sync.product_store.store import PostgreSQLRunStore

    # pylint: disable-next=import-outside-toplevel
    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the provider's own connection wrapper.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    def catalog() -> tuple[dict[str, set[str]], dict[str, str]]:
        with psycopg.connect(schema.dsn) as connection:
            columns: dict[str, set[str]] = {}
            for table, column in connection.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
            ):
                columns.setdefault(table, set()).add(column)
            indexes = dict(
                connection.execute(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema()"
                ).fetchall()
            )
        return columns, indexes

    schema.create()
    try:
        with psycopg.connect(schema.dsn) as connection:
            for statement in _LEGACY_RUN_DISCOVERY_TABLES:
                connection.execute(statement)
            connection.commit()

        store = PostgreSQLRunStore(connect)
        upgraded = catalog()
        PostgreSQLRunStore(connect)

        columns, indexes = upgraded
        for table, expected in _RUN_DISCOVERY_COLUMNS.items():
            assert expected <= columns[table], table
        for index, definition in _RUN_DISCOVERY_INDEXES.items():
            assert indexes[index].endswith(definition), indexes[index]
        assert catalog() == upgraded
        loaded = store.lookup("legacy-run-001").value
        assert loaded is not None
        assert loaded.destination_branch is None
    finally:
        schema.drop()
