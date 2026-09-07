"""Everything a repeat start or a restart must leave equal, read from the stores.

Deliberately not the deployment's own account of itself: a restart that lost
durable state and an API that failed to notice look identical from the API. So
this counts what PostgreSQL holds and what the object store holds, and prints a
stable digest of both for the driver to compare across an operation.

The table set is discovered rather than named, so a schema change does not
quietly narrow what is being compared.
"""

from __future__ import annotations

import os

import boto3  # ty: ignore[unresolved-import] - TODO: optional service dependency
import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency
from psycopg import sql  # ty: ignore[unresolved-import] - TODO: optional service dependency

lines = []
with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection, connection.cursor() as cursor:
    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
    )
    for (table,) in cursor.fetchall():
        cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        count = cursor.fetchone()
        lines.append(f"table {table} {count[0] if count else 0}")

store = boto3.client("s3", endpoint_url=os.environ["INFRAHUB_SYNC_S3_ENDPOINT_URL"])
pages = store.get_paginator("list_objects_v2").paginate(Bucket=os.environ["INFRAHUB_SYNC_S3_BUCKET"])
keys = sorted(item["Key"] for page in pages for item in page.get("Contents", []))
lines.extend(f"object {key}" for key in keys)

print("\n".join(lines))
