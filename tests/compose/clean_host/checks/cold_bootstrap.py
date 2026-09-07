"""The start after a reset carries none of what the reset removed.

Read from the stores rather than from the deployment: a bootstrap that skipped
its work and one that had nothing to do both report a healthy deployment.
"""

from __future__ import annotations

import os

import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency
from kit import BUNDLED_CONFIGURATION, deployment, refuse

with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection, connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
    tables = cursor.fetchone()
    if not tables or tables[0] == 0:
        refuse("the deployment came back with no product schema at all")

with deployment() as client:
    # Exactly one, and exactly one version of it: this is the state a bootstrap
    # produces from nothing, and anything more survived a reset that claims to
    # leave nothing behind.
    configs = client.list_configs()
    if len(configs) != 1:
        refuse(f"a cold bootstrap left {len(configs)} configurations rather than the one it registers")
    versions = client.list_config_versions(configs[0].config_id)
    if len(versions) != 1:
        refuse(f"a cold bootstrap left {len(versions)} versions of {BUNDLED_CONFIGURATION} rather than one")
