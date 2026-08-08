"""Canonical plan fingerprint — one shared digest for comparing two plan runs.

Both sides of the CLI-`diff` vs remote-`plan` comparison call
:func:`compute_plan_fingerprint`; nothing may reimplement the algorithm.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from infrahub_sync.cache.parquet_io import read_plan

if TYPE_CHECKING:
    from pathlib import Path

# The only plan columns that participate in the digest. Timestamps, run
# identifiers, and filesystem paths are excluded by construction: they are not
# in this tuple, so reset-fixture runs of the same plan compare equal.
PLAN_FINGERPRINT_FIELDS = ("action", "resource", "source_id", "attribute", "new_value")


def _sort_value(value: Any) -> str:
    """Normalize a sort-key field so the tuple sort stays total on null-bearing rows.

    `PLAN_SCHEMA` declares `attribute` and `new_value` nullable, so a row may
    carry `None` where another carries a string; comparing the two directly
    raises `TypeError`. Only the SORT KEY is normalized — the serialized row is
    untouched, so `None` still serializes as JSON `null`.
    """
    return value if value is not None else ""


def compute_plan_fingerprint(run_dir: Path) -> str:
    """Return the SHA-256 hex digest of the canonicalized plan in `run_dir`.

    Algorithm (binding):

    1. Read `<run_dir>/plan.parquet` and project exactly
       :data:`PLAN_FINGERPRINT_FIELDS` from every row.
    2. Serialize each projected row as compact sorted-key JSON.
    3. Sort by `(resource, source_id, action, attribute)` with the row's full
       serialized form as the final tie-breaker; every sort-key field
       normalizes `None` to `""` (see :func:`_sort_value`).
    4. Join with `"\\n"`, encode UTF-8, and return the SHA-256 hexdigest.
    """
    table = read_plan(run_dir=run_dir)
    entries: list[tuple[str, str, str, str, str]] = []
    for row in table.to_pylist():
        projected = {name: row.get(name) for name in PLAN_FINGERPRINT_FIELDS}
        serialized = json.dumps(projected, sort_keys=True, separators=(",", ":"))
        entries.append(
            (
                _sort_value(projected["resource"]),
                _sort_value(projected["source_id"]),
                _sort_value(projected["action"]),
                _sort_value(projected["attribute"]),
                serialized,
            )
        )
    entries.sort()
    payload = "\n".join(entry[-1] for entry in entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
