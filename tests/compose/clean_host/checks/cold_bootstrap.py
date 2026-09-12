"""The start after a reset carries none of what the reset removed.

The product schema is read from PostgreSQL rather than from the deployment: a
bootstrap that skipped its work and one that had nothing to do both report a
healthy deployment.
"""

from __future__ import annotations

import os

import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency
from kit import deployment, refuse

with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection, connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
    tables = cursor.fetchone()
    if not tables or tables[0] == 0:
        refuse("the deployment came back with no product schema at all")

with deployment() as client:
    # The infrastructure is converged and the registry is empty: that is the
    # whole state a cold bootstrap produces, and any configuration here survived
    # a reset that claims to leave nothing behind.
    configs = client.list_configs()
    if configs:
        refuse(f"a cold bootstrap came back holding {len(configs)} configurations rather than an empty registry")
