"""The operations digest: one value that is equal for two plans holding the same operations.

`operations_digest` is the SHA-256, in hexadecimal, of the sorted canonical JSON lines of
`{action, kind, identity, payload, relationships}` of each operation (spec 019 research R16).

It leaves out every value that differs between two plans of the same data:

- `destination_id`, the Infrahub ID of the node an update is keyed by, which differs on each
  rebuilt Infrahub.
- `operation_id` and `tier`, which derive from the fields the digest already covers or from the
  configuration.
- the order of the operations, because the lines are sorted before they are hashed.

The plan checksum (`infrahub_sync/plan/checksum.py`) covers the destination binding, the
lineage, the source snapshot digests and `destination_id`, so it cannot compare two plans of
the same data. The benchmark computes this digest from the operations of the saved-plan route,
and the second-plan tests compute it from the operations of the saved plan.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

# The fields of an operation the digest covers.
DIGEST_FIELDS: tuple[str, ...] = ("action", "kind", "identity", "payload", "relationships")


def _digest_fields(operation: BaseModel) -> dict[str, Any]:
    """The covered fields of one operation, as JSON values."""
    values = operation.model_dump(mode="json")
    return {name: values.get(name) for name in DIGEST_FIELDS}


def operations_digest(operations: Iterable[BaseModel]) -> str:
    """Return the SHA-256 hex digest of the sorted canonical lines of `operations`.

    An operation is a `PlannedOperation` or a `PlanOperationResource` of the saved-plan route.
    Each line is the canonical JSON of the fields in
    `DIGEST_FIELDS`, followed by a newline.
    """
    lines = sorted(canonical_json_bytes(_digest_fields(operation)) for operation in operations)
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line)
        digest.update(b"\n")
    return digest.hexdigest()


__all__ = ["DIGEST_FIELDS", "operations_digest"]
