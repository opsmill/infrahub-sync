"""SQL for the `worker_environment` record, shared by the SQLite and PostgreSQL stores.

Each worker keeps one row, which its latest publication replaces. The row holds the names of
the credential variables the worker holds, as a JSON array, and never a value.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store.models import WorkerEnvironment

if TYPE_CHECKING:
    from collections.abc import Sequence

# `CREATE TABLE IF NOT EXISTS` adds the table to a store created before it existed, on both
# backends, so no other migration is needed.
WORKER_ENVIRONMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_environment (
    worker_id TEXT PRIMARY KEY, credential_names TEXT NOT NULL, observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS worker_environment_observed
    ON worker_environment (observed_at);
"""

# A publication never replaces a newer one from the same worker.
UPSERT_WORKER_ENVIRONMENT = (
    "INSERT INTO worker_environment (worker_id, credential_names, observed_at) VALUES (?, ?, ?) "
    "ON CONFLICT (worker_id) DO UPDATE SET credential_names = excluded.credential_names, "
    "observed_at = excluded.observed_at WHERE worker_environment.observed_at <= excluded.observed_at"
)

SELECT_LATEST_WORKER_ENVIRONMENT = (
    "SELECT worker_id, credential_names, observed_at FROM worker_environment "
    "WHERE observed_at >= ? ORDER BY observed_at DESC, worker_id LIMIT 1"
)


def observed_at_text(value: datetime) -> str:
    """Return a fixed-width UTC timestamp, so that text order is time order on both backends."""
    if value.utcoffset() is None:
        msg = "worker environment timestamps must include a timezone"
        raise ValueError(msg)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def worker_environment_values(record: WorkerEnvironment) -> tuple[str, str, str]:
    """Return the row values of one record, in the column order of the upsert."""
    names = canonical_json_bytes(list(record.credential_names)).decode()
    return record.worker_id, names, observed_at_text(record.observed_at)


def worker_environment_from_row(row: Sequence[Any]) -> WorkerEnvironment:
    """Build a record from one row of `SELECT_LATEST_WORKER_ENVIRONMENT`."""
    return WorkerEnvironment(
        worker_id=str(row[0]),
        credential_names=tuple(json.loads(str(row[1]))),
        observed_at=datetime.fromisoformat(str(row[2])),
    )
