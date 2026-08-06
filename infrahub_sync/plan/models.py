"""The plan artifact's record types (FR-002, FR-006, FR-017, FR-026, FR-027, FR-028).

Pydantic v2 models in the existing `SyncConfig` style. These are the records nine later
outcomes consume, so the field sets here are the contract: the artifact format fixes their
on-disk encoding and the data model fixes the rules they enforce, both under
`dev/specs/archive/001-plan-artifact-saved-apply/`.

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
from hashlib import sha256
from pathlib import PurePath
from typing import Any, Literal, TypeAlias, get_args
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from infrahub_sync.plan.canonical import canonical_value
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
ACTIONS: tuple[PlanAction, ...] = get_args(PlanAction)

# Removed from the manifest before the checksum is computed, not blanked (AD035).
CHECKSUM_EXCLUDED_FIELDS: tuple[str, ...] = ("plan_checksum", "run_id", "created_at")

# SC-006's mask. Deliberately **not** the same set: `plan_checksum` needs no mask because
# it is a function of the checksummed bytes alone.
SC006_MASKED_FIELDS: tuple[str, ...] = ("run_id", "created_at")

# A canonical destination identity: attribute name to value, key-sorted, where a value
# that is itself a reference is a nested `{"peer_kind": …, "identity": …}` pair (AD043).
DestinationIdentity = dict[str, Any]


class RelationshipReference(BaseModel):
    """A peer named by kind and identity, never by a destination-assigned id.

    **A plan cannot clear a cardinality-one peer, by design in v1.**
    There is no encoding for an emptied cardinality-one relationship: `cardinality: "one"`
    requires exactly one peer, and the reference being *absent* from the operation means the
    field carries no value of that kind at all rather than that it should be emptied — the
    same asymmetry `_validate_cardinality` records for the many case. Derivation cannot
    produce one either, because `derive._resolve_references` treats a `None`-valued reference
    field as absent and skips it.

    This is **parity with live `sync`**, which skips a `None` here for the same reason, not a
    regression the plan path introduces: a relationship a plan does not mention is a
    relationship the apply leaves alone. So an operator who needs a cardinality-one peer
    cleared clears it at the destination. Extending the format to encode it is a follow-up
    issue; `format_version` is the mechanism that would carry the extension.
    """

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
        """Enforce the record-level rules: identifier, one source per field, payload, identity-in-payload."""
        derived = operation_id(self.action, self.kind, self.identity)
        if self.operation_id != derived:
            msg = (
                f"Operation identifier {self.operation_id!r} does not match its own triple "
                f"(action={self.action!r}, kind={self.kind!r}, identity={self.identity!r}), which "
                f"derives {derived!r}: the record is corrupt."
            )
            raise ValueError(msg)
        # Every destination field has exactly one write source. Two references for
        # one field would make the replace-set write depend on reference order.
        reference_fields = [reference.field for reference in self.relationships or ()]
        duplicated = sorted({name for name in reference_fields if reference_fields.count(name) > 1})
        if duplicated:
            msg = (
                f"Operation {self.operation_id!r} on kind {self.kind!r} carries more than one "
                f"relationship reference for field(s) {duplicated}: which peer set the apply "
                "writes would depend on reference order, so the record is ambiguous."
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
        # The other half of that rule: a field carried by the payload *and* a relationship reference
        # has two competing write sources — the upsert value and the flush value.
        referenced_fields = set(reference_fields)
        doubly_sourced = sorted(referenced_fields & set(self.payload))
        if doubly_sourced:
            msg = (
                f"Operation {self.operation_id!r} on kind {self.kind!r} carries field(s) "
                f"{doubly_sourced} in both its payload and a relationship reference: the upsert "
                "value and the relationship write would be two competing sources for one "
                "destination field, so the record is ambiguous."
            )
            raise ValueError(msg)
        unkeyed = sorted(key for key in self.identity if key not in self.payload and key not in referenced_fields)
        if unkeyed:
            msg = (
                f"Operation {self.operation_id!r} on kind {self.kind!r} carries identity attributes "
                f"{unkeyed} in neither its payload nor a relationship reference. The destination's "
                "convergent write is keyed on those components, so the write would be unkeyed and "
                "every re-apply would duplicate (AD042)."
            )
            raise ValueError(msg)
        # Presence is not enough — the value the write source carries must equal
        # the identity component review rendered and the operation id hashed. Otherwise
        # apply builds the mutation from the payload/reference (writing the *other* object)
        # and memoizes the result under the disagreeing reviewed identity. The rule above
        # guarantees exactly one source per field, so the comparison is unambiguous.
        references_by_field = {reference.field: reference for reference in self.relationships or ()}
        for name, recorded in self.identity.items():
            reference = references_by_field.get(name)
            if name in self.payload:
                written = canonical_value(self.payload[name], kind=self.kind, field=name)
                source = "canonical payload value"
            elif reference is not None:
                pairs: list[dict[str, Any]] = [
                    {"peer_kind": reference.peer_kind, "identity": canonical_value(peer, kind=self.kind, field=name)}
                    for peer in reference.peers
                ]
                written = pairs if reference.cardinality == "many" else pairs[0]
                source = "relationship reference"
            else:  # pragma: no cover — the AD042 guard above already refused this shape
                continue
            if written != recorded:
                msg = (
                    f"Operation {self.operation_id!r} on kind {self.kind!r} records identity component "
                    f"{name!r} as {recorded!r} while its {source} is {written!r}: the identity reviewed "
                    "and hashed disagrees with the value that would be written, so the record is corrupt."
                )
                raise ValueError(msg)
        return self


def require_run_relative_path(value: str) -> str:
    """Refuse a snapshot path that could escape the run directory.

    The manifest is operator-editable input joined onto the run directory, so a `..`
    segment, an absolute path, or a bare `.` would send the verifier to digest — and vouch
    for — a file outside the run directory the plan claims to be bound to. Mirrors
    `_require_safe_segment` (`infrahub_sync/cache/paths.py`), relaxed to allow multiple
    segments because the writer records paths like `A/BuiltinTag.parquet`. Shared by
    `SourceSnapshotRecord` below and by the verifier, which reads the raw manifest mapping
    without constructing the model.

    Raises:
        ValueError: the path is absolute, empty, or carries a `.` or `..` segment.
    """
    pure = PurePath(value)
    # The raw `/`-split alongside `PurePath.parts`, because `PurePath` silently *normalizes*
    # `.` and empty segments away — a path the writer would never emit must be refused, not
    # laundered — while `parts` is what understands platform-native absolutes.
    raw_segments = value.split("/")
    if (
        pure.is_absolute()
        or not value
        or any(segment in {"", ".", ".."} for segment in raw_segments)
        or any(part in {".", ".."} for part in pure.parts)
    ):
        msg = f"a snapshot path must be run-relative with no '.' or '..' segments (got {value!r})"
        raise ValueError(msg)
    return value


class SourceSnapshotRecord(BaseModel):
    """One source-snapshot file the plan was computed against (FR-004, AD037)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    digest: str
    row_count: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _require_run_relative(cls, value: str) -> str:
        """The recorded path never escapes the run directory it is joined onto."""
        return require_run_relative_path(value)


def _normalized_destination_url(url: str) -> str:
    """Normalize a destination endpoint URL so equivalent addresses compare equal.

    Scheme and host are lowercased and trailing path slashes dropped, so
    `HTTP://Infrahub:8000/` and `http://infrahub:8000` name the same destination and do
    not false-refuse an apply. Userinfo is dropped outright: the record must never carry
    a credential, however the endpoint was spelled. Query text is represented
    by a digest so query-bearing endpoints remain distinguishable without
    persisting or displaying their raw values. Fragments never take part in an
    HTTP request and are discarded.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    # urlsplit().hostname removes IPv6 brackets, while urlunsplit() requires
    # them to preserve a valid authority component.
    formatted_host = f"[{host}]" if ":" in host else host
    netloc = formatted_host if parts.port is None else f"{formatted_host}:{parts.port}"
    safe_query = f"query-sha256={sha256(parts.query.encode()).hexdigest()}" if parts.query else ""
    return urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/"), safe_query, ""))


class DestinationBindingRecord(BaseModel):
    """The effective destination identity a plan was computed against.

    The **resolved** endpoint URL and branch — environment variables already applied over
    settings, exactly what the destination adapter connects with — and never the token.
    The URL is normalized on construction, so a record built from a manifest and one built
    from a live adapter compare equal whenever they name the same destination.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    branch: str | None = None

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        return _normalized_destination_url(value)


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
    # Additive: manifests written before this field exists carry no
    # binding, and the apply-time destination comparison skips them.
    destination_binding: DestinationBindingRecord | None = None

    @model_serializer(mode="wrap")
    def _serialize_without_absent_binding(self, handler: Any) -> dict[str, Any]:
        """Serialize an absent binding as **absent**, never as `null`.

        The writer omits the field when there is no binding — the exact byte shape older manifests carry —
        so a manifest read back and re-serialized must not grow a `null` the file never
        carried: the model's rendering stays byte-faithful to the artifact.
        """
        data: dict[str, Any] = handler(self)
        if data.get("destination_binding") is None:
            data.pop("destination_binding", None)
        return data


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
    `as_summary_keys()` into the run file's `summary` before saving it (AD069).

    A destination rejection mid-apply carries the **partial** record on the raised
    `OperationApplyFailedError`, so the CLI can merge what was written before recording
    `failed`, and FR-025's last-applied pointer survives a partial apply. The failing
    operation is named on the record too: applying one operation is not one destination write,
    so a failure between the base upsert and the relationship flush leaves the destination
    changed by an operation in neither the applied nor the skipped-delete set. Re-applying
    converges it (AD033).
    """

    applied_operations: tuple[str, ...] = ()
    skipped_delete_operations: tuple[str, ...] = ()
    failed_operation: str | None = None

    @property
    def skipped_delete_count(self) -> int:
        """How many recorded deletes this apply did not execute (FR-016, AD055).

        Derived, not stored. The **serialized** shape is the contract and this count is one
        of its keys (AD062), but its value is the length of the list above — so storing it
        would only introduce a state that can contradict itself on the record that is the
        single account of what an apply did.
        """
        return len(self.skipped_delete_operations)

    @property
    def may_have_partially_written(self) -> bool:
        """Whether `failed_operation` may have left part of its change at the destination.

        Deliberately "may". Applying one operation issues the base upsert first and the
        cardinality-many relationship flush second, and the engine learns only that the call
        raised — never how far it got. So the marker is true for any failed operation and
        false otherwise, which is the reading that never understates what reached the
        destination.
        """
        return self.failed_operation is not None

    def as_summary_keys(self) -> dict[str, Any]:
        """Render the record as the run-summary keys, ready to merge (AD062).

        Two JSON arrays of identifiers, one integer, the failing identifier or `null`, and
        the partial-write marker, under the key names below. This method is the only place
        those names are written. Every key is always present: "nothing was applied" and
        "nothing failed" must be readable from the run rather than inferred from an absent
        key.
        """
        return {
            "applied_operations": list(self.applied_operations),
            "skipped_delete_operations": list(self.skipped_delete_operations),
            "skipped_delete_count": self.skipped_delete_count,
            "failed_operation": self.failed_operation,
            "may_have_partially_written": self.may_have_partially_written,
        }


# The pre-apply check vocabulary, typed once so every construction site — the verifier's
# failure builder, `GATED_CHECKS`, and any new check — is checked by `ty` rather than
# failing at runtime on a misspelled name. The serialized values are
# unchanged: each member is the exact string the check has always carried.
VerificationCheck: TypeAlias = Literal[
    "format_version",
    "run_binding",
    "plan_checksum",
    "source_snapshot",
    "config_version",
    "torn_operations",
    "write_surface",
    "destination_binding",
]


class VerificationFailure(BaseModel):
    """One failed pre-apply check. The apply refuses when the list is non-empty (FR-009)."""

    model_config = ConfigDict(extra="forbid")

    check: VerificationCheck
    run_id: str
    expected: str | None = None
    found: str | None = None
    next_action: str = Field(min_length=1)
