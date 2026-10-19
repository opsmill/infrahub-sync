from __future__ import annotations

import csv
import logging
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectAlreadyExists
from typing_extensions import Self

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .utils import get_value

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def read_csv(path: Path, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Read a CSV file into a list of dictionaries keyed by the header row.

    Recognized settings:
        delimiter: Field separator (default ",").
        encoding: File encoding (default "utf-8-sig", which also strips a UTF-8 BOM).
        empty_as_none: Treat empty cells as ``None`` instead of "" (default True).

    Raises:
        ValueError: The header repeats a column name, or a row has more cells than the header.
    """
    delimiter = settings.get("delimiter", ",")
    encoding = settings.get("encoding", "utf-8-sig")
    empty_as_none = settings.get("empty_as_none", True)

    records: list[dict[str, Any]] = []
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            return records

        duplicated = sorted({name for name in reader.fieldnames if reader.fieldnames.count(name) > 1})
        if duplicated:
            msg = f"Duplicate column(s) in header of '{path}': {', '.join(duplicated)}"
            raise ValueError(msg)

        for row in reader:
            # DictReader stores cells beyond the header under the ``None`` key. Trailing empty
            # cells (a common spreadsheet export artefact) are harmless; anything else is data
            # that has no column to land in.
            extra = row.pop(None, None)
            if extra and any(cell.strip() for cell in extra):
                msg = f"Line {reader.line_num} of '{path}' has more cells than the header ({len(reader.fieldnames)})"
                raise ValueError(msg)
            if empty_as_none:
                records.append({key: (value or None) for key, value in row.items()})
            else:
                records.append(dict(row))
    return records


# Registry of file readers keyed by normalized format name. Adding support for a
# new format (json, yaml, excel, ...) is a matter of writing a reader function
# with the same signature and registering it here.
FILE_READERS: dict[str, Callable[[Path, dict[str, Any]], list[dict[str, Any]]]] = {
    "csv": read_csv,
}

# Map file extensions to a format name in FILE_READERS.
EXTENSION_FORMATS: dict[str, str] = {
    ".csv": "csv",
}


def _annotation_is_list(annotation: Any) -> bool:
    """Return True when a type annotation is a list, including ``list[X] | None``."""
    if annotation is list or get_origin(annotation) is list:
        return True
    if get_origin(annotation) in {Union, types.UnionType}:
        return any(_annotation_is_list(arg) for arg in get_args(annotation))
    return False


class FileAdapter(DiffSyncMixin, Adapter):
    """Adapter that loads data from local file exports (CSV today, extensible to other formats).

    Each entry in ``schema_mapping`` maps a model to a file via its ``mapping`` key,
    which is interpreted as a path relative to the configured base directory (or an
    absolute path). The file format is inferred from the extension unless overridden
    through the ``format`` setting.
    """

    type = "File"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.settings = adapter.settings or {}
        self.config = config
        self.base_directory = self._resolve_base_directory(config=config)
        self.format_override = self.settings.get("format")
        self.list_separator = self.settings.get("list_separator", ";")
        if not isinstance(self.list_separator, str) or not self.list_separator:
            msg = "The 'list_separator' setting of the file adapter must be a non-empty string."
            raise ValueError(msg)
        # Per-model lookup of {identifier value or local_id: unique_id}, built lazily on first use.
        self._reference_index: dict[str, dict[str, str]] = {}

    def _resolve_base_directory(self, config: SyncConfig) -> Path:
        """Determine the directory used to resolve relative file paths.

        Priority: the ``directory`` setting, then the sync instance directory, then CWD.
        A relative ``directory`` is resolved against the sync instance directory so a
        config works regardless of where the CLI is invoked from.
        """
        instance_directory = getattr(config, "directory", None)
        base = Path(instance_directory) if instance_directory else Path.cwd()
        directory = self.settings.get("directory")
        if directory:
            return base / directory
        return base

    def _resolve_path(self, mapping: str) -> Path:
        """Resolve a schema mapping value to an absolute file path."""
        path = Path(mapping)
        if not path.is_absolute():
            path = self.base_directory / path
        return path

    def _read_file(self, path: Path) -> list[dict[str, Any]]:
        """Read a file using the reader registered for its format."""
        if not path.is_file():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)

        file_format = (self.format_override or EXTENSION_FORMATS.get(path.suffix.lower()) or "").lower()
        reader = FILE_READERS.get(file_format)
        if reader is None:
            supported = ", ".join(sorted(FILE_READERS)) or "none"
            msg = (
                f"Unsupported file format for '{path}'. "
                f"Supported formats: {supported}. "
                f"Set the 'format' setting explicitly or use a recognized extension "
                f"({', '.join(sorted(EXTENSION_FORMATS))})."
            )
            raise ValueError(msg)

        return reader(path, self.settings)

    def _validate_mapping(self, mapping: SchemaMappingModel, model: type[FileModel]) -> None:
        """Fail fast on schema mappings the adapter cannot load meaningfully."""
        if not mapping.fields:
            msg = f"No fields defined in schema mapping for model {model.__name__}"
            raise ValueError(msg)
        known_models = {element.name for element in self.config.schema_mapping}
        for field in mapping.fields:
            if field.reference and field.reference not in known_models:
                msg = (
                    f"Field '{field.name}' of {mapping.name} references '{field.reference}', "
                    "which is not defined in schema_mapping."
                )
                raise ValueError(msg)

    def model_loader(self, model_name: str, model: type[FileModel]) -> None:
        """Load and process models using schema mapping filters and transformations.

        This reads each mapped file, applies filters and transformations as specified
        in the schema mapping, and loads the processed records into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            if not element.mapping:
                logger.info("No mapping defined for '%s', skipping", element.name)
                continue

            self._validate_mapping(mapping=element, model=model)

            path = self._resolve_path(mapping=element.mapping)
            try:
                objs = self._read_file(path=path)
            except (FileNotFoundError, ValueError, OSError, csv.Error) as exc:
                msg = f"Error reading data from '{path}': {exc!s}"
                raise ValueError(msg) from exc

            total = len(objs)
            adapter_type_title = (self.type or "").title()
            if self.config.source.name.title() == adapter_type_title:
                # Filter records
                filtered_objs = model.filter_records(records=objs, schema_mapping=element)
                logger.info("%s: Loading %d/%d %s", self.type, len(filtered_objs), total, element.mapping)
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                logger.info("%s: Loading all %d %s", self.type, total, element.mapping)
                transformed_objs = objs

            # Peers loaded by earlier models are now in the store; rebuild lookups on demand.
            self._reference_index.clear()
            list_fields = {field.name: self._is_list_field(model=model, name=field.name) for field in element.fields}

            # Flat-file rows often repeat the same identifier when a model is derived from a
            # foreign-key column (e.g. every device row carries the same site name). Identical
            # repeats are expected and skipped; repeats carrying different data are reported.
            duplicates = 0
            for obj in transformed_objs:
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model, list_fields=list_fields)
                item = model(**data)
                item.local_id = self._derive_local_id(obj=obj, item=item)
                try:
                    self.add(item)
                except ObjectAlreadyExists:
                    duplicates += 1
                    # LocalStore puts the rejected object (not the stored one) on the exception.
                    existing = self.get(obj=model, identifier=item.get_unique_id())
                    if existing.get_attrs() != item.get_attrs():
                        logger.warning(
                            "%s: %s '%s' appears more than once in %s with different values; "
                            "keeping the first occurrence",
                            self.type,
                            model_name,
                            item.get_unique_id(),
                            element.mapping,
                        )
            if duplicates:
                logger.info(
                    "%s: skipped %d duplicate %s in %s (first occurrence kept)",
                    self.type,
                    duplicates,
                    model_name,
                    element.mapping,
                )

    @staticmethod
    def _is_list_field(model: type[FileModel], name: str) -> bool:
        """Return True when a model field holds a list.

        Generated models declare list attributes as ``list[Any] | None = None``, which
        ``is_list`` (based on the default value) does not detect, so check the annotation too.
        """
        if model.is_list(name=name):
            return True
        field = model.model_fields.get(name)
        return field is not None and _annotation_is_list(field.annotation)

    @staticmethod
    def _derive_local_id(obj: dict[str, Any], item: FileModel) -> str:
        """Derive a source-local identifier for a record.

        Use the row's ``id`` column when the export has one, otherwise the model's unique id.
        ``*_id`` columns are deliberately ignored: in flat exports they are foreign keys.
        """
        obj_id = obj.get("id")
        return str(obj_id) if obj_id not in (None, "") else item.get_unique_id()

    def _resolve_reference(self, reference: str, value: Any, field_name: str) -> str:
        """Resolve a cell value to the unique id of the referenced object already in the store.

        The value may be the peer's identifier (its unique id, with ``__`` joining composite
        identifiers) or the value of the peer file's ``id`` column.
        """
        index = self._reference_index.get(reference)
        if index is None:
            index = {}
            for node in self.store.get_all(model=reference):
                unique_id = node.get_unique_id()
                index[unique_id] = unique_id
                local_id = getattr(node, "local_id", None)
                if local_id is not None:
                    index.setdefault(str(local_id), unique_id)
            self._reference_index[reference] = index

        key = str(value)
        if key not in index:
            msg = (
                f"Unable to resolve '{key}' for field '{field_name}': no {reference} with that identifier "
                f"or id was loaded. Check that {reference} is listed before this model in 'order'."
            )
            raise ValueError(msg)
        return index[key]

    def obj_to_diffsync(
        self,
        obj: dict[str, Any],
        mapping: SchemaMappingModel,
        model: type[FileModel],
        list_fields: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Convert a file record into a DiffSync-ready dictionary.

        Supports static values, simple field mappings, delimited list values, and scalar/list
        references that point at another model's identifier (the natural shape for flat file
        exports). ``local_id`` is set by the caller once the model is built.
        """
        if not mapping.fields:
            msg = f"No fields defined in schema mapping for model {model.__name__}"
            raise ValueError(msg)
        if list_fields is None:
            list_fields = {field.name: self._is_list_field(model=model, name=field.name) for field in mapping.fields}

        data: dict[str, Any] = {}
        for field in mapping.fields:
            field_is_list = list_fields[field.name]

            if field.static is not None:
                data[field.name] = field.static
            elif not field.mapping:
                continue
            elif not field.reference:
                value = get_value(obj, field.mapping)
                if value is not None:
                    data[field.name] = self._split_list(value=value) if field_is_list else value
            else:
                value = get_value(obj, field.mapping)
                if not field_is_list:
                    if value not in (None, ""):
                        data[field.name] = self._resolve_reference(
                            reference=field.reference, value=value, field_name=field.name
                        )
                else:
                    references = self._split_list(value=value) if value is not None else []
                    data[field.name] = sorted(
                        self._resolve_reference(reference=field.reference, value=item, field_name=field.name)
                        for item in references
                        if item not in (None, "")
                    )

        return data

    def _split_list(self, value: Any) -> list[Any]:
        """Normalize a value into a list, splitting delimited strings."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [part.strip() for part in value.split(self.list_separator) if part.strip()]
        return [value]


class FileModel(DiffSyncModelMixin, DiffSyncModel):
    """Model for the file adapter. The file adapter is read-only: it can only be a source."""

    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: dict[Any, Any],
        attrs: dict[Any, Any],
    ) -> Self | None:
        msg = "The file adapter is source-only; writing to files is not supported."
        raise NotImplementedError(msg)

    def update(self, attrs: dict) -> Self | None:
        msg = "The file adapter is source-only; writing to files is not supported."
        raise NotImplementedError(msg)

    def delete(self) -> Self | None:
        msg = "The file adapter is source-only; writing to files is not supported."
        raise NotImplementedError(msg)
