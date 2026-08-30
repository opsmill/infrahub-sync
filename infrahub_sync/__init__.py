from __future__ import annotations

import logging
import operator
import re
from typing import TYPE_CHECKING, Any, ClassVar, Union

from infrahub_sync.cache.cursors import CursorTier

if TYPE_CHECKING:
    from collections.abc import Iterable

    from infrahub_sync.cache.cursors import CursorState

import pydantic

if TYPE_CHECKING:
    from collections.abc import Callable

    from diffsync.store import BaseStore
from diffsync.enum import DiffSyncFlags
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
from netutils.ip import is_ip_within as netutils_is_ip_within
from packaging import version

from infrahub_sync.adapters.utils import get_value

logger = logging.getLogger(__name__)

# Pydantic v1/v2 compatibility shim — runtime branch picks the right decorator + kwargs.
if version.parse(pydantic.__version__) >= version.parse("2.0.0"):
    from pydantic import field_validator as validator_decorator

    validator_kwargs: dict[str, Any] = {"mode": "before"}
else:
    from pydantic import validator as validator_decorator  # ty: ignore[deprecated]

    validator_kwargs = {"pre": True, "allow_reuse": True}


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

    @validator_decorator("diffsync_flags", **validator_kwargs)  # ty: ignore[no-matching-overload]
    def convert_str_to_enum(cls, v):  # pylint: disable=no-self-argument  # a pydantic validator: `cls` is correct
        if not isinstance(v, list):
            msg = "diffsync_flags must be provided as a list"
            raise TypeError(msg)
        new_flags = []
        for item in v:
            if isinstance(item, str):
                try:
                    new_flags.append(DiffSyncFlags[item])
                except KeyError:
                    msg = f"Invalid DiffSyncFlags value: {item}"
                    raise ValueError(msg)
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

    def compute_order_and_tiers(self) -> tuple[list[str], list[set[str]] | None]:
        """Return `(flat_order, tiers)` from a single topological pass.

        `tiers` is `None` when an explicit `order` is configured. Callers that
        need both the flat order and the tier layout should use this rather
        than calling `compute_order()` and `compute_tiers()` separately, which
        would sort the graph twice. Logs the tier layout and any dropped
        optional edges at INFO level.
        """
        if self.order:
            return list(self.order), None
        # Imported here to avoid a circular import at module load.
        from infrahub_sync.dependency_graph import compute_tiers, flatten_tiers

        tiers, dropped = compute_tiers(self.schema_mapping)
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
    if not effective & DiffSyncFlags.SKIP_UNMATCHED_DST:
        requested.add("delete")
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


class DiffSyncMixin:
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

    def model_loader(self, model_name: str, model):
        raise NotImplementedError

    def cursor_tier_for(self, model_name: str) -> CursorTier:  # noqa: ARG002
        """Strongest cursor tier the adapter supports for this model.

        Default = NONE (always full extract). Override per adapter.
        """
        return CursorTier.NONE

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterable[dict]:
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

    def list_existing_ids(self, model_name: str) -> Iterable[str]:
        """Yield current `unique_id` strings for `model_name` in the source
        of truth. Used for delete detection between incremental runs.
        """
        msg = f"{type(self).__name__}.list_existing_ids is not implemented. Override it for soft-delete detection."
        raise NotImplementedError(msg)


class DiffSyncModelMixin:
    # Set on generated subclasses (see generator/templates/diffsync_models.j2).
    local_id: str | None = None

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

        Uses Jinja's NativeEnvironment so expressions return native Python types
        (list/dict/bool/int/str) instead of always strings.
        """
        try:
            native_env = NativeEnvironment(
                undefined=StrictUndefined,  # fail fast on missing keys
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
            )

            # Allow subclasses to add custom filters
            add_custom_filters: Callable[..., None] | None = getattr(cls, "_add_custom_filters", None)
            if add_custom_filters is not None:
                add_custom_filters(native_env, item)

            # Compile the template with the native env
            template = native_env.from_string(transform_expr)

            # Render with the item as context → returns a native Python value
            transformed_value = template.render(**item)

            # Always assign the result, even if it's an empty list/dict/False/0.
            # Only skip if the result is literally None (meaning "don't set").
            if transformed_value is not None:
                item[field] = transformed_value

        except Exception as exc:
            msg = f"Failed to transform '{field}' with '{transform_expr}': {exc}"
            raise ValueError(msg) from exc

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
    def get_resource_name(cls, schema_mapping: list[SchemaMappingModel]) -> str:
        """Get the resource name from the schema mapping."""
        for element in schema_mapping:
            if element.name == cls.__name__:
                if element.mapping is None:
                    msg = f"Resource mapping is unset for class {cls.__name__}"
                    raise ValueError(msg)
                return element.mapping
        msg = f"Resource name not found for class {cls.__name__}"
        raise ValueError(msg)

    @classmethod
    def is_list(cls, name):
        # Pydantic v2 exposes `model_fields`; v1 uses `__fields__`. Try both.
        fields = getattr(cls, "model_fields", None) or getattr(cls, "__fields__", None) or {}
        field = fields.get(name)
        if not field:
            msg = f"Unable to find the field {name} under {cls}"
            raise ValueError(msg)

        return isinstance(field.default, list)
