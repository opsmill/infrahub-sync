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

    Captures the operator's schema_mapping shape (resource mapping path,
    per-field mapping/reference/static, filters, and transforms) AND the
    destination schema's kind names. Anything that affects how a row is
    extracted or transformed must contribute to the hash, otherwise a
    config edit could silently reuse a stale plan or cursor.

    Returns a 12-hex-char prefix of SHA-256.
    """
    payload = {
        "schema_mapping": [
            {
                "name": sm.name,
                "mapping": getattr(sm, "mapping", None),
                "identifiers": sm.identifiers,
                "fields": [
                    {
                        "name": f.name,
                        "mapping": getattr(f, "mapping", None),
                        "reference": getattr(f, "reference", None),
                        "static": getattr(f, "static", None),
                    }
                    for f in (sm.fields or [])
                ],
                "filters": [
                    {
                        "field": getattr(fltr, "field", None),
                        "operation": getattr(fltr, "operation", None),
                        "value": getattr(fltr, "value", None),
                    }
                    for fltr in (getattr(sm, "filters", None) or [])
                ],
                "transforms": [
                    {
                        "field": getattr(t, "field", None),
                        "expression": getattr(t, "expression", None),
                    }
                    for t in (getattr(sm, "transforms", None) or [])
                ],
            }
            for sm in config.schema_mapping
        ],
        "schema_kinds": sorted(schema.keys()),
    }
    serialized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:12]
