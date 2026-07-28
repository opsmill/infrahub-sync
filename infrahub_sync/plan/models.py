"""The plan artifact's record types (FR-002, FR-006, FR-017, FR-026, FR-027, FR-028).

Pydantic v2 models in the existing `SyncConfig` style. These are the records nine later
outcomes consume, so the field sets here are the contract: the artifact format fixes their
on-disk encoding and the data model fixes the rules they enforce, both under
`dev/specs/001-plan-artifact-saved-apply/`.

Two asymmetries are deliberate. `PlanManifest` tolerates unknown fields — FR-027's
forward-compatibility carve-out is written about the manifest, where a later outcome adds
a schema-fingerprint field — while `PlannedOperation` is a **closed** field set, so an
operation record this version cannot fully interpret is a torn record rather than a
silently truncated one. And no field at either level groups operations into write units
(FR-026).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from infrahub_sync.plan.config_version import CONFIG_VERSION_PATTERN
from infrahub_sync.plan.errors import UnsupportedOperationActionError
from infrahub_sync.plan.identity import OPERATION_ID_PATTERN, canonical_identity, operation_id

# `1` is reserved for the pre-existing row format the reader refuses (FR-019); no manifest
# ever carries it.
PLAN_FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = frozenset({2})

PlanAction = Literal["create", "update", "delete"]

# FR-002's closed vocabulary (AD009). An operation record whose `action` falls outside it
# is the genuinely unsupported operation FR-017 fails the run for, refused while reading
# and therefore before any destination write (AD055).
ACTIONS: tuple[PlanAction, ...] = ("create", "update", "delete")

# Removed from the manifest before the checksum is computed, not blanked (AD035).
CHECKSUM_EXCLUDED_FIELDS: tuple[str, ...] = ("plan_checksum", "run_id", "created_at")

# SC-006's mask. Deliberately **not** the same set: `plan_checksum` needs no mask because
# it is a function of the checksummed bytes alone.
SC006_MASKED_FIELDS: tuple[str, ...] = ("run_id", "created_at")

# A canonical destination identity: attribute name to value, key-sorted, where a value
# that is itself a reference is a nested `{"peer_kind": …, "identity": …}` pair (AD043).
DestinationIdentity = dict[str, Any]


class RelationshipReference(BaseModel):
    """A peer named by kind and identity, never by a destination-assigned id."""

    model_config = ConfigDict(extra="forbid")

    field: str
    peer_kind: str
    cardinality: Literal["one", "many"]
    peers: list[DestinationIdentity]

    @model_validator(mode="after")
    def _validate_cardinality(self) -> RelationshipReference:
        """A `one` reference names exactly one peer; a `many` reference may name none.

        An **empty** `peers` under `cardinality == "many"` means the peer set is
        deliberately empty and the replace-set write acts on it; the reference being
        **absent** from the operation means it carries no value of that kind at all. The
        two are never interchangeable (FR-028.2).
        """
        if self.cardinality == "one" and len(self.peers) != 1:
            msg = (
                f"Relationship reference {self.field!r} declares cardinality 'one' and must name "
                f"exactly one peer, got {len(self.peers)}."
            )
            raise ValueError(msg)
        return self


class PlannedOperation(BaseModel):
    """One proposed change to one destination object."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)
    action: PlanAction
    kind: str
    identity: DestinationIdentity
    tier: int = Field(ge=0)
    payload: dict[str, Any] | None = None
    relationships: list[RelationshipReference] | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_unrecognized_action(cls, data: Any) -> Any:
        """Refuse an action outside `ACTIONS` with the named error, and canonicalise identity.

        A bare `Literal` mismatch would raise pydantic's `ValidationError`, not the class
        the taxonomy names, so the check runs here — before the model exists — and reads the
        operation identifier out of the **raw input** mapping, because that identifier is
        what the message has to name. Pydantic v2 propagates a non-`ValueError` raised
        inside a validator unchanged, so `UnsupportedOperationActionError` reaches the
        caller intact; `action`'s `Literal` annotation stays for `ty` (AD055, AD059).
        """
        if not isinstance(data, Mapping):
            return data
        values = dict(data)
        if "action" in values and values["action"] not in ACTIONS:
            recorded_id = values.get("operation_id", "<no operation_id recorded>")
            msg = (
                f"Operation {recorded_id!r} declares action {values['action']!r}, which this version "
                f"of infrahub-sync does not recognize. Recognized actions: {', '.join(ACTIONS)}."
            )
            raise UnsupportedOperationActionError(msg)
        kind = values.get("kind")
        identity = values.get("identity")
        if isinstance(identity, Mapping):
            values["identity"] = canonical_identity(identity, kind=kind if isinstance(kind, str) else None)
        return values

    @model_validator(mode="after")
    def _validate_record(self) -> PlannedOperation:
        """Enforce the three record-level rules: identifier, payload, identity-in-payload."""
        derived = operation_id(self.action, self.kind, self.identity)
        if self.operation_id != derived:
            msg = (
                f"Operation identifier {self.operation_id!r} does not match its own triple "
                f"(action={self.action!r}, kind={self.kind!r}, identity={self.identity!r}), which "
                f"derives {derived!r}: the record is corrupt."
            )
            raise ValueError(msg)
        if self.action == "delete":
            if self.payload is not None:
                msg = f"Operation {self.operation_id!r} is a delete and must carry no payload."
                raise ValueError(msg)
            return self
        if self.payload is None:
            msg = f"Operation {self.operation_id!r} is a {self.action} and must carry a payload."
            raise ValueError(msg)
        referenced_fields = {reference.field for reference in self.relationships or ()}
        unkeyed = sorted(key for key in self.identity if key not in self.payload and key not in referenced_fields)
        if unkeyed:
            msg = (
                f"Operation {self.operation_id!r} on kind {self.kind!r} carries identity attributes "
                f"{unkeyed} in neither its payload nor a relationship reference. The destination's "
                "convergent write is keyed on those components, so the write would be unkeyed and "
                "every re-apply would duplicate (AD042)."
            )
            raise ValueError(msg)
        return self


class SourceSnapshotRecord(BaseModel):
    """One source-snapshot file the plan was computed against (FR-004, AD037)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    digest: str
    row_count: int = Field(ge=0)


class PlanManifest(BaseModel):
    """The artifact's header, and the format contract (FR-027).

    Unknown fields are tolerated, preserved verbatim, and included in the checksummed
    bytes, because a later outcome adds a schema-fingerprint field here (AD028).
    """

    model_config = ConfigDict(extra="allow")

    format_version: int
    run_id: str
    created_at: str
    config_version: str = Field(pattern=CONFIG_VERSION_PATTERN)
    source_snapshot: list[SourceSnapshotRecord]
    operations_count: int = Field(ge=0)
    delete_operations_computed: bool
    plan_checksum: str


class PlanSummary(BaseModel):
    """The counts a review renders, plus the two disclosure fields (FR-006, AD056).

    `delete_operations_computed` and `deletes_not_executed` are **required** and derived on
    read — from the manifest and from the operation set — so the artifact format and
    `plan_checksum` are untouched. Without the first, a plan whose whole delete class was
    never computed is indistinguishable from one that has no deletes.
    """

    model_config = ConfigDict(extra="forbid")

    by_action: dict[str, int]
    by_kind: dict[str, int]
    total: int = Field(ge=0)
    delete_operations_computed: bool
    deletes_not_executed: int = Field(ge=0)


@dataclass(frozen=True)
class ApplyRecord:
    """What one apply did, handed from the engine to its caller (FR-017, FR-020, AD062, AD069).

    Not an artifact record — nothing here is written to `plan/`. It is the value
    `Potenda.apply_plan` **returns**, and the CLI is the single writer that merges
    `as_summary_keys()` into the run file's `summary` before saving it (AD069). It carries a
    name and a type rather than three loose keys because it crosses a layer boundary: a bare
    mapping infers as `dict[str, Any]`, which loses every guarantee at the merge site and
    turns a later key relocation into a `KeyError` downstream instead of a type error here.

    A destination rejection mid-apply carries the **partial** record on the raised
    `OperationApplyFailedError`, so the CLI can merge what was written before recording
    `failed` — which is what lets FR-025's last-applied pointer survive a partial apply.
    """

    applied_operations: tuple[str, ...] = ()
    skipped_delete_operations: tuple[str, ...] = ()
    skipped_delete_count: int = 0

    def as_summary_keys(self) -> dict[str, Any]:
        """Render the record as the three run-summary keys, ready to merge (AD062).

        The **serialized** shape is the contract and is unchanged by this type existing:
        two JSON arrays of identifiers and one integer, under the key names below. This
        method is the only place those names are written.
        """
        return {
            "applied_operations": list(self.applied_operations),
            "skipped_delete_operations": list(self.skipped_delete_operations),
            "skipped_delete_count": self.skipped_delete_count,
        }


class VerificationFailure(BaseModel):
    """One failed pre-apply check. The apply refuses when the list is non-empty (FR-009)."""

    model_config = ConfigDict(extra="forbid")

    check: Literal[
        "format_version",
        "run_binding",
        "plan_checksum",
        "source_snapshot",
        "config_version",
        "torn_operations",
        "write_surface",
    ]
    run_id: str
    expected: str | None = None
    found: str | None = None
    next_action: str = Field(min_length=1)
