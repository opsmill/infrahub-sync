"""The database health check against a real PostgreSQL server (spec 029, research R-2 and R-3).

Skipped unless ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names a server, as for the product store's
PostgreSQL cases. The failure cases change only the host or the port of that DSN.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from psycopg.conninfo import make_conninfo

from infrahub_sync.service.health import Check, ErrorCategory, classify_error, database_check
from tests.product_store.postgresql_isolation import DSN_ENVIRONMENT_NAME

_DSN = os.environ.get(DSN_ENVIRONMENT_NAME)

pytestmark = pytest.mark.skipif(not _DSN, reason=f"{DSN_ENVIRONMENT_NAME} is not set")

_LIMIT = 3


async def _run(check: Check) -> None:
    await check()


def _category(dsn: str) -> ErrorCategory:
    with pytest.raises(Exception) as failure:
        asyncio.run(_run(database_check(dsn, _LIMIT)))
    return classify_error(failure.value)


def test_the_database_check_passes_on_a_reachable_server() -> None:
    assert _DSN is not None
    asyncio.run(_run(database_check(_DSN, _LIMIT)))


def test_a_host_that_does_not_resolve_is_name_resolution_failed() -> None:
    assert _DSN is not None
    assert _category(make_conninfo(_DSN, host="no-such-host.invalid")) is ErrorCategory.NAME_RESOLUTION_FAILED


def test_a_port_where_nothing_listens_is_connection_refused() -> None:
    assert _DSN is not None
    assert _category(make_conninfo(_DSN, host="127.0.0.1", port=1)) is ErrorCategory.CONNECTION_REFUSED
