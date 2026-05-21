"""Cache subsystem: persists every sync run's source/destination snapshots,
the computed plan, and per-row errors as Parquet files under
`cache/<sync_name>/<run_id>/`."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from infrahub_sync import SyncConfig


def compute_schema_subhash(config: SyncConfig, schema: dict[str, Any]) -> str:
    """Hash inputs that, if changed, must invalidate the cache.

    Captures the operator's schema_mapping shape AND the destination
    schema's kind names. Returns a 12-hex-char prefix of SHA-256.
    """
    payload = {
        "schema_mapping": [
            {
                "name": sm.name,
                "identifiers": sm.identifiers,
                "fields": [f.name for f in (sm.fields or [])],
            }
            for sm in config.schema_mapping
        ],
        "schema_kinds": sorted(schema.keys()),
    }
    serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:12]
