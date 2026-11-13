from __future__ import annotations

import dataclasses
import logging
import operator
import re
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Union

import pydantic
from diffsync.enum import DiffSyncFlags
from diffsync.exceptions import ObjectAlreadyExists
from jinja2 import StrictUndefined, Undefined
from jinja2.exceptions import SecurityError
from jinja2.nativetypes import NativeCodeGenerator, NativeTemplate, native_concat
from jinja2.sandbox import ImmutableSandboxedEnvironment
from netutils.ip import is_ip_within as netutils_is_ip_within

from infrahub_sync.adapters.utils import get_value
from infrahub_sync.cache.cursors import CursorTier
from infrahub_sync.dependency_graph import compute_tiers, flatten_tiers
from infrahub_sync.load_report import LoadReport, LoadReportEntry, filter_entry, source_record_id, transform_entry
from infrahub_sync.plan.models import NOT_IN_SOURCE_VALUE_PREFIX, SourceRecordReference
from infrahub_sync.warm_read import WarmSourceReadMixin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from diffsync.store import BaseStore

    from infrahub_sync.cache.cursors import CursorState
    from infrahub_sync.load_report import LoadReportReason
    from infrahub_sync.runtime_schema import RuntimeModelPlan

logger = logging.getLogger(__name__)


class SandboxedNativeEnvironment(ImmutableSandboxedEnvironment):
    """Jinja2 sandbox that returns native Python types, for schema_mapping transforms.

    Transform expressions come from registered configuration packages and render inside the
    Sync worker, so they must not reach private attributes, unsafe callables or mutating
    methods. The immutable sandbox refuses those with ``SecurityError``; the native code
    generator and concat keep the result's type (``list``, ``dict``, ``bool``, ``int``).
    """

    code_generator_class = NativeCodeGenerator
    concat = staticmethod(native_concat)


class SandboxedNativeTemplate(NativeTemplate):
    """Template bound to ``SandboxedNativeEnvironment``; its render concatenates natively."""

    environment_class = SandboxedNativeEnvironment


SandboxedNativeEnvironment.template_class = SandboxedNativeTemplate


def _reject_lazy_key(key: Any) -> None:
    """Refuse a dictionary key that is, or holds, a lazy iterator: it cannot be consumed into a hashable key."""
    if isinstance(key, Iterator):
        msg = "a dictionary key is a lazy iterator; convert it to a hashable value, such as with the join filter"
        raise TypeError(msg)
    if isinstance(key, (tuple, frozenset)):
        for nested in key:
            _reject_lazy_key(nested)


def _materialize_lazy(value: Any) -> Any:
    """Consume lazy iterators, at any depth, into lists; keep list/tuple/dict container types; refuse them in sets."""
    if isinstance(value, Iterator):
        return [_materialize_lazy(nested) for nested in value]
    if isinstance(value, dict):
        for key in value:
            _reject_lazy_key(key)
        return {key: _materialize_lazy(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_materialize_lazy(nested) for nested in value]
    if isinstance(value, tuple):
        return tuple(_materialize_lazy(nested) for nested in value)
    if isinstance(value, (set, frozenset)):
        for member in value:
            _reject_lazy_key(member)
    return value


def _raise_if_undefined(value: Any) -> None:
    """Raise the error an Undefined carries when a rendered value is, or contains, one."""
    if isinstance(value, Undefined):
        # Jinja's own trigger for the error an Undefined was built with (UndefinedError or SecurityError).
        value._fail_with_undefined_error()  # pylint: disable=protected-access
    if isinstance(value, dict):
        for key, nested in value.items():
            _raise_if_undefined(key)
            _raise_if_undefined(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            _raise_if_undefined(nested)


def new_transform_environment() -> SandboxedNativeEnvironment:
    """Build the environment every schema_mapping transform expression renders in."""
    return SandboxedNativeEnvironment(
        undefined=StrictUndefined,  # fail fast on missing keys
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


class TransformError(ValueError):
    """A configured transform failed for one record (spec 019 research R7).

    `field` is the field the transform writes and `position` the transform's position in the
    mapping entry's `transforms` list, or `None` when the transform was applied on its own.
    Neither holds the record's value nor the error text. The message is kept for the worker
    log only; a failure record reads the named attributes. A subclass of `ValueError`, so every
    handler that matches `ValueError` still matches it.
    """

    def __init__(self, message: str, *, field: str, position: int | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.position = position

    def __reduce__(self) -> tuple[Any, ...]:
        # Keep the named attributes when the error is pickled, as it is across a process boundary.
        return (_rebuild_transform_error, (str(self), self.field, self.position))


def _rebuild_transform_error(message: str, field: str, position: int | None) -> TransformError:
    return TransformError(message, field=field, position=position)


class SchemaMappingFilter(pydantic.BaseModel):
    field: str
    operation: str
    value: Any | None = None


class SchemaMappingTransform(pydantic.BaseModel):
    field: str
    expression: str


class SchemaMappingField(pydantic.BaseModel):
    name: str
    mapping: str | None = pydantic.Field(default=None)
    static: Any | None = pydantic.Field(default=None)
    reference: str | None = pydantic.Field(default=None)


class SchemaMappingModel(pydantic.BaseModel):
    name: str
    mapping: str | None = pydantic.Field(default=None)
    identifiers: list[str] | None = pydantic.Field(default=None)
    filters: list[SchemaMappingFilter] | None = pydantic.Field(default=None)
    transforms: list[SchemaMappingTransform] | None = pydantic.Field(default=None)
    fields: list[SchemaMappingField] = pydantic.Field(default_factory=list)


class SyncAdapter(pydantic.BaseModel):
    name: str
    adapter: str | None = None  # Optional adapter specification (path, dotted path, etc.)
    settings: dict[str, Any] | None = {}


class SyncStore(pydantic.BaseModel):
    type: str
    settings: dict[str, Any] | None = {}


class IncrementalConfig(pydantic.BaseModel):
    """Optional configuration block for incremental-extraction behaviour."""

    full_resync_every: int = 10


class SyncConfig(pydantic.BaseModel):
    name: str
    store: SyncStore | None = None  # Fix default value that was incorrectly set as list
    source: SyncAdapter
    destination: SyncAdapter
    adapters_path: list[str] | None = None  # New field for adapter path configuration
    order: list[str] = pydantic.Field(default_factory=list)
    schema_mapping: list[SchemaMappingModel] = []
    diffsync_flags: list[Union[str, DiffSyncFlags]] | None = []
    incremental: IncrementalConfig | None = None

    @pydantic.field_validator("diffsync_flags", mode="before")
    def convert_str_to_enum(cls, v):  # pylint: disable=no-self-argument  # a pydantic validator: `cls` is correct
        if not isinstance(v, list):
            msg = "diffsync_flags must be provided as a list"
            raise TypeError(msg)
        new_flags = []
        for item in v:
            if isinstance(item, str):
                try:
                    new_flags.append(DiffSyncFlags[item])
                except KeyError as exc:
                    msg = f"Invalid DiffSyncFlags value: {item}"
                    raise ValueError(msg) from exc
            else:
                new_flags.append(item)
        return new_flags

    def compute_order(self) -> list[str]:
        """Return the operator-provided `order` if set, else flattened tiers
        auto-computed from `schema_mapping`.

        Logs the tier layout at INFO and any dropped optional edges at WARNING,
        as `compute_order_and_tiers` does by default.
        """
        order, _tiers = self.compute_order_and_tiers()
        return order

    def compute_order_and_tiers(
        self, generic_peers: Mapping[str, tuple[str, ...]] | None = None, *, announce: bool = True
    ) -> tuple[list[str], list[set[str]] | None]:
        """Return `(flat_order, tiers)` from a single topological pass.

        `tiers` is `None` when an explicit `order` is configured. Callers that
        need both the flat order and the tier layout should use this rather
        than calling `compute_order()` and `compute_tiers()` separately, which
        would sort the graph twice.

        Logs the tier layout at INFO and any dropped optional edges at WARNING.
        `announce=False` logs both at DEBUG, for a caller that recomputes a
        layout the same run already logged.
        """
        if self.order:
            return list(self.order), None
        tiers, dropped = compute_tiers(self.schema_mapping, generic_peers)
        layout_level, dropped_level = (logging.INFO, logging.WARNING) if announce else (logging.DEBUG, logging.DEBUG)
        for idx, tier in enumerate(tiers):
            logger.log(layout_level, "tier %d (%d): %s", idx, len(tier), sorted(tier))
        if dropped:
            logger.log(dropped_level, "dropped optional edges to break cycles: %s", dropped)
        return flatten_tiers(tiers), tiers


class SyncInstance(SyncConfig):
    directory: str
    # Worker-only state, deliberately absent from serialized configuration data.
    _configuration_binding: tuple[str, int, str] | None = pydantic.PrivateAttr(default=None)
    # The registered run's runtime model plan, when one was built. Its presence is what
    # tells engine assembly to use installed resolution and bind in-memory classes rather
    # than the legacy generated-wrapper path.
    _runtime_models: RuntimeModelPlan | None = pydantic.PrivateAttr(default=None)
    # Generic-to-concrete peers read from the destination adapter's live schema on a direct
    # run, which has no `_runtime_models`. Plan derivation uses it so it expands a generic
    # reference the same way ordering does.
    _direct_generic_peers: Mapping[str, tuple[str, ...]] | None = pydantic.PrivateAttr(default=None)


def resolve_effective_diffsync_flags(
    configured_flags: Iterable[str | DiffSyncFlags] | None,
) -> DiffSyncFlags:
    """Resolve the effective DiffSync flags for the supported live-sync profile.

    The SYNC-78 rule: ``SKIP_UNMATCHED_DST`` is invariant — a destination-only
    object is never turned into a delete action, no matter which unrelated flags
    are configured. Configured flags are OR-combined on top of that invariant,
    so no flags resolve to ``SKIP_UNMATCHED_DST`` alone and a set that already
    contains it is unchanged. This is the one place that interprets flag
    aggregation; consumers (the sync engine, the destination write-operations
    capability check) call it rather than re-deriving the rule.

    Raises ``KeyError`` for a flag name that ``DiffSyncFlags`` does not define.
    """
    flags = DiffSyncFlags.SKIP_UNMATCHED_DST
    for flag in configured_flags or []:
        flags |= flag if isinstance(flag, DiffSyncFlags) else DiffSyncFlags[flag]
    return flags


def requested_destination_write_operations(
    configured_flags: Iterable[str | DiffSyncFlags] | None,
) -> frozenset[str]:
    """Derive the destination write operations one configuration requests.

    The operations-level sibling of :func:`resolve_effective_diffsync_flags`: the
    effective flags come from that one shared rule, so ``SKIP_UNMATCHED_DST`` is
    invariant and ``"delete"`` is never requested under the supported live-sync
    profile. ``"update"`` is always requested for matched objects, and
    ``"create"`` only while ``SKIP_UNMATCHED_SRC`` is not in effect. Consumers
    (the destination write-operations capability check) import this symbol
    rather than testing ``DiffSyncFlags`` bits themselves.

    Raises ``KeyError`` for a flag name that ``DiffSyncFlags`` does not define.
    """
    effective = resolve_effective_diffsync_flags(configured_flags)
    requested = {"update"}
    if not effective & DiffSyncFlags.SKIP_UNMATCHED_SRC:
        requested.add("create")
    return frozenset(requested)


def is_ip_within_filter(ip: str, ip_compare: Union[str, list[str]]) -> bool:
    """Check if an IP address is within a given subnet."""
    return netutils_is_ip_within(ip=ip, ip_compare=ip_compare)


def convert_to_int(value: Any) -> int:
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        msg = f"Cannot convert '{value}' to int"
        raise ValueError(msg) from exc


FILTERS_OPERATIONS: dict[str, Callable[..., Any]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": lambda field, value: operator.gt(convert_to_int(field), convert_to_int(value)),
    "<": lambda field, value: operator.lt(convert_to_int(field), convert_to_int(value)),
    ">=": lambda field, value: operator.ge(convert_to_int(field), convert_to_int(value)),
    "<=": lambda field, value: operator.le(convert_to_int(field), convert_to_int(value)),
    "in": lambda field, value: value and field in value,
    "not in": lambda field, value: field not in value,
    "contains": lambda field, value: field and value in field,
    "not contains": lambda field, value: field and value not in field,
    "is_empty": lambda field: field is None or not field,
    "is_not_empty": lambda field: field is not None and field,
    "regex": lambda field, pattern: re.match(pattern, field) is not None,
    # Netutils
    "is_ip_within": lambda field, value: is_ip_within_filter(ip=field, ip_compare=value),
}


NOT_IN_SOURCE_PLACEHOLDER_PREFIX = NOT_IN_SOURCE_VALUE_PREFIX
"""Prefix of the reference value that stands for an optional peer the source does not return."""


def not_in_source_placeholder(peer_kind: str, peer_source_id: str) -> str:
    """Return the reference value that marks an optional peer as not in the source (research R5).

    The problem pass (spec 019 T025, T026) removes it from the plan; it never reaches a write.
    """
    return f"{NOT_IN_SOURCE_PLACEHOLDER_PREFIX}{peer_kind}:{peer_source_id}"


def is_not_in_source_placeholder(value: object) -> bool:
    """Tell whether a reference value is a placeholder of `not_in_source_placeholder`."""
    return isinstance(value, str) and value.startswith(NOT_IN_SOURCE_PLACEHOLDER_PREFIX)


class PeerReferenceError(Exception):
    """A source record refers to a peer that the read cannot resolve (spec 019 research R2, R5).

    The message is built from the named attributes only, never from record values or from the
    text of another error.
    """

    problem: ClassVar[str] = "cannot resolve its peer"

    def __init__(self, *, kind: str, source_id: str | None, field: str, peer_kind: str, peer_source_id: str) -> None:
        self.kind = kind
        self.source_id = source_id
        self.field = field
        self.peer_kind = peer_kind
        self.peer_source_id = peer_source_id
        super().__init__(
            f"{kind} record {source_id} field {field} {self.problem} (peer kind {peer_kind}, source ID {peer_source_id})"
        )

    def __reduce__(self) -> tuple[Any, ...]:
        # Keyword-only arguments: rebuild from the named attributes when the error is pickled.
        details = {
            "kind": self.kind,
            "source_id": self.source_id,
            "field": self.field,
            "peer_kind": self.peer_kind,
            "peer_source_id": self.peer_source_id,
        }
        return (_rebuild_peer_reference_error, (type(self), details))


def _rebuild_peer_reference_error(error_class: type[PeerReferenceError], details: dict[str, Any]) -> PeerReferenceError:
    return error_class(**details)


class ExcludedRequiredPeerError(PeerReferenceError):
    """A required relationship's peer was removed by a configured filter."""

    problem = "refers to a required peer that a configured filter removed"


class PeerNotLoadedError(PeerReferenceError):
    """A required relationship's peer is in the source, and the read could not build it."""

    problem = "refers to a required peer that was not loaded"


class PeerNotFoundError(PeerReferenceError):
    """A required relationship's peer is not in the source (the source side of the read)."""

    problem = "refers to a required peer that is not in the source"


class PeerNotAdmittedYetError(Exception):
    """A record refers to a record of its own kind that the read lists and has not admitted yet.

    A reference within one kind (a location's `parent`, for example) gets no read order, so a
    child can come before its parent. `load_records` admits the record again after the other
    records of the kind; it is never recorded and never leaves the read loop.
    """

    def __init__(self, *, peer_kind: str, peer_source_id: str) -> None:
        self.peer_kind = peer_kind
        self.peer_source_id = peer_source_id
        super().__init__(f"peer kind {peer_kind}, source ID {peer_source_id} is not admitted yet")


class RecordLoadError(Exception):
    """A source record failed to load for a reason the admission step does not record (FR-018).

    It fails the plan with the failure type `record_load_failed`. The message names the kind,
    the source record ID and the field only.
    """

    def __init__(self, *, kind: str, source_id: str | None, field: str | None = None) -> None:
        self.kind = kind
        self.source_id = source_id
        self.field = field
        location = f" field {field}" if field else ""
        super().__init__(f"{kind} record {source_id}{location} cannot be loaded")

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild_record_load_error, (self.kind, self.source_id, self.field))


def _rebuild_record_load_error(kind: str, source_id: str | None, field: str | None) -> RecordLoadError:
    return RecordLoadError(kind=kind, source_id=source_id, field=field)


_TYPE_ERROR_SUFFIXES = ("_type", "_parsing", "_parsing_size", "_from_float")


def _is_type_error(error_type: str) -> bool:
    """Tell whether a pydantic error type refuses a value because of its type."""
    return error_type.endswith(_TYPE_ERROR_SUFFIXES)


def _error_field(error: Mapping[str, Any]) -> str | None:
    location = error.get("loc") or ()
    return str(location[0]) if location else None


def _identity_of(model: type[Any], data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the identifier values of `data`, or `None` when one identifier is missing."""
    if data is None:
        return None
    identifiers = tuple(getattr(model, "_identifiers", ()))
    if not identifiers or any(name not in data for name in identifiers):
        return None
    return {name: data[name] for name in identifiers}


def first_failing_filter(model: type[Any], record: Any, filters: list[SchemaMappingFilter]) -> int | None:
    """Return the position of the first filter of `filters` that removes `record`, or `None` when it passes."""
    for position, filter_obj in enumerate(filters):
        field_value = get_value(obj=record, name=filter_obj.field)
        if not model.apply_filter(field_value=field_value, operation=filter_obj.operation, value=filter_obj.value):
            return position
    return None


@dataclasses.dataclass
class _ReadState:
    """What the read loop of one adapter keeps between records (spec 019 research R2).

    `peer_index` maps (kind, source record ID) to the unique ID of each stored record;
    `not_loaded` maps (kind, source record ID) to the load report entry of a record the read
    could not build; `read_kinds` names the kinds the source read listed in full;
    `retained_ids` holds, per kind, the source IDs that the filters of at least one mapping
    entry kept, so a record that another entry of the kind removes is not recorded as filtered.
    """

    report: LoadReport
    peer_index: dict[tuple[str, str], str] = dataclasses.field(default_factory=dict)
    not_loaded: dict[tuple[str, str], LoadReportEntry] = dataclasses.field(default_factory=dict)
    read_kinds: set[str] = dataclasses.field(default_factory=set)
    retained_ids: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    # Per kind being admitted by `load_records`: the listed source IDs not admitted yet.
    pending: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    # The number of the source admission step in progress (`LoadReportEntry.admission`).
    admission: int = 0


class DiffSyncMixin(WarmSourceReadMixin):
    """Adapter base class: owns the per-record read loop and its admission step.

    An adapter implements `list_records` and `record_to_data`. The default `model_loader`
    lists the records of each mapping entry, applies the configured filters on the source
    side and passes each record to `load_record`. On the source side, `load_record` records a
    record that it cannot build in `load_report` and continues with the next record (research
    R2). References to peers resolve through `infrahub_sync.adapters.references`.
    """

    top_level: ClassVar[list[str]] = []
    config: SyncConfig
    store: BaseStore

    def load(self):
        """Load all the models, one by one based on the order defined in top_level."""
        for item in self.top_level:
            logger.debug("Loading %s", item)
            if hasattr(self, f"load_{item}"):
                method = getattr(self, f"load_{item}")
                method()
            else:
                self.model_loader(model_name=item, model=getattr(self, item))

    # -- Methods an adapter implements ---------------------------------------------------

    def list_records(self, model_name: str, element: SchemaMappingModel) -> Iterable[Any]:  # pylint: disable=unused-argument  # abstract hook
        """Return the raw records of one mapping entry, before any filter."""
        msg = f"{type(self).__name__}.list_records is not implemented."
        raise NotImplementedError(msg)

    def record_to_data(self, record: Any, element: SchemaMappingModel, model: type[Any]) -> dict[str, Any] | None:  # pylint: disable=unused-argument  # abstract hook
        """Convert one raw record to model data, with `local_id` set to its source record ID."""
        msg = f"{type(self).__name__}.record_to_data is not implemented."
        raise NotImplementedError(msg)

    def record_source_id(self, record: Any) -> str | None:
        """Return the source record ID of a raw record; an adapter whose records name it differently overrides it."""
        value = record.get("id") if isinstance(record, Mapping) else getattr(record, "id", None)
        return source_record_id(value)

    # -- The read loop ---------------------------------------------------------------------

    @property
    def _read_state(self) -> _ReadState:
        state = self.__dict__.get("_spec019_read_state")
        if state is None:
            state = _ReadState(report=LoadReport())
            self.__dict__["_spec019_read_state"] = state
        return state

    @property
    def load_report(self) -> LoadReport:
        """The load report of this adapter: the source records the read could not build."""
        return self._read_state.report

    def _is_source(self) -> bool:
        return getattr(self, "target", None) == "source"

    def model_loader(self, model_name: str, model: type[Any]) -> None:
        """Read every record of `model_name` and admit each one through `load_record`.

        On the source side, the configured filters run here, and the source IDs they remove are
        recorded in the load report with the position of the first filter that removed each
        one, so a reference to one of them is told apart from a reference to a peer the source
        does not return.
        """
        is_source = self._is_source()
        listed: list[tuple[SchemaMappingModel, Any]] = []
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue
            if not element.mapping:
                logger.info("No mapping defined for '%s', skipping", element.name)
                continue
            records = list(self.list_records(model_name, element))
            total = len(records)
            if is_source:
                records = self.filter_source_records(model, element, records)
                logger.info("%s: Loading %d/%d %s", getattr(self, "type", ""), len(records), total, element.mapping)
            else:
                logger.info("%s: Loading all %d %s", getattr(self, "type", ""), total, element.mapping)
            listed.extend((element, record) for record in records)
        self.load_records(model, listed)
        if is_source:
            self._read_state.read_kinds.add(model_name)

    def load_records(self, model: type[Any], listed: Sequence[tuple[SchemaMappingModel, Any]]) -> None:
        """Admit the listed records of one kind through `load_record`, in order.

        On the source side, a record that refers to a record of its own kind that is listed
        and not admitted yet (a child before its parent) is admitted again after the other
        records of the kind, until a pass admits no more of them. The records still waiting
        then (a cycle of references) are admitted without waiting, so a reference to a peer
        that is still not stored is reported as usual.
        """
        if not self._is_source():
            for element, record in listed:
                self.load_record(model, element, record)
            return
        if not listed:
            return
        state = self._read_state
        kind = listed[0][0].name
        pending = state.pending.setdefault(kind, set())
        for _element, record in listed:
            record_id = source_record_id(self.record_source_id(record))
            if record_id is not None:
                pending.add(record_id)
        waiting = list(listed)
        transforms_applied = False
        try:
            while waiting:
                deferred: list[tuple[SchemaMappingModel, Any]] = []
                for element, record in waiting:
                    try:
                        self.load_record(model, element, record, transforms_applied=transforms_applied)
                    except PeerNotAdmittedYetError:
                        deferred.append((element, record))
                        continue
                    record_id = source_record_id(self.record_source_id(record))
                    if record_id is not None:
                        pending.discard(record_id)
                if len(deferred) == len(waiting):
                    # No record was admitted in this pass: the references form a cycle.
                    pending.clear()
                waiting = deferred
                # A deferred record ran its transforms before its references were resolved.
                transforms_applied = True
        finally:
            state.pending.pop(kind, None)

    def peer_is_pending(self, peer_kind: str, peer_source_id: str) -> bool:
        """Tell whether the read lists the record `peer_source_id` of `peer_kind` and has not admitted it yet."""
        return peer_source_id in self._read_state.pending.get(peer_kind, ())

    def filter_source_records(self, model: type[Any], element: SchemaMappingModel, records: list[Any]) -> list[Any]:
        """Return the source records the entry's filters keep, and record the IDs they remove.

        Each removed source ID is recorded with the position of the first filter that removed
        it, in the load report's `filtered_ids` of the entry's kind. A kind can have several
        mapping entries with complementary filters: a source ID that the filters of any entry
        of the kind keep is not recorded as filtered, whichever entry is read first.
        """
        if not element.filters:
            # An entry without filters keeps every record, so another entry's filter did not remove any of them.
            kept_ids: set[str] = set()
            for record in records:
                record_id = source_record_id(self.record_source_id(record))
                if record_id is not None:
                    kept_ids.add(record_id)
            self._read_state.retained_ids.setdefault(element.name, set()).update(kept_ids)
            filtered_ids = self.load_report.filtered_ids.get(element.name, {})
            for record_id in kept_ids:
                filtered_ids.pop(record_id, None)
            return records
        removed: dict[str, int] = {}
        retained: set[str] = set()
        kept: list[Any] = []
        for record in records:
            source_id = source_record_id(self.record_source_id(record))
            position = first_failing_filter(model, record, element.filters)
            if position is None:
                kept.append(record)
                if source_id is not None:
                    retained.add(source_id)
            elif source_id is not None:
                removed.setdefault(source_id, position)
        kind_retained = self._read_state.retained_ids.setdefault(element.name, set())
        kind_retained.update(retained)
        filtered = self.load_report.filtered_ids.setdefault(element.name, {})
        for source_id in retained:
            filtered.pop(source_id, None)
        for source_id, position in removed.items():
            if source_id not in kind_retained:
                filtered.setdefault(source_id, position)
        return kept

    def filtered_peer_position(self, peer_kind: str, peer_source_id: str) -> tuple[bool, int | None]:
        """Tell whether a configured filter removed the record `peer_source_id` of `peer_kind`, and which one."""
        report = self.load_report
        return report.was_filtered(peer_kind, peer_source_id), report.filter_position(peer_kind, peer_source_id)

    def stored_peer_unique_id(self, peer_kind: str, peer_source_id: str) -> str | None:
        """Return the unique ID of the stored record `peer_source_id` of `peer_kind`, or `None`.

        Records stored by `load_record` are indexed; a record stored another way is found by
        its `local_id` in the store.
        """
        if source_record_id(peer_source_id) is None:
            return None
        state = self._read_state
        unique_id = state.peer_index.get((peer_kind, peer_source_id))
        if unique_id is not None:
            return unique_id
        for item in self.store.get_all(model=peer_kind):
            if source_record_id(getattr(item, "local_id", None)) == peer_source_id:
                unique_id = item.get_unique_id()
                state.peer_index[peer_kind, peer_source_id] = unique_id
                return unique_id
        return None

    def not_loaded_entry(self, peer_kind: str, peer_source_id: str) -> LoadReportEntry | None:
        """Return the load report entry of the record `peer_source_id` of `peer_kind` the read could not build."""
        return self._read_state.not_loaded.get((peer_kind, peer_source_id))

    def report_reference(self, entry: LoadReportEntry) -> None:
        """Record an entry about a reference of the record being admitted (an optional peer, kept)."""
        self._report(entry)

    def load_record(  # noqa: PLR0911  # pylint: disable=too-many-return-statements,too-many-branches  # one return per outcome of the admission step
        self,
        model: type[Any],
        element: SchemaMappingModel,
        record: Any,
        *,
        store_item: Callable[[Any], Any] | None = None,
        after_add: Callable[[Any, Any], None] | None = None,
        transforms_applied: bool = False,
    ) -> Any:
        """Build one record, store it and return it (the admission step, research R2).

        On the source side, the record's transforms run first, unless `transforms_applied` says
        an earlier admission of the same record ran them. A record that cannot be built is
        recorded in `load_report` and `None` is returned; any other failure is raised again as
        `RecordLoadError`. `PeerNotAdmittedYetError` is raised again for `load_records`, with
        the entries of this admission removed. On the destination side nothing is caught.
        `store_item` replaces `self.add` and returns the stored model; `after_add` runs with the
        stored model and the record. A `record_to_data` that returns `None` skips the record.
        """
        if not self._is_source():
            data = self.record_to_data(record, element, model)  # pylint: disable=assignment-from-no-return  # adapters implement it
            if data is None:
                return None
            item = self._store(model(**data), store_item)
            self._index(item, data)
            if after_add is not None:
                after_add(item, record)
            return item

        kind = element.name
        source_id = source_record_id(self.record_source_id(record))
        self._read_state.admission += 1
        start = len(self.load_report.entries)
        data: dict[str, Any] | None = None
        item: Any = None
        try:
            if element.transforms and not transforms_applied:
                model.apply_transforms(item=record, transforms=element.transforms)
            data = self.record_to_data(record, element, model)  # pylint: disable=assignment-from-no-return  # adapters implement it
            if data is None:
                self._settle_pending(start, kind, source_id, loaded=False)
                return None
            if source_record_id(data.get("local_id")) is not None:
                source_id = source_record_id(data["local_id"])
            item = model(**data)
            stored = self._store(item, store_item)
        except TransformError as exc:
            self._settle_pending(start, kind, source_id, loaded=False)
            self._report(
                LoadReportEntry(
                    record=SourceRecordReference(kind=kind, source_id=source_id),
                    reason="transform_failed",
                    field=exc.field,
                    config_entry=transform_entry(exc.position),
                )
            )
            return None
        except PeerReferenceError as exc:
            self._settle_pending(start, kind, source_id, loaded=False)
            self._record_peer_error(kind, exc)
            return None
        except pydantic.ValidationError as exc:
            self._settle_pending(start, kind, source_id, loaded=False)
            self._record_validation_error(kind, model=model, source_id=source_id, data=data, exc=exc)
            return None
        except ObjectAlreadyExists as exc:
            return self._record_duplicate(
                start, kind, model=model, record=record, item=item, source_id=source_id, data=data, exc=exc
            )
        except PeerNotAdmittedYetError:
            del self.load_report.entries[start:]
            raise
        except RecordLoadError:
            raise
        except Exception as exc:  # research R2: every other failure fails the plan as RecordLoadError
            raise RecordLoadError(kind=kind, source_id=source_id) from exc
        # A defaulted identifier is absent from the data; the stored record holds its value.
        identity = _identity_of(model, data)
        get_identifiers = getattr(stored, "get_identifiers", None)
        if identity is None and get_identifiers is not None:
            identity = dict(get_identifiers())
        self._settle_pending(start, kind, source_id, loaded=True, identity=identity)
        self._index(stored, data)
        if after_add is not None:
            after_add(stored, record)
        return stored

    def _store(self, item: Any, store_item: Callable[[Any], Any] | None) -> Any:
        if store_item is not None:
            return store_item(item)
        self.add(item)  # ty: ignore[unresolved-attribute] -- DiffSync adapter method
        return item

    def _index(self, item: Any, data: Mapping[str, Any]) -> None:
        local_id = source_record_id(data.get("local_id"))
        if local_id is not None:
            self._read_state.peer_index[item.get_type(), local_id] = item.get_unique_id()

    def _report(self, entry: LoadReportEntry) -> None:
        state = self._read_state
        if entry.record.source_id is not None and source_record_id(entry.record.source_id) is None:
            # An adapter that names a record without an ID by a placeholder (`""`, `"None"`).
            entry = entry.model_copy(update={"record": entry.record.model_copy(update={"source_id": None})})
        if entry.admission is None and state.admission:
            entry = entry.model_copy(update={"admission": state.admission})
        state.report.add(entry)
        if not entry.loaded and entry.record.source_id is not None:
            state.not_loaded.setdefault((entry.record.kind, entry.record.source_id), entry)

    def _settle_pending(
        self, start: int, kind: str, source_id: str | None, *, loaded: bool, identity: dict[str, Any] | None = None
    ) -> None:
        """Settle the entries the reference helper made for the record being admitted.

        When the record loaded, they receive its identity, when it is known. When it did not
        load, they no longer describe a loaded record, so they are marked `loaded: false`.
        """
        entries = self.load_report.entries
        for index in range(start, len(entries)):
            entry = entries[index]
            if entry.record.kind != kind or entry.record.source_id != source_id or not entry.loaded:
                continue
            if not loaded:
                entries[index] = entry.model_copy(update={"loaded": False})
            elif identity is not None:
                record = entry.record.model_copy(update={"identity": identity})
                entries[index] = entry.model_copy(update={"record": record})

    def _record_peer_error(self, kind: str, exc: PeerReferenceError) -> None:

        reasons: dict[type[PeerReferenceError], LoadReportReason] = {
            ExcludedRequiredPeerError: "required_peer_excluded_by_filter",
            PeerNotLoadedError: "peer_not_loaded",
            PeerNotFoundError: "peer_not_found_in_source",
        }
        reason = reasons.get(type(exc))
        if reason is None:
            raise RecordLoadError(kind=kind, source_id=exc.source_id, field=exc.field) from exc
        config_entry = None
        if reason == "required_peer_excluded_by_filter":
            config_entry = filter_entry(self.filtered_peer_position(exc.peer_kind, exc.peer_source_id)[1])
        self._report(
            LoadReportEntry(
                record=SourceRecordReference(kind=kind, source_id=exc.source_id),
                reason=reason,
                field=exc.field,
                peer=SourceRecordReference(kind=exc.peer_kind, source_id=exc.peer_source_id),
                config_entry=config_entry,
            )
        )

    def _record_validation_error(
        self,
        kind: str,
        *,
        model: type[Any],
        source_id: str | None,
        data: Mapping[str, Any] | None,
        exc: pydantic.ValidationError,
    ) -> None:

        errors = exc.errors(include_url=False)
        refused = [error for error in errors if not _is_type_error(error["type"])]
        if data is None or not errors or refused:
            field_name = _error_field(refused[0]) if refused else None
            raise RecordLoadError(kind=kind, source_id=source_id, field=field_name) from exc
        record = SourceRecordReference(kind=kind, identity=_identity_of(model, data), source_id=source_id)
        for error in errors:
            self._report(
                LoadReportEntry(
                    record=record, reason="wrong_value_type", field=_error_field(error), value=error.get("input")
                )
            )

    def _record_duplicate(  # pylint: disable=too-many-arguments  # the record being admitted, keyword-only
        self,
        start: int,
        kind: str,
        *,
        model: type[Any],
        record: Any,
        item: Any,
        source_id: str | None,
        data: Mapping[str, Any] | None,
        exc: ObjectAlreadyExists,
    ) -> Any:
        """Record a `duplicate_source_identity`, or return the stored record that `item` repeats.

        A record the source returns again under the same source ID (an overlapping page, for
        example) is one record, not two with one identity: the first read stands, and the
        entries of the second read are dropped. Only a real source ID decides this: a record
        whose raw record has no ID (`record_source_id` is `None`) is always a duplicate.
        """
        if item is None:
            raise RecordLoadError(kind=kind, source_id=source_id) from exc
        existing = self.store.get(model=item.get_type(), identifier=item.get_unique_id())
        existing_source_id = source_record_id(getattr(existing, "local_id", None))
        if (
            source_record_id(self.record_source_id(record)) is not None
            and existing_source_id is not None
            and existing_source_id == source_id
        ):
            del self.load_report.entries[start:]
            logger.warning("%s record %s was read twice; the first read is kept", kind, source_id)
            return existing
        self._settle_pending(start, kind, source_id, loaded=False)
        identity = _identity_of(model, data)
        self._report(
            LoadReportEntry(
                record=SourceRecordReference(kind=kind, identity=identity, source_id=source_id),
                reason="duplicate_source_identity",
                existing=SourceRecordReference(
                    kind=kind,
                    identity=identity,
                    source_id=existing_source_id,
                ),
            )
        )
        return None

    def cursor_tier_for(self, model_name: str) -> CursorTier:  # noqa: ARG002  # pylint: disable=unused-argument
        """Strongest cursor tier the adapter supports for this model.

        Default = NONE (always full extract). Override per adapter.
        """
        return CursorTier.NONE

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterable[dict]:  # pylint: disable=unused-argument  # abstract hook
        """Yield raw upstream records changed since `cursor`.

        Adapters that override `cursor_tier_for` to a non-NONE tier MUST
        implement this. Records are dicts in the same shape `model_loader`
        feeds to `add(...)` (DiffSync model fields).
        """
        msg = (
            f"{type(self).__name__}.list_changed_since is not implemented. "
            "Override it or keep cursor_tier_for returning NONE."
        )
        raise NotImplementedError(msg)

    def list_existing_ids(self, model_name: str) -> Iterable[str]:  # pylint: disable=unused-argument  # abstract hook
        """Yield current `unique_id` strings for `model_name` in the source
        of truth. Used for delete detection between incremental runs.
        """
        msg = f"{type(self).__name__}.list_existing_ids is not implemented. Override it for soft-delete detection."
        raise NotImplementedError(msg)


class DiffSyncModelMixin:
    # Set on generated subclasses (see generator/templates/diffsync_models.j2).
    local_id: str | None = None

    def _get_unvalidated_unique_id(self) -> str:
        """Return the DiffSync identifier for diagnostic string conversion."""
        return super().get_unique_id()  # ty: ignore[unresolved-attribute] -- cooperative DiffSync mixin

    def get_unique_id(self) -> str:
        """Return the DiffSync identifier, refusing nullable identifier values."""
        model_kind = getattr(self, "_modelname", type(self).__name__)
        for identifier in getattr(self, "_identifiers", ()):
            if getattr(self, identifier, None) is None:
                msg = f"{model_kind} identifier field {identifier!r} cannot be None"
                raise ValueError(msg)
        return self._get_unvalidated_unique_id()

    def __str__(self) -> str:
        """Return a diagnostic string even when an identifier is incomplete."""
        return self._get_unvalidated_unique_id()

    def __repr__(self) -> str:
        """Return a diagnostic representation even when an identifier is incomplete."""
        model_kind = getattr(self, "_modelname", type(self).__name__)
        return f'{model_kind} "{self._get_unvalidated_unique_id()}"'

    @classmethod
    def apply_filter(cls, field_value: Any, operation: str, value: Any) -> bool:
        """Apply a specified operation to a field value."""
        operation_func = FILTERS_OPERATIONS.get(operation)
        if operation_func is None:
            msg = f"Unsupported operation: {operation}"
            raise ValueError(msg)

        # Handle is_empty and is_not_empty which do not use the value argument
        if operation in {"is_empty", "is_not_empty"}:
            return operation_func(field_value)

        return operation_func(field_value, value)

    @classmethod
    def apply_filters(cls, item: dict[str, Any], filters: list[SchemaMappingFilter]) -> bool:
        """Apply filters to an item and return True if it passes all filters."""
        return first_failing_filter(cls, item, filters) is None

    @classmethod
    def apply_transform(cls, item: dict[str, Any], transform_expr: str, field: str) -> None:
        """Apply a transformation expression using Jinja2 to a specified field in the item.

        Renders in ``SandboxedNativeEnvironment`` so expressions return native Python types
        (list/dict/bool/int/str) instead of always strings, and cannot reach private
        attributes, unsafe callables or mutating methods.
        """
        try:
            native_env = new_transform_environment()

            # Allow subclasses to add custom filters
            add_custom_filters: Callable[..., None] | None = getattr(cls, "_add_custom_filters", None)
            if add_custom_filters is not None:
                add_custom_filters(native_env, item)

            # Compile the template with the native env
            template = native_env.from_string(transform_expr)

            # Render with the item as context → returns a native Python value
            transformed_value = template.render(**item)

            # Filters such as map and select return a lazy generator, possibly nested inside
            # other results. Consume them here so a missing key raises inside this method and
            # the field holds lists, not generators.
            transformed_value = _materialize_lazy(transformed_value)

            # Native rendering returns a lone expression's value without str(), so a missing
            # key or a refused attribute comes back as an Undefined instead of raising.
            _raise_if_undefined(transformed_value)

            # Always assign the result, even if it's an empty list/dict/False/0.
            # Only skip if the result is literally None (meaning "don't set").
            if transformed_value is not None:
                item[field] = transformed_value

        except SecurityError as exc:
            msg = f"Failed to transform '{field}' with '{transform_expr}': not allowed in the transform sandbox: {exc}"
            raise TransformError(msg, field=field) from exc
        except Exception as exc:
            msg = f"Failed to transform '{field}' with '{transform_expr}': {exc}"
            raise TransformError(msg, field=field) from exc

    @classmethod
    def apply_transforms(cls, item: dict[str, Any], transforms: list[SchemaMappingTransform]) -> dict[str, Any]:
        """Apply a list of structured transformations to an item.

        A failed transform raises `TransformError` with its position in `transforms`.
        """
        for position, transform_obj in enumerate(transforms):
            field = transform_obj.field
            expr = transform_obj.expression
            try:
                cls.apply_transform(item=item, transform_expr=expr, field=field)
            except TransformError as exc:
                exc.position = position
                raise
        return item

    @classmethod
    def filter_records(cls, records: list[dict], schema_mapping: SchemaMappingModel) -> list[dict]:
        """
        Apply filters to the records based on the schema mapping configuration.
        """
        filters = schema_mapping.filters or []
        if not filters:
            return records
        filtered_records = []
        for record in records:
            if cls.apply_filters(item=record, filters=filters):
                filtered_records.append(record)
        return filtered_records

    @classmethod
    def transform_records(cls, records: list[dict], schema_mapping: SchemaMappingModel) -> list[dict]:
        """
        Apply transformations to the records based on the schema mapping configuration.
        """
        transforms = schema_mapping.transforms or []
        if not transforms:
            return records
        transformed_records = []
        for record in records:
            transformed_record = cls.apply_transforms(item=record, transforms=transforms)
            transformed_records.append(transformed_record)
        return transformed_records

    @classmethod
    def is_list(cls, name):
        # The declared default decides, not the annotation: a list-typed field
        # left unset is not a list here.
        fields = getattr(cls, "model_fields", None) or {}
        field = fields.get(name)
        if not field:
            msg = f"Unable to find the field {name} under {cls}"
            raise ValueError(msg)

        return isinstance(field.default, list)
