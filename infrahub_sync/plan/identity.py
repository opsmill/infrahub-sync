"""Canonical destination identity and operation-identifier derivation (FR-003, FR-028.3).

The canonical identity is one key-sorted mapping of identity attribute name to value —
the single representation FR-028.3 fixes, used by the identifier hash, by
relationship-reference ordering, and by review output, so the identity an operator reads
is the identity the identifier was derived from.

The identifier hashes a JSON **array** in the order `(action, kind, identity)`, fixed by
PD-001. The payload is deliberately excluded (AD002): the identifier names the logical
operation and stays stable across re-plans, and payload exactness is guaranteed by
`plan_checksum` instead.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from infrahub_sync.plan.canonical import canonical_json_bytes, canonical_value

# The shape every `operation_id` matches. Shared so the record types and the tests
# assert one pattern rather than two copies of it.
OPERATION_ID_PATTERN = r"^op_[0-9a-f]{16}$"

# Length of the hex digest prefix the identifier carries (AD002, PD-001).
OPERATION_ID_DIGEST_LENGTH = 16


def canonical_identity(mapping: Mapping[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    """Return `mapping` as a canonical destination identity.

    Keys are sorted and values normalized by `canonical_value`, recursively — so a nested
    peer reference `{"peer_kind": …, "identity": …}` is canonicalised at every level
    (AD043) and the identity's key order never affects the derived identifier.

    `kind` is optional and only makes an encoding failure name the owning kind.
    """
    if not isinstance(mapping, Mapping):
        msg = f"A destination identity must be a mapping, got {type(mapping).__name__!r}."
        raise TypeError(msg)
    return canonical_value(dict(mapping), kind=kind)


def operation_id(action: str, kind: str, identity: Mapping[str, Any]) -> str:
    """Derive an operation identifier from the triple `(action, kind, identity)`.

    `"op_" + sha256(canonical_json_bytes([action, kind, canonical_identity(identity)]))`
    truncated to sixteen hex characters (PD-001). The payload is not an input (AD002).
    """
    hash_input = canonical_json_bytes([action, kind, canonical_identity(identity, kind=kind)])
    digest = hashlib.sha256(hash_input).hexdigest()
    return f"op_{digest[:OPERATION_ID_DIGEST_LENGTH]}"
