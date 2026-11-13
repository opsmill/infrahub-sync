# pylint: disable=too-many-lines  # the plan artifact's records, the manifest and the plan report
"""The plan artifact's record types (FR-002, FR-006, FR-017, FR-026, FR-027, FR-028).

Pydantic v2 models in the existing `SyncConfig` style. These are the records nine later
outcomes consume, so the field sets here are the contract: the artifact format fixes their
on-disk encoding and the data model fixes the rules they enforce, both under
`dev/specs/archive/001-plan-artifact-saved-apply/`.

Two asymmetries are deliberate. `PlanManifest` tolerates unknown fields — FR-027's
forward-compatibility carve-out is written about the manifest — while `PlannedOperation` is
a **closed** field set, so an operation record this version cannot fully interpret is a
torn record rather than a silently truncated one. And no field at either level groups
operations into write units (FR-026).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from hashlib import sha256
from pathlib import PurePath
from typing import Any, Literal, TypeAlias, get_args
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_serializer, model_validator

from infrahub_sync.plan.canonical import canonical_json_bytes, canonical_value
from infrahub_sync.plan.config_version import CONFIG_VERSION_PATTERN
from infrahub_sync.plan.errors import UnsupportedOperationActionError
from infrahub_sync.plan.identity import OPERATION_ID_PATTERN, canonical_identity, operation_id

# `1` is reserved for the pre-existing row format the reader refuses (FR-019); no manifest
# ever carries it.
PLAN_FORMAT_VERSION = 4
# Read and review accept all three. A format-2 plan's updates carry no recorded destination
# id, so they cannot be keyed — reviewing such a plan is still useful, applying it is not safe
# (see `PlanFormatApplyUnsupportedError`). Format 3 has no plan report (spec 019 research R9).
SUPPORTED_FORMAT_VERSIONS = frozenset({2, 3, 4})

# The format that introduced `plan/report.jsonl` and the manifest's `report` record (spec 019,
# contracts/plan-artifact-format-4.md). A manifest of this format or later must carry it.
REPORT_FORMAT_VERSION = 4
REPORT_FILE_NAME = "report.jsonl"

# The format that introduced `PlannedOperation.destination_id`. `PlannedOperation` is
# `extra="forbid"`, so the field's presence is itself a format discriminator: it must be
# absent under 2 and governed by the action under 3.
DESTINATION_ID_FORMAT_VERSION = 3

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

    A relationship a plan does not mention is a
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
    # The destination object's Infrahub `id`, recorded at plan time on updates alone and set
    # on the node before the convergent upsert. Optional on the model because a create and a
    # delete must not carry one; which of the three is required is decided per format version
    # by `_validate_destination_id_for_format` — from the reader's context when reading an
    # artifact, and from the current version when building one in process.
    destination_id: str | None = None

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

    @model_validator(mode="before")
    @classmethod
    def _validate_destination_id_for_format(cls, data: Any, info: ValidationInfo) -> Any:
        """Apply the format's rule for `destination_id`, reading the **raw** mapping.

        Absent and null are different things here — under format 2 the field must not appear
        at all, while under format 3 a create or delete may carry it as null — and only the
        raw input can tell them apart, so this runs before the model exists.

        The rule needs the artifact's declared version, which the record itself does not
        carry, so the reader supplies it as pydantic validation context. With **no** context
        the record is being built in process, which can only be the **current** format — there
        is no way to construct an older one — so the current version's rule applies.

        That symmetry is the point: derivation and the artifact enforce one rule. Without it a
        caller could build an update carrying no destination id, and the refusal would arrive
        later and somewhere else — from a reader, against a file, naming a line number — for a
        record that should never have existed.
        """
        if not isinstance(data, Mapping):
            return data
        context = info.context
        format_version = context.get("format_version") if isinstance(context, Mapping) else None
        if not isinstance(format_version, int):
            format_version = PLAN_FORMAT_VERSION
        recorded_id = data.get("destination_id")
        present = "destination_id" in data
        identifier = data.get("operation_id", "<no operation_id recorded>")
        if format_version < DESTINATION_ID_FORMAT_VERSION:
            if present:
                msg = (
                    f"Operation {identifier!r} carries 'destination_id', which plan format "
                    f"{format_version} does not define."
                )
                raise ValueError(msg)
            # A format-2 update legitimately carries none, which is exactly why such a plan is
            # readable and reviewable but not applyable.
            return data
        if data.get("action") == "update":
            # Present and non-blank is the whole rule: the plan boundary owns "there is a key",
            # and the destination owns whether that key names anything. The value is never
            # trimmed — the checksum covers the recorded bytes.
            if not isinstance(recorded_id, str) or not recorded_id.strip():
                msg = (
                    f"Operation {identifier!r} is an update and carries no usable 'destination_id' "
                    f"(found {recorded_id!r}). A format-{format_version} update is keyed by the "
                    "destination id recorded for it at plan time."
                )
                raise ValueError(msg)
        elif recorded_id is not None:
            msg = (
                f"Operation {identifier!r} is a {data.get('action')!r} and must carry no "
                f"'destination_id' (found {recorded_id!r}): it names no existing destination object."
            )
            raise ValueError(msg)
        return data

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
        # has two competing write sources — the upsert value and the relationship value.
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


class LineageAccountRecord(BaseModel):
    """The import account a plan was computed with: the account behind the token."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    id: str = Field(min_length=1)


class LineageNodeRecord(BaseModel):
    """A source or owner node, by kind and name, as the plan recorded it."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)


class PlanLineageRecord(BaseModel):
    """The lineage a plan writes, recorded in the manifest (FR-012).

    `import_account` is what an apply compares its own account with, by `id`. `source` is
    `CoreAccount` and the import account's name when the package names no source. `owner`
    is `None` when the package names no owner.
    """

    model_config = ConfigDict(extra="forbid")

    import_account: LineageAccountRecord
    source: LineageNodeRecord
    owner: LineageNodeRecord | None = None
    protect: bool


# The last-change file, relative to the plan directory. The manifest's `last_changes.path`
# must name exactly this file, so a manifest cannot point the reader at another path.
LAST_CHANGES_FILE_NAME = "last_changes.jsonl"

LastChangeFieldType = Literal["attribute", "relationship"]


class RecordedAccountRecord(BaseModel):
    """The account Infrahub recorded as `updated_by` for a value, by id and display label.

    Frozen, so the Infrahub adapter can share one record among every value the same account changed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    display_label: str | None = None


class LastChangeRecord(BaseModel):
    """Who last changed one field an update proposes to change, and when (FR-007).

    One line of `plan/last_changes.jsonl`. `updated_by` or `updated_at` is `None` when
    Infrahub holds no value for it, or when the plan did not read metadata; review shows
    such a value as "not recorded". The record is display data: apply does not read it.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(pattern=OPERATION_ID_PATTERN)
    field: str = Field(min_length=1)
    field_type: LastChangeFieldType
    updated_by: RecordedAccountRecord | None
    updated_at: str | None


class PlanLastChangesRecord(BaseModel):
    """The manifest's record of `plan/last_changes.jsonl`: its path, digest and record count.

    The plan checksum covers this record, and through `sha256` the file's bytes.
    `metadata_read` is `False` when the plan did not ask Infrahub for `updated_by` and
    `updated_at`: the server is older than 1.7.0 or its version could not be parsed.
    """

    model_config = ConfigDict(extra="forbid")

    path: Literal["last_changes.jsonl"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=0)
    metadata_read: bool


class PlanReportRecord(BaseModel):
    """The manifest's record of `plan/report.jsonl` (spec 019, contracts/plan-artifact-format-4.md).

    The plan checksum covers this record, and through `digest` the file's bytes. The counts
    are the report's: problem entries, left-out records (each once, split into those with a
    problem and those that depend on one) and kept records.
    """

    model_config = ConfigDict(extra="forbid")

    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    problems: int = Field(ge=0)
    left_out: int = Field(ge=0)
    left_out_with_problem: int = Field(ge=0)
    left_out_dependent: int = Field(ge=0)
    kept: int = Field(ge=0)


class PlanManifest(BaseModel):
    """The artifact's header, and the format contract (FR-027).

    Unknown fields are tolerated, preserved verbatim, and included in the checksummed
    bytes (AD028).
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
    # Registered plans bind the exact configuration package the worker verified.  The
    # three values are deliberately separate artifact fields so they participate in the
    # canonical checksum alongside every other manifest fact.
    config_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    registry_version: int | None = Field(default=None, ge=1, le=2**63 - 1)
    package_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Additive: manifests written before this field exists carry no
    # binding, and the apply-time destination comparison skips them.
    destination_binding: DestinationBindingRecord | None = None
    # The consumed destination-schema semantics the plan was computed against, as the full
    # SHA-256 digest of the canonical projection (`infrahub_sync.runtime_schema`).  Required
    # on a registered plan and absent from an unregistered one, so a registered apply always
    # has something to compare the live schema against and legacy manifests keep reading.
    schema_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Additive: the import account and the lineage the plan writes. Absent on a plan whose
    # destination records no lineage, and on plans made before the field existed; an
    # Infrahub apply refuses such a plan (FR-012).
    lineage: PlanLineageRecord | None = None
    # Additive: the digest and record count of `plan/last_changes.jsonl`. Absent on a plan
    # whose destination keeps no last changes, and on plans made before the field existed;
    # review then shows no last-change data.
    last_changes: PlanLastChangesRecord | None = None
    # Format 4: the digest, size and counts of `plan/report.jsonl` (spec 019). Absent on a
    # format 2 or 3 plan; the reader refuses a format 4 manifest without it.
    report: PlanReportRecord | None = None
    # Format 4: `False` when the run had no destination schema snapshot, so the plan could not
    # check Dropdown values; absent otherwise (spec 019 research R4).
    dropdown_values_checked: bool | None = None

    @field_validator("registry_version", mode="before")
    @classmethod
    def _require_exact_registry_version(cls, value: object) -> object:
        """Refuse coercible values; a binding is an exact registry identity."""
        if value is not None and type(value) is not int:  # pylint: disable=unidiomatic-typecheck
            # Exact identity excludes bool and int subclasses.
            msg = "registry_version must be int"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_complete_configuration_binding(self) -> PlanManifest:
        binding = (self.config_id, self.registry_version, self.package_checksum)
        if any(value is None for value in binding) and any(value is not None for value in binding):
            msg = "configuration binding must be all absent or all present"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _report_only_from_format_4(self) -> PlanManifest:
        """A format 2 or 3 manifest has no plan report, so it cannot claim the report's checks."""
        if self.format_version < REPORT_FORMAT_VERSION and (
            self.report is not None or self.dropdown_values_checked is not None
        ):
            msg = f"'report' and 'dropdown_values_checked' need format_version {REPORT_FORMAT_VERSION} or later"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _require_registered_schema_binding(self) -> PlanManifest:
        """A registered plan records the schema semantics it was computed against.

        Refused here rather than at the apply gate so the rule holds on both sides of the
        artifact: the writer cannot emit a registered plan without the binding, and a
        registered manifest that lost it reads as torn instead of reaching an apply that has
        nothing to compare.
        """
        if self.config_id is not None and self.schema_fingerprint is None:
            msg = "a registered plan manifest must record its schema_fingerprint"
            raise ValueError(msg)
        return self

    @property
    def configuration_binding(self) -> tuple[str, int, str] | None:
        """Return the complete registered package identity, never a partial tuple."""
        if self.config_id is None:
            return None
        assert self.registry_version is not None
        assert self.package_checksum is not None
        return self.config_id, self.registry_version, self.package_checksum

    @property
    def registered_schema_fingerprint(self) -> str | None:
        """Return a registered plan's recorded schema identity, `None` when unregistered."""
        if self.configuration_binding is None:
            return None
        assert self.schema_fingerprint is not None
        return self.schema_fingerprint

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
        if self.schema_fingerprint is None:
            data.pop("schema_fingerprint", None)
        if self.lineage is None:
            data.pop("lineage", None)
        if self.last_changes is None:
            data.pop("last_changes", None)
        if self.report is None:
            data.pop("report", None)
        if self.dropdown_values_checked is None:
            data.pop("dropdown_values_checked", None)
        if self.configuration_binding is None:
            data.pop("config_id", None)
            data.pop("registry_version", None)
            data.pop("package_checksum", None)
        return data


class PlanSummary(BaseModel):
    """The counts a review renders, plus the two disclosure fields (FR-006, AD056).

    `delete_operations_computed` and `deletes_not_executed` are **required** and derived on
    read — from the manifest and from the operation set — so the artifact format and
    `plan_checksum` are untouched. Without the first, a plan whose whole delete class was
    never computed is indistinguishable from one that has no deletes.

    The counts of spec 019 FR-007 and FR-008 come from the plan report (data-model.md "Plan
    summary additions"). A format 2 or 3 plan has no report: its counts are zeros and
    `dropdown_values_checked` is false.
    """

    model_config = ConfigDict(extra="forbid")

    by_action: dict[str, int]
    by_kind: dict[str, int]
    total: int = Field(ge=0)
    delete_operations_computed: bool
    deletes_not_executed: int = Field(ge=0)
    left_out: int = Field(default=0, ge=0)
    left_out_with_problem: int = Field(default=0, ge=0)
    left_out_dependent: int = Field(default=0, ge=0)
    left_out_by_kind: dict[str, int] = Field(default_factory=dict)
    problems: int = Field(default=0, ge=0)
    problems_by_type: dict[str, int] = Field(default_factory=dict)
    kept: int = Field(default=0, ge=0)
    deletes_withheld_by_kind: dict[str, int] = Field(default_factory=dict)
    dropdown_values_checked: bool = False


@dataclass(frozen=True)
class ApplyRecord:
    """What one apply did, handed from the engine to its caller (FR-017, FR-020, AD062, AD069).

    Not an artifact record — nothing here is written to `plan/`. It is the value
    `Potenda.apply_plan` **returns**, and the CLI is the single writer that merges
    `as_summary_keys()` into the run file's `summary` before saving it (AD069).

    A destination rejection mid-apply carries the **partial** record on the raised
    `OperationApplyFailedError`, so the CLI can merge what was written before recording
    `failed`, and FR-025's last-applied pointer survives a partial apply. The failing
    operation is named on the record too: its mutation may have committed before the failure
    was observed, which would leave the destination changed by an operation in neither the
    applied nor the skipped-delete set. Re-applying converges it (AD033).
    """

    applied_operations: tuple[str, ...] = ()
    skipped_delete_operations: tuple[str, ...] = ()
    failed_operation: str | None = None
    # Whether the failing operation reached a destination mutation. `None` is "unknown",
    # which is what every failure meant before this field existed; `False` is carried up
    # from a refusal that attempted no mutation, or that the destination proved wrote
    # nothing. `True` is never set — an operation that failed after dispatching is exactly
    # the case that cannot be known.
    failed_operation_wrote: bool | None = None
    # The plan's operation count, and the failed operation's action, kind and identity, read
    # from the plan by the engine (spec 019 FR-014). The failure record of a stopped apply
    # holds them. Not summary keys, and not part of the record's equality: two records of
    # one apply compare equal whether or not they carry them.
    operations_total: int | None = dataclass_field(default=None, compare=False)
    failed_operation_detail: Mapping[str, Any] | None = dataclass_field(default=None, compare=False)

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

        Deliberately "may". An operation is one destination mutation, and one mutation can
        still commit remotely before its response — or the transport carrying it — fails; the
        engine learns only that the call raised, never how far it got. So the marker stays
        true for any failed operation whose reach is unknown, which is the reading that never
        understates what reached the destination.

        The one exception is a failure that is **proven** not to have written: a refusal
        raised before the SDK write, or one carrying the destination's own not-found for an
        id-keyed upsert, which creates nothing. Reporting those as possibly-partial made
        every such refusal indistinguishable from a real interruption and sent an operator
        to reconcile a destination that was never touched (S6).
        """
        return self.failed_operation is not None and self.failed_operation_wrote is not False

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
            "failed_operation_wrote": self.failed_operation_wrote,
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
    "last_changes",
    "report",
    "write_surface",
    "destination_binding",
]

# The pre-write checks `apply` runs before its first write and `verify` reports as passed,
# in the order `verify` reports them. `verify` and `apply` share one gate, so a check that
# refuses an apply fails a verify under the same name.
PreWriteCheck: TypeAlias = Literal[
    "checkpoint",
    "run_binding",
    "plan_checksum",
    "source_snapshot",
    "config_version",
    "format_version",
    "destination_schema",
    "destination_binding",
    "write_surface",
    "planned_fields",
    "lineage",
]
PRE_WRITE_CHECKS: tuple[PreWriteCheck, ...] = get_args(PreWriteCheck)
# The name a refusal records for the check it failed on. A file check can fail under a name
# narrower than the pre-write check that covers it (`torn_operations` and `last_changes`
# belong to the plan files), so a failure names either vocabulary.
CheckName: TypeAlias = VerificationCheck | PreWriteCheck


def ordered_checks(checks: Iterable[PreWriteCheck]) -> tuple[PreWriteCheck, ...]:
    """Return `checks` once each, in the order `PRE_WRITE_CHECKS` lists them."""
    present = set(checks)
    return tuple(check for check in PRE_WRITE_CHECKS if check in present)


class VerificationFailure(BaseModel):
    """One failed pre-apply check. The apply refuses when the list is non-empty (FR-009)."""

    model_config = ConfigDict(extra="forbid")

    check: VerificationCheck
    run_id: str
    expected: str | None = None
    found: str | None = None
    next_action: str = Field(min_length=1)


# --- The plan report (spec 019 data-model.md) ----------------------------------------------
#
# The records the plan writes for source data it cannot plan. Nothing builds them yet: the
# plan step that finds problems and the format 4 report file come with later tasks.

ProblemType: TypeAlias = Literal[
    "dropdown_value_not_in_choices",
    "duplicate_source_identity",
    "destination_identity_collision",
    "wrong_value_type",
    "required_peer_excluded_by_filter",
    "identities_merge_in_destination",
    "peer_not_found_in_source",
    "destination_identity_ambiguous",
    "transform_failed",
]
"""The stable problem type names (FR-002, FR-025). A later release may add a name; an
existing name is never renamed or removed."""

PROBLEM_TYPES: tuple[ProblemType, ...] = get_args(ProblemType)

# The longest reason a problem holds, and the longest string the report keeps of a source value.
REPORT_REASON_LIMIT = 512
REPORT_VALUE_LIMIT = 256


def limited_report_value(value: Any) -> Any:  # a source value can be any JSON value
    """Return `value` with every string limited to `REPORT_VALUE_LIMIT` characters.

    A value outside JSON's types is kept as its limited text, so the report stays encodable
    whatever the source returned.
    """
    if isinstance(value, float) and not math.isfinite(value):
        # JSON has no encoding for NaN or Infinity; the report keeps their text.
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:REPORT_VALUE_LIMIT]
    if isinstance(value, Mapping):
        return {str(key)[:REPORT_VALUE_LIMIT]: limited_report_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [limited_report_value(item) for item in value]
    return str(value)[:REPORT_VALUE_LIMIT]


class SourceRecordReference(BaseModel):
    """Names a source record by destination kind, source identity and source record ID.

    `identity` is the source identity as plan operations show it, or `None` when the record
    could not be converted far enough to compute it. `source_id` is the adapter's record ID
    (`local_id`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1)
    identity: dict[str, Any] | None = None
    source_id: str | None = None


class PlanProblem(BaseModel):
    """One problem the plan found in one source record (FR-003).

    A record with several problems has one entry per problem.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    problem_type: ProblemType
    record: SourceRecordReference
    # The attribute or relationship involved.
    field: str | None = None
    # The source value; `None` for `transform_failed`.
    value: Any = None
    # The Dropdown choices (`dropdown_value_not_in_choices`).
    allowed: list[str] | None = None
    # The excluded or missing peer.
    peer: SourceRecordReference | None = None
    # The other records of an identity problem.
    other_records: list[SourceRecordReference] = Field(default_factory=list)
    # The Infrahub node IDs that hold one identity (`destination_identity_ambiguous` only).
    destination_ids: list[str] | None = None
    # The position of the transform (`transforms[0]`) or filter (`filters[0]`), never its expression.
    config_entry: str | None = None
    reason: str = Field(min_length=1, max_length=REPORT_REASON_LIMIT)

    @field_validator("value", mode="before")
    @classmethod
    def _limit_value(cls, value: Any) -> Any:
        return limited_report_value(value)

    @model_serializer(mode="wrap")
    def _omit_absent_optional_fields(self, handler: Any) -> Any:
        # `destination_ids` and `config_entry` are absent, not null, when they do not apply.
        data = handler(self)
        if isinstance(data, dict):
            for name in ("destination_ids", "config_entry"):
                if data.get(name) is None:
                    data.pop(name, None)
        return data


class LeftOutRecord(BaseModel):
    """One source record the plan leaves out, counted once (FR-005, FR-006)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: SourceRecordReference
    cause: Literal["has_problem", "depends_on_left_out"]
    problem_types: list[ProblemType] = Field(default_factory=list)
    depends_on: SourceRecordReference | None = None
    # The required relationship the record depends through.
    relationship: str | None = None
    # False when the record could not be built.
    loaded: bool

    @model_validator(mode="after")
    def _require_cause_fields(self) -> LeftOutRecord:
        if self.cause == "has_problem" and not self.problem_types:
            msg = "a record left out because of a problem names its problem types"
            raise ValueError(msg)
        if self.cause == "depends_on_left_out" and self.depends_on is None:
            msg = "a record left out as a dependent names the record it depends on"
            raise ValueError(msg)
        return self


class KeptRecord(BaseModel):
    """A source record the plan keeps without one optional relationship (FR-005, FR-024)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: SourceRecordReference
    cause: Literal["peer_left_out", "peer_not_found_in_source"]
    # The optional relationship written without the peer.
    relationship: str = Field(min_length=1)
    # The left-out record, or the missing peer with `identity` null.
    left_out_peer: SourceRecordReference


def _identity_order(reference: SourceRecordReference) -> bytes:
    """A record's identity as canonical bytes; empty when it is not known."""
    return b"" if reference.identity is None else canonical_json_bytes(reference.identity)


def _report_line(entry: str, record: BaseModel) -> dict[str, Any]:
    return {"entry": entry, **record.model_dump()}


@dataclass(frozen=True)
class PlanReport:
    """The problems, left-out records and kept records of one plan (spec 019 data-model.md "Plan report").

    `encode` returns the bytes of `plan/report.jsonl`: problems, then left-out records, then
    kept records, each group in the stable order of data-model.md, ties broken by the line's
    canonical bytes, so the bytes do not depend on the order the records were read in.
    """

    problems: tuple[PlanProblem, ...] = ()
    left_out: tuple[LeftOutRecord, ...] = ()
    kept: tuple[KeptRecord, ...] = ()
    # Not written to the report: the (kind, identity) of each kept record whose source and
    # Infrahub values differ only in the relationships it is written without. The selection
    # step plans no update for it, since that update would change nothing (spec 019 research R3).
    unchanged_without_kept_relationships: frozenset[tuple[str, bytes]] = frozenset()

    def lines(self) -> list[dict[str, Any]]:
        """Every report line as a mapping, in the report's order."""
        problems = sorted(
            (_report_line("problem", problem) for problem in self.problems),
            key=lambda line: (
                line["problem_type"],
                line["record"]["kind"],
                _identity_order(SourceRecordReference(**line["record"])),
                line["record"]["source_id"] or "",
                canonical_json_bytes(line),
            ),
        )
        left_out = sorted(
            (_report_line("left_out", item) for item in self.left_out),
            key=lambda line: (
                line["record"]["kind"],
                _identity_order(SourceRecordReference(**line["record"])),
                line["record"]["source_id"] or "",
                canonical_json_bytes(line),
            ),
        )
        kept = sorted(
            (_report_line("kept", item) for item in self.kept),
            key=lambda line: (
                line["record"]["kind"],
                _identity_order(SourceRecordReference(**line["record"])),
                line["relationship"],
                canonical_json_bytes(line),
            ),
        )
        return [*problems, *left_out, *kept]

    def encode(self) -> bytes:
        """The bytes of `plan/report.jsonl`: one canonical JSON object per line, LF-terminated."""
        return b"".join(canonical_json_bytes(line) + b"\n" for line in self.lines())

    def record(self, encoded: bytes | None = None) -> PlanReportRecord:
        """The manifest's `report` record for this report, whose bytes are `encoded` (by default `encode()`)."""
        payload = self.encode() if encoded is None else encoded
        with_problem = sum(1 for item in self.left_out if item.cause == "has_problem")
        return PlanReportRecord(
            digest=sha256(payload).hexdigest(),
            size=len(payload),
            problems=len(self.problems),
            left_out=len(self.left_out),
            left_out_with_problem=with_problem,
            left_out_dependent=len(self.left_out) - with_problem,
            kept=len(self.kept),
        )

    def deletes_withheld_by_kind(self) -> dict[str, int]:
        """Per kind, the left-out records without a known identity plus the ambiguous destination identities.

        The plan computes no delete of these kinds (FR-008, spec 019 research R21).
        """
        withheld: dict[str, int] = {}
        for item in self.left_out:
            if item.record.identity is None:
                withheld[item.record.kind] = withheld.get(item.record.kind, 0) + 1
        for problem in self.problems:
            if problem.problem_type == "destination_identity_ambiguous":
                withheld[problem.record.kind] = withheld.get(problem.record.kind, 0) + 1
        return dict(sorted(withheld.items()))

    @classmethod
    def from_lines(cls, lines: Iterable[Mapping[str, Any]]) -> PlanReport:
        """The report whose `lines()` are `lines`, as the plan reader returns them."""
        problems: list[PlanProblem] = []
        left_out: list[LeftOutRecord] = []
        kept: list[KeptRecord] = []
        for line in lines:
            fields = {key: value for key, value in line.items() if key != "entry"}
            if line.get("entry") == "problem":
                problems.append(PlanProblem.model_validate(fields))
            elif line.get("entry") == "left_out":
                left_out.append(LeftOutRecord.model_validate(fields))
            else:
                kept.append(KeptRecord.model_validate(fields))
        return cls(problems=tuple(problems), left_out=tuple(left_out), kept=tuple(kept))

    def summary_counts(self) -> dict[str, Any]:
        """The plan summary counts of this report (FR-007, FR-008; data-model.md "Plan summary additions").

        Counted from the records, as `record` counts them, without encoding and hashing the report.
        """
        with_problem = sum(1 for item in self.left_out if item.cause == "has_problem")
        left_out_by_kind: dict[str, int] = {}
        for item in self.left_out:
            left_out_by_kind[item.record.kind] = left_out_by_kind.get(item.record.kind, 0) + 1
        problems_by_type: dict[str, int] = {}
        for problem in self.problems:
            problems_by_type[problem.problem_type] = problems_by_type.get(problem.problem_type, 0) + 1
        return {
            "left_out": len(self.left_out),
            "left_out_with_problem": with_problem,
            "left_out_dependent": len(self.left_out) - with_problem,
            "left_out_by_kind": dict(sorted(left_out_by_kind.items())),
            "problems": len(self.problems),
            "problems_by_type": dict(sorted(problems_by_type.items())),
            "kept": len(self.kept),
            "deletes_withheld_by_kind": self.deletes_withheld_by_kind(),
        }


# --- A peer the source does not return (spec 019 research R5) ------------------------------
#
# Plan derivation names a reference to a peer that the source does not return with this
# placeholder identity instead of refusing the plan. The selection step of the problem pass
# removes every placeholder, and the plan writer refuses an operation that still holds one.

NOT_IN_SOURCE_VALUE_PREFIX = "infrahub-sync:not-in-source:"
"""Prefix of the reference value a source adapter stores for a peer the source does not return
(`infrahub_sync.not_in_source_placeholder`): the prefix, the peer kind, `:` and the source ID."""

NOT_IN_SOURCE_PEER_KEY = "infrahub-sync:not-in-source-peer"
"""The only key of a placeholder peer identity. No mapping field can have this name."""


def not_in_source_peer_identity(source_id: str | None) -> dict[str, Any]:
    """The placeholder identity of a peer the source does not return, with its source record ID."""
    return {NOT_IN_SOURCE_PEER_KEY: {"source_id": source_id}}


def is_not_in_source_peer(identity: object) -> bool:
    """Tell whether a peer identity is the placeholder of `not_in_source_peer_identity`."""
    return isinstance(identity, Mapping) and NOT_IN_SOURCE_PEER_KEY in identity


def not_in_source_peer_pair(value: object) -> dict[str, Any] | None:
    """The `{"peer_kind", "identity"}` placeholder pair of a reference value marked not in the source.

    `None` when `value` is not such a reference value.
    """
    if not isinstance(value, str) or not value.startswith(NOT_IN_SOURCE_VALUE_PREFIX):
        return None
    peer_kind, _, source_id = value.removeprefix(NOT_IN_SOURCE_VALUE_PREFIX).partition(":")
    return {"peer_kind": peer_kind, "identity": not_in_source_peer_identity(source_id or None)}


def holds_not_in_source_peer(value: object) -> bool:
    """Tell whether `value`, at any depth, holds a placeholder identity or a not-in-source reference value."""
    if isinstance(value, str):
        return value.startswith(NOT_IN_SOURCE_VALUE_PREFIX)
    if isinstance(value, Mapping):
        return NOT_IN_SOURCE_PEER_KEY in value or any(
            holds_not_in_source_peer(key) or holds_not_in_source_peer(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(holds_not_in_source_peer(item) for item in value)
    return False
