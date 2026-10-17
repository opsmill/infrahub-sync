from __future__ import annotations

import logging
import operator
import re
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Union

import pydantic
from diffsync import DiffSyncModel
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
from infrahub_sync.load_errors import SideLoadError, TransformError, record_source_id  # noqa: F401 -- re-exported
from infrahub_sync.load_report import (  # noqa: F401 -- re-exported
    LOAD_REPORT_VALUE_LIMIT,
    AdmissionState,
    LoadReportEntry,
    LoadReportReason,
    SourceRecordReference,
    filter_entry,
    limited_value,
    removed_by_filters,
    transform_each,
    transform_failure_entry,
    transform_records,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from diffsync.store import BaseStore

    from infrahub_sync.cache.cursors import CursorState
    from infrahub_sync.runtime_schema import RuntimeModelPlan

    RecordToData = Callable[[Any, "SchemaMappingModel | None", type[DiffSyncModel]], Mapping[str, Any]]

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

        Logs the tier layout and any dropped optional edges at INFO level.
        """
        order, _tiers = self.compute_order_and_tiers()
        return order

    def compute_order_and_tiers(
        self, generic_peers: Mapping[str, tuple[str, ...]] | None = None
    ) -> tuple[list[str], list[set[str]] | None]:
        """Return `(flat_order, tiers)` from a single topological pass.

        `tiers` is `None` when an explicit `order` is configured. Callers that
        need both the flat order and the tier layout should use this rather
        than calling `compute_order()` and `compute_tiers()` separately, which
        would sort the graph twice. Logs the tier layout and any dropped
        optional edges at INFO level.
        """
        if self.order:
            return list(self.order), None
        tiers, dropped = compute_tiers(self.schema_mapping, generic_peers)
        for idx, tier in enumerate(tiers):
            logger.info("tier %d (%d): %s", idx, len(tier), sorted(tier))
        if dropped:
            logger.warning(
                "dropped optional edges to break cycles: %s",
                dropped,
            )
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


NOT_IN_SOURCE_PLACEHOLDER_PREFIX = "infrahub-sync:not-in-source:"
"""Prefix of the reference value that stands for an optional peer the source does not return."""


def not_in_source_placeholder(peer_kind: str, peer_source_id: str) -> str:
    """Return the reference value that marks an optional peer as not in the source (research R4)."""
    return f"{NOT_IN_SOURCE_PLACEHOLDER_PREFIX}{peer_kind}:{peer_source_id}"


def is_not_in_source_placeholder(value: object) -> bool:
    """Tell whether a reference value is a placeholder of `not_in_source_placeholder`."""
    return isinstance(value, str) and value.startswith(NOT_IN_SOURCE_PLACEHOLDER_PREFIX)


class PeerReferenceError(Exception):
    """A source record refers to a peer that the load cannot resolve.

    The message is built from the typed attributes only, never from record data or from the
    text of another error (research R13).
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


class ExcludedRequiredPeerError(PeerReferenceError):
    """A required relationship's peer was removed by a configured filter."""

    problem = "refers to a required peer that a configured filter removed"


class PeerNotLoadedError(PeerReferenceError):
    """The peer is in the load report, and its identity is not known."""

    problem = "refers to a peer that was not loaded"


class PeerNotFoundError(PeerReferenceError):
    """A required relationship's peer is not in the source."""

    problem = "refers to a required peer that is not in the source"


class RecordLoadError(Exception):
    """A source record failed to load for a reason the admission step does not record.

    It fails the plan (FR-018). The message names the kind, the record ID and the field only.
    """

    def __init__(self, *, kind: str, source_id: str | None, field: str | None) -> None:
        self.kind = kind
        self.source_id = source_id
        self.field = field
        location = f" field {field}" if field else ""
        super().__init__(f"{kind} record {source_id}{location} cannot be loaded")


_TYPE_ERROR_SUFFIXES = ("_type", "_parsing", "_parsing_size", "_from_float")


def _is_type_error(error_type: str) -> bool:
    """Tell whether a pydantic error type refuses a value because of its type."""
    return error_type.endswith(_TYPE_ERROR_SUFFIXES)


def _identity_of(model: type[DiffSyncModel], data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the identifier values of `data`, or None when one identifier is missing."""
    if data is None:
        return None
    identifiers = tuple(getattr(model, "_identifiers", ()))
    if not identifiers or any(name not in data for name in identifiers):
        return None
    return {name: data[name] for name in identifiers}


def _error_field(error: Mapping[str, Any]) -> str | None:
    location = error.get("loc") or ()
    return str(location[0]) if location else None


def identity_record_data(
    record: Mapping[str, Any],
    element: SchemaMappingModel | None,  # noqa: ARG001  # pylint: disable=unused-argument  # the `to_data` signature
    model: type[DiffSyncModel],  # noqa: ARG001  # pylint: disable=unused-argument  # the `to_data` signature
) -> dict[str, Any]:
    """Return a row that already holds model data unchanged (the engine's incremental rows)."""
    return dict(record)


class DiffSyncMixin:
    """Adapter base class: owns the per-record load loop and the one admission step.

    An adapter implements `list_records` and `record_to_data`. The default `model_loader`
    fetches, filters and transforms the records of each mapping entry and passes each record
    to `load_record`, which builds the model, adds it to the store and, on the source side,
    records a record that cannot be built in `load_report` (research R3). References to peers
    resolve through `peer_unique_id` (research R4).
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

    def record_to_data(self, record: Any, element: SchemaMappingModel, model: type[DiffSyncModel]) -> dict[str, Any]:  # pylint: disable=unused-argument  # abstract hook
        """Convert one raw record to model data, with `local_id` set to its source record ID."""
        msg = f"{type(self).__name__}.record_to_data is not implemented."
        raise NotImplementedError(msg)

    def record_source_id(self, record: Any) -> str | None:  # an adapter may override it
        """Return the source record ID of a raw record, used to know which IDs a filter removed."""
        value = record.get("id") if isinstance(record, Mapping) else getattr(record, "id", None)
        return None if value is None else str(value)

    # -- Shared loader step ---------------------------------------------------------------

    @property
    def _admission(self) -> AdmissionState:
        state = self.__dict__.get("_admission_state")
        if state is None:
            state = AdmissionState()
            self.__dict__["_admission_state"] = state
        return state

    @property
    def load_report(self) -> list[LoadReportEntry]:
        """The source records the admission step recorded, in the order they were found."""
        return self._admission.load_report

    def _is_source(self) -> bool:
        return getattr(self, "target", None) == "source"

    def model_loader(self, model_name: str, model: type[Any]) -> None:
        """Load every record of `model_name` through `load_record`.

        On the source side, the IDs a configured filter removed are kept, each with the
        position of the filter that removed it, so that a reference to one of them is told
        apart from a reference to a peer the source does not return. A record whose transform
        fails is recorded in `load_report` and is not loaded.
        """
        is_source = self._is_source()
        removed: dict[str, int | None] = {}
        retained_ids: set[str] = set()
        filtered_entry = False
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue
            if not element.mapping:
                logger.info("No mapping defined for '%s', skipping", element.name)
                continue
            records = list(self.list_records(model_name, element))
            if is_source:
                filtered = model.filter_records(records=records, schema_mapping=element)
                if element.filters:
                    filtered_entry = True
                    kept = self._source_ids(filtered)
                    retained_ids.update(kept)
                    for source_id, position in removed_by_filters(
                        model, records, element, kept_ids=kept, source_id=self.record_source_id
                    ).items():
                        removed.setdefault(source_id, position)
                records = transform_records(model, filtered, element, self.record_transform_failure)
            for record in records:
                self.load_record(model, element, record)
        if is_source and filtered_entry:
            excluded = {source_id: position for source_id, position in removed.items() if source_id not in retained_ids}
            self._admission.filtered_ids.setdefault(model_name, {}).update(excluded)

    def _source_ids(self, records: Iterable[Any]) -> set[str]:
        return {source_id for record in records if (source_id := self.record_source_id(record)) is not None}

    def excluded_peer_ids(self, peer_kind: str) -> set[str]:
        """Return the source IDs of `peer_kind` that a configured filter removed."""
        return set(self._admission.filtered_ids.get(peer_kind, {}))

    def excluded_peer_filter(self, peer_kind: str, peer_source_id: str) -> str | None:
        """Name the filter that removed a peer (`filters[0]`), or None when it is not known."""
        self.excluded_peer_ids(peer_kind)  # an adapter may read the filtered IDs on first use
        return filter_entry(self._admission.filtered_ids.get(peer_kind, {}).get(peer_source_id))

    def record_transform_failure(self, error: TransformError) -> None:
        """Record a source record whose transform failed; it is not loaded (spec 009 `transform_failed`)."""
        self._report(transform_failure_entry(error))

    def load_record(  # pylint: disable=too-many-arguments  # the hooks are keyword-only
        self,
        model: type[DiffSyncModel],
        element: SchemaMappingModel | None,
        record: Any,  # a raw record of any adapter
        *,
        after_add: Callable[[DiffSyncModel, Any], None] | None = None,
        to_data: RecordToData | None = None,
        store_item: Callable[[DiffSyncModel], DiffSyncModel] | None = None,
    ) -> DiffSyncModel | None:
        """Build one record, add it to the store and return it (the admission step, research R3).

        On the source side, a record that cannot be built is recorded in `load_report` and
        None is returned; any other failure is raised again as `RecordLoadError`. On the
        destination side nothing is caught.

        `to_data` replaces `record_to_data` (the engine passes `identity_record_data` for its
        incremental rows); `store_item` replaces `self.add` and returns the stored model.
        """
        if not self._is_source():
            data = self._record_data(record, element, model, to_data)
            item = self._store(model(**data), store_item)
            self._index(item, data)
            if after_add is not None:
                after_add(item, record)
            return item
        return self._admit_source_record(
            model, element, record, after_add=after_add, to_data=to_data, store_item=store_item
        )

    def _record_data(
        self,
        record: Any,  # a raw record of any adapter
        element: SchemaMappingModel | None,
        model: type[DiffSyncModel],
        to_data: RecordToData | None,
    ) -> dict[str, Any]:
        if to_data is not None:
            return dict(to_data(record, element, model))
        if element is None:
            msg = "load_record needs a mapping entry unless a `to_data` conversion is given."
            raise TypeError(msg)
        return self.record_to_data(record, element, model)  # pylint: disable=assignment-from-no-return  # adapters implement it

    def _store(self, item: DiffSyncModel, store_item: Callable[[DiffSyncModel], DiffSyncModel] | None) -> DiffSyncModel:
        if store_item is not None:
            return store_item(item)
        self.add(item)  # ty: ignore[unresolved-attribute]  # DiffSync adapter method
        return item

    def _admit_source_record(  # pylint: disable=too-many-arguments  # the hooks are keyword-only
        self,
        model: type[DiffSyncModel],
        element: SchemaMappingModel | None,
        record: Any,  # a raw record of any adapter
        *,
        after_add: Callable[[DiffSyncModel, Any], None] | None,
        to_data: RecordToData | None,
        store_item: Callable[[DiffSyncModel], DiffSyncModel] | None,
    ) -> DiffSyncModel | None:
        kind = getattr(model, "_modelname", element.name if element is not None else model.__name__)
        report_start = len(self.load_report)
        data: dict[str, Any] | None = None
        try:
            data = self._record_data(record, element, model, to_data)
            item = self._store(model(**data), store_item)
        except PeerReferenceError as exc:
            self._settle_pending(report_start, kind, exc.source_id, identity=None)
            self._record_peer_error(kind, exc, since=report_start)
            return None
        except pydantic.ValidationError as exc:
            self._settle_pending(report_start, kind, self._data_source_id(record, data), identity=None)
            self._record_validation_error(kind, model=model, record=record, data=data, exc=exc)
            return None
        except ObjectAlreadyExists as exc:
            self._settle_pending(report_start, kind, self._data_source_id(record, data), identity=None)
            self._record_duplicate(kind, model=model, record=record, data=data or {}, exc=exc)
            return None
        except RecordLoadError:
            raise
        except Exception as exc:  # research R3: every other failure fails the plan as RecordLoadError
            raise RecordLoadError(kind=kind, source_id=self._data_source_id(record, data), field=None) from exc
        self._settle_pending(report_start, kind, self._data_source_id(record, data), identity=_identity_of(model, data))
        self._index(item, data)
        if after_add is not None:
            after_add(item, record)
        return item

    def _data_source_id(self, record: Any, data: Mapping[str, Any] | None) -> str | None:
        if data is not None and data.get("local_id") is not None:
            return str(data["local_id"])
        return self.record_source_id(record)

    def _index(self, item: DiffSyncModel, data: Mapping[str, Any]) -> None:
        local_id = data.get("local_id")
        if local_id is not None:
            self._admission.peer_index[item.get_type(), str(local_id)] = item.get_unique_id()

    def _report(self, entry: LoadReportEntry) -> None:
        self._admission.load_report.append(entry)
        if not entry.loaded and entry.source_id is not None:
            self._admission.not_loaded.setdefault((entry.kind, entry.source_id), entry)

    def _settle_pending(self, start: int, kind: str, source_id: str | None, *, identity: dict[str, Any] | None) -> None:
        """Settle the entries `peer_unique_id` made for the record being admitted.

        When the record loaded, they receive its identity. When it did not, they no longer
        describe a loaded record, so they are marked `loaded: false`.
        """
        state = self._admission
        report = state.load_report
        for index in range(start, len(report)):
            entry = report[index]
            if entry.kind != kind or entry.source_id != source_id or not entry.loaded:
                continue
            if identity is not None:
                report[index] = entry.model_copy(update={"identity": identity})
            else:
                report[index] = entry.model_copy(update={"loaded": False})
                if source_id is not None:
                    state.not_loaded.setdefault((kind, source_id), report[index])

    def _record_peer_error(self, kind: str, exc: PeerReferenceError, *, since: int) -> None:
        reasons: dict[type[PeerReferenceError], LoadReportReason] = {
            ExcludedRequiredPeerError: "required_peer_excluded_by_filter",
            PeerNotLoadedError: "peer_not_loaded",
            PeerNotFoundError: "peer_not_found_in_source",
        }
        reason = reasons.get(type(exc))
        if reason is None:
            raise RecordLoadError(kind=kind, source_id=exc.source_id, field=exc.field) from exc
        peer = SourceRecordReference(kind=exc.peer_kind, identity=None, source_id=exc.peer_source_id)
        entry = LoadReportEntry(kind=kind, source_id=exc.source_id, reason=reason, field=exc.field, peer=peer)
        if reason == "required_peer_excluded_by_filter":
            entry = entry.model_copy(
                update={"config_entry": self.excluded_peer_filter(exc.peer_kind, exc.peer_source_id)}
            )
        # A cardinality-many reference records each peer before it raises for the first one.
        if entry not in self._admission.load_report[since:]:
            self._report(entry)

    def _record_validation_error(
        self,
        kind: str,
        *,
        model: type[DiffSyncModel],
        record: Any,
        data: Mapping[str, Any] | None,
        exc: pydantic.ValidationError,
    ) -> None:
        errors = exc.errors(include_url=False)
        source_id = self._data_source_id(record, data)
        refused = [error for error in errors if not _is_type_error(error["type"])]
        if data is None or not errors or refused:
            field_name = _error_field(refused[0]) if refused else None
            raise RecordLoadError(kind=kind, source_id=source_id, field=field_name) from exc
        identity = _identity_of(model, data)
        for error in errors:
            self._report(
                LoadReportEntry(
                    kind=kind,
                    source_id=source_id,
                    identity=identity,
                    reason="wrong_value_type",
                    field=_error_field(error),
                    error_type=error["type"],
                    value=limited_value(error.get("input")),
                )
            )

    def _record_duplicate(
        self,
        kind: str,
        *,
        model: type[DiffSyncModel],
        record: Any,
        data: Mapping[str, Any],
        exc: ObjectAlreadyExists,
    ) -> None:
        # diffsync 2.2 passes the refused object as `existing_object`; the record kept in the
        # store is the one stored under the same unique ID.
        refused = exc.existing_object
        if not isinstance(refused, DiffSyncModel):
            raise RecordLoadError(kind=kind, source_id=self._data_source_id(record, data), field=None) from exc
        existing = self.store.get(model=kind, identifier=refused.get_unique_id())
        existing_identity = existing.get_identifiers()
        existing_source_id = getattr(existing, "local_id", None)
        self._report(
            LoadReportEntry(
                kind=kind,
                source_id=self._data_source_id(record, data),
                identity=_identity_of(model, data),
                reason="duplicate_source_identity",
                existing=SourceRecordReference(
                    kind=kind,
                    identity=dict(existing_identity) if existing_identity is not None else None,
                    source_id=None if existing_source_id is None else str(existing_source_id),
                ),
            )
        )

    def peer_unique_id(
        self,
        peer_kind: str,
        peer_source_id: str,
        *,
        owner: tuple[str, str | None],
        field: str,
        required: bool,
    ) -> str | None:
        """Resolve a source reference to the peer's unique ID (research R4).

        `owner` is the referring record's kind and source record ID. The peer is looked up in
        the store, then in the load report, then in the IDs a configured filter removed. A
        peer found nowhere is not in the source. A peer in the load report without a known
        identity gives no value through an optional relationship: the record is kept without
        that relationship (FR-005).
        """
        peer_source_id = str(peer_source_id)
        state = self._admission
        unique_id = state.peer_index.get((peer_kind, peer_source_id))
        if unique_id is None:
            unique_id = self._scan_store(peer_kind, peer_source_id)
        if unique_id is not None:
            return unique_id

        owner_kind, owner_source_id = owner
        details = {
            "kind": owner_kind,
            "source_id": owner_source_id,
            "field": field,
            "peer_kind": peer_kind,
            "peer_source_id": peer_source_id,
        }
        peer_entry = state.not_loaded.get((peer_kind, peer_source_id))
        if peer_entry is not None and peer_entry.identity is not None:
            return getattr(self, peer_kind).create_unique_id(**peer_entry.identity)
        if peer_entry is not None:
            if required:
                raise PeerNotLoadedError(**details)
            reason, value = "peer_not_loaded", None  # FR-005: kept without the link
        elif peer_source_id in self.excluded_peer_ids(peer_kind):
            if required:
                raise ExcludedRequiredPeerError(**details)
            return None
        elif required:
            raise PeerNotFoundError(**details)
        else:
            reason, value = "peer_not_found_in_source", not_in_source_placeholder(peer_kind, peer_source_id)
        peer = SourceRecordReference(kind=peer_kind, identity=None, source_id=peer_source_id)
        entry = LoadReportEntry(kind=owner_kind, source_id=owner_source_id, field=field, reason=reason, peer=peer)
        self._report(entry.model_copy(update={"loaded": True}))
        return value

    def _scan_store(self, peer_kind: str, peer_source_id: str) -> str | None:
        """Find a peer added outside `load_record`, by its `local_id`."""
        for item in self.store.get_all(model=peer_kind):
            if getattr(item, "local_id", None) == peer_source_id:
                unique_id = item.get_unique_id()
                self._admission.peer_index[peer_kind, peer_source_id] = unique_id
                return unique_id
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

    def load_changed_since(
        self,
        model_name: str,
        model: type[DiffSyncModel],
        cursor: CursorState,
        *,
        store_item: Callable[[DiffSyncModel], DiffSyncModel],
    ) -> None:
        """Admit each record changed since `cursor` through `load_record` (research R3).

        The default reads `list_changed_since`, whose rows hold model data. An adapter that
        converts with `record_to_data` overrides it to admit the raw records, as a full load does.
        """
        for row in self.list_changed_since(model_name, cursor):
            self.load_record(model, None, row, to_data=identity_record_data, store_item=store_item)

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
        for filter_obj in filters:
            # Use dot notation to access attributes
            field_value = get_value(obj=item, name=filter_obj.field)
            if not cls.apply_filter(
                field_value=field_value,
                operation=filter_obj.operation,
                value=filter_obj.value,
            ):
                return False
        return True

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
            raise TransformError(msg, field=field, source_id=record_source_id(item)) from exc
        except Exception as exc:
            msg = f"Failed to transform '{field}' with '{transform_expr}': {exc}"
            raise TransformError(msg, field=field, source_id=record_source_id(item)) from exc

    @classmethod
    def apply_transforms(cls, item: dict[str, Any], transforms: list[SchemaMappingTransform]) -> dict[str, Any]:
        """Apply a list of structured transformations to an item."""
        for transform_obj in transforms:
            field = transform_obj.field
            expr = transform_obj.expression
            cls.apply_transform(item=item, transform_expr=expr, field=field)
        return item

    @classmethod
    def filter_records(cls, records: list[dict], schema_mapping: SchemaMappingModel) -> list[dict]:
        """Apply filters to the records based on the schema mapping configuration."""
        filters = schema_mapping.filters or []
        if not filters:
            return records
        return [record for record in records if cls.apply_filters(item=record, filters=filters)]

    @classmethod
    def transform_records(
        cls,
        records: list[dict],
        schema_mapping: SchemaMappingModel,
        on_failure: Callable[[TransformError], None] | None = None,
    ) -> list[dict]:
        """Apply transformations to the records; a `TransformError` names the entry's kind and the transform.

        With `on_failure`, a record whose transform fails is passed to it and left out (`transform_each`).
        """
        return transform_each(cls, records, schema_mapping, on_failure)

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
